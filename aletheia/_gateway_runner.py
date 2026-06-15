"""Background gateway runner — spawned by `aletheia gateway start`.

Runs the API server under a supervisor loop: if the inner server process exits
abnormally it is respawned with exponential backoff, up to a bounded number of
restarts within a window, after which the supervisor gives up and records it.
This lets the system survive transient crashes while staying unattended.

The supervisor writes the PID file exactly once (its own pid), so `gateway
stop`/`status` keep working. The inner server's stderr is sent to
data/logs/errors.log instead of being discarded.
"""

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from aletheia.config.loader import bootstrap, get_data_dir
from aletheia.gateway.pidfile import (
    write_pid, _resolve_real_python,
    find_gateway_orphans, find_port_holders, kill_pid_tree, port_in_use, wait_port_free,
)
from aletheia.gateway.browser import open_browser
from aletheia.runtime_continuity.processes import current_gateway_launch_context, runtime_process_env

# Win32: forbid the OS from allocating a console for the inner uvicorn process.
# Without this, Win11 hosts the console via Windows Terminal and a wt window pops
# up every time the supervisor spawns the server. Suppression only — stderr still
# flows to data/logs/errors.log via the existing redirect.
CREATE_NO_WINDOW = 0x08000000
_SERVER_PORT_STARTUP_GRACE_SEC = 90.0
_SERVER_PORT_LOST_GRACE_SEC = 15.0
_SERVER_PORT_CHECK_INTERVAL_SEC = 1.0


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _cleanup_before_spawn(cfg) -> None:
    """Kill leftover gateway orphans and port holders before spawning."""
    my_pid = os.getpid()
    for pid in find_gateway_orphans():
        if pid != my_pid:
            kill_pid_tree(pid)
    port = cfg.app.dashboard_port
    host = cfg.app.bind_host
    if not wait_port_free(port, host, timeout=2.0):
        for pid in find_port_holders(port):
            if pid != my_pid:
                kill_pid_tree(pid)


def _spawn_server(cfg) -> int:
    """Spawn the inner uvicorn server as a child process; return its exit code."""
    errors_log = get_data_dir() / "logs" / "errors.log"
    errors_log.parent.mkdir(parents=True, exist_ok=True)
    from aletheia.gateway.logs import rotate_if_needed
    rotate_if_needed(errors_log)
    real_python, env_patch = _resolve_real_python()
    runtime_context = current_gateway_launch_context(cfg)

    cmd = [
        real_python, "-m", "uvicorn", "aletheia.server.app:app",
        "--host", runtime_context["host"], "--port", str(runtime_context["port"]),
        "--log-level", "info",
    ]
    env = runtime_process_env(
        base_env=os.environ.copy(),
        runtime_id=runtime_context["runtime_id"],
        runtime_mode=runtime_context["runtime_mode"],
        runtime_state=runtime_context["runtime_state"],
        port=runtime_context["port"],
        workdir=runtime_context["workdir"],
        env_patch=env_patch,
    )

    popen_kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "cwd": str(runtime_context["workdir"]),
        "env": env,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = CREATE_NO_WINDOW
    with errors_log.open("a", encoding="utf-8") as ef:
        popen_kwargs["stderr"] = ef
        proc = subprocess.Popen(cmd, **popen_kwargs)
        started_at = time.monotonic()
        saw_listening_port = False
        port_missing_since: float | None = None
        while True:
            code = proc.poll()
            if code is not None:
                try:
                    kill_pid_tree(proc.pid)  # defensive: ensure no orphan subtree
                except (AttributeError, OSError):
                    pass
                return code

            now = time.monotonic()
            if port_in_use(runtime_context["port"], runtime_context["host"]):
                saw_listening_port = True
                port_missing_since = None
            else:
                if saw_listening_port:
                    if port_missing_since is None:
                        port_missing_since = now
                    if now - port_missing_since >= _SERVER_PORT_LOST_GRACE_SEC:
                        _record_port_liveness_failure(runtime_context["port"], proc.pid)
                        kill_pid_tree(proc.pid)
                        return 1
                elif now - started_at >= _SERVER_PORT_STARTUP_GRACE_SEC:
                    _record_port_liveness_failure(runtime_context["port"], proc.pid)
                    kill_pid_tree(proc.pid)
                    return 1

            _sleep(_SERVER_PORT_CHECK_INTERVAL_SEC)


def _start_vite_build_watch() -> subprocess.Popen | None:
    """Start `vite build --watch` as a sibling process. Returns Popen or None.

    DISABLED: Vite watch creates a race with app.py's static file mount — uvicorn
    loads before Vite finishes its first build, so dashboard_static/ is incomplete
    and the mount fails. For dev iteration use scripts/dev.ps1 instead (runs Vite
    watch + backend watcher separately). Background Gateway startup relies on a
    pre-built dashboard_static/ (committed or built via `npm run build`).
    """
    return None  # Disabled — see docstring above
    project_root = Path(__file__).resolve().parent.parent
    frontend_dir = project_root / "frontend"
    vite_bin = frontend_dir / "node_modules" / "vite" / "bin" / "vite.js"
    if not vite_bin.exists():
        return None
    try:
        popen_kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(frontend_dir),
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = CREATE_NO_WINDOW
        proc = subprocess.Popen(
            ["node", str(vite_bin), "build", "--watch"],
            **popen_kwargs,
        )
        return proc
    except Exception:
        return None


def _stop_vite_watch(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def run_supervised(spawn_fn=None) -> dict:
    """Supervise the inner server. Respawns on abnormal exit with backoff.

    spawn_fn() runs one server lifetime and returns its exit code (0 = clean
    shutdown → stop supervising). Injectable for tests.
    """
    cfg = bootstrap()
    write_pid()  # supervisor owns the pid file (written exactly once)
    if spawn_fn is None:
        spawn_fn = lambda: _spawn_server(cfg)
    from aletheia.gateway.operator_shell import OperatorShellMonitor
    operator_monitor = OperatorShellMonitor(cfg)
    operator_monitor.start()
    vite_proc = _start_vite_build_watch()

    h = cfg.health
    max_restarts = h.supervisor_max_restarts
    window = h.supervisor_restart_window_sec
    cap = h.supervisor_backoff_cap_sec

    restarts = 0
    restart_times: list[float] = []
    try:
        while True:
            _cleanup_before_spawn(cfg)
            code = spawn_fn()
            if code == 0:
                return {"gave_up": False, "restarts": restarts}  # clean shutdown

            # Abnormal exit — decide whether to respawn.
            import time as _time
            now = _time.monotonic()
            restart_times.append(now)
            recent = [t for t in restart_times if now - t <= window]
            restart_times[:] = recent
            if len(recent) >= max_restarts:
                _record_give_up(restarts + 1)
                return {"gave_up": True, "restarts": restarts + 1}

            restarts += 1
            backoff = min(2 ** (restarts - 1), cap) if cap else 0
            _sleep(backoff)
    finally:
        _stop_vite_watch(vite_proc)
        operator_monitor.stop()


def _record_give_up(attempts: int) -> None:
    try:
        from aletheia.files.jsonl import write_audit, write_error
        msg = f"Gateway supervisor gave up after {attempts} restart attempts"
        write_audit({
            "actor": "system", "source": "gateway", "event_type": "gateway_supervisor_gave_up",
            "entity_type": "gateway", "message": msg,
        })
        write_error({"source": "gateway_supervisor", "message": msg})
        from aletheia.agent_runtime.health import _send_alert
        _send_alert(msg)
    except Exception:
        pass


def _record_port_liveness_failure(port: int, pid: int) -> None:
    try:
        from aletheia.files.jsonl import write_audit, write_error

        msg = (
            f"Gateway inner server PID {pid} stopped listening on port {port}; "
            "supervisor is restarting it"
        )
        write_audit({
            "actor": "system",
            "source": "gateway",
            "event_type": "gateway_inner_port_lost",
            "entity_type": "gateway",
            "entity_id": str(pid),
            "message": msg,
        })
        write_error({"source": "gateway_supervisor", "message": msg})
    except Exception:
        pass


def main():
    no_browser = "--no-browser" in sys.argv
    cfg = bootstrap()
    if not no_browser and cfg.app.auto_open_browser:
        open_browser(cfg.app.dashboard_port)
    run_supervised()  # writes the pid file itself


if __name__ == "__main__":
    main()

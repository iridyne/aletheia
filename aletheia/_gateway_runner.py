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
from aletheia.gateway.pidfile import write_pid, _resolve_real_python
from aletheia.gateway.browser import open_browser
from aletheia.runtime_continuity.processes import current_gateway_launch_context, runtime_process_env

# Win32: forbid the OS from allocating a console for the inner uvicorn process.
# Without this, Win11 hosts the console via Windows Terminal and a wt window pops
# up every time the supervisor spawns the server. Suppression only — stderr still
# flows to data/logs/errors.log via the existing redirect.
CREATE_NO_WINDOW = 0x08000000


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _spawn_server(cfg) -> int:
    """Spawn the inner uvicorn server as a child process; return its exit code."""
    errors_log = get_data_dir() / "logs" / "errors.log"
    errors_log.parent.mkdir(parents=True, exist_ok=True)
    from aletheia.gateway.logs import rotate_if_needed
    rotate_if_needed(errors_log)
    real_python, env_patch = _resolve_real_python()
    runtime_context = current_gateway_launch_context(cfg)
    project_root = Path(__file__).resolve().parent.parent
    aletheia_dir = project_root / "aletheia"

    cmd = [
        real_python, "-m", "uvicorn", "aletheia.server.app:app",
        "--host", runtime_context["host"], "--port", str(runtime_context["port"]),
        "--log-level", "info",
        "--reload",
        "--reload-dir", str(aletheia_dir),
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
    # Force polling mode so watchfiles works under CREATE_NO_WINDOW on Windows
    # (ReadDirectoryChangesW silently fails without a console handle).
    env["WATCHFILES_FORCE_POLLING"] = "true"
    # Enable dev-reload SSE endpoint so the browser auto-refreshes on build.
    env["ALETHEIA_DEV_RELOAD"] = "1"

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
        return proc.wait()


def _start_vite_build_watch() -> subprocess.Popen | None:
    """Start `vite build --watch` as a sibling process. Returns Popen or None."""
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


def main():
    no_browser = "--no-browser" in sys.argv
    cfg = bootstrap()
    if not no_browser and cfg.app.auto_open_browser:
        open_browser(cfg.app.dashboard_port)
    run_supervised()  # writes the pid file itself


if __name__ == "__main__":
    main()

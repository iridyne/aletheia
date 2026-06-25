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
from aletheia.gateway.frontdoor_route import frontdoor_route_path, write_frontdoor_route
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
_BACKEND_PORT_OFFSET = 1
_FRONTDOOR_STARTUP_GRACE_SEC = 20.0
_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "*"}


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


def backend_port_for(cfg) -> int:
    """Return the internal backend worker port behind the stable front door."""
    raw = os.environ.get("ALETHEIA_BACKEND_PORT")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return int(cfg.app.dashboard_port) + _BACKEND_PORT_OFFSET


def _cleanup_backend_before_spawn(cfg, backend_port: int) -> None:
    """Free the internal backend port without touching the front-door port."""
    host = cfg.app.bind_host
    if not wait_port_free(backend_port, host, timeout=2.0):
        my_pid = os.getpid()
        for pid in find_port_holders(backend_port):
            if pid != my_pid:
                kill_pid_tree(pid)


def _connect_host(host: str) -> str:
    return "127.0.0.1" if host in _WILDCARD_HOSTS else host


def _backend_base_url(cfg, backend_port: int) -> str:
    return f"http://{_connect_host(cfg.app.bind_host)}:{backend_port}"


def _publish_frontdoor_route(
    cfg,
    *,
    backend_port: int,
    runtime_context: dict | None = None,
    backend_pid: int | None = None,
    reason: str,
) -> None:
    try:
        runtime_context = runtime_context or current_gateway_launch_context(cfg, port=backend_port)
        write_frontdoor_route(
            backend_base_url=_backend_base_url(cfg, backend_port),
            runtime_id=str(runtime_context.get("runtime_id") or "runtime-local-current"),
            runtime_mode=str(runtime_context.get("runtime_mode") or "current"),
            runtime_state=str(runtime_context.get("runtime_state") or "current"),
            backend_pid=backend_pid,
            reason=reason,
        )
    except Exception:
        pass


def _spawn_frontdoor(cfg, backend_port: int) -> subprocess.Popen:
    """Spawn the stable local front door that owns the dashboard port."""
    log_path = get_data_dir() / "logs" / "frontdoor-errors.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from aletheia.gateway.logs import rotate_if_needed
    rotate_if_needed(log_path)
    real_python, env_patch = _resolve_real_python()
    backend_base_url = _backend_base_url(cfg, backend_port)
    cmd = [
        real_python,
        "-m",
        "uvicorn",
        "aletheia.gateway.frontdoor:app",
        "--host",
        cfg.app.bind_host,
        "--port",
        str(cfg.app.dashboard_port),
        "--log-level",
        "warning",
        # loop=none → uvicorn passes loop_factory=None → standard asyncio.run()
        # → respects the process WindowsSelectorEventLoopPolicy set at the top of
        # gateway/frontdoor.py. Without this, uvicorn's own factory hardcodes
        # ProactorEventLoop on win32 and bypasses the policy (§23 WinError 64).
        "--loop",
        "none",
    ]
    env = os.environ.copy()
    env.update(env_patch)
    env["ALETHEIA_BACKEND_HOST"] = cfg.app.bind_host
    env["ALETHEIA_BACKEND_PORT"] = str(backend_port)
    env["ALETHEIA_BACKEND_BASE_URL"] = backend_base_url
    env["ALETHEIA_FRONTDOOR_ROUTE_PATH"] = str(frontdoor_route_path())
    popen_kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "cwd": str(Path(__file__).resolve().parent.parent),
        "env": env,
    }
    if platform.system() == "Windows":
        popen_kwargs["creationflags"] = CREATE_NO_WINDOW
    with log_path.open("a", encoding="utf-8") as log_file:
        popen_kwargs["stderr"] = log_file
        return subprocess.Popen(cmd, **popen_kwargs)


def _wait_frontdoor_listening(cfg, proc: subprocess.Popen, *, timeout: float = _FRONTDOOR_STARTUP_GRACE_SEC) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if port_in_use(cfg.app.dashboard_port, cfg.app.bind_host):
            return True
        _sleep(0.2)
    return port_in_use(cfg.app.dashboard_port, cfg.app.bind_host)


def _ensure_frontdoor_running(cfg, backend_port: int, proc: subprocess.Popen | None) -> subprocess.Popen:
    if proc is not None and proc.poll() is None and port_in_use(cfg.app.dashboard_port, cfg.app.bind_host):
        return proc
    if proc is not None:
        try:
            kill_pid_tree(proc.pid)
        except Exception:
            pass
        _record_frontdoor_restart(cfg.app.dashboard_port, getattr(proc, "pid", 0))
    new_proc = _spawn_frontdoor(cfg, backend_port)
    if not _wait_frontdoor_listening(cfg, new_proc):
        _record_frontdoor_restart(cfg.app.dashboard_port, getattr(new_proc, "pid", 0))
        try:
            kill_pid_tree(new_proc.pid)
        except Exception:
            pass
        return new_proc
    return new_proc


def _spawn_server(cfg, *, port: int | None = None, frontdoor_holder: list | None = None) -> int:
    """Spawn the inner uvicorn server as a child process; return its exit code.

    frontdoor_holder is a 1-element mutable list owned by run_supervised holding
    the current frontdoor Popen. When the frontdoor is detected unhealthy we
    restart ONLY the frontdoor (in place) and write the new process back into the
    holder so run_supervised's finally + next loop iteration see the live one.
    The backend (`proc`) is NEVER killed for a frontdoor fault — that coupling
    was the §23→§24 cascade where a frontdoor hiccup also tore down the backend.
    """
    errors_log = get_data_dir() / "logs" / "errors.log"
    errors_log.parent.mkdir(parents=True, exist_ok=True)
    from aletheia.gateway.logs import rotate_if_needed
    rotate_if_needed(errors_log)
    real_python, env_patch = _resolve_real_python()
    runtime_context = current_gateway_launch_context(cfg, port=port)

    cmd = [
        real_python, "-m", "uvicorn", "aletheia.server.app:app",
        "--host", runtime_context["host"], "--port", str(runtime_context["port"]),
        "--log-level", "info",
        # loop=none → uvicorn uses the process WindowsSelectorEventLoopPolicy set
        # at the top of server/app.py instead of its hardcoded ProactorEventLoop
        # factory (§23/§24). See gateway/frontdoor.py cmd for the same rationale.
        "--loop", "none",
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
        _publish_frontdoor_route(
            cfg,
            backend_port=runtime_context["port"],
            runtime_context=runtime_context,
            backend_pid=getattr(proc, "pid", None),
            reason="backend_spawn",
        )
        started_at = time.monotonic()
        saw_listening_port = False
        port_missing_since: float | None = None
        if not hasattr(proc, "poll"):
            return proc.wait()
        while True:
            code = proc.poll()
            if code is not None:
                try:
                    kill_pid_tree(proc.pid)  # defensive: ensure no orphan subtree
                except (AttributeError, OSError):
                    pass
                return code

            if frontdoor_holder is not None and frontdoor_holder[0] is not None:
                frontdoor_proc = frontdoor_holder[0]
                frontdoor_dead = frontdoor_proc.poll() is not None
                frontdoor_port_lost = not port_in_use(cfg.app.dashboard_port, cfg.app.bind_host)
                if frontdoor_dead or frontdoor_port_lost:
                    # Restart ONLY the frontdoor — keep the backend running.
                    # _ensure_frontdoor_running records the restart + respawns,
                    # and we write the new proc back so the holder stays live.
                    frontdoor_holder[0] = _ensure_frontdoor_running(
                        cfg, runtime_context["port"], frontdoor_proc
                    )

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
    frontdoor_enabled = spawn_fn is None
    backend_port = backend_port_for(cfg) if frontdoor_enabled else cfg.app.dashboard_port
    # 1-element mutable holder so _spawn_server can restart the frontdoor in place
    # (writing the new Popen back) without killing the backend. See _spawn_server.
    frontdoor_holder: list = [None]
    if spawn_fn is None:
        spawn_fn = lambda: _spawn_server(cfg, port=backend_port, frontdoor_holder=frontdoor_holder)
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
        if frontdoor_enabled:
            _cleanup_before_spawn(cfg)
            _cleanup_backend_before_spawn(cfg, backend_port)
            _publish_frontdoor_route(cfg, backend_port=backend_port, reason="supervisor_start")
            frontdoor_holder[0] = _ensure_frontdoor_running(cfg, backend_port, frontdoor_holder[0])
        while True:
            code: int | None = None
            try:
                if frontdoor_enabled:
                    frontdoor_holder[0] = _ensure_frontdoor_running(cfg, backend_port, frontdoor_holder[0])
                    _cleanup_backend_before_spawn(cfg, backend_port)
                else:
                    _cleanup_before_spawn(cfg)
                code = spawn_fn()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                _record_supervisor_exception(exc)
                code = 1

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
        fp = frontdoor_holder[0]
        if fp is not None:
            try:
                kill_pid_tree(fp.pid)
            except Exception:
                pass
        operator_monitor.stop()


def _record_supervisor_exception(exc: Exception) -> None:
    try:
        from aletheia.files.jsonl import write_error
        write_error({
            "source": "gateway_supervisor",
            "message": f"Supervisor loop caught unexpected exception: {type(exc).__name__}: {exc}",
        })
    except Exception:
        pass


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


def _record_frontdoor_restart(port: int, pid: int) -> None:
    try:
        from aletheia.files.jsonl import write_audit, write_error

        msg = f"Gateway front door PID {pid} is not serving port {port}; supervisor is restarting it"
        write_audit({
            "actor": "system",
            "source": "gateway",
            "event_type": "gateway_frontdoor_restart",
            "entity_type": "gateway_frontdoor",
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

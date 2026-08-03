"""Background gateway runner — spawned by `aletheia gateway start`.

Runs the API server under a supervisor loop: if the inner server process exits
abnormally it is respawned with exponential backoff, up to a bounded number of
restarts within a window, after which the supervisor gives up and records it.
This lets the system survive transient crashes while staying unattended.

The supervisor writes the PID file exactly once (its own pid), so `gateway
stop`/`status` keep working. The inner server's stderr is sent to
the runtime home's logs/errors.log instead of being discarded.
"""

from __future__ import annotations

import os
import platform
import signal
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
from aletheia.gateway.network import backend_bind_host, frontdoor_bind_hosts
from aletheia.runtime_continuity.processes import current_gateway_launch_context, runtime_process_env

# Win32: forbid the OS from allocating a console for the inner uvicorn process.
# Without this, Win11 hosts the console via Windows Terminal and a wt window pops
# up every time the supervisor spawns the server. Suppression only — stderr still
# flows to the runtime home's logs/errors.log via the existing redirect.
CREATE_NO_WINDOW = 0x08000000
_SERVER_PORT_STARTUP_GRACE_SEC = 90.0
_SERVER_PORT_LOST_GRACE_SEC = 15.0
_SERVER_PORT_CHECK_INTERVAL_SEC = 1.0
_BACKEND_PORT_OFFSET = 1
_FRONTDOOR_STARTUP_GRACE_SEC = 20.0
_WILDCARD_HOSTS = {"", "0.0.0.0", "::", "*"}

# Signals that must trigger a controlled supervisor shutdown (unwind through
# run_supervised's finally so every owned subprocess is torn down). SIGINT is
# already KeyboardInterrupt by default; SIGTERM — the signal launchd/systemd send
# on a managed stop — is NOT, and its default POSIX disposition kills the process
# without unwinding Python frames, orphaning the collector/frontdoor/backend.
_SHUTDOWN_SIGNALS = (signal.SIGTERM, signal.SIGINT)
_SIGNAL_NAMES = {int(signal.SIGTERM): "SIGTERM", int(signal.SIGINT): "SIGINT"}


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _cleanup_before_spawn(cfg) -> None:
    """Kill leftover gateway orphans and port holders before spawning."""
    my_pid = os.getpid()
    for pid in find_gateway_orphans():
        if pid != my_pid:
            kill_pid_tree(pid)
    port = cfg.app.dashboard_port
    for host in frontdoor_bind_hosts(cfg):
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
    host = backend_bind_host()
    if not wait_port_free(backend_port, host, timeout=2.0):
        my_pid = os.getpid()
        for pid in find_port_holders(backend_port):
            if pid != my_pid:
                kill_pid_tree(pid)


def _connect_host(host: str) -> str:
    return "127.0.0.1" if host in _WILDCARD_HOSTS else host


def _backend_base_url(cfg, backend_port: int) -> str:
    return f"http://{backend_bind_host()}:{backend_port}"


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
        "aletheia.gateway.frontdoor_runner",
    ]
    env = os.environ.copy()
    env.update(env_patch)
    env["ALETHEIA_BACKEND_HOST"] = backend_bind_host()
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


def _frontdoor_listening(cfg) -> bool:
    return all(
        port_in_use(cfg.app.dashboard_port, host)
        for host in frontdoor_bind_hosts(cfg)
    )


def _wait_frontdoor_listening(cfg, proc: subprocess.Popen, *, timeout: float = _FRONTDOOR_STARTUP_GRACE_SEC) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if _frontdoor_listening(cfg):
            return True
        _sleep(0.2)
    return _frontdoor_listening(cfg)


def _ensure_frontdoor_running(cfg, backend_port: int, proc: subprocess.Popen | None) -> subprocess.Popen:
    if proc is not None and proc.poll() is None and _frontdoor_listening(cfg):
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


def _spawn_server(cfg, *, port: int | None = None, frontdoor_holder: list | None = None, collector_holder: list | None = None, backend_holder: list | None = None) -> int:
    """Spawn the inner uvicorn server as a child process; return its exit code.

    frontdoor_holder is a 1-element mutable list owned by run_supervised holding
    the current frontdoor Popen. When the frontdoor is detected unhealthy we
    restart ONLY the frontdoor (in place) and write the new process back into the
    holder so run_supervised's finally + next loop iteration see the live one.
    The backend (`proc`) is NEVER killed for a frontdoor fault — that coupling
    was the §23→§24 cascade where a frontdoor hiccup also tore down the backend.

    backend_holder is the analogous holder for the backend Popen: we publish the
    live process into it so run_supervised's finally can tear the backend down on
    a signal-triggered shutdown (SIGTERM), which would otherwise orphan it.
    """
    errors_log = get_data_dir() / "logs" / "errors.log"
    errors_log.parent.mkdir(parents=True, exist_ok=True)
    from aletheia.gateway.logs import rotate_if_needed
    rotate_if_needed(errors_log)
    real_python, env_patch = _resolve_real_python()
    runtime_context = current_gateway_launch_context(cfg, port=port)
    runtime_context["host"] = backend_bind_host()

    cmd = [
        real_python, "-m", "uvicorn", "aletheia.server.app:app",
        "--host", runtime_context["host"], "--port", str(runtime_context["port"]),
        "--log-level", "debug",
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
        if backend_holder is not None:
            backend_holder[0] = proc
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
        ready_beacon_emitted = False  # T6: one-shot per _spawn_server lifetime
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
                frontdoor_port_lost = not all(
                    port_in_use(cfg.app.dashboard_port, host)
                    for host in frontdoor_bind_hosts(cfg)
                )
                if frontdoor_dead or frontdoor_port_lost:
                    # Restart ONLY the frontdoor — keep the backend running.
                    # _ensure_frontdoor_running records the restart + respawns,
                    # and we write the new proc back so the holder stays live.
                    frontdoor_holder[0] = _ensure_frontdoor_running(
                        cfg, runtime_context["port"], frontdoor_proc
                    )

            if collector_holder is not None and collector_holder[0] is not None:
                cp = collector_holder[0]
                if cp.poll() is not None:
                    # Collector exited — respawn immediately (independent of the backend).
                    _record_collector_restart(getattr(cp, "pid", 0))
                    try:
                        kill_pid_tree(cp.pid)
                    except Exception:
                        pass
                    collector_holder[0] = _spawn_collector_process(cfg)

            now = time.monotonic()
            if port_in_use(runtime_context["port"], runtime_context["host"]):
                saw_listening_port = True
                port_missing_since = None
                # One-shot: emit the ready beacon the first time the backend port
                # is confirmed alive.  T6: lets _verify_started short-circuit its
                # HTTP polling loop instead of waiting for the full 60s deadline.
                if not ready_beacon_emitted:
                    ready_beacon_emitted = True
                    _write_ready_beacon(cfg)
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


def _spawn_collector_process(cfg) -> subprocess.Popen:
    """Spawn the data-collector as an independent child process."""
    log_path = get_data_dir() / "logs" / "collector-errors.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    from aletheia.gateway.logs import rotate_if_needed
    rotate_if_needed(log_path)
    real_python, env_patch = _resolve_real_python()
    env = os.environ.copy()
    env.update(env_patch)
    # The supervisor sets ALETHEIA_COLLECTOR_PROCESS=1 on its own env so uvicorn
    # inherits it and skips those APScheduler jobs.  The collector process itself
    # must NOT see this flag — it is the one doing the actual work.
    env.pop("ALETHEIA_COLLECTOR_PROCESS", None)
    cmd = [real_python, "-m", "aletheia.data.collector_runner"]
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


def _record_collector_restart(pid: int) -> None:
    try:
        from aletheia.files.jsonl import write_audit, write_error
        msg = f"Data-collector subprocess (pid {pid}) exited; supervisor is restarting it"
        write_audit({
            "actor": "system", "source": "gateway",
            "event_type": "collector_subprocess_restart",
            "entity_type": "collector", "entity_id": str(pid), "message": msg,
        })
        write_error({"source": "gateway_supervisor", "message": msg})
    except Exception:
        pass


def _install_shutdown_handlers(state: dict) -> dict:
    """Install SIGTERM/SIGINT handlers that route to run_supervised's cleanup.

    On the first signal the handler records its number in ``state["num"]`` and
    re-arms both signals to SIG_IGN — so neither the ensuing unwind nor the
    finally-block teardown (which signals our own process group, see the finally
    comment) can be aborted by a second delivery — then raises KeyboardInterrupt
    to break out of the otherwise-blocking supervisor loop.

    Returns the previous handlers so the caller restores them in its finally
    block; this keeps in-process callers (tests) from leaking a SIG_IGN/handler.
    Signal handlers can only be installed from the main thread — an off-main
    caller gets an empty mapping and the default (non-intercepted) behavior.
    """
    def _handle(signum, _frame):
        if state.get("num") is None:
            state["num"] = signum
            for s in _SHUTDOWN_SIGNALS:
                try:
                    signal.signal(s, signal.SIG_IGN)
                except (ValueError, OSError):
                    pass
        raise KeyboardInterrupt

    prev: dict = {}
    for s in _SHUTDOWN_SIGNALS:
        try:
            prev[s] = signal.signal(s, _handle)
        except (ValueError, OSError):
            # Not the main thread (e.g. run_supervised invoked from a worker):
            # leave the default disposition in place rather than crash.
            pass
    return prev


def _restore_shutdown_handlers(prev: dict) -> None:
    for s, handler in prev.items():
        try:
            signal.signal(s, handler)
        except (ValueError, OSError):
            pass


def _record_signal_shutdown(signum: int) -> None:
    """Record a durable event for a signal-triggered supervisor shutdown.

    actor is ``system``: SIGTERM/SIGINT is a process-lifecycle signal (launchd,
    a deploy, an operator's stop) and does not, on its own, identify a human — so
    we must not fabricate a human actor. Best-effort; never raises.
    """
    try:
        from aletheia.files.jsonl import write_audit, write_error
        name = _SIGNAL_NAMES.get(int(signum), str(signum))
        msg = (
            f"Gateway supervisor received {name}; performing controlled shutdown "
            "and tearing down owned subprocesses (frontdoor, backend, collector)"
        )
        write_audit({
            "actor": "system", "source": "gateway",
            "event_type": "gateway_signal_shutdown",
            "entity_type": "gateway", "entity_id": str(os.getpid()),
            "message": msg,
        })
        write_error({"source": "gateway_supervisor", "message": msg})
    except Exception:
        pass


def run_supervised(spawn_fn=None) -> dict:
    """Supervise the inner server. Respawns on abnormal exit with backoff.

    spawn_fn() runs one server lifetime and returns its exit code (0 = clean
    shutdown → stop supervising). Injectable for tests.

    SIGTERM/SIGINT (a managed stop, a deploy, Ctrl-C) unwinds the supervisor loop
    through the finally block — the single, idempotent teardown path that kills
    every owned subprocess, removes the pid file, and records a durable
    signal-shutdown event — then returns cleanly, instead of letting the default
    POSIX SIGTERM disposition kill the process and orphan its children.
    """
    cfg = bootstrap()
    write_pid()  # supervisor owns the pid file (written exactly once)
    frontdoor_enabled = spawn_fn is None
    backend_port = backend_port_for(cfg) if frontdoor_enabled else cfg.app.dashboard_port
    # 1-element mutable holders so _spawn_server can restart the frontdoor in place
    # (writing the new Popen back) without killing the backend, and so the finally
    # block can tear down the live backend/collector on a signal. See _spawn_server.
    frontdoor_holder: list = [None]
    collector_holder: list = [None]
    backend_holder: list = [None]
    if spawn_fn is None:
        spawn_fn = lambda: _spawn_server(
            cfg, port=backend_port,
            frontdoor_holder=frontdoor_holder,
            collector_holder=collector_holder,
            backend_holder=backend_holder,
        )
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
    restart_history: list[dict] = []   # T5: per-restart details for persistent log
    gave_up_flag: list[bool] = [False]  # T4: skip frontdoor kill on gave_up
    shutdown_signal: dict = {"num": None}
    prev_handlers: dict = {}
    try:
        try:
            # Route SIGTERM/SIGINT into this try so its finally tears down every
            # owned subprocess. Installed here (not earlier) so the finally always
            # restores the previous handlers.
            prev_handlers = _install_shutdown_handlers(shutdown_signal)
            if frontdoor_enabled:
                # Tell the uvicorn child to skip APScheduler's data-collector jobs
                # — this process owns the collection cadence via collector_holder.
                os.environ["ALETHEIA_COLLECTOR_PROCESS"] = "1"
                _cleanup_before_spawn(cfg)
                _cleanup_backend_before_spawn(cfg, backend_port)
                _publish_frontdoor_route(cfg, backend_port=backend_port, reason="supervisor_start")
                frontdoor_holder[0] = _ensure_frontdoor_running(cfg, backend_port, frontdoor_holder[0])
                collector_holder[0] = _spawn_collector_process(cfg)
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
                # T5: record restart details for persistent history log
                restart_history.append({"exit_code": code, "restart_n": restarts + 1})
                if len(recent) >= max_restarts:
                    _record_give_up(restarts + 1)
                    _write_restart_history(restart_history)
                    gave_up_flag[0] = True  # T4: keep frontdoor alive on gave_up
                    return {"gave_up": True, "restarts": restarts + 1}

                restarts += 1
                backoff = min(2 ** (restarts - 1), cap) if cap else 0
                _sleep(backoff)
        except KeyboardInterrupt:
            # A shutdown signal (our SIGTERM/SIGINT handler) or a genuine Ctrl-C
            # unwound the loop. The finally below is the single cleanup path; here
            # we only turn the unwind into a clean return so main() exits quietly.
            return {"gave_up": False, "restarts": restarts, "stopped_by_signal": True}
    finally:
        # Neutralize the shutdown signals FIRST: children share this supervisor's
        # process group, so kill_pid_tree() below signals the group — which would
        # re-deliver SIGTERM to us and abort the teardown mid-way (leaving the pid
        # file and the durable event unwritten). Ignoring them makes the whole
        # cleanup idempotent under repeated/racing signals; the original handlers
        # are restored at the end.
        for s in _SHUTDOWN_SIGNALS:
            try:
                signal.signal(s, signal.SIG_IGN)
            except (ValueError, OSError):
                pass
        _stop_vite_watch(vite_proc)
        # Tear down backend and collector unconditionally.  Frontdoor gets special
        # treatment on a gave_up exit: leave it alive so it can serve 503s while
        # the watchdog or recovery task restarts the full gateway.  A subsequent
        # `gateway start` detects it as an orphan and cleans it up before spawning
        # a fresh supervisor.  On clean shutdown or signal, kill everything.
        for holder in (backend_holder, collector_holder):
            proc = holder[0]
            if proc is not None:
                try:
                    kill_pid_tree(proc.pid)
                except Exception:
                    pass
        if not gave_up_flag[0]:
            proc = frontdoor_holder[0]
            if proc is not None:
                try:
                    kill_pid_tree(proc.pid)
                except Exception:
                    pass
        operator_monitor.stop()
        # Remove our own pid file so status/stop don't act on a stale live pid.
        # Guard on ownership so a supervisor that already lost the pidfile to a
        # newer instance (start/stop race) does not clear the new owner's pid.
        try:
            from aletheia.gateway.pidfile import read_pid, clear_pid
            if read_pid() == os.getpid():
                clear_pid()
        except Exception:
            pass
        # T6: clear the ready beacon so a stale file never misleads the next start.
        _clear_ready_beacon()
        if shutdown_signal["num"] is not None:
            _record_signal_shutdown(shutdown_signal["num"])
        _restore_shutdown_handlers(prev_handlers)


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


def _write_restart_history(history: list[dict]) -> None:
    """Persist the crash-loop restart history so operators can diagnose root cause.

    Writes one entry per crash cycle to restart_history.jsonl in the logs dir.
    Each entry includes the exit code and restart sequence number so the operator
    can correlate with errors.log timestamps without grep.
    """
    if not history:
        return
    try:
        import datetime as _dt
        from aletheia.config.loader import get_data_dir
        log_dir = get_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "restart_history.jsonl"
        import json as _json
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        with path.open("a", encoding="utf-8") as fh:
            for entry in history:
                fh.write(_json.dumps({"ts": ts, **entry}) + "\n")
    except Exception:
        pass


def _ready_beacon_path():
    """Return the path of the ready beacon file."""
    return get_data_dir() / "gateway.ready"


def _write_ready_beacon(cfg) -> None:
    """Write a ready beacon that _verify_started can use as a fast-path signal.

    The beacon contains the supervisor's PID so the reader can verify ownership
    and reject a stale file from a previous run.  Written exactly once per
    supervisor lifetime; cleared in finally so a subsequent start never sees a
    leftover beacon.

    Also emits sd_notify READY=1 for Linux systemd integration (T8). No-op on
    Windows or when not running under systemd.
    """
    try:
        import json as _json
        import datetime as _dt
        path = _ready_beacon_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        path.write_text(_json.dumps(payload), encoding="utf-8")
    except Exception:
        pass
    _emit_sd_notify()


def _clear_ready_beacon() -> None:
    """Delete the ready beacon file. Best-effort; never raises."""
    try:
        _ready_beacon_path().unlink(missing_ok=True)
    except Exception:
        pass


def _emit_sd_notify() -> None:
    """Emit READY=1 to systemd via the sd_notify protocol (Linux only).

    No-op when:
    - NOTIFY_SOCKET is not set (not under systemd supervision)
    - sdnotify package is not installed (optional dependency)
    - Any other error (always best-effort)
    """
    if not os.environ.get("NOTIFY_SOCKET"):
        return
    try:
        import sdnotify  # optional dependency: sdnotify>=0.3.2
        sdnotify.SystemdNotifier().notify("READY=1")
    except Exception:
        pass


def _schedule_supervisor_recovery() -> None:
    """Report exhausted recovery without changing operator service enablement."""
    try:
        from aletheia.gateway import service_control
        service = service_control.status()
        _write_error_event(
            "supervisor_recovery: runner gave up; service enablement was preserved "
            f"(backend={service.get('backend')}, recovery={service.get('recovery_scope')})"
        )
    except Exception as exc:
        try:
            _write_error_event(f"supervisor_recovery: unexpected error: {type(exc).__name__}: {exc}")
        except Exception:
            pass


def _write_error_event(message: str) -> None:
    try:
        from aletheia.files.jsonl import write_error
        write_error({"source": "gateway_supervisor", "message": message})
    except Exception:
        pass


def main():
    from aletheia.logging_config import configure_logging
    configure_logging()
    no_browser = "--no-browser" in sys.argv
    cfg = bootstrap()
    if not no_browser and cfg.app.auto_open_browser:
        open_browser(cfg.app.dashboard_port)
    result = run_supervised()  # writes the pid file itself
    if result.get("gave_up"):
        _schedule_supervisor_recovery()


if __name__ == "__main__":
    main()

"""Gateway lifecycle operations: start, stop, status, pause, resume, logs."""

import logging
import platform
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

import typer

from aletheia.config.loader import bootstrap, load_config, save_config, get_data_dir
from aletheia.gateway.pidfile import (
    is_running, read_pid, write_pid, clear_pid, pid_path,
    kill_pid_tree, port_in_use, wait_port_free, find_gateway_orphans,
    find_port_holders,
)
from aletheia.gateway.browser import open_browser
from aletheia.gateway.logs import tail_all_logs, log_path


class GatewayResumeApiError(RuntimeError):
    """Raised when an in-process gateway resume request cannot complete."""

    def __init__(self, message: str, *, blocked: bool = False, payload: dict | None = None):
        super().__init__(message)
        self.blocked = blocked
        self.payload = payload or {}


def do_start(*, no_browser: bool = False, foreground: bool = False) -> None:
    """Start the gateway (foreground or background)."""
    cfg = bootstrap()

    # T7: If a circuit-break state file exists and 30 min of stability has elapsed,
    # auto-clear the breaker and re-enable the watchdog before attempting a new start.
    _check_circuit_break_auto_reset()

    # Preflight checks — fail fast if environment is broken
    from aletheia.gateway.preflight import run_preflight_checks, assert_preflight_or_exit
    preflight_result = run_preflight_checks()
    if not preflight_result.passed:
        _notify_gateway_problem(
            "Aletheia Gateway preflight failed",
            "; ".join(f"{c.name}: {c.message}" for c in preflight_result.failures()),
            source="gateway_lifecycle",
        )
    assert_preflight_or_exit(preflight_result)  # exits on failure

    if is_running():
        _echo_stdout_safe(f"Gateway already running (PID {read_pid()}).")
        raise typer.Exit(0)

    # Detect abnormal termination: pid file exists (was running) but process is dead.
    # Normal first-start or clean stop leaves no pid file.
    _stale_pid = read_pid()
    if _stale_pid is not None:
        _notify_gateway_problem(
            "Gateway abnormal termination detected",
            f"Previous process (PID {_stale_pid}) ended unexpectedly. Starting a new gateway.",
            source="gateway_lifecycle",
        )

    # Port guard: if the port stays held throughout a short grace window with no
    # tracked gateway, attempt to auto-clean our own orphans before giving up.
    # This self-heals the common case where the supervisor died but the inner
    # uvicorn survived and holds the port, preventing a later explicit start.
    if not wait_port_free(cfg.app.dashboard_port, cfg.app.bind_host, timeout=3.0):
        orphans = find_gateway_orphans()
        if orphans:
            for opid in orphans:
                kill_pid_tree(opid)
            _audit_orphan_cleanup(orphans)

        # Never kill an arbitrary port holder.  The orphan scan above identifies
        # this runtime's gateway processes; a PID discovered only from the socket
        # is not proof that Aletheia owns it.
        if not wait_port_free(cfg.app.dashboard_port, cfg.app.bind_host, timeout=5.0):
            holders = find_port_holders(cfg.app.dashboard_port)
            _echo_safe(
                f"Port {cfg.app.dashboard_port} is still in use after verified Aletheia "
                f"cleanup; it may be held by a foreign process. Refusing to terminate "
                f"unverified holder(s): {holders or 'unknown'}.",
            )
            raise typer.Exit(1)

    if foreground:
        _echo_stdout_safe(f"Starting Aletheia gateway on http://{cfg.app.bind_host}:{cfg.app.dashboard_port} (foreground)...")
        write_pid()
        if not no_browser and cfg.app.auto_open_browser:
            open_browser(cfg.app.dashboard_port)
        _run_server(cfg)
    else:
        _echo_stdout_safe(f"Starting Aletheia gateway on http://{cfg.app.bind_host}:{cfg.app.dashboard_port} (background)...")
        from aletheia.gateway import managed_service
        managed = managed_service.start(no_browser=no_browser)
        if not managed.get("ok"):
            reason = managed.get("reason", "service_start_failed")
            detail = managed.get("detail")
            message = f"Gateway service manager failed to start the gateway: {reason}"
            if detail:
                message += f" — {detail}"
            _echo_safe(message)
            raise typer.Exit(1)
        # Registration is not success: require the managed gateway to serve both
        # its readiness endpoint and Web UI before reporting a successful start.
        try:
            _verify_started(cfg)
        except typer.Exit:
            # A manager definition that failed readiness must not survive as a
            # misleading registered service. The regular failed-start cleanup
            # already terminates any partial gateway process tree.
            managed_service.stop()
            raise
        _auto_start_tunnel(cfg)


def _verify_webui_accessible(cfg) -> bool:
    """验证 WebUI 根路径返回 HTML（不是 JSON 404）。

    Quick check: GET / 应返回 200 + text/html。如果返回 404 + application/json，
    说明静态文件 mount 失败（虽然 preflight 应该已经捕获这个问题）。
    """
    import urllib.request
    try:
        url = f"http://{cfg.app.bind_host}:{cfg.app.dashboard_port}/"
        req = urllib.request.Request(url, headers={"User-Agent": "aletheia-preflight"})
        with urllib.request.urlopen(req, timeout=2) as response:
            content_type = response.getheader("Content-Type", "")
            return response.status == 200 and "text/html" in content_type
    except Exception:
        return False


def _verify_api_ready_accessible(cfg) -> bool:
    """Verify that the gateway HTTP worker is serving the cheap liveness route.

    A TCP connect only proves that *something* is listening; readiness requires
    the Aletheia FastAPI app to answer a deterministic, DB-free probe.
    """
    import urllib.request
    try:
        url = f"http://{cfg.app.bind_host}:{cfg.app.dashboard_port}/api/ready"
        req = urllib.request.Request(url, headers={"User-Agent": "aletheia-preflight"})
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def _read_ready_beacon(cfg) -> bool:
    """Return True when a valid ready beacon from the current supervisor exists.

    Validates PID ownership so a stale file from a previous run is rejected.
    Best-effort; returns False on any error.
    """
    try:
        import json as _json
        from aletheia.config.loader import get_data_dir
        path = get_data_dir() / "gateway.ready"
        if not path.exists():
            return False
        data = _json.loads(path.read_text(encoding="utf-8"))
        beacon_pid = data.get("pid")
        if beacon_pid is None:
            return False
        live_pid = read_pid()
        return live_pid is not None and int(beacon_pid) == int(live_pid)
    except Exception:
        return False


def _verify_started(cfg, *, timeout: float = 60.0, poll_interval: float = 0.3) -> None:
    """Block until the managed gateway is confirmed up, or handle timeout gracefully.

    Three outcomes:
      - pid + port + /api/ready 200 + WebUI HTML before timeout  → confirmed up; return.
      - timeout but process alive (pid present or port held)      → slow start; audit and
        return without killing. Supervisor + self-heal own the recovery path.
      - timeout with no process and no port                       → genuine failure; Exit(1).

    The "alive at timeout" path deliberately does NOT kill the process. Killing from
    the CLI start command is premature: the gateway may complete its initialization
    (migrations, LLM probe, scheduler boot) shortly after the CLI deadline. The
    supervisor restart policy and self-heal mechanism are the right owners for
    detecting and recovering from a gateway that stays unhealthy long-term.

    Timeout budget: covers supervisor + uvicorn startup to port BIND. LLM probes
    now run as post-yield background tasks, so the port binds promptly on healthy
    hosts. 60s is a generous margin for cold migrations / Windows Defender scan.
    Do NOT couple this to the LLM probe budget — they are independent.

    On genuine failure (no process, no port), surface errors.log and audit so the
    death is never silent.
    """
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # T6 fast path: supervisor wrote a ready beacon the moment backend port
        # first appeared.  If the beacon is valid (pid matches live pid) we can
        # skip straight to the HTTP checks — no need to wait for the next poll tick.
        beacon_ready = _read_ready_beacon(cfg)
        if beacon_ready or (is_running() and port_in_use(cfg.app.dashboard_port, cfg.app.bind_host)):
            if not _verify_api_ready_accessible(cfg):
                time.sleep(poll_interval)
                continue
            # Port is up — verify WebUI is actually accessible (not just API).
            # Keep waiting until timeout if root is not HTML; static assets may
            # still be appearing during a frontend build.
            try:
                if _verify_webui_accessible(cfg):
                    return  # Gateway confirmed fully operational
            except Exception:
                pass
        time.sleep(poll_interval)

    # Timeout — distinguish "process alive" from "process truly dead".
    if is_running() or port_in_use(cfg.app.dashboard_port, cfg.app.bind_host):
        # Process is alive or port is held — this is a slow start, not a failure.
        # Leave it running. The supervisor restart policy and health self-heal
        # will detect and correct any persistent unhealthy state.
        _audit_slow_start(timeout)
        _echo_stdout_safe(
            f"Gateway process started (readiness not confirmed within {timeout:.0f}s; "
            "still initializing — run `gateway status` to check)"
        )
        return

    # No process and no port: genuine failure.
    _fail_spawn(f"gateway did not come up within {timeout:.0f}s (no pid file / port not listening)")


def _audit_slow_start(timeout: float) -> None:
    """Best-effort audit of a slow-start event. Never raises."""
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system", "source": "gateway_lifecycle",
            "event_type": "gateway_slow_start",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": (
                f"Gateway did not complete readiness checks within {timeout:.0f}s "
                "but the process is alive; left running for self-heal / supervisor to monitor."
            ),
        })
    except Exception:
        pass


def _circuit_break_state_path():
    """Return path of the circuit-breaker state file."""
    return get_data_dir() / "circuit_break.json"


def _write_circuit_break_state() -> None:
    """Persist a circuit-break timestamp so auto-reset can evaluate elapsed time."""
    try:
        import json as _json
        import datetime as _dt
        path = _circuit_break_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps({
                "tripped_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "source": "circuit_breaker",
            }),
            encoding="utf-8",
        )
    except Exception:
        pass


def _check_circuit_break_auto_reset() -> None:
    """Auto-clear the circuit breaker if the system has been stable for 30 minutes.

    When tripped, the circuit breaker disables the autostart watchdog to stop a
    restart churn loop.  This function is called at the top of every do_start so
    that the next manual restart attempt checks whether the protection window has
    elapsed.  If it has, and no new spawn failures occurred in that window, the
    state file is deleted and the watchdog is re-enabled — mirroring PM2's
    "stable for 30 minutes → reset" behaviour.
    """
    _RESET_WINDOW_SEC = 1800  # 30 minutes — same as the circuit-breaker trip window

    try:
        import json as _json
        import datetime as _dt
        path = _circuit_break_state_path()
        if not path.exists():
            return
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            tripped_ts = data.get("tripped_at", "")
            tripped_at = _dt.datetime.fromisoformat(tripped_ts.replace("Z", "+00:00"))
        except Exception:
            path.unlink(missing_ok=True)
            return

        now = _dt.datetime.now(_dt.timezone.utc)
        elapsed = (now - tripped_at).total_seconds()
        if elapsed < _RESET_WINDOW_SEC:
            return  # still within protection window

        # Check that no new spawn failures occurred since the circuit tripped.
        try:
            from aletheia.files.jsonl import read_jsonl
            events = list(read_jsonl("audit.jsonl"))
            recent_failures = [
                e for e in events
                if e.get("event_type") == "gateway_spawn_failed"
                and (
                    _dt.datetime.fromisoformat(
                        (e.get("ts") or e.get("timestamp") or "1970-01-01T00:00:00+00:00")
                        .replace("Z", "+00:00")
                    ) > tripped_at
                )
            ]
        except Exception:
            recent_failures = []

        if recent_failures:
            return  # new failures since the trip — leave the breaker closed

        # Stable for >30 min with no new failures: reset.
        path.unlink(missing_ok=True)
        try:
            from aletheia.files.jsonl import write_audit
            write_audit({
                "actor": "system", "source": "gateway_lifecycle",
                "event_type": "gateway_circuit_breaker_reset",
                "entity_type": "gateway", "entity_id": "gateway",
                "message": (
                    f"Circuit breaker auto-reset after {elapsed / 60:.0f} min of stability. "
                    "Watchdog re-enabled."
                ),
            })
        except Exception:
            pass
        try:
            from aletheia.gateway import autostart
            if autostart.is_supported_platform():
                autostart.enable(source="auto_reset")
        except Exception:
            pass
    except Exception:
        pass


def _check_spawn_circuit_breaker() -> None:
    """Disable the autostart watchdog if gateway_spawn_failed events are piling up.

    If the last _CIRCUIT_BREAKER_THRESHOLD consecutive spawn failures all occurred
    within _CIRCUIT_BREAKER_WINDOW_SEC, the gateway has a persistent startup problem
    that repeated watchdog ticks will not fix. Disable the watchdog to stop the churn
    loop and send an operator alert. The operator can re-enable after fixing the root
    cause.
    """
    _CIRCUIT_BREAKER_THRESHOLD = 3
    _CIRCUIT_BREAKER_WINDOW_SEC = 1800  # 30 minutes

    try:
        import time
        from aletheia.files.jsonl import read_jsonl
        events = list(read_jsonl("audit.jsonl"))
        spawn_failures = [
            e for e in events
            if e.get("event_type") == "gateway_spawn_failed"
        ]
        if len(spawn_failures) < _CIRCUIT_BREAKER_THRESHOLD:
            return
        recent = spawn_failures[-_CIRCUIT_BREAKER_THRESHOLD:]
        # Timestamps in audit events are ISO strings; parse them.
        import datetime as _dt
        now = _dt.datetime.now(_dt.timezone.utc)
        oldest_ts = recent[0].get("ts") or recent[0].get("timestamp") or ""
        try:
            oldest = _dt.datetime.fromisoformat(oldest_ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return
        if (now - oldest).total_seconds() > _CIRCUIT_BREAKER_WINDOW_SEC:
            return
        # Threshold hit within window — disable watchdog and alert.
        try:
            from aletheia.gateway import autostart
            if autostart.is_supported_platform() and autostart.status().get("enabled"):
                autostart.disable(source="circuit_breaker")
                _write_circuit_break_state()  # T7: persist timestamp for auto-reset
                _notify_gateway_problem(
                    "Aletheia Gateway autostart disabled by circuit breaker",
                    f"Gateway failed to start {_CIRCUIT_BREAKER_THRESHOLD} times in "
                    f"{_CIRCUIT_BREAKER_WINDOW_SEC // 60} minutes. Autostart watchdog "
                    "has been disabled to stop the restart loop. Fix the root cause and "
                    "re-enable with `gateway autostart enable`, or wait 30 min for "
                    "auto-reset.",
                    source="gateway_lifecycle",
                )
        except Exception:
            pass
    except Exception:
        pass


def _fail_spawn(reason: str) -> None:
    """Capture errors.log tail, audit the failure, surface it, and Exit(1)."""
    from aletheia.gateway.logs import errors_log_path
    tail = ""
    try:
        from aletheia.gateway.logs import _tail_file
        tail_lines = _tail_file(errors_log_path(), 15)
        tail = "\n".join(tail_lines)
    except Exception:
        pass

    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system", "source": "gateway_lifecycle", "event_type": "gateway_spawn_failed",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": f"Gateway spawn failed: {reason}",
        })
    except Exception:
        pass

    _notify_gateway_problem(
        "Aletheia Gateway failed to start",
        reason,
        source="gateway_lifecycle",
        error_log=True,
        audit=False,
    )

    msg = f"Gateway failed to start: {reason}."
    if tail:
        msg += f"\n--- errors.log (tail) ---\n{tail}"
    _echo_safe(msg)
    _check_spawn_circuit_breaker()
    raise typer.Exit(1)


def _run_server(cfg) -> None:
    """Run uvicorn in-process (foreground mode, no hot-reload)."""
    import uvicorn
    uvicorn.run(
        "aletheia.server.app:app",
        host=cfg.app.bind_host,
        port=cfg.app.dashboard_port,
        log_level="info",
    )


def do_stop() -> None:
    """Stop the gateway: tree-kill the supervisor (+ its uvicorn child), clean any
    orphaned uvicorn processes we previously spawned, and wait for the port to free.

    Fixes the restart leak where killing only the supervisor (no /T) orphaned the
    inner uvicorn, which kept holding the port and made the next start crash-loop.
    """
    # Ask the operating-system owner to stop and unregister the on-demand
    # service before retaining verified legacy PID/orphan cleanup.  A failed
    # manager operation is an ownership conflict, not permission to bypass the
    # OS owner by killing an arbitrary process on the gateway port.
    try:
        from aletheia.gateway import managed_service
        managed = managed_service.stop()
    except Exception as exc:
        managed = {"ok": False, "reason": "service_manager_error", "detail": type(exc).__name__}
    if not managed.get("ok"):
        platform_name = managed.get("platform", "service manager")
        reason = managed.get("reason", "service_stop_failed")
        detail = managed.get("detail")
        message = f"Gateway stop blocked by {platform_name}: {reason}. No processes were terminated."
        if detail:
            message += f" {detail}"
        _echo_safe(message)
        _audit_stop_blocked(platform_name=platform_name, reason=reason)
        raise typer.Exit(1)

    _auto_stop_tunnel()

    cfg = load_config()
    port = cfg.app.dashboard_port
    host = cfg.app.bind_host

    pid = read_pid()
    tracked = pid is not None and is_running()
    killed_any = False

    if tracked:
        kill_pid_tree(pid)          # supervisor + inner uvicorn (tree kill)
        killed_any = True

    # Sweep our own orphans (supervisors first, then inners — the helper orders
    # them so a still-alive supervisor cannot respawn the inner we just killed).
    # Skip PIDs we've already targeted to avoid double-kills on the tracked one.
    targeted = {pid} if tracked else set()
    for opid in find_gateway_orphans():
        if opid in targeted:
            continue
        kill_pid_tree(opid)
        targeted.add(opid)
        killed_any = True

    if not killed_any and not port_in_use(port, host):
        # Nothing to do: no tracked gateway, no orphans, port is free.
        clear_pid()
        _echo_stdout_safe("Gateway is not running.")
        return

    # Wait for the port to actually release BEFORE clearing the pidfile, so a
    # concurrent start in the kill window still sees a tracked gateway
    # rather than "untracked but port held" → false-orphan misdiagnosis.
    freed = wait_port_free(port, host, timeout=5.0)

    if freed:
        clear_pid()
        _echo_stdout_safe("Gateway stopped.")
    else:
        _echo_safe(
            f"Verified gateway processes were stopped, but port {port} is still in use. "
            "Refusing to terminate an unverified holder.",
        )

    _audit_stop(freed=freed, killed_any=killed_any)
    if not freed:
        raise typer.Exit(1)
    _auto_deregister_watchdog()


def _auto_deregister_watchdog() -> None:
    """Disable the OS autostart watchdog after a stop so the user's intent is honored.

    The legacy LaunchAgent / Task Scheduler watchdog can otherwise resurrect the
    gateway after an explicit stop. It is deliberately not re-registered by a
    subsequent `gateway start`.
    """
    try:
        from aletheia.gateway import autostart
        if not autostart.is_supported_platform():
            return
        current = autostart.status()
        if not current.get("enabled"):
            return
        autostart.disable(source="auto")
    except Exception:
        pass


def _audit_stop(*, freed: bool, killed_any: bool) -> None:
    """Best-effort audit of a gateway stop. Never raises."""
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "human", "source": "cli",
            "event_type": "gateway_stopped" if freed else "gateway_stop_incomplete",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": f"Gateway stopped (killed={killed_any}, port_freed={freed})",
        })
    except Exception:
        pass


def _audit_stop_blocked(*, platform_name: str, reason: str) -> None:
    """Record an OS-manager ownership conflict without claiming a stop happened."""
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "human", "source": "cli", "event_type": "gateway_stop_blocked",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": f"Gateway stop blocked by {platform_name}: {reason}",
        })
    except Exception:
        pass


def do_status() -> None:
    """Show gateway status."""
    cfg = load_config()
    running = is_running()
    pid = read_pid()
    typer.echo(f"Gateway: {'running' if running else 'stopped'}" + (f" (PID {pid})" if running else ""))
    typer.echo(f"Paused: {cfg.runtime.paused}")
    typer.echo(f"Dashboard: http://{cfg.app.bind_host}:{cfg.app.dashboard_port}")
    exchange_label = (
        "Local paper (no external exchange)"
        if cfg.exchange.paper_execution_mode == "paper_internal"
        else f"{cfg.exchange.name} ({cfg.exchange.mode})"
    )
    typer.echo(f"Exchange: {exchange_label}")
    typer.echo(f"Live state: {cfg.live.state}")
    typer.echo(f"Permission: {cfg.agent.permission_mode}")
    try:
        from aletheia.gateway import managed_service
        service = managed_service.status()
        if service.get("managed") is True:
            lifecycle_label = f"managed ({service.get('platform')})"
        elif service.get("managed") is False:
            lifecycle_label = "not registered"
        else:
            lifecycle_label = f"unknown ({service.get('reason', 'error')})"
        typer.echo(f"Lifecycle: {lifecycle_label}")
    except Exception:
        typer.echo("Lifecycle: unknown (manager query failed)")

    try:
        from aletheia.gateway.tunnel import tunnel_status as _ts
        ts = _ts()
        if ts["enabled"]:
            if ts["running"]:
                typer.echo(f"Tunnel: running → https://{ts['hostname']} (PID {ts['pid']})")
            else:
                typer.echo("Tunnel: enabled but not running")
        else:
            typer.echo("Tunnel: disabled")
    except Exception:
        pass


def do_logs(lines: int = 50) -> None:
    """Show recent operational logs with file headers and redaction."""
    groups = tail_all_logs(lines)
    if not groups:
        typer.echo("No logs yet.")
        return
    for i, group in enumerate(groups):
        if i:
            typer.echo("")
        typer.echo(f"== {group['name']} ({group['bytes']} bytes) ==")
        for line in group["lines"]:
            typer.echo(line)


def _is_headless() -> bool:
    """True when running under pythonw.exe or with no attached console.

    In this state, writing to stderr would race-allocate a console on Win11
    (hosted via Windows Terminal → black window flash). All user-facing output
    should be suppressed or redirected to a log file.
    """
    if platform.system() != "Windows":
        return False
    import ctypes
    # pythonw.exe: no console subsystem
    if sys.executable.lower().endswith("pythonw.exe"):
        return True
    # Detached process: console handle is invalid
    try:
        return ctypes.windll.kernel32.GetConsoleWindow() == 0
    except Exception:
        return False


def _echo_safe(msg: str) -> None:
    """Write a message via typer.echo(err=True), but only if a console is attached.

    Under pythonw/detached (headless) mode, writing to stderr causes Win11 to
    allocate a new console (hosted by Windows Terminal), which flashes a black
    window. In headless mode we log instead.
    """
    if _is_headless():
        import logging
        logging.getLogger("aletheia.gateway.lifecycle").warning(msg)
        return
    typer.echo(msg, err=True)


def _echo_stdout_safe(msg: str) -> None:
    """Write a message via typer.echo (stdout), but only if a console is attached.

    Under pythonw/detached mode, writing to stdout with no console handle can
    trigger Win11 console race-allocation. In headless mode we log instead.
    This guards a managed service's "already running" status path.
    """
    if _is_headless():
        import logging
        logging.getLogger("aletheia.gateway.lifecycle").info(msg)
        return
    typer.echo(msg)


def _audit_orphan_cleanup(orphan_pids: list[int]) -> None:
    """Best-effort audit of lifecycle orphan cleanup."""
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system", "source": "gateway_lifecycle", "event_type": "orphan_cleanup",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": f"Lifecycle cleaned {len(orphan_pids)} orphan(s): {orphan_pids}",
        })
    except Exception:
        pass


def _notify_gateway_problem(
    title: str,
    message: str,
    *,
    source: str,
    error_log: bool = True,
    audit: bool = True,
) -> None:
    """Best-effort operator-visible notification for gateway lifecycle faults."""
    try:
        from aletheia.notifications import notify_operator
        notify_operator(
            title,
            message,
            source=source,
            event_type="gateway_operator_notification",
            entity_type="gateway",
            entity_id="gateway",
            level="error",
            audit=audit,
            error_log=error_log,
        )
    except Exception:
        pass


def do_pause() -> None:
    """Pause autonomous jobs."""
    cfg = load_config()
    cfg.runtime.paused = True
    save_config(cfg)
    _echo_stdout_safe("Gateway paused. Autonomous jobs suspended.")


def do_resume() -> None:
    """Resume paused jobs."""
    from aletheia.agent_runtime import resume_gate

    try:
        if resume_running_gateway_via_api_if_running():
            _echo_stdout_safe("Gateway resumed.")
            return
    except GatewayResumeApiError as exc:
        _echo_safe(str(exc))
        raise typer.Exit(1)

    result = resume_gate.resume_if_llm_available_sync(
        actor="human",
        source="cli",
        cause="gateway_resume_cli",
    )
    if not result["allowed"]:
        typer.echo(f"Gateway resume blocked: {resume_gate.summarize_resume_gate(result)}", err=True)
        raise typer.Exit(1)
    _echo_stdout_safe("Gateway resumed.")


def resume_running_gateway_via_api_if_running() -> bool:
    """Resume through the live gateway process when it is already running.

    The scheduler lives inside the gateway process. A CLI-only config write can
    clear runtime.paused while leaving that in-process scheduler paused, which is
    the exact false-recovery state this guard prevents.
    """
    cfg = load_config()
    if not (is_running() and port_in_use(cfg.app.dashboard_port, cfg.app.bind_host)):
        return False
    _post_gateway_resume_api(cfg)
    return True


def _post_gateway_resume_api(cfg) -> dict:
    """Call the running gateway's own resume endpoint and return its JSON body."""
    import urllib.error
    import urllib.request

    url = f"http://{cfg.app.bind_host}:{cfg.app.dashboard_port}/api/gateway/resume"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30.0) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        payload = _decode_resume_api_payload(body)
        if exc.code == 409:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else {}
            reason = detail.get("reason") or "llm_unavailable"
            raise GatewayResumeApiError(
                f"Gateway resume blocked: {reason}",
                blocked=True,
                payload=detail,
            ) from exc
        raise GatewayResumeApiError(
            f"Gateway resume API failed: HTTP {exc.code} {body.strip()}",
            payload=payload if isinstance(payload, dict) else {},
        ) from exc
    except urllib.error.URLError as exc:
        raise GatewayResumeApiError(f"Gateway resume API unavailable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise GatewayResumeApiError("Gateway resume API timed out") from exc

    payload = _decode_resume_api_payload(body)
    if not isinstance(payload, dict):
        raise GatewayResumeApiError("Gateway resume API returned a non-JSON response")
    if payload.get("paused") is not False:
        raise GatewayResumeApiError(f"Gateway resume API did not confirm resumed: {payload}")
    return payload


def _decode_resume_api_payload(body: str) -> dict:
    import json

    try:
        data = json.loads(body or "{}")
    except json.JSONDecodeError:
        return {"raw": body}
    return data if isinstance(data, dict) else {"raw": data}


def _auto_start_tunnel(cfg) -> None:
    """Start Cloudflare Tunnel if enabled. Best-effort, never blocks gateway start."""
    if not getattr(cfg, "tunnel", None) or not cfg.tunnel.enabled:
        return
    try:
        from aletheia.gateway.tunnel import start_tunnel
        result = start_tunnel()
        if result.get("ok"):
            _echo_stdout_safe(f"Tunnel started → https://{cfg.tunnel.hostname}")
        else:
            _echo_safe(f"Tunnel failed: {result.get('reason', 'unknown')}")
    except Exception as e:
        _echo_safe(f"Tunnel start error: {e}")


def _auto_stop_tunnel() -> None:
    """Stop Cloudflare Tunnel if running. Best-effort, never blocks gateway stop."""
    try:
        from aletheia.gateway.tunnel import is_tunnel_running, stop_tunnel
        if is_tunnel_running():
            stop_tunnel()
    except Exception:
        pass

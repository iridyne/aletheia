"""Gateway lifecycle operations: start, stop, status, pause, resume, logs."""

import logging
import platform
import signal
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

import typer

from aletheia.config.loader import bootstrap, load_config, save_config, get_data_dir
from aletheia.gateway.pidfile import (
    is_running, read_pid, write_pid, clear_pid, pid_path,
    kill_pid_tree, port_in_use, wait_port_free, find_gateway_orphans,
    find_port_holders, _resolve_real_python,
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

    # Preflight checks — fail fast if environment is broken
    from aletheia.gateway.preflight import run_preflight_checks, assert_preflight_or_exit
    preflight_result = run_preflight_checks()
    if not preflight_result.passed:
        _notify_gateway_problem(
            "Aletheia Gateway preflight failed",
            "; ".join(f"{c.name}: {c.message}" for c in preflight_result.failures()),
            source="watchdog",
        )
    assert_preflight_or_exit(preflight_result)  # exits on failure

    if is_running():
        _echo_stdout_safe(f"Gateway already running (PID {read_pid()}).")
        _write_watchdog_tick("already_running")
        raise typer.Exit(0)

    # Detect abnormal termination: pid file exists (was running) but process is dead.
    # Normal first-start or clean stop leaves no pid file.
    _stale_pid = read_pid()
    if _stale_pid is not None:
        _notify_gateway_problem(
            "Gateway abnormal termination detected",
            f"Previous process (PID {_stale_pid}) died unexpectedly. Watchdog restarting.",
            source="watchdog",
        )

    # Port guard: if the port stays held throughout a short grace window with no
    # tracked gateway, attempt to auto-clean our own orphans before giving up.
    # This self-heals the common case where the supervisor died but the inner
    # uvicorn survived and holds the port — the PT5M watchdog would otherwise
    # reject every 5 minutes, flashing a console window each time on Win11.
    if not wait_port_free(cfg.app.dashboard_port, cfg.app.bind_host, timeout=3.0):
        orphans = find_gateway_orphans()
        if orphans:
            for opid in orphans:
                kill_pid_tree(opid)
            _audit_orphan_cleanup(orphans)

        # Fallback: kill anything holding the port (catches multiprocessing
        # workers whose cmdline lacks our markers — the root cause of "ghost
        # socket" issues on Windows where parent dies but children keep the fd).
        if not wait_port_free(cfg.app.dashboard_port, cfg.app.bind_host, timeout=5.0):
            holders = find_port_holders(cfg.app.dashboard_port)
            if holders:
                for hpid in holders:
                    kill_pid_tree(hpid)
                _audit_orphan_cleanup(holders)

        if not wait_port_free(cfg.app.dashboard_port, cfg.app.bind_host, timeout=5.0):
            _echo_safe(
                f"Port {cfg.app.dashboard_port} is still in use after all cleanup "
                f"attempts. A foreign process may be holding it.",
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
        proc = _spawn_background(no_browser)
        # Verify the detached gateway actually came up. Without this, Popen returns
        # exit 0 the instant it forks — even when the supervisor immediately dies —
        # so the PT2M watchdog records LastResult=0x0 ("success") on every tick
        # while the gateway stays dead (the "False confidence" zombie: a dead
        # gateway with green-looking schtasks history). Verifying turns the
        # watchdog's success signal honest: a non-zero Exit here means the tick
        # genuinely failed and the next tick will retry from a clean state.
        _verify_started(proc, cfg)
        _write_watchdog_tick("started")
        _auto_register_watchdog(cfg)
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


_REPLACED_LAUNCHER_EXIT_CODES = {-signal.SIGTERM}


def _verify_started(proc, cfg, *, timeout: float = 60.0, poll_interval: float = 0.3) -> None:
    """Block until the detached gateway is confirmed up, or raise typer.Exit(1).

    Four outcomes:
      - proc exits before the gateway is up  → spawn died immediately; capture why.
      - pid file written + port listening     → confirmed up; verify WebUI accessible.
      - timeout with neither                   → never came up; capture why.
      - WebUI returns 404                      → static files missing (should be caught by preflight).

    Timeout budget: this only has to cover the supervisor + inner-uvicorn process
    startup up to the port BIND — run_migrations + init_tool_registry + init_scheduler,
    then uvicorn opens the socket. The LLM provider probe (up to _PROBE_TIMEOUT_SEC
    PER provider) used to run inline BEFORE the bind and routinely blew past this
    window, causing false `gateway_spawn_failed` reports on a healthy-but-slow boot;
    it now runs as a post-yield background task (see server/app.py _llm_startup_gate),
    so the port binds promptly. 60s is a generous margin on slow-start hosts
    (cold migrations, cold filesystem cache, Windows Defender scan of a fresh
    process) — earlier 15s tripped `gateway_spawn_failed` on a healthy-but-slow
    boot and the watchdog then killed the gateway it was supposed to protect.
    Do NOT couple this to the LLM probe budget — they are independent again.

    On failure, surface the tail of errors.log (the inner uvicorn's stderr sink)
    and audit a `gateway_spawn_failed` event so the death is never silent again.
    """
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # The detached launcher process exiting non-zero before the gateway is up
        # means the supervisor failed to take hold (import error, instant crash).
        if proc is not None and proc.poll() is not None and proc.returncode != 0:
            if proc.returncode not in _REPLACED_LAUNCHER_EXIT_CODES:
                _fail_spawn(f"launcher exited with code {proc.returncode} before gateway came up")
            # A git-hook restart and the OS watchdog can overlap. A newer restart
            # deliberately sends SIGTERM to this launcher before its replacement
            # binds the port. Keep waiting for that replacement; timeout still
            # records a real failure if no healthy gateway takes over.
            proc = None
        if is_running() and port_in_use(cfg.app.dashboard_port, cfg.app.bind_host):
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

    if is_running() and port_in_use(cfg.app.dashboard_port, cfg.app.bind_host):
        _stop_failed_gateway(cfg)
        _fail_spawn(
            "gateway port came up but API/WebUI readiness did not pass; "
            "dashboard_static may be missing or mid-build"
        )
    _fail_spawn(f"gateway did not come up within {timeout:.0f}s (no pid file / port not listening)")


def _stop_failed_gateway(cfg) -> None:
    """Stop a just-started gateway that failed spawn verification.

    If we leave an API-only process running, the watchdog sees "already running"
    and never retries, while users get a broken dashboard. Kill only the tracked
    Aletheia gateway tree, then clear the pid after the port is free.
    """
    try:
        pid = read_pid()
        if pid:
            kill_pid_tree(pid)
        wait_port_free(cfg.app.dashboard_port, cfg.app.bind_host, timeout=5.0)
        clear_pid()
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
            "actor": "system", "source": "watchdog", "event_type": "gateway_spawn_failed",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": f"Gateway spawn failed: {reason}",
        })
    except Exception:
        pass

    _notify_gateway_problem(
        "Aletheia Gateway failed to start",
        reason,
        source="watchdog",
        error_log=True,
        audit=False,
    )

    msg = f"Gateway failed to start: {reason}."
    if tail:
        msg += f"\n--- errors.log (tail) ---\n{tail}"
    _echo_safe(msg)
    raise typer.Exit(1)


def _write_watchdog_tick(action: str) -> None:
    """Record a watchdog tick for diagnostics. Best-effort, never raises."""
    try:
        from aletheia.gateway.autostart import write_watchdog_tick
        write_watchdog_tick(action)
    except Exception:
        pass


def _auto_register_watchdog(cfg) -> None:
    """Auto-register the autostart watchdog after a successful start.

    Controlled by `runtime.autostart_on_start` (default True). This makes the
    watchdog opt-out instead of opt-in — users no longer need to run
    `enable-autostart` separately after `gateway start`.
    """
    if not getattr(cfg.runtime, "autostart_on_start", True):
        return
    try:
        from aletheia.gateway import autostart
        if not autostart.is_supported_platform():
            return
        # Skip if already registered (avoid re-register on every watchdog tick).
        current = autostart.status()
        if current.get("enabled") and current.get("config_valid") is not False:
            return
        result = autostart.enable(source="auto")
        if result.get("ok"):
            _echo_stdout_safe("Autostart watchdog registered automatically.")
    except Exception:
        pass  # auto-register is best-effort; never block gateway start


def _run_server(cfg) -> None:
    """Run uvicorn in-process (foreground mode) with auto-reload."""
    import os
    import uvicorn
    project_root = Path(__file__).resolve().parent.parent.parent
    aletheia_dir = str(project_root / "aletheia")
    os.environ.setdefault("ALETHEIA_DEV_RELOAD", "1")
    uvicorn.run(
        "aletheia.server.app:app",
        host=cfg.app.bind_host,
        port=cfg.app.dashboard_port,
        log_level="debug",
        reload=True,
        reload_dirs=[aletheia_dir],
    )


def _spawn_background(no_browser: bool):
    """Spawn a detached background process running the gateway. Returns the Popen.

    The returned handle lets the caller verify the launcher didn't die instantly
    (see _verify_started). It is the launcher process, not the supervisor — the
    supervisor writes its own pid file, which is what is_running() checks.
    """
    import os
    script = Path(__file__).resolve().parent.parent / "_gateway_runner.py"
    real_python, env_patch = _resolve_real_python()
    args = [real_python, str(script)]
    if no_browser:
        args.append("--no-browser")

    if platform.system() == "Windows":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        popen_kwargs: dict = {
            "creationflags": CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if env_patch:
            env = os.environ.copy()
            env.update(env_patch)
            popen_kwargs["env"] = env
        proc = subprocess.Popen(args, **popen_kwargs)
    else:
        proc = subprocess.Popen(
            args,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    _echo_stdout_safe("Gateway started in background.")
    return proc


def do_stop() -> None:
    """Stop the gateway: tree-kill the supervisor (+ its uvicorn child), clean any
    orphaned uvicorn processes we previously spawned, and wait for the port to free.

    Fixes the restart leak where killing only the supervisor (no /T) orphaned the
    inner uvicorn, which kept holding the port and made the next start crash-loop.
    """
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
    # concurrent watchdog/start in the kill window still sees a tracked gateway
    # rather than "untracked but port held" → false-orphan misdiagnosis.
    freed = wait_port_free(port, host, timeout=5.0)

    # Fallback: kill port holders directly (catches multiprocessing workers
    # whose cmdline doesn't match our markers).
    if not freed:
        holders = find_port_holders(port)
        for hpid in holders:
            if hpid not in targeted:
                kill_pid_tree(hpid)
                targeted.add(hpid)
                killed_any = True
        freed = wait_port_free(port, host, timeout=5.0)

    clear_pid()

    if freed:
        _echo_stdout_safe("Gateway stopped.")
    else:
        _echo_safe(
            f"Gateway processes signalled, but port {port} is still in use. "
            f"A non-Aletheia process may be holding it.",
        )

    _audit_stop(freed=freed, killed_any=killed_any)


def _audit_stop(*, freed: bool, killed_any: bool) -> None:
    """Best-effort audit of a gateway stop. Never raises."""
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "human", "source": "cli", "event_type": "gateway_stopped",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": f"Gateway stopped (killed={killed_any}, port_freed={freed})",
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
    from aletheia.gateway.autostart import status as autostart_status
    a = autostart_status()
    if not a.get("supported"):
        autostart_label = "not supported"
    elif a.get("enabled") is True:
        autostart_label = "enabled"
    elif a.get("enabled") is False:
        autostart_label = "disabled"
    else:
        autostart_label = f"unknown ({a.get('reason', 'error')})"
    typer.echo(f"Autostart: {autostart_label}")

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
    This guards the watchdog's "already running" path which fires every 5 min.
    """
    if _is_headless():
        import logging
        logging.getLogger("aletheia.gateway.lifecycle").info(msg)
        return
    typer.echo(msg)


def _audit_orphan_cleanup(orphan_pids: list[int]) -> None:
    """Best-effort audit of watchdog self-healing orphan cleanup."""
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "system", "source": "watchdog", "event_type": "orphan_cleanup",
            "entity_type": "gateway", "entity_id": "gateway",
            "message": f"Watchdog auto-cleaned {len(orphan_pids)} orphan(s): {orphan_pids}",
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

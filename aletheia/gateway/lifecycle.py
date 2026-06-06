"""Gateway lifecycle operations: start, stop, status, pause, resume, logs."""

import platform
import subprocess
import sys
from pathlib import Path

import typer

from aletheia.config.loader import bootstrap, load_config, save_config, get_data_dir
from aletheia.gateway.pidfile import (
    is_running, read_pid, write_pid, clear_pid, pid_path,
    kill_pid_tree, port_in_use, wait_port_free, find_gateway_orphans,
    _resolve_real_python,
)
from aletheia.gateway.browser import open_browser
from aletheia.gateway.logs import tail_logs, log_path


def do_start(*, no_browser: bool = False, foreground: bool = False) -> None:
    """Start the gateway (foreground or background)."""
    cfg = bootstrap()

    if is_running():
        typer.echo(f"Gateway already running (PID {read_pid()}).")
        raise typer.Exit(0)

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
            # Give the port time to release after killing orphans.
            if not wait_port_free(cfg.app.dashboard_port, cfg.app.bind_host, timeout=5.0):
                _echo_safe(
                    f"Port {cfg.app.dashboard_port} is still in use after cleaning "
                    f"orphaned Aletheia processes. A foreign process may be holding it.",
                )
                raise typer.Exit(1)
        else:
            _echo_safe(
                f"Port {cfg.app.dashboard_port} is already in use but no tracked gateway "
                f"or Aletheia orphan found. A foreign process is holding the port.",
            )
            raise typer.Exit(1)

    if foreground:
        typer.echo(f"Starting Aletheia gateway on http://{cfg.app.bind_host}:{cfg.app.dashboard_port} (foreground)...")
        write_pid()
        if not no_browser and cfg.app.auto_open_browser:
            open_browser(cfg.app.dashboard_port)
        _run_server(cfg)
    else:
        typer.echo(f"Starting Aletheia gateway on http://{cfg.app.bind_host}:{cfg.app.dashboard_port} (background)...")
        _spawn_background(no_browser)


def _run_server(cfg) -> None:
    """Run uvicorn in-process (foreground mode)."""
    import uvicorn
    uvicorn.run(
        "aletheia.server.app:app",
        host=cfg.app.bind_host,
        port=cfg.app.dashboard_port,
        log_level="info",
    )


def _spawn_background(no_browser: bool) -> None:
    """Spawn a detached background process running the gateway."""
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
        subprocess.Popen(args, **popen_kwargs)
    else:
        subprocess.Popen(
            args,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    typer.echo("Gateway started in background.")


def do_stop() -> None:
    """Stop the gateway: tree-kill the supervisor (+ its uvicorn child), clean any
    orphaned uvicorn processes we previously spawned, and wait for the port to free.

    Fixes the restart leak where killing only the supervisor (no /T) orphaned the
    inner uvicorn, which kept holding the port and made the next start crash-loop.
    """
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
        typer.echo("Gateway is not running.")
        return

    # Wait for the port to actually release BEFORE clearing the pidfile, so a
    # concurrent watchdog/start in the kill window still sees a tracked gateway
    # rather than "untracked but port held" → false-orphan misdiagnosis.
    freed = wait_port_free(port, host, timeout=10.0)
    clear_pid()

    if freed:
        typer.echo("Gateway stopped.")
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
    typer.echo(f"Exchange: {cfg.exchange.name} ({cfg.exchange.mode})")
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


def do_logs(lines: int = 50) -> None:
    """Show recent gateway logs."""
    entries = tail_logs(lines)
    if not entries:
        typer.echo("No logs yet.")
        return
    for line in entries:
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


def do_pause() -> None:
    """Pause autonomous jobs."""
    cfg = load_config()
    cfg.runtime.paused = True
    save_config(cfg)
    typer.echo("Gateway paused. Autonomous jobs suspended.")


def do_resume() -> None:
    """Resume paused jobs."""
    cfg = load_config()
    cfg.runtime.paused = False
    save_config(cfg)
    typer.echo("Gateway resumed.")

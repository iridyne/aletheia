"""Gateway lifecycle operations: start, stop, status, pause, resume, logs."""

import sys
import subprocess
from pathlib import Path

import typer

from aletheia.config.loader import bootstrap, load_config, save_config, get_data_dir
from aletheia.gateway.pidfile import (
    is_running, read_pid, write_pid, clear_pid, pid_path,
    kill_pid_tree, port_in_use, wait_port_free, find_gateway_orphans,
)
from aletheia.gateway.browser import open_browser
from aletheia.gateway.logs import tail_logs, log_path


def do_start(*, no_browser: bool = False, foreground: bool = False) -> None:
    """Start the gateway (foreground or background)."""
    cfg = bootstrap()

    if is_running():
        typer.echo(f"Gateway already running (PID {read_pid()}).")
        raise typer.Exit(0)

    # Port guard: if the port is held but no gateway is tracked, an orphaned process
    # is squatting on it. Spawning now would just crash-loop the new supervisor, so
    # refuse and point at `stop` (which cleans our own orphans).
    if port_in_use(cfg.app.dashboard_port, cfg.app.bind_host):
        typer.echo(
            f"Port {cfg.app.dashboard_port} is already in use but no tracked gateway "
            f"is running. Run `aletheia gateway stop` to clear orphaned processes, "
            f"then retry.", err=True,
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
    import platform

    script = Path(__file__).resolve().parent.parent / "_gateway_runner.py"
    args = [sys.executable, str(script)]
    if no_browser:
        args.append("--no-browser")

    if platform.system() == "Windows":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            args,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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
    clear_pid()

    # Proactively clean orphaned uvicorns WE spawned (matched by app marker only;
    # never touches a non-Aletheia process).
    for opid in find_gateway_orphans(port):
        kill_pid_tree(opid)
        killed_any = True

    if not killed_any and not port_in_use(port, host):
        typer.echo("Gateway is not running.")
        return

    freed = wait_port_free(port, host, timeout=10.0)
    if freed:
        typer.echo("Gateway stopped.")
    else:
        typer.echo(
            f"Gateway processes signalled, but port {port} is still in use. "
            f"A non-Aletheia process may be holding it.", err=True,
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

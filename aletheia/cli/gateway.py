"""Gateway lifecycle CLI commands."""

import platform
from pathlib import Path
import sys
import time

import typer

gateway_app = typer.Typer(no_args_is_help=True)
service_app = typer.Typer(no_args_is_help=True, help="Install and inspect the OS service backend.")
firewall_app = typer.Typer(no_args_is_help=True, help="Inspect and configure Windows dashboard LAN access.")
gateway_app.add_typer(service_app, name="service")
gateway_app.add_typer(firewall_app, name="firewall")

_WILDCARD_HOSTS = {"0.0.0.0", "::"}


def _service_result(result: dict, *, success: str) -> None:
    if result.get("ok"):
        typer.echo(success)
        return
    reason = result.get("reason") or "service_error"
    detail = result.get("detail")
    typer.echo(f"Gateway service operation failed: {reason}" + (f" — {detail}" if detail else ""), err=True)
    if reason == "elevation_required":
        typer.echo("Re-run without --no-elevate to use the Windows UAC secure desktop.", err=True)
    raise typer.Exit(1)


def _safe_firewall_state(state: dict) -> dict:
    return {
        key: state.get(key)
        for key in (
            "supported", "configured", "rule_present", "port", "program",
            "conflicting_block_rules", "reason",
        )
        if key in state
    }


def _audit_firewall_change(*, action: str, before: dict, after: dict) -> bool:
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "human",
            "source": "cli",
            "event_type": "gateway_firewall_changed",
            "entity_type": "gateway_firewall",
            "entity_id": "windows-dashboard-lan",
            "before": _safe_firewall_state(before),
            "after": _safe_firewall_state(after),
            "message": f"Windows dashboard LAN firewall {action}",
        })
        return True
    except Exception:
        return False


def _maybe_configure_windows_lan_firewall(*, enabled: bool) -> None:
    """Request UAC only from an explicit interactive start/restart command."""
    if not enabled or platform.system() != "Windows":
        return
    from aletheia.config.loader import load_config
    from aletheia.gateway import windows_firewall, windows_machine_service
    cfg = load_config()
    if cfg.app.bind_host not in _WILDCARD_HOSTS:
        return
    before = windows_firewall.status(cfg.app.dashboard_port)
    if before.get("configured"):
        return
    if not sys.stdin.isatty():
        typer.echo(
            "Windows LAN firewall access is not configured; run "
            "`aletheia gateway firewall enable-lan` from an interactive desktop.",
            err=True,
        )
        return
    typer.echo(
        f"Requesting Windows Firewall permission for local-subnet TCP {cfg.app.dashboard_port} "
        "(UAC may prompt)..."
    )
    after = windows_machine_service.firewall_enable_lan(cfg.app.dashboard_port, elevate=True)
    if after.get("ok"):
        audited = _audit_firewall_change(action="enabled", before=before, after=after)
        typer.echo(
            f"Windows LAN access enabled for TCP {cfg.app.dashboard_port}; "
            f"backend port {cfg.app.dashboard_port + 1} was not opened."
        )
        if not audited:
            typer.echo("Warning: firewall access changed but its audit record could not be written.", err=True)
    else:
        reason = after.get("reason") or "firewall_error"
        typer.echo(
            f"Windows LAN firewall setup did not complete ({reason}); gateway will still start for local access. "
            "Run `aletheia gateway firewall enable-lan` to retry.",
            err=True,
        )


@gateway_app.command()
def start(
    browser: bool = typer.Option(
        False, "--browser/--no-browser",
        help="Open dashboard in browser after start. Default: off. Use "
             "'aletheia gateway open-dashboard' to open it later.",
    ),
    foreground: bool = typer.Option(False, "--foreground", help="Run in foreground."),
    lan_firewall: bool = typer.Option(
        True,
        "--lan-firewall/--no-lan-firewall",
        help="On interactive Windows starts, request one-time UAC approval for local-subnet dashboard access.",
    ),
):
    """Start the Aletheia gateway."""
    _maybe_configure_windows_lan_firewall(enabled=lan_firewall)
    from aletheia.gateway.lifecycle import do_start
    do_start(no_browser=not browser, foreground=foreground)


@gateway_app.command()
def stop():
    """Stop the running gateway."""
    from aletheia.gateway.lifecycle import do_stop
    do_stop()


@gateway_app.command()
def restart(
    browser: bool = typer.Option(
        False, "--browser/--no-browser",
        help="Open dashboard in browser after restart. Default: off. Use "
             "'aletheia gateway open-dashboard' to open it later.",
    ),
    lan_firewall: bool = typer.Option(
        True,
        "--lan-firewall/--no-lan-firewall",
        help="On interactive Windows restarts, request one-time UAC approval for local-subnet dashboard access.",
    ),
):
    """Restart the gateway.

    do_stop tree-kills the supervisor + inner uvicorn, cleans our orphans, and waits
    for the port to free; do_start then refuses to spawn if the port is somehow still
    held — so a failed stop no longer stacks crash-looping supervisors.
    """
    _maybe_configure_windows_lan_firewall(enabled=lan_firewall)
    from aletheia.gateway.lifecycle import do_stop, do_start
    started_at = time.monotonic()
    do_stop()
    stopped_at = time.monotonic()
    do_start(no_browser=not browser, foreground=False)
    finished_at = time.monotonic()
    typer.echo(
        "Gateway restart completed in "
        f"{finished_at - started_at:.1f}s "
        f"(stop {stopped_at - started_at:.1f}s; start/readiness {finished_at - stopped_at:.1f}s)."
    )


@gateway_app.command()
def status():
    """Show gateway status."""
    from aletheia.gateway.lifecycle import do_status
    do_status()


@gateway_app.command()
def logs(
    lines: int = typer.Option(50, "-n", "--lines", help="Number of lines to show."),
):
    """Show gateway logs."""
    from aletheia.gateway.lifecycle import do_logs
    do_logs(lines=lines)


@gateway_app.command()
def pause():
    """Pause all autonomous agent/trading jobs."""
    from aletheia.gateway.lifecycle import do_pause
    do_pause()


@gateway_app.command()
def resume():
    """Resume paused jobs."""
    from aletheia.gateway.lifecycle import do_resume
    do_resume()


@gateway_app.command(name="enable")
def enable_autostart():
    """Enable login/boot recovery without starting the Gateway now."""
    from aletheia.gateway import service_control
    _service_result(service_control.enable(source="cli"), success="Gateway service enabled. The running state was not changed.")


@gateway_app.command(name="disable")
def disable_autostart():
    """Disable login/boot recovery without stopping the Gateway now."""
    from aletheia.gateway import service_control
    _service_result(service_control.disable(source="cli"), success="Gateway service disabled. The running state was not changed.")


@service_app.command(name="status")
def service_status():
    """Show installed, enabled, running, ownership, and drift independently."""
    from aletheia.gateway import service_control
    result = service_control.status()
    for key in ("backend", "scope", "registration", "installed", "enabled", "runtime", "owned", "config_valid", "recovery_scope", "data_dir"):
        value = result.get(key)
        if value is not None:
            typer.echo(f"{key}: {str(value).lower() if isinstance(value, bool) else value}")
    if result.get("reason"):
        typer.echo(f"reason: {result['reason']}")


@service_app.command(name="install")
def service_install(
    backend: str = typer.Option("session", "--backend", help="session or windows-service"),
    elevate: bool = typer.Option(True, "--elevate/--no-elevate", help="Use Windows UAC for machine-scope changes."),
):
    """Install a disabled persistent service definition."""
    from aletheia.gateway import service_control
    _service_result(
        service_control.install(backend=backend, elevate=elevate, source="cli"),
        success=f"Gateway service installed ({backend}); it remains disabled and stopped.",
    )


@service_app.command(name="uninstall")
def service_uninstall(
    backend: str | None = typer.Option(None, "--backend", help="session or windows-service; defaults to the installed backend"),
    elevate: bool = typer.Option(True, "--elevate/--no-elevate", help="Use Windows UAC for machine-scope changes."),
):
    """Uninstall a stopped service definition while preserving runtime data."""
    from aletheia.gateway import service_control
    _service_result(
        service_control.uninstall(backend=backend, elevate=elevate, source="cli"),
        success="Gateway service uninstalled. Runtime data and credentials were preserved.",
    )


@service_app.command(name="elevated-apply", hidden=True)
def service_elevated_apply(request: Path = typer.Option(..., "--request")):
    """Apply one validated Windows service transaction after UAC elevation."""
    from aletheia.gateway.windows_machine_service import apply_elevated_request
    result = apply_elevated_request(request)
    raise typer.Exit(0 if result.get("ok") else 1)


@firewall_app.command(name="status")
def firewall_status():
    """Show whether Windows permits local-subnet access to the dashboard port."""
    from aletheia.config.loader import load_config
    from aletheia.gateway.windows_firewall import status as read_status
    cfg = load_config()
    result = read_status(cfg.app.dashboard_port)
    for key in (
        "supported", "configured", "rule_present", "port", "program",
        "conflicting_block_rules", "reason",
    ):
        if key in result:
            typer.echo(f"{key}: {result[key]}")


@firewall_app.command(name="enable-lan")
def firewall_enable_lan(
    elevate: bool = typer.Option(True, "--elevate/--no-elevate", help="Use the Windows UAC secure desktop."),
):
    """Allow local-subnet TCP access to the configured dashboard port."""
    from aletheia.config.loader import load_config
    from aletheia.gateway import windows_firewall, windows_machine_service
    cfg = load_config()
    if cfg.app.bind_host not in _WILDCARD_HOSTS:
        typer.echo("Firewall setup refused: app.bind_host is loopback; select 0.0.0.0 or :: first.", err=True)
        raise typer.Exit(1)
    before = windows_firewall.status(cfg.app.dashboard_port)
    result = windows_machine_service.firewall_enable_lan(cfg.app.dashboard_port, elevate=elevate)
    if not result.get("ok"):
        reason = result.get("reason") or "firewall_error"
        typer.echo(f"Windows LAN firewall setup failed: {reason}", err=True)
        if reason == "elevation_required":
            typer.echo("Re-run without --no-elevate to use the Windows UAC secure desktop.", err=True)
        raise typer.Exit(1)
    audited = _audit_firewall_change(action="enabled", before=before, after=result)
    typer.echo(
        f"Windows LAN access enabled for TCP {cfg.app.dashboard_port}; "
        f"backend port {cfg.app.dashboard_port + 1} was not opened."
    )
    if not audited:
        typer.echo("Warning: firewall access changed but its audit record could not be written.", err=True)


@firewall_app.command(name="disable-lan")
def firewall_disable_lan(
    elevate: bool = typer.Option(True, "--elevate/--no-elevate", help="Use the Windows UAC secure desktop."),
):
    """Remove Aletheia's LAN rule and restore narrowed Python block rules."""
    from aletheia.config.loader import load_config
    from aletheia.gateway import windows_firewall, windows_machine_service
    cfg = load_config()
    before = windows_firewall.status(cfg.app.dashboard_port)
    result = windows_machine_service.firewall_disable_lan(cfg.app.dashboard_port, elevate=elevate)
    if not result.get("ok"):
        reason = result.get("reason") or "firewall_error"
        typer.echo(f"Windows LAN firewall removal failed: {reason}", err=True)
        if reason == "elevation_required":
            typer.echo("Re-run without --no-elevate to use the Windows UAC secure desktop.", err=True)
        raise typer.Exit(1)
    audited = _audit_firewall_change(action="disabled", before=before, after=result)
    typer.echo("Windows dashboard LAN firewall rule removed; prior Python block filters were restored.")
    if not audited:
        typer.echo("Warning: firewall access changed but its audit record could not be written.", err=True)


@gateway_app.command(name="open-dashboard")
def open_dashboard():
    """Open the dashboard in the default browser."""
    import webbrowser
    from aletheia.config.loader import load_config
    cfg = load_config()
    webbrowser.open(f"http://127.0.0.1:{cfg.app.dashboard_port}")

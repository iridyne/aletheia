"""Gateway lifecycle CLI commands."""

from pathlib import Path

import typer

gateway_app = typer.Typer(no_args_is_help=True)
service_app = typer.Typer(no_args_is_help=True, help="Install and inspect the OS service backend.")
gateway_app.add_typer(service_app, name="service")


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


@gateway_app.command()
def start(
    browser: bool = typer.Option(
        False, "--browser/--no-browser",
        help="Open dashboard in browser after start. Default: off. Use "
             "'aletheia gateway open-dashboard' to open it later.",
    ),
    foreground: bool = typer.Option(False, "--foreground", help="Run in foreground."),
):
    """Start the Aletheia gateway."""
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
):
    """Restart the gateway.

    do_stop tree-kills the supervisor + inner uvicorn, cleans our orphans, and waits
    for the port to free; do_start then refuses to spawn if the port is somehow still
    held — so a failed stop no longer stacks crash-looping supervisors.
    """
    from aletheia.gateway.lifecycle import do_stop, do_start
    do_stop()
    do_start(no_browser=not browser, foreground=False)


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


@gateway_app.command(name="open-dashboard")
def open_dashboard():
    """Open the dashboard in the default browser."""
    from aletheia.gateway.browser import open_browser
    from aletheia.config.loader import load_config
    cfg = load_config()
    open_browser(cfg.app.dashboard_port)

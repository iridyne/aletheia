"""Gateway lifecycle CLI commands."""

import typer

gateway_app = typer.Typer(no_args_is_help=True)


@gateway_app.command()
def start(
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser."),
    foreground: bool = typer.Option(False, "--foreground", help="Run in foreground."),
):
    """Start the Aletheia gateway."""
    from aletheia.gateway.lifecycle import do_start
    do_start(no_browser=no_browser, foreground=foreground)


@gateway_app.command()
def stop():
    """Stop the running gateway."""
    from aletheia.gateway.lifecycle import do_stop
    do_stop()


@gateway_app.command()
def restart(
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open browser."),
):
    """Restart the gateway.

    do_stop tree-kills the supervisor + inner uvicorn, cleans our orphans, and waits
    for the port to free; do_start then refuses to spawn if the port is somehow still
    held — so a failed stop no longer stacks crash-looping supervisors.
    """
    from aletheia.gateway.lifecycle import do_stop, do_start
    do_stop()
    do_start(no_browser=no_browser, foreground=False)


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


@gateway_app.command(name="open-dashboard")
def open_dashboard():
    """Open the dashboard in the default browser."""
    from aletheia.gateway.browser import open_browser
    from aletheia.config.loader import load_config
    cfg = load_config()
    open_browser(cfg.app.dashboard_port)

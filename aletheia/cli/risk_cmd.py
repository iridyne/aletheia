"""Risk / kill-switch CLI commands."""

import typer

risk_app = typer.Typer(no_args_is_help=True)


@risk_app.callback()
def _deprecated():
    from aletheia.cli._deprecated import warn_deprecated
    warn_deprecated("Risk / Kill-switch panel")


@risk_app.command()
def status():
    """Show kill-switch state and key risk boundaries."""
    from aletheia.config.loader import load_config
    from aletheia.risk import kill_switch
    cfg = load_config()
    typer.echo(f"Kill switch: {'ACTIVE' if kill_switch.is_active() else 'inactive'}")
    typer.echo(f"Max daily loss: {cfg.risk.max_daily_loss_pct}%")
    mode = cfg.risk.symbol_filter_mode
    syms = ', '.join(cfg.risk.symbol_list) or '(none)'
    typer.echo(f"Symbol filter: {mode} — {syms}")
    typer.echo(f"Live state: {cfg.live.state}")


@risk_app.command()
def kill(reason: str = typer.Option("", "--reason", help="Why the brake was pulled")):
    """Engage the global trading brake — rejects every proposal."""
    from aletheia.risk import kill_switch
    kill_switch.activate(reason=reason, actor="human")
    typer.echo("Kill switch ACTIVE — all trading proposals will be rejected.")


@risk_app.command()
def resume():
    """Release the global trading brake."""
    from aletheia.risk import kill_switch
    kill_switch.deactivate(actor="human")
    typer.echo("Kill switch released.")

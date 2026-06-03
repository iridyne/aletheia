"""Strategy lifecycle CLI commands."""

import typer

strategy_app = typer.Typer(no_args_is_help=True)


@strategy_app.command()
def list():
    """List strategies."""
    typer.echo("Strategy list: stub (implement in M9)")


@strategy_app.command()
def status(strategy_id: str = typer.Argument(...)):
    """Show strategy lifecycle status."""
    typer.echo(f"Strategy {strategy_id}: stub (implement in M9)")

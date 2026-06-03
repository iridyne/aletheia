"""Bitget CLI commands."""

import typer

bitget_app = typer.Typer(no_args_is_help=True)


@bitget_app.command()
def test():
    """Test Bitget demo connection."""
    typer.echo("Bitget test: stub (implement in M7)")

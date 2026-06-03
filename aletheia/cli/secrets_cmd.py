"""Secrets management CLI commands."""

import typer

secrets_app = typer.Typer(no_args_is_help=True)


@secrets_app.command(name="set")
def set_secret(key: str = typer.Argument(..., help="Secret key path, e.g. bitget.demo.api_key")):
    """Set a secret value (prompted securely)."""
    typer.echo(f"Secrets set: stub for '{key}' (implement in M1)")


@secrets_app.command()
def test(service: str = typer.Argument(..., help="Service to test, e.g. bitget")):
    """Test secrets connectivity for a service."""
    typer.echo(f"Secrets test: stub for '{service}' (implement in M7)")

"""Model provider CLI commands."""

import typer

model_app = typer.Typer(no_args_is_help=True)


@model_app.command()
def status():
    """Show current model provider status."""
    typer.echo("Model provider status: stub (implement in M4)")


@model_app.command()
def providers():
    """List configured providers."""
    typer.echo("Providers: stub (implement in M4)")


@model_app.command()
def test():
    """Test model provider connectivity."""
    typer.echo("Model test: stub (implement in M4)")

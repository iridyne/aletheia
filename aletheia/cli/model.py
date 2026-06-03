"""Model provider CLI commands."""

import asyncio
import typer

model_app = typer.Typer(no_args_is_help=True)


@model_app.command()
def status():
    """Show current model provider status."""
    from aletheia.config.loader import load_config
    cfg = load_config()
    typer.echo(f"Default provider: {cfg.models.default_provider}")
    for name, pcfg in cfg.models.providers.items():
        enabled = "enabled" if pcfg.enabled else "disabled"
        typer.echo(f"  {name}: {pcfg.base_url or pcfg.command} ({enabled})")


@model_app.command()
def providers():
    """List configured providers."""
    from aletheia.config.loader import load_config
    cfg = load_config()
    for name, pcfg in cfg.models.providers.items():
        typer.echo(f"{name}:")
        if pcfg.base_url:
            typer.echo(f"  base_url: {pcfg.base_url}")
        if pcfg.protocol:
            typer.echo(f"  protocol: {pcfg.protocol}")
        typer.echo(f"  model: {pcfg.model}")
        typer.echo(f"  enabled: {pcfg.enabled}")


@model_app.command()
def test():
    """Test model provider connectivity."""
    from aletheia.model_provider.router import healthcheck_all
    results = asyncio.run(healthcheck_all())
    for h in results:
        icon = "✓" if h.available else "✗"
        msg = f"  {icon} {h.provider}: {'available' if h.available else h.error}"
        typer.echo(msg)

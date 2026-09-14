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
        icon = "✓" if h.state in {"reachable", "verified"} else "✗"
        detail = h.state if h.state else ("available" if h.available else h.error)
        if h.reason_code and h.reason_code != h.state:
            detail += f" ({h.reason_code})"
        if h.available and h.models:
            detail += f" ({len(h.models)} models)"
        typer.echo(f"  {icon} {h.provider}: {detail}")
    if not any(
        h.enabled and h.state in {"reachable", "verified"}
        for h in results
    ):
        typer.echo("No enabled model provider is reachable.", err=True)
        raise typer.Exit(1)


@model_app.command()
def probe():
    """Probe enabled providers with a real minimal inference call."""
    from aletheia.config.loader import load_config
    from aletheia.agent_runtime import startup_gate

    result = asyncio.run(startup_gate.probe_llm_providers_at_startup(load_config()))
    for row in result.get("results") or []:
        if row.get("usable"):
            typer.echo(f"  ✓ {row['provider']}: usable")
        else:
            typer.echo(f"  ✗ {row['provider']}: {row.get('error') or 'not usable'}")
    if not result.get("any_usable"):
        typer.echo("No enabled LLM provider could complete real inference.", err=True)
        raise typer.Exit(1)

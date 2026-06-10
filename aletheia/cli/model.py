"""Model provider CLI commands."""

import asyncio
import typer

model_app = typer.Typer(no_args_is_help=True)


@model_app.callback()
def _deprecated():
    from aletheia.cli._deprecated import warn_deprecated
    warn_deprecated("Settings → Model Provider")


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


@model_app.command(name="set")
def set_cmd(
    provider: str = typer.Option(..., "--provider", help="openai_compatible|anthropic_compatible|codex_cli"),
    base_url: str = typer.Option(None, "--base-url"),
    protocol: str = typer.Option(None, "--protocol", help="chat_completions|responses"),
    model: str = typer.Option(None, "--model"),
    enabled: bool = typer.Option(None, "--enabled"),
    default: bool = typer.Option(False, "--default", help="Set as default provider"),
    api_key: bool = typer.Option(False, "--api-key", help="Prompt for an API key (stored as secret)"),
):
    """Configure a model provider (guide §12.3). Dashboard is the primary surface."""
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    from aletheia.model_provider.router import clear_cache

    valid = {"openai_compatible", "anthropic_compatible", "codex_cli"}
    if provider not in valid:
        typer.echo(f"Invalid provider. Choose from: {sorted(valid)}", err=True)
        raise typer.Exit(1)

    cfg = load_config()
    pcfg = cfg.models.providers.get(provider)
    if pcfg is None:
        from aletheia.config.schema import ProviderConfig
        pcfg = ProviderConfig()
        cfg.models.providers[provider] = pcfg

    if base_url is not None:
        pcfg.base_url = base_url
    if protocol is not None:
        pcfg.protocol = protocol
    if model is not None:
        pcfg.model = model
    if enabled is not None:
        pcfg.enabled = enabled
    if default:
        cfg.models.default_provider = provider
    if api_key:
        value = typer.prompt(f"API key for '{provider}'", hide_input=True, confirmation_prompt=True)
        ref = f"model.{provider}.api_key"
        set_secret(ref, value)
        pcfg.api_key_secret_ref = ref

    save_config(cfg)
    clear_cache()
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "cli", "event_type": "model_provider_changed",
        "entity_type": "model_provider", "entity_id": provider,
        "message": f"Model provider '{provider}' updated via CLI",
    })
    typer.echo(f"Provider '{provider}' updated. Default: {cfg.models.default_provider}")


@model_app.command()
def test():
    """Test model provider connectivity."""
    from aletheia.model_provider.router import healthcheck_all
    results = asyncio.run(healthcheck_all())
    for h in results:
        icon = "✓" if h.available else "✗"
        detail = "available" if h.available else h.error
        if h.available and h.models:
            detail += f" ({len(h.models)} models)"
        typer.echo(f"  {icon} {h.provider}: {detail}")

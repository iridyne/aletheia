"""CLI ingress for HTTP endpoint credentials."""

import typer


def set_cmd(
    provider: str = typer.Option(..., "--provider", help="openai_compatible|anthropic_compatible"),
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
    from aletheia.model_provider.router import (
        clear_cache,
        set_default_model_provider,
        clear_provider_verification,
        provider_configuration_identity,
    )

    valid = {"openai_compatible", "anthropic_compatible"}
    if provider not in valid:
        typer.echo(f"Invalid provider. Choose from: {sorted(valid)}", err=True)
        raise typer.Exit(1)

    cfg = load_config()
    pcfg = cfg.models.providers.get(provider)
    if pcfg is not None and (pcfg.type or provider) not in valid:
        typer.echo("Unsupported historical provider; configure a separate HTTP endpoint in Settings.", err=True)
        raise typer.Exit(1)
    identity_before = (
        provider_configuration_identity(provider, pcfg) if pcfg is not None else None
    )
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
        set_default_model_provider(cfg, provider)
    if api_key:
        value = typer.prompt(f"API key for '{provider}'", hide_input=True, confirmation_prompt=True)
        ref = f"model.{provider}.api_key"
        set_secret(ref, value)
        pcfg.api_key_secret_ref = ref

    identity_after = provider_configuration_identity(provider, pcfg)
    if identity_before is not None and identity_before != identity_after:
        clear_provider_verification(pcfg)

    save_config(cfg)
    clear_cache()
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "cli", "event_type": "model_provider_changed",
        "entity_type": "model_provider", "entity_id": provider,
        "message": f"Model provider '{provider}' updated via CLI",
    })
    typer.echo(f"Provider '{provider}' updated. Default: {cfg.models.default_provider}")

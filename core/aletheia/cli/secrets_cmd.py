"""Secrets management CLI commands (secondary surface — dashboard is primary)."""

import typer

from aletheia.config.secrets import set_secret, redact

secrets_app = typer.Typer(no_args_is_help=True)

@secrets_app.callback()
def secrets():
    """Explicit credential setup; inspect configured endpoints in Settings."""


@secrets_app.command(name="set")
def set_cmd(key: str = typer.Argument(..., help="Secret key path, e.g. bitget.demo.api_key")):
    """Set a secret value (prompted securely, never echoed)."""
    from aletheia.config.loader import load_config

    cfg = load_config()
    selected_live_prefix = f"{str(cfg.exchange.name or '').lower()}.live."
    if cfg.live.state == "live_limited" and key.strip().lower().startswith(selected_live_prefix):
        typer.echo(
            "Error: lock live trading and re-run the selected-account preflight before rotating the selected live credentials.",
            err=True,
        )
        raise typer.Exit(1)
    value = typer.prompt(f"Value for '{key}'", hide_input=True, confirmation_prompt=True)
    set_secret(key, value)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "cli", "event_type": "secret_rotated",
        "entity_type": "secret", "entity_id": key,
        "message": f"Secret '{key}' set via CLI",
    })
    typer.echo(f"Stored '{key}' = {redact(value)}")

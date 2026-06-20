"""Secrets management CLI commands (secondary surface — dashboard is primary)."""

import asyncio
import typer

from aletheia.config.secrets import set_secret, get_secret, redact

secrets_app = typer.Typer(no_args_is_help=True)

# Known secret keys the system uses, for `secrets list`.
KNOWN_KEYS = [
    "bitget.demo.api_key",
    "bitget.demo.api_secret",
    "bitget.demo.passphrase",
    "bitget.live.api_key",
    "bitget.live.api_secret",
    "bitget.live.passphrase",
    "binance.testnet.api_key",
    "binance.testnet.api_secret",
    "telegram.bot_token",
    "model.openai_compatible.api_key",
    "model.anthropic_compatible.api_key",
]


@secrets_app.callback()
def _deprecated():
    from aletheia.cli._deprecated import warn_deprecated
    warn_deprecated("Settings → Credentials / Model Provider")


@secrets_app.command(name="set")
def set_cmd(key: str = typer.Argument(..., help="Secret key path, e.g. bitget.demo.api_key")):
    """Set a secret value (prompted securely, never echoed)."""
    value = typer.prompt(f"Value for '{key}'", hide_input=True, confirmation_prompt=True)
    set_secret(key, value)
    from aletheia.files.jsonl import write_audit
    write_audit({
        "actor": "human", "source": "cli", "event_type": "secret_rotated",
        "entity_type": "secret", "entity_id": key,
        "message": f"Secret '{key}' set via CLI",
    })
    typer.echo(f"Stored '{key}' = {redact(value)}")


@secrets_app.command(name="list")
def list_cmd():
    """List known secret keys and whether each is SET (values shown redacted only)."""
    for key in KNOWN_KEYS:
        val = get_secret(key)
        if val:
            typer.echo(f"  SET     {key}  ({redact(val)})")
        else:
            typer.echo(f"  MISSING {key}")


@secrets_app.command()
def test(service: str = typer.Argument(..., help="Service to test, e.g. bitget")):
    """Test secrets connectivity for a service."""
    if service == "bitget":
        from aletheia.config.loader import load_config
        from aletheia.exchanges.bitget.client import BitgetClient
        cfg = load_config()
        client = BitgetClient(mode=cfg.exchange.mode)
        result = asyncio.run(client.test_connection())
        if result["connected"]:
            typer.echo(f"✓ Bitget {result['mode']} connection successful")
        else:
            typer.echo(f"✗ Bitget {result['mode']} connection failed: {result['error']}", err=True)
            raise typer.Exit(1)
    else:
        typer.echo(f"Unknown service '{service}'. Supported: bitget", err=True)
        raise typer.Exit(1)

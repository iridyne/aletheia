"""Bitget CLI commands."""

import asyncio
import typer

bitget_app = typer.Typer(no_args_is_help=True)


@bitget_app.command()
def test():
    """Test Bitget demo connection."""
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

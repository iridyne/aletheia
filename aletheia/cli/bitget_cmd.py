"""Bitget CLI commands."""

import asyncio
import typer

bitget_app = typer.Typer(no_args_is_help=True)


@bitget_app.command()
def test():
    """Test Bitget demo connection."""
    from aletheia.config.loader import load_config
    from aletheia.exchanges.bitget.client import BitgetClient
    from aletheia.exchanges.bitget.profile_config import configured_bitget_profile

    cfg = load_config()
    client = BitgetClient(mode=cfg.exchange.mode, profile=configured_bitget_profile())
    result = asyncio.run(client.test_connection())
    if result["connected"]:
        typer.echo(f"✓ Bitget {result['mode']} connection successful")
    else:
        typer.echo(f"✗ Bitget {result['mode']} connection failed: {result['error']}", err=True)
        raise typer.Exit(1)


@bitget_app.command()
def sync():
    """Refresh the configured Bitget account through UTA."""
    from aletheia.config.loader import load_config
    from aletheia.storage.migrations import assert_migrations_current
    from aletheia.portfolio.exchange_sync import sync_all_exchanges, status_for

    assert_migrations_current()
    mode = "bitget_live_limited" if load_config().exchange.mode == "live" else "bitget_demo"
    state = sync_all_exchanges(execution_modes=(mode,))
    if state is None or status_for(state) != "ok":
        typer.echo(f"UTA account unavailable: {state}", err=True)
        raise typer.Exit(1)
    account = next(row for row in state["accounts"] if row["broker_id"] == mode)
    typer.echo(f"mode: {mode}")
    typer.echo(f"equity_usdt: {account.get('equity_usdt')}")
    typer.echo(f"available_usdt: {account.get('available_usdt')}")
    typer.echo(f"fetched_at: {state.get('fetched_at')}")

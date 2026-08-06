"""Bitget CLI commands."""

import asyncio
import typer

bitget_app = typer.Typer(no_args_is_help=True)


@bitget_app.command()
def test():
    """Test Bitget demo connection."""
    from aletheia.cli._deprecated import warn_deprecated
    warn_deprecated("Settings → Credentials (Test connection)")
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


@bitget_app.command()
def sync():
    """Pull real account state from Bitget once (debug helper for the cached sync)."""
    from aletheia.storage.migrations import assert_migrations_current
    from aletheia.portfolio.exchange_sync import sync_exchange_account, get_cached_state, status_for

    assert_migrations_current()
    state = sync_exchange_account()
    cached = get_cached_state()
    if state is None and cached is None:
        typer.echo("Sync skipped or failed (no credentials, or exchange error). "
                   "Check `aletheia bitget test` and `aletheia secrets list`.", err=True)
        raise typer.Exit(1)
    s = state.__dict__ if state else (cached or {})
    typer.echo(f"status: {status_for(cached)}")
    typer.echo(f"mode: {s.get('mode')}")
    typer.echo(f"equity_usdt: {s.get('equity_usdt')}")
    typer.echo(f"  available: {s.get('available_usdt')}  frozen: {s.get('frozen_usdt')}  "
               f"futures_uPnL: {s.get('futures_unrealized_pnl')}")
    typer.echo(f"open_positions: {s.get('open_positions_count')}  open_orders: {s.get('open_orders_count')}")
    typer.echo(f"fetched_at: {s.get('fetched_at')}")

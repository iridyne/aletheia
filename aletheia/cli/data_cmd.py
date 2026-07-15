"""CLI: aletheia data — historical data and accounting management."""

import asyncio
import json
from typing import Optional

import typer

data_app = typer.Typer(no_args_is_help=True)


@data_app.command("reconcile-local-paper-accounting")
def reconcile_local_paper_accounting(
    apply: bool = typer.Option(
        False, "--apply",
        help="Apply only exact backlinks and additive reconciliation rows. Default is dry-run.",
    ),
):
    """Recover provable historical local-paper accounting without changing raw PnL.

    This command never writes an exchange, Risk decision, Position economics, or
    paper cash event.  It reports unavailable evidence instead of estimating it.
    """
    from aletheia.storage.migrations import run_migrations
    from aletheia.maintenance.historical_accounting import reconcile_historical_local_paper_accounting

    run_migrations()
    typer.echo(json.dumps(
        reconcile_historical_local_paper_accounting(dry_run=not apply),
        indent=2, ensure_ascii=False, sort_keys=True,
    ))


@data_app.command("download")
def download(
    symbol: str = typer.Argument(..., help="Canonical symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe (1m/5m/15m/1h/4h/1d)"),
    months: int = typer.Option(6, help="How many months of history to fetch"),
    exchange: str = typer.Option("", help="Source exchange (default: configured primary)"),
    market_type: str = typer.Option("spot", help="Instrument market type: spot | perp"),
):
    """Bulk-download historical OHLCV candles from the configured exchange."""
    from aletheia.data.history_downloader import download_symbol_history

    typer.echo(f"Downloading {symbol} {timeframe} — {months} months...")

    def _progress(n: int):
        typer.echo(f"  {n} bars fetched...", nl=False)
        typer.echo("\r", nl=False)

    result = asyncio.run(download_symbol_history(
        symbol, timeframe, months=months, exchange=exchange or None,
        market_type=market_type, progress_callback=_progress,
    ))

    if result.error:
        typer.echo(f"\nCompleted with error: {result.error}")
    typer.echo(f"\nDone: {result.bars_fetched} bars [{result.oldest_ts} → {result.newest_ts}]")


@data_app.command("backfill")
def backfill(
    months: int = typer.Option(1, help="Months of history to backfill"),
    symbols: Optional[str] = typer.Option(None, help="Comma-separated symbols (default: all active members)"),
    timeframes: str = typer.Option("1h,4h", help="Comma-separated timeframes"),
    exchange: str = typer.Option("", help="Venue for explicit --symbols"),
    market_type: str = typer.Option("spot", help="Market type for explicit --symbols"),
):
    """Batch-backfill OHLCV history for all active universe members."""
    from aletheia.data.history_downloader import download_symbol_history
    from aletheia.config.loader import load_config
    from aletheia.storage.repositories import get_active_universe_members, get_universe_asset

    if symbols:
        venue = exchange or load_config().market_data.ccxt_exchange
        instruments = [(venue, s.strip(), market_type) for s in symbols.split(",") if s.strip()]
    else:
        members = get_active_universe_members()
        instruments = []
        for member in members:
            asset = get_universe_asset(member.get("asset_id")) or {}
            venue = asset.get("venue")
            mkt = member.get("market_type") or asset.get("market_type")
            if venue and member.get("symbol") and mkt:
                instruments.append((venue, member["symbol"], mkt))

    if not instruments:
        typer.echo("No symbols to backfill (empty universe).")
        raise typer.Exit(1)

    tf_list = [t.strip() for t in timeframes.split(",")]
    typer.echo(f"Backfilling {len(instruments)} instruments × {len(tf_list)} timeframes ({months}mo)...")

    total_bars = 0
    for venue, sym, mkt in instruments:
        for tf in tf_list:
            result = asyncio.run(download_symbol_history(
                sym, tf, months=months, exchange=venue, market_type=mkt,
            ))
            total_bars += result.bars_fetched
            status = f"  {venue}:{sym}:{mkt}/{tf}: +{result.bars_fetched} bars"
            if result.error:
                status += f" (error: {result.error})"
            typer.echo(status)

    typer.echo(f"\nTotal: +{total_bars} bars")


@data_app.command("coverage")
def coverage(
    symbol: str = typer.Argument(..., help="Canonical symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe"),
    exchange: str = typer.Option("", help="Filter by source exchange"),
    market_type: str = typer.Option("", help="Filter by market type: spot | perp"),
):
    """Show local OHLCV data coverage for a symbol."""
    from aletheia.storage.repositories import get_ohlcv_coverage

    cov = get_ohlcv_coverage(
        symbol, timeframe, exchange=exchange, market_type=market_type,
    )
    identity = f"{exchange or '*'}:{symbol}:{market_type or '*'}"
    typer.echo(f"{identity} {timeframe}: {cov['bar_count']} bars")
    if cov["oldest_ts"]:
        from datetime import datetime, timezone
        oldest = datetime.fromtimestamp(cov["oldest_ts"] / 1000, tz=timezone.utc)
        newest = datetime.fromtimestamp(cov["newest_ts"] / 1000, tz=timezone.utc)
        typer.echo(f"  Range: {oldest.strftime('%Y-%m-%d %H:%M')} → {newest.strftime('%Y-%m-%d %H:%M')} UTC")

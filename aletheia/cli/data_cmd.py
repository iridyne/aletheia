"""CLI: aletheia data — historical OHLCV management."""

import asyncio

import typer

data_app = typer.Typer(no_args_is_help=True)


@data_app.command("download")
def download(
    symbol: str = typer.Argument(..., help="Canonical symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe (1m/5m/15m/1h/4h/1d)"),
    months: int = typer.Option(6, help="How many months of history to fetch"),
):
    """Bulk-download historical OHLCV candles from the configured exchange."""
    from aletheia.data.history_downloader import download_symbol_history

    typer.echo(f"Downloading {symbol} {timeframe} — {months} months...")

    def _progress(n: int):
        typer.echo(f"  {n} bars fetched...", nl=False)
        typer.echo("\r", nl=False)

    result = asyncio.run(download_symbol_history(
        symbol, timeframe, months=months, progress_callback=_progress,
    ))

    if result.error:
        typer.echo(f"\nCompleted with error: {result.error}")
    typer.echo(f"\nDone: {result.bars_fetched} bars [{result.oldest_ts} → {result.newest_ts}]")


@data_app.command("coverage")
def coverage(
    symbol: str = typer.Argument(..., help="Canonical symbol, e.g. BTC/USDT"),
    timeframe: str = typer.Option("1h", help="Candle timeframe"),
):
    """Show local OHLCV data coverage for a symbol."""
    from aletheia.storage.repositories import get_ohlcv_coverage

    cov = get_ohlcv_coverage(symbol, timeframe)
    typer.echo(f"{symbol} {timeframe}: {cov['bar_count']} bars")
    if cov["oldest_ts"]:
        from datetime import datetime, timezone
        oldest = datetime.fromtimestamp(cov["oldest_ts"] / 1000, tz=timezone.utc)
        newest = datetime.fromtimestamp(cov["newest_ts"] / 1000, tz=timezone.utc)
        typer.echo(f"  Range: {oldest.strftime('%Y-%m-%d %H:%M')} → {newest.strftime('%Y-%m-%d %H:%M')} UTC")

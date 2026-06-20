"""Bulk OHLCV history downloader — paginated fetch + incremental sync.

Used by:
- ``aletheia data download`` CLI for manual bulk imports
- Nightly backfill scheduler job
- Backtest orchestrator when coverage is insufficient
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger("aletheia.history_downloader")

_PAGE_SIZE = 500
_RATE_LIMIT_SEC = 0.35


@dataclass
class DownloadResult:
    symbol: str
    timeframe: str
    bars_fetched: int
    oldest_ts: int | None
    newest_ts: int | None
    error: str | None = None


async def download_symbol_history(
    symbol: str,
    timeframe: str,
    months: int = 6,
    exchange: str | None = None,
    progress_callback=None,
) -> DownloadResult:
    """Download historical OHLCV data for a symbol, persisting to SQLite.

    Incremental: detects newest existing bar and only fetches the gap.
    Paginates backward from now in chunks of 500 bars.
    """
    from aletheia.config.loader import load_config
    from aletheia.data.ccxt_provider import get_market_data_provider, timeframe_ms
    from aletheia.storage.repositories import (
        get_ohlcv_newest_ts,
        upsert_ohlcv_candles,
    )

    cfg = load_config()
    tf_ms = timeframe_ms(timeframe)
    if tf_ms == 0:
        return DownloadResult(symbol, timeframe, 0, None, None, f"unsupported timeframe: {timeframe}")

    max_bars = cfg.historical_data.max_bars_per_pair
    now_ms = int(time.time() * 1000)
    start_ts = now_ms - months * 30 * 24 * 3600 * 1000

    newest_existing = get_ohlcv_newest_ts(symbol, timeframe)
    if newest_existing and newest_existing > start_ts:
        fetch_since = newest_existing + tf_ms
    else:
        fetch_since = start_ts

    if fetch_since >= now_ms:
        return DownloadResult(symbol, timeframe, 0, newest_existing, newest_existing)

    provider = get_market_data_provider(exchange=exchange)
    total_fetched = 0
    first_ts: int | None = None
    last_ts: int | None = None
    error: str | None = None
    current_since = fetch_since

    try:
        while current_since < now_ms and total_fetched < max_bars:
            try:
                candles = await provider.get_ohlcv(
                    symbol, timeframe, limit=_PAGE_SIZE, since=current_since
                )
            except Exception as e:
                error = str(e)
                logger.warning("history_downloader: fetch failed %s %s since=%d: %s",
                               symbol, timeframe, current_since, e)
                break

            if not candles:
                break

            upsert_ohlcv_candles(symbol, timeframe, candles, exchange=exchange or "")

            page_oldest = candles[0].timestamp
            page_newest = candles[-1].timestamp

            if first_ts is None:
                first_ts = page_oldest
            last_ts = page_newest
            total_fetched += len(candles)

            if progress_callback:
                progress_callback(total_fetched)

            current_since = page_newest + tf_ms

            if len(candles) < _PAGE_SIZE:
                break

            await asyncio.sleep(_RATE_LIMIT_SEC)
    finally:
        await provider.close()

    logger.info("history_downloader: %s %s fetched %d bars [%s → %s]",
                symbol, timeframe, total_fetched,
                first_ts, last_ts)

    return DownloadResult(
        symbol=symbol,
        timeframe=timeframe,
        bars_fetched=total_fetched,
        oldest_ts=first_ts,
        newest_ts=last_ts,
        error=error,
    )


def check_coverage(symbol: str, timeframe: str, min_bars: int | None = None) -> bool:
    """Return True if enough local bars exist for backtesting."""
    from aletheia.config.loader import load_config
    from aletheia.storage.repositories import get_ohlcv_coverage

    if min_bars is None:
        min_bars = load_config().historical_data.min_bars_for_backtest
    cov = get_ohlcv_coverage(symbol, timeframe)
    return cov["bar_count"] >= min_bars

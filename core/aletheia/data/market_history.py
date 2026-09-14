"""Core persistence policy for confirmed market candles, independent of transport."""

from aletheia.data.providers import Candle


def persist_closed_candles(
    canon: str,
    timeframe: str,
    candles: list[Candle],
    exchange: str,
    market_type: str = "spot",
    *,
    provider: str,
) -> None:
    """Persist confirmed bars; callers decide whether a failed write is optional."""
    closed_candles = [candle for candle in candles if candle.is_closed]
    if not closed_candles:
        return
    from aletheia.storage.repositories import upsert_ohlcv_candles

    upsert_ohlcv_candles(
        symbol=canon, timeframe=timeframe, candles=closed_candles,
        provider=provider, exchange=exchange, market_type=market_type,
    )

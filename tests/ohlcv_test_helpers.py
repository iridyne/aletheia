from __future__ import annotations

from aletheia.data.providers import OHLCVResult, parse_ohlcv_candle


def observation_reader(exchange):
    """Adapt a fixture exchange's raw rows to the provider observation contract."""

    async def read(instrument, *, timeframe="1h", limit=100, use_cache=False):
        raw = await exchange.fetch_ohlcv(
            instrument.to_ccxt_symbol(), timeframe=timeframe, limit=limit,
        )
        candles = [
            candle
            for row in raw
            if (candle := parse_ohlcv_candle(
                row,
                provider="fixture",
                exchange=instrument.exchange,
            )) is not None
        ]
        return OHLCVResult(candles, requested=limit)

    return read

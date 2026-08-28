"""Binance public REST API adapter — no auth required.

Fourth-tier fallback (after CCXT primary, CCXT fallback, CoinGecko).
Supports all standard timeframes (1m/5m/15m/1h/4h/1d). Generous rate
limit (1200 req/min) so no per-call throttle needed.
"""

from __future__ import annotations

import logging
import time

from aletheia.data.providers import (
    OHLCVAttempt,
    OHLCVResult,
    Ticker,
    _required_ticker_field,
    classify_ohlcv_outcome,
    parse_ohlcv_candle,
)
from aletheia.data.symbols import (
    InstrumentId,
    MARKET_SPOT,
    canonical_of,
    to_binance,
)

logger = logging.getLogger("aletheia.data.sources.binance_public")

_SPOT_BASE = "https://api.binance.com/api/v3"

_TF_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1w": "1w",
}


def _spot_canonical(symbol: InstrumentId | str) -> str:
    if isinstance(symbol, InstrumentId) and symbol.market_type != MARKET_SPOT:
        raise NotImplementedError("Binance public supports spot instruments only")
    return canonical_of(symbol)


class BinancePublicProvider:
    """MarketDataProvider backed by Binance public REST (no auth)."""

    venue_name = "binance"

    async def get_ticker(self, symbol) -> Ticker:
        from aletheia.net import httpx_client

        canon = _spot_canonical(symbol)
        bn_sym = to_binance(canon)

        try:
            async with httpx_client(timeout=10) as client:
                resp = await client.get(
                    f"{_SPOT_BASE}/ticker/24hr",
                    params={"symbol": bn_sym},
                )
                resp.raise_for_status()
                raw = resp.json()
        except Exception as exc:
            from aletheia.data.venue_health import is_venue_refusal, record_venue_failure

            if is_venue_refusal(exc):
                record_venue_failure(self.venue_name, exc)
            raise
        ticker = Ticker(
            symbol=canon,
            last_price=_required_ticker_field(
                raw, "lastPrice", field="last_price",
            ),
            volume_24h=_required_ticker_field(
                raw, "quoteVolume", field="volume_24h",
            ),
            change_pct_24h=_required_ticker_field(
                raw, "priceChangePercent", field="change_pct_24h",
            ),
            high_24h=raw.get("highPrice"),
            low_24h=raw.get("lowPrice"),
            provider="binance_public",
        )
        from aletheia.data.venue_health import record_venue_success

        record_venue_success(self.venue_name)
        return ticker

    async def get_ohlcv(
        self,
        symbol,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
        *,
        use_cache: bool = True,
    ) -> OHLCVResult:
        from aletheia.net import httpx_client

        canon = _spot_canonical(symbol)
        bn_sym = to_binance(canon)
        interval = _TF_MAP.get(timeframe)
        if interval is None:
            raise NotImplementedError(
                f"Binance public does not support {timeframe} candles"
            )

        params: dict = {
            "symbol": bn_sym,
            "interval": interval,
            "limit": min(limit, 1000),
        }
        if since is not None:
            params["startTime"] = since

        try:
            async with httpx_client(timeout=15) as client:
                resp = await client.get(f"{_SPOT_BASE}/klines", params=params)
                resp.raise_for_status()
                rows = resp.json()
        except Exception as exc:
            from aletheia.data.venue_health import is_venue_refusal, record_venue_failure

            if is_venue_refusal(exc):
                record_venue_failure(self.venue_name, exc)
            raise
        from aletheia.data.venue_health import record_venue_success

        record_venue_success(self.venue_name)

        parsed = [
            parse_ohlcv_candle(
                row,
                provider="binance_public",
                exchange=self.venue_name,
            )
            for row in rows
        ]
        candles = [candle for candle in parsed if candle is not None]
        if candles:
            from aletheia.data.ccxt_provider import timeframe_ms

            tf_ms = timeframe_ms(timeframe)
            if (
                tf_ms > 0
                and candles[-1].timestamp + tf_ms > time.time_ns() // 1_000_000
            ):
                candles[-1].is_closed = False
        rejected = len(parsed) - len(candles)
        attempt = OHLCVAttempt(
            provider="binance_public",
            outcome=classify_ohlcv_outcome(
                accepted=len(candles), rejected=rejected, requested=limit,
            ),
            accepted=len(candles),
            rejected=rejected,
        )
        if rejected:
            logger.warning("ohlcv quality rejected %d/%d Binance row(s)", rejected, len(parsed))
        return OHLCVResult(
            candles,
            requested=limit,
            rejected=rejected,
            attempts=(attempt,),
            source="provider_fetch",
        )

    async def close(self) -> None:
        pass

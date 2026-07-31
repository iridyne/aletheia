"""Binance public REST API adapter — no auth required.

Fourth-tier fallback (after CCXT primary, CCXT fallback, CoinGecko).
Supports all standard timeframes (1m/5m/15m/1h/4h/1d). Generous rate
limit (1200 req/min) so no per-call throttle needed.
"""

from __future__ import annotations

import logging

from aletheia.data.providers import Candle, Ticker

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


class BinancePublicProvider:
    """MarketDataProvider backed by Binance public REST (no auth)."""

    venue_name = "binance"

    async def get_ticker(self, symbol) -> Ticker:
        from aletheia.data.symbols import canonical_of
        from aletheia.data.sources.symbol_map import to_binance_symbol
        from aletheia.net import httpx_client

        canon = canonical_of(symbol)
        bn_sym = to_binance_symbol(canon)

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
        from aletheia.data.venue_health import record_venue_success

        record_venue_success(self.venue_name)

        return Ticker(
            symbol=canon,
            last_price=float(raw.get("lastPrice") or 0.0),
            volume_24h=float(raw.get("quoteVolume") or 0.0),
            change_pct_24h=float(raw.get("priceChangePercent") or 0.0),
            high_24h=float(raw.get("highPrice") or 0.0) or None,
            low_24h=float(raw.get("lowPrice") or 0.0) or None,
            provider="binance_public",
        )

    async def get_ohlcv(
        self,
        symbol,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
    ) -> list[Candle]:
        from aletheia.data.symbols import canonical_of
        from aletheia.data.sources.symbol_map import to_binance_symbol
        from aletheia.net import httpx_client

        canon = canonical_of(symbol)
        bn_sym = to_binance_symbol(canon)
        interval = _TF_MAP.get(timeframe, timeframe)

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

        candles = [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]
        return candles

    async def close(self) -> None:
        pass

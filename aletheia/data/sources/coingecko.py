"""CoinGecko public API adapter — third-tier fallback for tickers and OHLCV.

Rate limit: free tier allows 10-30 req/min. Conservative 1 req/6s throttle.
Only called when both CCXT primary and CCXT fallback have failed.

Supported timeframes: "4h" (via /coins/{id}/ohlc?days=7) and "1d" (days=31+).
"1h" requests mapped to 4h. Finer timeframes (1m/5m/15m) raise NotImplementedError.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aletheia.data.providers import Candle, Ticker

logger = logging.getLogger("aletheia.data.sources.coingecko")

_BASE_URL = "https://api.coingecko.com/api/v3"
_MIN_INTERVAL_SEC = 6.0
_last_request_time: float = 0.0
_rate_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _rate_lock
    if _rate_lock is None:
        _rate_lock = asyncio.Lock()
    return _rate_lock


async def _throttle() -> None:
    global _last_request_time
    async with _get_lock():
        now = time.monotonic()
        wait = _MIN_INTERVAL_SEC - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_time = time.monotonic()


class CoinGeckoProvider:
    """MarketDataProvider backed by CoinGecko public API."""

    async def get_ticker(self, symbol) -> Ticker:
        from aletheia.data.symbols import to_canonical
        from aletheia.data.sources.symbol_map import to_coingecko_id
        from aletheia.net import httpx_client

        canon = to_canonical(str(symbol))
        cg_id = to_coingecko_id(canon)
        if cg_id is None:
            # F6: unmapped symbol — fail clearly so the provider fallback chain tries
            # another source instead of querying CoinGecko with ids=None.
            raise ValueError(f"CoinGecko has no slug mapping for {canon}")

        await _throttle()
        async with httpx_client(timeout=10) as client:
            resp = await client.get(
                f"{_BASE_URL}/simple/price",
                params={
                    "ids": cg_id,
                    "vs_currencies": "usd",
                    "include_24hr_vol": "true",
                    "include_24hr_change": "true",
                },
            )
            resp.raise_for_status()
            data = resp.json().get(cg_id, {})

        return Ticker(
            symbol=canon,
            last_price=float(data.get("usd") or 0.0),
            volume_24h=float(data.get("usd_24h_vol") or 0.0),
            change_pct_24h=float(data.get("usd_24h_change") or 0.0),
            provider="coingecko",
        )

    async def get_ohlcv(
        self,
        symbol,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
    ) -> list[Candle]:
        from aletheia.data.symbols import to_canonical
        from aletheia.data.sources.symbol_map import to_coingecko_id
        from aletheia.net import httpx_client

        if timeframe in ("1m", "5m", "15m"):
            raise NotImplementedError(
                f"CoinGecko does not support {timeframe} candles"
            )

        canon = to_canonical(str(symbol))
        cg_id = to_coingecko_id(canon)
        if cg_id is None:
            raise ValueError(f"CoinGecko has no slug mapping for {canon}")

        if timeframe in ("1h", "4h"):
            days = 7
        else:
            days = max(limit, 31)

        await _throttle()
        async with httpx_client(timeout=15) as client:
            resp = await client.get(
                f"{_BASE_URL}/coins/{cg_id}/ohlc",
                params={"vs_currency": "usd", "days": str(days)},
            )
            resp.raise_for_status()
            rows = resp.json()

        candles = [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=0.0,
            )
            for row in rows
            if isinstance(row, (list, tuple)) and len(row) >= 5
        ]
        if limit and len(candles) > limit:
            candles = candles[-limit:]
        return candles

    async def close(self) -> None:
        pass

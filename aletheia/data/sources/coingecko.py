"""CoinGecko public API adapter — third-tier fallback for tickers.

Rate limit: free tier allows 10-30 req/min. Conservative 1 req/6s throttle.
Only called when both CCXT primary and CCXT fallback have failed.

The public ``/coins/{id}/ohlc`` endpoint is not an OHLCV source: its candle
granularity is range-dependent and its rows contain no volume. ``get_ohlcv``
therefore reports unsupported instead of relabeling coarse OHLC or synthesizing
zero volume.
"""

from __future__ import annotations

import asyncio
import time

from aletheia.data.providers import OHLCVResult, Ticker

_BASE_URL = "https://api.coingecko.com/api/v3"
_MIN_INTERVAL_SEC = 6.0
_last_request_time: float = 0.0

# Canonical symbol → CoinGecko coin ID (previously in data/sources/symbol_map.py)
_CG_SLUG: dict[str, str] = {
    "BTC/USDT": "bitcoin", "ETH/USDT": "ethereum", "SOL/USDT": "solana",
    "BNB/USDT": "binancecoin", "XRP/USDT": "ripple", "ADA/USDT": "cardano",
    "AVAX/USDT": "avalanche-2", "DOT/USDT": "polkadot", "MATIC/USDT": "matic-network",
    "LINK/USDT": "chainlink", "DOGE/USDT": "dogecoin", "LTC/USDT": "litecoin",
    "UNI/USDT": "uniswap", "ATOM/USDT": "cosmos", "FIL/USDT": "filecoin",
    "NEAR/USDT": "near", "OP/USDT": "optimism", "ARB/USDT": "arbitrum",
    "SUI/USDT": "sui", "APT/USDT": "aptos", "PEPE/USDT": "pepe",
    "WIF/USDT": "dogwifcoin", "TRX/USDT": "tron", "TON/USDT": "the-open-network",
    "RENDER/USDT": "render-token", "INJ/USDT": "injective-protocol",
    "FET/USDT": "fetch-ai", "AAVE/USDT": "aave", "MKR/USDT": "maker",
    "CRV/USDT": "curve-dao-token",
}


def _to_coingecko_id(canonical: str) -> str | None:
    """Return the CoinGecko coin ID for a canonical symbol, or None if unmapped."""
    from aletheia.data.symbols import to_canonical
    return _CG_SLUG.get(to_canonical(canonical))


_rate_lock = asyncio.Lock()


async def _throttle() -> None:
    global _last_request_time
    async with _rate_lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_SEC - (now - _last_request_time)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_time = time.monotonic()


class CoinGeckoProvider:
    """MarketDataProvider backed by CoinGecko public API."""

    venue_name = "coingecko"

    async def get_ticker(self, symbol) -> Ticker:
        from aletheia.data.symbols import canonical_of
        from aletheia.net import httpx_client

        canon = canonical_of(symbol)
        cg_id = _to_coingecko_id(canon)
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
    ) -> OHLCVResult:
        raise NotImplementedError(
            "CoinGecko public OHLC has range-dependent intervals and no volume; "
            f"exact {timeframe} OHLCV is unsupported"
        )

    async def close(self) -> None:
        pass

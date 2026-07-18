"""Macro collector — BTC dominance, total market cap, Nasdaq, DXY, VIX.

Data sources (priority order):
1. FRED API (primary for VIX/DXY/SPY/Nasdaq) — official, reliable, free 120 req/min
2. CoinGecko /global (BTC dominance + total crypto market cap)
3. yFinance (fallback for tradfi when FRED is unavailable)

FRED API key: env var FRED_API_KEY (register free at https://fred.stlouisfed.org/docs/api/api_key.html).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import MacroSnapshot

logger = logging.getLogger("aletheia.data.collectors.macro")

_COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
_FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
# Alternative.me Fear & Greed Index — single value 0-100, updated ~daily.
# Public, no API key, no documented rate limit. Use limit=1 for the latest reading.
_ALT_FNG_URL = "https://api.alternative.me/fng/"

# FRED series IDs for our macro indicators
_FRED_SERIES = {
    "vix": "VIXCLS",          # VIX close (daily)
    "dxy": "DTWEXBGS",        # Trade-Weighted Dollar Index (Broad)
    "spy": "SP500",           # S&P 500
    "nasdaq": "NASDAQCOM",    # Nasdaq Composite
}


class MacroCollector:
    domain = "macro"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Collect macro indicators. symbols param unused (global data).

        Note: MacroSnapshot.top_movers_summary is NOT populated here.
        The scheduler injects it from CoinGeckoTopCollector.movers_summary
        before persisting the snapshot in the Phase B tick.
        """
        now = datetime.now(timezone.utc).isoformat()
        errors: list[str] = []

        try:
            from aletheia.config.secrets import get_secret
            fred_key = get_secret("fred_api_key") or os.environ.get("FRED_API_KEY")
        except Exception:
            fred_key = os.environ.get("FRED_API_KEY")
        fng_enabled = False
        try:
            from aletheia.config.loader import load_config
            fng_enabled = load_config().market_data.enable_fear_greed
        except Exception:
            pass

        # CoinGecko, FRED, and FNG are fully independent — run concurrently.
        cg_coro = _fetch_coingecko()
        fred_coro = _fetch_fred(fred_key) if fred_key else None
        fng_coro = _fetch_fng() if fng_enabled else None

        coros: list = [cg_coro]
        if fred_coro is not None:
            coros.append(fred_coro)
        if fng_coro is not None:
            coros.append(fng_coro)

        gathered = await asyncio.gather(*coros, return_exceptions=True)
        idx = 0

        cg_result = gathered[idx]; idx += 1
        btc_dominance = total_cap = total_cap_change = None
        if isinstance(cg_result, BaseException):
            errors.append(f"coingecko: {cg_result}")
            logger.debug("CoinGecko global fetch failed: %s", cg_result)
        else:
            btc_dominance, total_cap, total_cap_change = cg_result

        spy = vix = dxy = nasdaq = None
        if fred_coro is not None:
            fred_result = gathered[idx]; idx += 1
            if isinstance(fred_result, BaseException):
                errors.append(f"fred: {fred_result}")
                logger.debug("FRED fetch failed, falling back to yfinance: %s", fred_result)
            else:
                spy, vix, dxy, nasdaq = fred_result
                logger.debug("FRED macro: spy=%s vix=%s dxy=%s nasdaq=%s", spy, vix, dxy, nasdaq)

        fng_value: int | None = None
        fng_classification: str | None = None
        if fng_coro is not None:
            fng_result = gathered[idx]; idx += 1
            if isinstance(fng_result, BaseException):
                errors.append(f"fng: {fng_result}")
                logger.debug("Alt.me F&G fetch failed (non-fatal): %s", fng_result)
            else:
                fng_value, fng_classification = fng_result

        # Fallback: fill any None tradfi values from yFinance.
        # #92 fix: yfinance's Ticker.history() is synchronous and can block the
        # event loop for several seconds — each ticker does its own HTTP round
        # trip. This tick runs from the scheduler's async worker, so a sync
        # call here stalled every other scheduled task (market observers,
        # heartbeats, exchange sync) for the duration. Route through
        # asyncio.to_thread so the blocking work happens off-loop.
        # Each ticker runs in its own thread concurrently so fallback wall-clock
        # is max(each ticker) instead of sum(each ticker).
        if any(v is None for v in (spy, vix, dxy, nasdaq)):
            try:
                yf_results = await asyncio.gather(
                    asyncio.to_thread(_fetch_one_tradfi_ticker, "SPY"),
                    asyncio.to_thread(_fetch_one_tradfi_ticker, "^VIX"),
                    asyncio.to_thread(_fetch_one_tradfi_ticker, "DX-Y.NYB"),
                    asyncio.to_thread(_fetch_one_tradfi_ticker, "^IXIC"),
                    return_exceptions=True,
                )
                yf_spy, yf_vix, yf_dxy, yf_nasdaq = [
                    None if isinstance(r, BaseException) else r for r in yf_results
                ]
                spy = spy or yf_spy
                vix = vix or yf_vix
                dxy = dxy or yf_dxy
                nasdaq = nasdaq or yf_nasdaq
            except Exception as e:
                errors.append(f"yfinance: {e}")
                logger.debug("yfinance fallback also failed: %s", e)

        # Risk regime heuristic
        risk_regime = _classify_risk_regime(vix, total_cap_change)

        snapshot = MacroSnapshot(
            btc_dominance=btc_dominance,
            total_market_cap_usd=total_cap,
            total_market_cap_change_24h_pct=total_cap_change,
            spy_close=spy,
            vix_close=vix,
            dxy_close=dxy,
            nasdaq_close=nasdaq,
            risk_regime=risk_regime,
            fng_value=fng_value,
            fng_classification=fng_classification,
            observed_at=now,
        )

        return {"snapshot": snapshot, "errors": errors}


# ---------------------------------------------------------------------------
# Alternative.me Fear & Greed
# ---------------------------------------------------------------------------

async def _fetch_fng() -> tuple[int | None, str | None]:
    """Return (value, classification). Returns (None, None) on failure."""
    async with httpx_client(timeout=8) as client:
        resp = await client.get(_ALT_FNG_URL, params={"limit": 1, "format": "json"})
        resp.raise_for_status()
        items = resp.json().get("data", [])
        if not items:
            return None, None
        row = items[0]
        try:
            value = int(row.get("value"))
        except (TypeError, ValueError):
            value = None
        classification = row.get("value_classification") or None
        return value, classification


# ---------------------------------------------------------------------------
# FRED API
# ---------------------------------------------------------------------------

async def _fetch_fred(
    api_key: str,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Fetch latest observations for SPY, VIX, DXY, Nasdaq from FRED.

    FRED returns daily data; we grab the most recent non-"." observation
    (FRED uses "." for missing values on holidays/weekends).
    Free tier: 120 requests/min — we use 4 per call (one per series).
    All four requests run concurrently on a shared client connection pool.
    """
    results: dict[str, float | None] = {"spy": None, "vix": None, "dxy": None, "nasdaq": None}

    async def _one(client, key: str, series_id: str) -> tuple[str, float | None]:
        try:
            resp = await client.get(
                _FRED_BASE_URL,
                params={
                    "series_id": series_id,
                    "api_key": api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": 5,  # grab last 5 to skip holidays
                },
            )
            resp.raise_for_status()
            observations = resp.json().get("observations", [])
            for obs in observations:
                val = obs.get("value", ".")
                if val != ".":
                    return key, round(float(val), 2)
        except Exception as e:
            logger.debug("FRED series %s failed: %s", series_id, e)
        return key, None

    async with httpx_client(timeout=10) as client:
        pairs = await asyncio.gather(*[
            _one(client, key, series_id)
            for key, series_id in _FRED_SERIES.items()
        ])
    for key, value in pairs:
        results[key] = value

    return results["spy"], results["vix"], results["dxy"], results["nasdaq"]


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------

async def _fetch_coingecko() -> tuple[float | None, float | None, float | None]:
    async with httpx_client(timeout=10) as client:
        resp = await client.get(_COINGECKO_GLOBAL_URL)
        resp.raise_for_status()
        data = resp.json().get("data", {})

        btc_dom = data.get("market_cap_percentage", {}).get("btc")
        total_cap = data.get("total_market_cap", {}).get("usd")
        change_24h = data.get("market_cap_change_percentage_24h_usd")

        return (
            round(btc_dom, 2) if btc_dom else None,
            round(total_cap, 0) if total_cap else None,
            round(change_24h, 2) if change_24h else None,
        )


# ---------------------------------------------------------------------------
# yFinance (fallback)
# ---------------------------------------------------------------------------

def _fetch_one_tradfi_ticker(ticker_sym: str) -> float | None:
    """Fetch a single yfinance ticker. Intended to run in asyncio.to_thread."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker_sym)
        hist = t.history(period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2)
    except ImportError:
        logger.warning("yfinance not installed — tradfi macro fallback unavailable (FRED key required)")
    except Exception as e:
        logger.debug("yfinance tradfi ticker %s failed: %s", ticker_sym, e)
    return None


def _fetch_tradfi() -> tuple[float | None, float | None, float | None, float | None]:
    """Fetch SPY, VIX, DXY, Nasdaq via yfinance. Fallback when FRED unavailable."""
    spy = _fetch_one_tradfi_ticker("SPY")
    vix = _fetch_one_tradfi_ticker("^VIX")
    dxy = _fetch_one_tradfi_ticker("DX-Y.NYB")
    nasdaq = _fetch_one_tradfi_ticker("^IXIC")
    if all(v is None for v in (spy, vix, dxy, nasdaq)):
        logger.warning(
            "yfinance tradfi fallback: all tickers returned None "
            "(yfinance library change or Yahoo Finance API outage?)"
        )
    return spy, vix, dxy, nasdaq


def _classify_risk_regime(
    vix: float | None, cap_change: float | None
) -> str:
    """Simple heuristic: VIX > 25 or cap down > 3% → risk_off; VIX < 15 + cap up → risk_on."""
    if vix is not None and vix > 25:
        return "risk_off"
    if cap_change is not None and cap_change < -3.0:
        return "risk_off"
    if vix is not None and vix < 15 and (cap_change is None or cap_change > 0):
        return "risk_on"
    return "neutral"

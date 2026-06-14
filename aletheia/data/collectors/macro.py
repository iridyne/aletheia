"""Macro collector — BTC dominance, total market cap, Nasdaq, DXY, VIX.

Data sources (priority order):
1. FRED API (primary for VIX/DXY/SPY/Nasdaq) — official, reliable, free 120 req/min
2. CoinGecko /global (BTC dominance + total crypto market cap)
3. yFinance (fallback for tradfi when FRED is unavailable)

FRED API key: env var FRED_API_KEY (register free at https://fred.stlouisfed.org/docs/api/api_key.html).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from aletheia.data.models import MacroSnapshot

logger = logging.getLogger("aletheia.data.collectors.macro")

_COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
_FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

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
        """Collect macro indicators. symbols param unused (global data)."""
        now = datetime.now(timezone.utc).isoformat()
        errors: list[str] = []

        # CoinGecko global crypto metrics
        btc_dominance = None
        total_cap = None
        total_cap_change = None
        try:
            btc_dominance, total_cap, total_cap_change = await _fetch_coingecko()
        except Exception as e:
            errors.append(f"coingecko: {e}")
            logger.debug("CoinGecko global fetch failed: %s", e)

        # Traditional finance: FRED (primary) → yFinance (fallback)
        spy = None
        vix = None
        dxy = None
        nasdaq = None

        fred_key = os.environ.get("FRED_API_KEY")
        if fred_key:
            try:
                spy, vix, dxy, nasdaq = await _fetch_fred(fred_key)
                logger.debug("FRED macro: spy=%s vix=%s dxy=%s nasdaq=%s", spy, vix, dxy, nasdaq)
            except Exception as e:
                errors.append(f"fred: {e}")
                logger.debug("FRED fetch failed, falling back to yfinance: %s", e)

        # Fallback: fill any None values from yFinance
        if any(v is None for v in (spy, vix, dxy, nasdaq)):
            try:
                yf_spy, yf_vix, yf_dxy, yf_nasdaq = _fetch_tradfi()
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
            observed_at=now,
        )

        return {"snapshot": snapshot, "errors": errors}


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
    """
    results: dict[str, float | None] = {"spy": None, "vix": None, "dxy": None, "nasdaq": None}

    async with httpx.AsyncClient(trust_env=True, timeout=10) as client:
        for key, series_id in _FRED_SERIES.items():
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
                        results[key] = round(float(val), 2)
                        break
            except Exception as e:
                logger.debug("FRED series %s failed: %s", series_id, e)

    return results["spy"], results["vix"], results["dxy"], results["nasdaq"]


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------

async def _fetch_coingecko() -> tuple[float | None, float | None, float | None]:
    async with httpx.AsyncClient(trust_env=True, timeout=10) as client:
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

def _fetch_tradfi() -> tuple[float | None, float | None, float | None, float | None]:
    """Fetch SPY, VIX, DXY, Nasdaq via yfinance. Fallback when FRED unavailable."""
    try:
        import yfinance as yf
    except ImportError:
        return None, None, None, None

    tickers = {"SPY": None, "^VIX": None, "DX-Y.NYB": None, "^IXIC": None}
    for ticker_sym in tickers:
        try:
            t = yf.Ticker(ticker_sym)
            hist = t.history(period="2d")
            if not hist.empty:
                tickers[ticker_sym] = round(float(hist["Close"].iloc[-1]), 2)
        except Exception:
            pass

    return tickers["SPY"], tickers["^VIX"], tickers["DX-Y.NYB"], tickers["^IXIC"]


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

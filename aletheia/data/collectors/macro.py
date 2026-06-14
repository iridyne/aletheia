"""Macro collector — BTC dominance, total market cap, Nasdaq, DXY."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from aletheia.data.models import MacroSnapshot

logger = logging.getLogger("aletheia.data.collectors.macro")

_COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"


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

        # Traditional finance via yfinance (SPY, VIX, DXY, Nasdaq)
        spy = None
        vix = None
        dxy = None
        nasdaq = None
        try:
            spy, vix, dxy, nasdaq = _fetch_tradfi()
        except Exception as e:
            errors.append(f"yfinance: {e}")
            logger.debug("yfinance macro fetch failed: %s", e)

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


def _fetch_tradfi() -> tuple[float | None, float | None, float | None, float | None]:
    """Fetch SPY, VIX, DXY, Nasdaq via yfinance (sync, fast history call)."""
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

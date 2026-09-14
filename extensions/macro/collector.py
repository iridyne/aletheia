"""Optional macro enrichment — BTC dominance, market cap, Nasdaq, DXY, VIX.

Data sources (priority order):
1. FRED API (primary for VIX/DXY/SPY/Nasdaq) — official, reliable, free 120 req/min
2. CoinGecko /global (BTC dominance + total crypto market cap)
3. yFinance (fallback for tradfi when FRED is unavailable)

FRED acquisition and credential resolution belong to the optional FRED adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aletheia.net import httpx_client
from aletheia.data.models import MacroSnapshot

logger = logging.getLogger("aletheia.data.collectors.macro")

_COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
# Alternative.me Fear & Greed Index — single value 0-100, updated ~daily.
# Public, no API key, no documented rate limit. Use limit=1 for the latest reading.
_ALT_FNG_URL = "https://api.alternative.me/fng/"

_fred_macro_fetch: Callable[[], Awaitable[tuple]] | None = None
_fred_research_fetch: Callable[[], Awaitable[list[dict]]] | None = None
_fred_is_configured: Callable[[], bool] | None = None


def configure_fred_collector(
    macro: Callable[[], Awaitable[tuple]] | None,
    research: Callable[[], Awaitable[list[dict]]] | None,
    is_configured: Callable[[], bool] | None,
) -> None:
    global _fred_macro_fetch, _fred_research_fetch, _fred_is_configured
    _fred_macro_fetch = macro
    _fred_research_fetch = research
    _fred_is_configured = is_configured


async def _collect_fred_research() -> list[dict]:
    if _fred_research_fetch is None:
        raise RuntimeError("fred_extension_unavailable")
    return await _fred_research_fetch()


class MacroCollector:
    domain = "macro"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Collect macro indicators. symbols param unused (global data).

        Note: MacroSnapshot.top_movers_summary is NOT populated here.
        The scheduler injects it from CoinGeckoTopCollector.movers_summary
        before persisting the snapshot in the Phase B tick.
        """
        errors: list[str] = []

        fred_configured = bool(_fred_is_configured and _fred_is_configured())
        fng_enabled = False
        try:
            from aletheia.config.loader import load_config
            fng_enabled = load_config().market_data.enable_fear_greed
        except Exception:
            pass

        # CoinGecko, FRED, and FNG are fully independent — run concurrently.
        cg_coro = _fetch_coingecko()
        fred_coro = _fred_macro_fetch() if fred_configured and _fred_macro_fetch else None
        fng_coro = _fetch_fng() if fng_enabled else None

        coros: list = [cg_coro]
        if fred_coro is not None:
            coros.append(fred_coro)
        if fng_coro is not None:
            coros.append(fng_coro)
        coros.append(_collect_fred_research())

        gathered = await asyncio.gather(*coros, return_exceptions=True)
        research = gathered.pop()
        if isinstance(research, BaseException):
            # Research must not fail the existing macro consumer. Never print
            # HTTP exceptions, which can contain the FRED key in their URL.
            errors.append("fred_research:failed")
            research = []
        elif any(b["availability"] == "unavailable" and b["unavailable_reason"] != "not_configured" for b in research):
            errors.append("fred_research:partial")
        idx = 0

        cg_result = gathered[idx]; idx += 1
        btc_dominance = total_cap = total_cap_change = None
        if isinstance(cg_result, BaseException):
            errors.append(f"coingecko: {cg_result}")
            logger.debug("CoinGecko global fetch failed: %s", cg_result)
        else:
            btc_dominance, total_cap, total_cap_change = cg_result
            if all(v is None for v in cg_result):
                errors.append("coingecko:empty")

        spy = vix = dxy = nasdaq = None
        if fred_coro is not None:
            fred_result = gathered[idx]; idx += 1
            if isinstance(fred_result, BaseException):
                errors.append("fred:request_failed")
                logger.debug("FRED fetch failed, falling back to yfinance")
            else:
                spy, vix, dxy, nasdaq = fred_result
                logger.debug("FRED macro: spy=%s vix=%s dxy=%s nasdaq=%s", spy, vix, dxy, nasdaq)
                if all(v is None for v in fred_result):
                    errors.append("fred:empty")

        fng_value: int | None = None
        fng_classification: str | None = None
        if fng_coro is not None:
            fng_result = gathered[idx]; idx += 1
            if isinstance(fng_result, BaseException):
                errors.append(f"fng: {fng_result}")
                logger.debug("Alt.me F&G fetch failed (non-fatal): %s", fng_result)
            else:
                fng_value, fng_classification = fng_result
                if fng_value is None and fng_classification is None:
                    errors.append("fng:empty")

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
                if all(v is None for v in yf_results):
                    errors.append("yfinance:empty")
            except Exception as e:
                errors.append(f"yfinance: {e}")
                logger.debug("yfinance fallback also failed: %s", e)

        # Preserve source clocks separately from mixed-row acquisition time.
        source_times = {}
        values = []
        for field, observation in zip(
            ("spy_close", "vix_close", "dxy_close", "nasdaq_close"),
            (spy, vix, dxy, nasdaq),
        ):
            if observation is None:
                values.append(None)
                continue
            value, proof = observation
            values.append(value)
            source_times[field] = proof
        spy, vix, dxy, nasdaq = values
        now = datetime.now(timezone.utc).isoformat()
        # Classification is projected from usable inputs at read time as well.
        from aletheia.data.freshness import fresh_macro_row
        current = fresh_macro_row(dict(vix_close=vix,
            total_market_cap_change_24h_pct=total_cap_change, observed_at=now,
            source_times_json=json.dumps(source_times)))
        risk_regime = current["risk_regime"] if current else "unavailable"
        accepted_values = (
            btc_dominance,
            total_cap,
            total_cap_change,
            spy,
            vix,
            dxy,
            nasdaq,
            fng_value,
            fng_classification,
        )
        if all(value is None for value in accepted_values):
            errors.append("macro:no_usable_inputs")
            return {"snapshot": None, "errors": list(dict.fromkeys(errors)), "fred_source_batches": research}

        snapshot = MacroSnapshot(
            btc_dominance=btc_dominance,
            total_market_cap_usd=total_cap,
            total_market_cap_change_24h_pct=total_cap_change,
            spy_close=spy,
            vix_close=vix,
            dxy_close=dxy,
            nasdaq_close=nasdaq,
            risk_regime=risk_regime or "unavailable",
            fng_value=fng_value,
            fng_classification=fng_classification,
            observed_at=now,
            source_times_json=json.dumps(source_times, sort_keys=True),
        )

        return {"snapshot": snapshot, "errors": errors, "fred_source_batches": research}


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

def _fetch_one_tradfi_ticker(ticker_sym: str) -> tuple | None:
    """Fetch a single yfinance ticker. Intended to run in asyncio.to_thread."""
    try:
        import yfinance as yf
        t = yf.Ticker(ticker_sym)
        hist = t.history(period="2d")
        if not hist.empty:
            return round(float(hist["Close"].iloc[-1]), 2), {
                "source": "yfinance", "series_id": ticker_sym,
                "source_observed_at": hist.index[-1].date().isoformat(),
                "precision": "date",  # history() returns daily bars, not live quotes.
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            }
    except ImportError:
        logger.warning("yfinance not installed — tradfi macro fallback unavailable (FRED key required)")
    except Exception as e:
        logger.debug("yfinance tradfi ticker %s failed: %s", ticker_sym, e)
    return None

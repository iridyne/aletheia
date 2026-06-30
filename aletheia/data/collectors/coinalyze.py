"""Coinalyze collector — aggregated cross-exchange OI and liquidation data.

Data source: Coinalyze public/free-tier API (requires API key).
  https://coinalyze.net/

Endpoints used:
  GET /v1/open-interest        — current OI per symbol (aggregated, multi-exchange)
  GET /v1/liquidation-history  — recent liquidation events (aggregated)

Why Coinalyze instead of the previous dead paths:
- CoinGlass: fully paywalled (all endpoints return "Upgrade plan").
- CCXT fetchLiquidations: most exchanges don't implement it; Binance REST
  requires auth since 2025.
- Bitget OI history: never returns data (silently returns empty).

Coinalyze free tier provides aggregated OI (Binance + OKX + Bybit + Bitget)
and liquidation history — exactly the data the downstream consumers need.

Written to:
  - liquidation_events       (consumed by context_builder→liquidation_1h_usd)
  - open_interest_snapshots  (consumed by market_observer→crowding_score)

exchange tag: "coinalyze"

Key loading: follows the dual-path pattern (get_secret → os.environ fallback).
  config key: coinalyze_api_key
  env var:    COINALYZE_API_KEY

If no API key is configured the collector skips gracefully and returns
errors=["no_key"] — it never raises, consistent with all other optional
collectors (FRED, CoinGlass guard pattern).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import LiquidationEvent, OpenInterestSnapshot

logger = logging.getLogger("aletheia.data.collectors.coinalyze")

_BASE_URL = "https://api.coinalyze.net/v1"

# Coinalyze uses exchange-suffixed symbols like BTCUSDT_PERP.A (all exchanges
# aggregated). The ".A" suffix means "aggregated across all supported venues".
_AGG_SUFFIX = "_PERP.A"

# How far back to fetch liquidation history per collection tick.
_LIQUIDATION_WINDOW_HOURS = 4


class CoinalyzeCollector:
    """Collect aggregated OI and liquidation data from Coinalyze."""

    domain = "coinalyze"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Collect OI and liquidations for the given canonical symbols.

        Args:
            symbols: Canonical spot symbols, e.g. ["BTC/USDT", "ETH/USDT"].

        Returns:
            {
                "open_interest": [OpenInterestSnapshot, ...],
                "liquidations":  [LiquidationEvent, ...],
                "errors": [str, ...],
            }
        """
        api_key = _load_api_key()
        if not api_key:
            logger.debug("coinalyze: no API key configured — skipping collection")
            return {"open_interest": [], "liquidations": [], "errors": ["no_key"]}

        oi_list: list[OpenInterestSnapshot] = []
        liq_list: list[LiquidationEvent] = []
        errors: list[str] = []

        coinalyze_syms = [_to_coinalyze_symbol(s) for s in symbols if s]

        # --- Open Interest ---
        try:
            oi_list = await _fetch_open_interest(api_key, symbols, coinalyze_syms)
        except Exception as e:
            errors.append(f"oi: {e}")
            logger.warning("coinalyze OI fetch failed: %s", e)

        # --- Liquidations ---
        try:
            liq_list = await _fetch_liquidations(api_key, symbols, coinalyze_syms)
        except Exception as e:
            errors.append(f"liquidations: {e}")
            logger.warning("coinalyze liquidation fetch failed: %s", e)

        return {"open_interest": oi_list, "liquidations": liq_list, "errors": errors}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_api_key() -> str | None:
    """Load Coinalyze API key via dual-path: secrets store → env var fallback."""
    try:
        from aletheia.config.secrets import get_secret
        key = get_secret("coinalyze_api_key")
        if key:
            return key
    except Exception:
        pass
    return os.environ.get("COINALYZE_API_KEY") or None


def _to_coinalyze_symbol(canonical: str) -> str:
    """Convert canonical BTC/USDT → BTCUSDT_PERP.A (Coinalyze aggregated perp)."""
    return canonical.replace("/", "") + _AGG_SUFFIX


async def _fetch_open_interest(
    api_key: str, symbols: list[str], coinalyze_syms: list[str],
) -> list[OpenInterestSnapshot]:
    """Fetch current aggregated OI from Coinalyze /v1/open-interest."""
    results: list[OpenInterestSnapshot] = []
    syms_param = ",".join(coinalyze_syms[:20])  # cap to stay within rate limits

    async with httpx_client(timeout=15) as client:
        resp = await client.get(
            f"{_BASE_URL}/open-interest",
            params={"symbols": syms_param, "api_key": api_key},
        )
        if resp.status_code != 200:
            logger.warning("coinalyze OI non-200: %s %s", resp.status_code, resp.text[:200])
            return results

        data = resp.json()
        if not isinstance(data, list):
            logger.warning("coinalyze OI unexpected response shape: %r", type(data))
            return results

        now_iso = datetime.now(timezone.utc).isoformat()
        sym_map = dict(zip(coinalyze_syms[:20], symbols[:20]))

        for item in data:
            coinalyze_sym = item.get("symbol", "")
            canonical = sym_map.get(coinalyze_sym)
            if not canonical:
                continue

            oi_usd = item.get("open_interest_usd") or item.get("openInterestUsd")
            if oi_usd is None:
                # Try coin-denominated OI if USD not available
                oi_coin = item.get("open_interest") or item.get("openInterest")
                if oi_coin is None:
                    continue
                oi_usd = float(oi_coin)  # best-effort; no mark price available here
                oi_coin_val = float(oi_coin)
            else:
                oi_usd = float(oi_usd)
                oi_coin_val = None

            # Compute 1h change against last stored value
            oi_change_1h_pct: float | None = None
            try:
                from aletheia.storage.repositories import latest_open_interest
                prev = latest_open_interest(canonical)
                if prev and prev.get("open_interest_usd") and oi_usd:
                    prev_oi = float(prev["open_interest_usd"])
                    if prev_oi > 0:
                        oi_change_1h_pct = round((oi_usd - prev_oi) / prev_oi * 100, 2)
            except Exception:
                pass

            results.append(OpenInterestSnapshot(
                symbol=canonical,
                exchange="coinalyze",
                open_interest_usd=oi_usd,
                open_interest_coin=oi_coin_val,
                oi_change_1h_pct=oi_change_1h_pct,
                long_short_ratio=None,  # Coinalyze OI endpoint doesn't include LSR
                observed_at=now_iso,
            ))

    return results


async def _fetch_liquidations(
    api_key: str, symbols: list[str], coinalyze_syms: list[str],
) -> list[LiquidationEvent]:
    """Fetch recent aggregated liquidation events from Coinalyze."""
    results: list[LiquidationEvent] = []
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=_LIQUIDATION_WINDOW_HOURS)
    since_ts = int(since.timestamp())

    sym_map = dict(zip(coinalyze_syms[:20], symbols[:20]))

    async with httpx_client(timeout=15) as client:
        for coinalyze_sym, canonical in list(sym_map.items())[:10]:  # cap per rate limit
            try:
                resp = await client.get(
                    f"{_BASE_URL}/liquidation-history",
                    params={
                        "symbols": coinalyze_sym,
                        "from": since_ts,
                        "api_key": api_key,
                    },
                )
                if resp.status_code != 200:
                    logger.debug(
                        "coinalyze liq non-200 for %s: %s", canonical, resp.status_code
                    )
                    continue

                data = resp.json()
                if not isinstance(data, list):
                    continue

                for item in data:
                    # Coinalyze liquidation-history items contain:
                    #   t (timestamp ms), lsl (long liq USD), ssl (short liq USD)
                    ts_ms = item.get("t")
                    lsl = float(item.get("lsl") or 0)  # long side liquidated
                    ssl = float(item.get("ssl") or 0)  # short side liquidated

                    event_time = (
                        datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()
                        if ts_ms
                        else now.isoformat()
                    )

                    # Emit two events (one per side) when non-zero
                    if lsl > 0:
                        results.append(LiquidationEvent(
                            symbol=canonical,
                            exchange="coinalyze",
                            side="long",
                            quantity_usd=lsl,
                            price=0.0,  # aggregated; no single price
                            event_time=event_time,
                            observed_at=now.isoformat(),
                        ))
                    if ssl > 0:
                        results.append(LiquidationEvent(
                            symbol=canonical,
                            exchange="coinalyze",
                            side="short",
                            quantity_usd=ssl,
                            price=0.0,
                            event_time=event_time,
                            observed_at=now.isoformat(),
                        ))

            except Exception as e:
                logger.debug("coinalyze liq fetch failed for %s: %s", canonical, e)

    return results

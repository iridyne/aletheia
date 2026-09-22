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
import math
import os
import threading
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
_OI_DELTA_SANITY_PCT = 300.0
_OI_BATCH_SIZE = 20
_LIQUIDATION_BATCH_SIZE = 10
_LIQUIDATION_CURSORS: dict[tuple[str, ...], int] = {}
_LIQUIDATION_CURSOR_LOCK = threading.Lock()


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
        from extensions.learning.opportunity_information_policy import (
            ensure_opportunity_information_policy_epoch,
        )

        api_key = _load_api_key()
        if not api_key:
            logger.debug("coinalyze: no API key configured — skipping collection")
            return {"open_interest": [], "liquidations": [], "errors": ["no_key"]}
        ensure_opportunity_information_policy_epoch()

        oi_list: list[OpenInterestSnapshot] = []
        liq_list: list[LiquidationEvent] = []
        errors: list[str] = []

        pairs = _symbol_pairs(symbols)
        canonical_symbols = [canonical for canonical, _ in pairs]
        coinalyze_syms = [coinalyze for _, coinalyze in pairs]
        coverage = {
            "open_interest": _coverage(canonical_symbols),
            "liquidations": _coverage(canonical_symbols),
        }

        # --- Open Interest ---
        try:
            oi_list = await _fetch_open_interest(
                api_key,
                canonical_symbols,
                coinalyze_syms,
                coverage=coverage["open_interest"],
            )
        except Exception:
            errors.append("oi:request_failed")
            logger.warning("coinalyze OI fetch failed")

        # --- Liquidations ---
        try:
            liq_list = await _fetch_liquidations(
                api_key,
                canonical_symbols,
                coinalyze_syms,
                coverage=coverage["liquidations"],
            )
        except Exception:
            errors.append("liquidations:request_failed")
            logger.warning("coinalyze liquidation fetch failed")

        for family, state in coverage.items():
            if state["deferred"]:
                errors.append(f"{family}_deferred")
            if state["rejected"]:
                errors.append(f"{family}_rejected")

        return {
            "open_interest": oi_list,
            "liquidations": liq_list,
            "coverage": coverage,
            "errors": list(dict.fromkeys(errors)),
        }


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


def is_configured() -> bool:
    """Return credential presence without exposing the credential value."""
    return bool(_load_api_key())


def _to_coinalyze_symbol(canonical: str) -> str:
    """Convert canonical BTC/USDT → BTCUSDT_PERP.A (Coinalyze aggregated perp)."""
    return canonical.replace("/", "") + _AGG_SUFFIX


def _symbol_pairs(symbols: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in symbols:
        canonical = str(raw or "").strip().upper()
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        pairs.append((canonical, _to_coinalyze_symbol(canonical)))
    return pairs


def _coverage(symbols: list[str]) -> dict[str, list[str]]:
    return {
        "requested": list(symbols),
        "attempted": [],
        "accepted": [],
        "rejected": [],
        "deferred": [],
        "observed_empty": [],
    }


def _liquidation_attempt_pairs(
    pairs: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    if len(pairs) <= _LIQUIDATION_BATCH_SIZE:
        return pairs, []
    key = tuple(canonical for canonical, _ in pairs)
    with _LIQUIDATION_CURSOR_LOCK:
        start = _LIQUIDATION_CURSORS.get(key, 0) % len(pairs)
        attempted = [
            pairs[(start + offset) % len(pairs)]
            for offset in range(_LIQUIDATION_BATCH_SIZE)
        ]
        _LIQUIDATION_CURSORS[key] = (start + len(attempted)) % len(pairs)
    attempted_set = {canonical for canonical, _ in attempted}
    deferred = [pair for pair in pairs if pair[0] not in attempted_set]
    return attempted, deferred


async def _fetch_open_interest(
    api_key: str,
    symbols: list[str],
    coinalyze_syms: list[str],
    *,
    coverage: dict[str, list[str]] | None = None,
) -> list[OpenInterestSnapshot]:
    """Fetch current aggregated OI from Coinalyze /v1/open-interest."""
    results: list[OpenInterestSnapshot] = []

    async with httpx_client(timeout=15) as client:
        for start in range(0, len(coinalyze_syms), _OI_BATCH_SIZE):
            batch_syms = coinalyze_syms[start:start + _OI_BATCH_SIZE]
            batch_symbols = symbols[start:start + _OI_BATCH_SIZE]
            if not batch_syms:
                continue
            if coverage is not None:
                coverage["attempted"].extend(batch_symbols)
            resp = await client.get(
                f"{_BASE_URL}/open-interest",
                params={"symbols": ",".join(batch_syms), "api_key": api_key},
            )
            if resp.status_code != 200:
                logger.warning("coinalyze OI non-200: %s", resp.status_code)
                if coverage is not None:
                    coverage["rejected"].extend(batch_symbols)
                continue

            data = resp.json()
            if not isinstance(data, list):
                logger.warning("coinalyze OI unexpected response shape: %r", type(data))
                if coverage is not None:
                    coverage["rejected"].extend(batch_symbols)
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            sym_map = dict(zip(batch_syms, batch_symbols))

            for item in data:
                coinalyze_sym = item.get("symbol", "")
                canonical = sym_map.get(coinalyze_sym)
                if not canonical:
                    continue

                raw_oi_usd = item.get("open_interest_usd")
                if raw_oi_usd is None:
                    raw_oi_usd = item.get("openInterestUsd")
                if raw_oi_usd is None:
                    continue
                try:
                    oi_usd = float(raw_oi_usd)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(oi_usd) or oi_usd <= 0:
                    continue

                # Compute 1h change against a same-source 45-75 minute baseline.
                # Must pass exchange="coinalyze" so the lookup stays within Coinalyze
                # rows; without it _latest_deriv_where(None) excludes cross-venue tags
                # and returns the primary-exchange row, mixing heterogeneous OI pools.
                oi_change_1h_pct: float | None = None
                try:
                    from aletheia.storage.repositories import open_interest_near
                    prev = open_interest_near(
                        canonical,
                        "coinalyze",
                        reference_at=now_iso,
                    )
                    if prev and prev.get("open_interest_usd") is not None:
                        prev_oi = float(prev["open_interest_usd"])
                        if math.isfinite(prev_oi) and prev_oi > 0:
                            delta = round((oi_usd - prev_oi) / prev_oi * 100, 2)
                            if math.isfinite(delta) and abs(delta) <= _OI_DELTA_SANITY_PCT:
                                oi_change_1h_pct = delta
                except Exception:
                    pass

                if coverage is not None:
                    coverage["accepted"].append(canonical)
                results.append(OpenInterestSnapshot(
                    symbol=canonical,
                    exchange="coinalyze",
                    open_interest_usd=oi_usd,
                    open_interest_coin=None,
                    oi_change_1h_pct=oi_change_1h_pct,
                    long_short_ratio=None,  # Coinalyze OI endpoint doesn't include LSR
                    observed_at=now_iso,
                ))

    return results


async def _fetch_liquidations(
    api_key: str,
    symbols: list[str],
    coinalyze_syms: list[str],
    *,
    coverage: dict[str, list[str]] | None = None,
) -> list[LiquidationEvent]:
    """Fetch recent aggregated liquidation events from Coinalyze."""
    results: list[LiquidationEvent] = []
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=_LIQUIDATION_WINDOW_HOURS)
    since_ts = int(since.timestamp())

    attempt_pairs, deferred_pairs = _liquidation_attempt_pairs(
        list(zip(symbols, coinalyze_syms))
    )
    if coverage is not None:
        coverage["attempted"].extend(canonical for canonical, _ in attempt_pairs)
        coverage["deferred"].extend(canonical for canonical, _ in deferred_pairs)

    async with httpx_client(timeout=15) as client:
        for canonical, coinalyze_sym in attempt_pairs:
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
                    if coverage is not None:
                        coverage["rejected"].append(canonical)
                    continue

                data = resp.json()
                if not isinstance(data, list):
                    if coverage is not None:
                        coverage["rejected"].append(canonical)
                    continue

                accepted_before = len(results)
                for item in data:
                    # Coinalyze liquidation-history items contain:
                    #   t (timestamp ms), lsl (long liq USD), ssl (short liq USD)
                    if not isinstance(item, dict):
                        continue
                    try:
                        ts_ms = float(item.get("t"))
                        lsl = float(item.get("lsl") or 0)
                        ssl = float(item.get("ssl") or 0)
                    except (TypeError, ValueError):
                        continue
                    if not all(math.isfinite(value) for value in (ts_ms, lsl, ssl)):
                        continue
                    if ts_ms <= 0 or lsl < 0 or ssl < 0:
                        continue
                    try:
                        event_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                    except (OverflowError, OSError, ValueError):
                        continue
                    if event_dt < since or event_dt > now + timedelta(minutes=5):
                        continue
                    event_time = event_dt.isoformat()

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
                if coverage is not None:
                    if len(results) > accepted_before:
                        coverage["accepted"].append(canonical)
                    else:
                        coverage["observed_empty"].append(canonical)

            except Exception:
                logger.debug("coinalyze liq fetch failed for %s", canonical)
                if coverage is not None:
                    coverage["rejected"].append(canonical)

    return results

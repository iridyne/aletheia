"""Options market structure collector — Deribit public options chain GEX factors.

GEX sign convention v1 (retail_call_minus_put):
    net_gex = Σ[call_gamma * call_oi - put_gamma * put_oi] * spot² * 0.01 (USD/1%move)

    Dealer model assumption: dealers are short calls (sold to retail bulls) and
    long puts (bought from retail hedgers). Both assumptions are approximations —
    actual dealer inventory is not observable from public data alone.

    positive net_gex → call OI heavy → conventionally "positive gamma" (stabilizing)
    negative net_gex → put OI heavy → conventionally "negative gamma" (amplifying)

Data source: Deribit public REST API — no authentication required.
Instruments: BTC and ETH vanilla options only.
Rate budget: two batch calls per tick (one per currency).

These factors are supplementary market context only. Do not promote to hard
filters, Risk, sizing, or execution without Shadow/counterfactual validation.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import OptionsMarketStructureSnapshot

logger = logging.getLogger("aletheia.data.collectors.options_structure")

_DERIBIT_BASE = "https://www.deribit.com/api/v2/public"
_CURRENCIES = ("BTC", "ETH")
_MIN_VALID_ENTRIES = 5  # fail closed below this threshold


class OptionsStructureCollector:
    domain = "options_structure"

    async def collect(self) -> dict[str, Any]:
        snapshots: list[OptionsMarketStructureSnapshot] = []
        errors: list[str] = []

        for currency in _CURRENCIES:
            try:
                snap = await _collect_currency(currency)
                if snap:
                    snapshots.append(snap)
            except Exception as exc:
                errors.append(f"{currency}:{exc}")
                logger.debug("options_structure: %s failed: %s", currency, exc)

        return {"snapshots": snapshots, "errors": errors}


async def _collect_currency(currency: str) -> OptionsMarketStructureSnapshot | None:
    observed_at = datetime.now(timezone.utc).isoformat()

    async with httpx_client(timeout=15) as client:
        resp = await client.get(
            f"{_DERIBIT_BASE}/get_book_summary_by_currency",
            params={"currency": currency, "kind": "option"},
        )
        resp.raise_for_status()
        data = resp.json()

    items = data.get("result") or []
    if not items:
        return None

    underlying_price: float | None = None
    entries: list[dict] = []

    for item in items:
        name = item.get("instrument_name", "")
        oi = float(item.get("open_interest") or 0.0)
        # Deribit returns mid_iv as a percentage (e.g. 65.0 means 65% annual vol)
        mid_iv = float(item.get("mid_iv") or item.get("mark_iv") or 0.0)
        spot = float(item.get("underlying_price") or 0.0)

        if spot and underlying_price is None:
            underlying_price = spot
        if not spot:
            spot = underlying_price or 0.0

        parsed = _parse_instrument_name(name)
        if parsed is None:
            continue
        strike, option_type, expiry_dt = parsed

        if spot <= 0 or mid_iv <= 0 or strike <= 0:
            continue

        T = (expiry_dt - datetime.now(timezone.utc)).total_seconds() / (365.25 * 86400)
        if T <= 1 / 365:  # skip same-day expiries
            continue

        sigma = mid_iv / 100.0
        gamma = _bsm_gamma(spot, strike, T, sigma)
        if gamma is None:
            continue

        entries.append({
            "strike": strike,
            "type": option_type,
            "oi": oi,
            "gamma": gamma,
        })

    if len(entries) < _MIN_VALID_ENTRIES:
        logger.debug(
            "options_structure: %s has only %d valid entries — fail closed",
            currency, len(entries),
        )
        return None

    spot = underlying_price or entries[0].get("spot", 0.0)
    if not spot:
        return None

    return _build_snapshot(currency, spot, entries, observed_at)


def _parse_instrument_name(name: str) -> tuple[float, str, datetime] | None:
    """Parse 'BTC-30MAY25-100000-C' → (strike, option_type, expiry_utc)."""
    try:
        parts = name.split("-")
        if len(parts) != 4:
            return None
        _ccy, expiry_str, strike_str, option_type = parts
        if option_type not in ("C", "P"):
            return None
        expiry_dt = datetime.strptime(expiry_str, "%d%b%y").replace(
            hour=8, minute=0, second=0, tzinfo=timezone.utc
        )
        return float(strike_str), option_type, expiry_dt
    except Exception:
        return None


def _bsm_gamma(S: float, K: float, T: float, sigma: float) -> float | None:
    """Black-Scholes gamma (r=0; adequate for crypto short-dated options)."""
    try:
        if sigma <= 0 or T <= 0 or S <= 0 or K <= 0:
            return None
        d1 = (math.log(S / K) + 0.5 * sigma**2 * T) / (sigma * math.sqrt(T))
        nd1 = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
        return nd1 / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError):
        return None


def _build_snapshot(
    currency: str,
    spot: float,
    entries: list[dict],
    observed_at: str,
) -> OptionsMarketStructureSnapshot:
    call_gex_by_strike: dict[float, float] = {}
    put_gex_by_strike: dict[float, float] = {}
    total_call_oi = 0.0
    total_put_oi = 0.0

    for e in entries:
        s = e["strike"]
        # USD exposure per 1% spot move: gamma * OI_contracts * spot² * 0.01
        # Deribit BTC/ETH options: 1 contract = 1 BTC or 1 ETH
        gex = e["gamma"] * e["oi"] * spot * spot * 0.01
        if e["type"] == "C":
            call_gex_by_strike[s] = call_gex_by_strike.get(s, 0.0) + gex
            total_call_oi += e["oi"]
        else:
            put_gex_by_strike[s] = put_gex_by_strike.get(s, 0.0) + gex
            total_put_oi += e["oi"]

    all_strikes = set(call_gex_by_strike) | set(put_gex_by_strike)
    net_gex = sum(
        call_gex_by_strike.get(s, 0.0) - put_gex_by_strike.get(s, 0.0)
        for s in all_strikes
    )

    call_wall = max(call_gex_by_strike, key=call_gex_by_strike.__getitem__) if call_gex_by_strike else None
    put_wall = max(put_gex_by_strike, key=put_gex_by_strike.__getitem__) if put_gex_by_strike else None
    nearest_wall = _nearest_wall(spot, call_wall, put_wall)
    nearest_wall_dist = abs(nearest_wall - spot) / spot * 100.0 if nearest_wall else None

    # Threshold for regime: 1M USD/1%move is a reasonable noise floor
    _REGIME_THRESHOLD = 1_000_000.0
    if abs(net_gex) < _REGIME_THRESHOLD:
        gamma_regime = "neutral"
    elif net_gex > 0:
        gamma_regime = "positive"
    else:
        gamma_regime = "negative"

    return OptionsMarketStructureSnapshot(
        symbol=currency,
        underlying_price=spot,
        net_gex_estimate=net_gex,
        gamma_regime=gamma_regime,
        gamma_flip_estimate=_gamma_flip(call_gex_by_strike, put_gex_by_strike),
        call_gamma_wall=float(call_wall) if call_wall is not None else None,
        put_gamma_wall=float(put_wall) if put_wall is not None else None,
        nearest_gamma_wall_distance=nearest_wall_dist,
        options_open_interest=total_call_oi + total_put_oi,
        call_oi=total_call_oi,
        put_oi=total_put_oi,
        valid_strike_count=len(all_strikes),
        observed_at=observed_at,
    )


def _nearest_wall(spot: float, call_wall: float | None, put_wall: float | None) -> float | None:
    if call_wall is not None and put_wall is not None:
        return min(call_wall, put_wall, key=lambda x: abs(x - spot))
    return call_wall if call_wall is not None else put_wall


def _gamma_flip(
    call_gex: dict[float, float],
    put_gex: dict[float, float],
) -> float | None:
    """Estimate the spot level where cumulative net GEX (by strike) crosses zero."""
    strikes = sorted(set(call_gex) | set(put_gex))
    if len(strikes) < 2:
        return None

    # Build cumulative net GEX scanning from lowest to highest strike
    cumulative: list[tuple[float, float]] = []
    running = 0.0
    for s in strikes:
        running += call_gex.get(s, 0.0) - put_gex.get(s, 0.0)
        cumulative.append((s, running))

    # Find sign change; linearly interpolate the crossing
    for i in range(len(cumulative) - 1):
        s0, g0 = cumulative[i]
        s1, g1 = cumulative[i + 1]
        if (g0 <= 0 < g1) or (g1 <= 0 < g0):
            if g0 == g1:
                return (s0 + s1) / 2.0
            return s0 + (s1 - s0) * (-g0) / (g1 - g0)

    return None

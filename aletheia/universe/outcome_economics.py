"""Pure economics for fixed-horizon Opportunity outcomes.

All percentage values use percentage points: ``0.25`` means ``0.25%``.
Positive ``estimated_funding_pct`` is a cost; negative is a credit.
"""

from __future__ import annotations

from typing import Any


_FUNDING_INTERVAL_SEC = 8 * 3600
_PERP_MARKETS = frozenset({"perp", "futures", "swap", "usdt_futures"})
_VALID_DIRECTIONS = frozenset({"long", "short"})
_MIN_EDGE_FLOOR_PCT = 0.05
COST_MODEL_VERSION = "v1"


def estimate_outcome_economics(
    *,
    entry_price: float,
    exit_price: float,
    direction: str | None,
    market_type: str,
    hold_seconds: float,
    entry_spread_bps: float | None,
    fallback_spread_bps: float,
    funding_rate_at_entry: float | None,
    execution_fee_pct: float,
    slippage_bps: float,
    range_high: float | None = None,
    range_low: float | None = None,
    price_quality_ok: bool = True,
) -> dict[str, Any]:
    """Return gross, directional, path and estimated net outcome metrics.

    Net is deliberately unavailable when the direction is unknown, a derivative
    has no point-in-time funding rate, or the settlement price is too far from the
    requested horizon. Unknown economic inputs must not become optimistic zeros.
    """
    entry = float(entry_price)
    exit_ = float(exit_price)
    if entry <= 0 or exit_ <= 0:
        raise ValueError("entry_price and exit_price must be positive")

    side = str(direction or "").lower()
    market = str(market_type or "spot").lower()
    hold = max(0.0, float(hold_seconds or 0.0))
    gross = (exit_ - entry) / entry * 100.0
    directional = _directional(gross, side)

    fee_pct = max(0.0, float(execution_fee_pct or 0.0)) * 2.0
    spread_bps = (
        max(0.0, float(entry_spread_bps))
        if entry_spread_bps is not None
        else max(0.0, float(fallback_spread_bps or 0.0))
    )
    slip_bps = max(0.0, float(slippage_bps or 0.0))
    spread_slippage_pct = (spread_bps + 2.0 * slip_bps) / 100.0
    min_edge_pct = max(_MIN_EDGE_FLOOR_PCT, slip_bps / 100.0)

    funding_pct: float | None
    if market in _PERP_MARKETS:
        if side not in _VALID_DIRECTIONS or funding_rate_at_entry is None:
            funding_pct = None
        else:
            # Positive venue funding: longs pay, shorts receive.
            direction_sign = 1.0 if side == "long" else -1.0
            funding_pct = (
                float(funding_rate_at_entry)
                * 100.0
                * (hold / _FUNDING_INTERVAL_SEC)
                * direction_sign
            )
    else:
        funding_pct = 0.0

    net: float | None = None
    if (
        price_quality_ok
        and directional is not None
        and funding_pct is not None
    ):
        net = directional - fee_pct - spread_slippage_pct - funding_pct

    mfe, mae = _path_excursions(
        entry=entry,
        direction=side,
        range_high=range_high,
        range_low=range_low,
    )
    return {
        "gross_return_pct": gross,
        "directional_return_pct": directional,
        "estimated_fee_pct": fee_pct,
        "estimated_spread_slippage_pct": spread_slippage_pct,
        "estimated_funding_pct": funding_pct,
        "net_return_pct": net,
        "min_tradable_edge_pct": min_edge_pct,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "cost_model_version": COST_MODEL_VERSION,
    }


def _directional(gross: float, direction: str) -> float | None:
    if direction == "long":
        return gross
    if direction == "short":
        return -gross
    return None


def _path_excursions(
    *,
    entry: float,
    direction: str,
    range_high: float | None,
    range_low: float | None,
) -> tuple[float | None, float | None]:
    if direction not in _VALID_DIRECTIONS or range_high is None or range_low is None:
        return None, None
    high = float(range_high)
    low = float(range_low)
    if high <= 0 or low <= 0:
        return None, None
    if direction == "long":
        favorable = (high - entry) / entry * 100.0
        adverse = (low - entry) / entry * 100.0
    else:
        favorable = (entry - low) / entry * 100.0
        adverse = (entry - high) / entry * 100.0
    return max(0.0, favorable), min(0.0, adverse)

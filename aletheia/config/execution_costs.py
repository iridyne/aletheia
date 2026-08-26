"""Canonical bounds and runtime validation for local execution costs."""

from __future__ import annotations

import math


EXECUTION_FEE_PCT_BOUNDS = (0.0, 5.0)
SLIPPAGE_BPS_BOUNDS = (0.0, 200.0)
FALLBACK_SPREAD_BPS_BOUNDS = (1.0, 200.0)


def validate_execution_costs(
    *,
    execution_fee_pct: float,
    slippage_bps: float,
    fallback_spread_bps: float | None = None,
) -> tuple[float, float, float | None]:
    """Return finite in-domain costs or fail before a decision-grade write."""
    fee = _bounded(execution_fee_pct, "execution_fee_pct", EXECUTION_FEE_PCT_BOUNDS)
    slippage = _bounded(slippage_bps, "slippage_bps", SLIPPAGE_BPS_BOUNDS)
    fallback = (
        None
        if fallback_spread_bps is None
        else _bounded(
            fallback_spread_bps,
            "fallback_spread_bps",
            FALLBACK_SPREAD_BPS_BOUNDS,
        )
    )
    return fee, slippage, fallback


def _bounded(value: float, name: str, bounds: tuple[float, float]) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or not bounds[0] <= result <= bounds[1]:
        raise ValueError(f"{name} must be between {bounds[0]} and {bounds[1]}")
    return result

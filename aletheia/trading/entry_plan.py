"""Deterministic entry-plan plausibility shared by Operator and Risk."""

from __future__ import annotations

import math


ENTRY_PLAN_MIN_RISK_REWARD = 1.0
ENTRY_PLAN_MAX_RISK_REWARD = 10.0

OPERATOR_RISK_REWARD_OUTSIDE_ENVELOPE = (
    "invalid_operator_entry_plan_risk_reward:outside_envelope"
)
RISK_REWARD_OUTSIDE_ENVELOPE = "entry_plan_risk_reward_outside_envelope"
STOP_DISTANCE_BELOW_VOL_TARGET = "stop_distance_below_vol_target"
# Published-sideways demo floor: 1.5% vol_target sits inside a >2% daily range.
SIDEWAYS_STOP_MULT = 2.0


def stop_distance_pct(
    entry_price: float | None, stop_price: float | None
) -> float | None:
    """Absolute stop distance as a percent of entry. ``None`` if undefined."""
    try:
        entry = float(entry_price)
        stop = float(stop_price)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (entry, stop)) or entry <= 0:
        return None
    distance = abs(entry - stop)
    if distance <= 0:
        return None
    return (distance / entry) * 100.0


def min_stop_distance_pct(*, execution_mode: str | None, cfg) -> float | None:
    """Exchange-demo floor: stop must be at least ``trading.vol_target_pct``.

    That field is the existing ATR vol-target percent used for sizing, not a
    new magic number. In a published sideways regime the floor is
    ``vol_target_pct * SIDEWAYS_STOP_MULT`` (never tighter than the base
    floor). Live and ``paper_internal`` stay unchanged. ``0`` disables.
    Unpublished regime is fail-open to the base floor.
    """
    from aletheia.risk.demo_entry_quality import (
        applies_to_execution_mode,
        load_published_regime,
    )

    if not applies_to_execution_mode(execution_mode):
        return None
    try:
        floor = float(getattr(getattr(cfg, "trading", None), "vol_target_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(floor) or floor <= 0:
        return None
    try:
        state, _confidence = load_published_regime()
    except Exception:
        state = None
    if str(state or "").lower() == "sideways":
        widened = floor * SIDEWAYS_STOP_MULT
        if math.isfinite(widened) and widened > floor:
            floor = widened
    return floor


def stop_distance_below_min_floor(
    entry_price: float | None,
    stop_price: float | None,
    *,
    execution_mode: str | None,
    cfg,
) -> str | None:
    """Return a violation token when the stop is tighter than the demo floor."""
    floor = min_stop_distance_pct(execution_mode=execution_mode, cfg=cfg)
    distance = stop_distance_pct(entry_price, stop_price)
    if floor is None or distance is None:
        return None
    if distance + 1e-12 < floor:
        return f"{STOP_DISTANCE_BELOW_VOL_TARGET}:{distance:.4f}<{floor:.4f}"
    return None


def calculate_entry_plan_risk_reward(
    entry_price: float | None,
    stop_price: float | None,
    take_profit: float | None,
) -> float | None:
    """Return the contract's rounded reward:risk ratio, or ``None`` if undefined."""
    try:
        entry = float(entry_price)
        stop = float(stop_price)
        target = float(take_profit)
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in (entry, stop, target)):
        return None
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return None
    return round(abs(target - entry) / risk_per_unit, 2)


def entry_plan_risk_reward_is_plausible(risk_reward: float | None) -> bool:
    """Whether a calculated ratio is inside the fail-closed inclusive envelope."""
    if risk_reward is None:
        return False
    try:
        value = float(risk_reward)
    except (TypeError, ValueError):
        return False
    return (
        math.isfinite(value)
        and ENTRY_PLAN_MIN_RISK_REWARD <= value <= ENTRY_PLAN_MAX_RISK_REWARD
    )

"""Deterministic entry-plan plausibility shared by Operator and Risk."""

from __future__ import annotations

import math


ENTRY_PLAN_MIN_RISK_REWARD = 1.0
ENTRY_PLAN_MAX_RISK_REWARD = 10.0

OPERATOR_RISK_REWARD_OUTSIDE_ENVELOPE = (
    "invalid_operator_entry_plan_risk_reward:outside_envelope"
)
RISK_REWARD_OUTSIDE_ENVELOPE = "entry_plan_risk_reward_outside_envelope"


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

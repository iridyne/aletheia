"""Deterministic entry-plan plausibility shared by Operator and Risk."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ENTRY_PLAN_MIN_RISK_REWARD = 1.0
ENTRY_PLAN_MAX_RISK_REWARD = 10.0

OPERATOR_RISK_REWARD_OUTSIDE_ENVELOPE = "invalid_operator_entry_plan_risk_reward:outside_envelope"
RISK_REWARD_OUTSIDE_ENVELOPE = "entry_plan_risk_reward_outside_envelope"
STOP_DISTANCE_BELOW_VOL_TARGET = "stop_distance_below_vol_target"
# Published-sideways demo floor: 1.5% vol_target sits inside a >2% daily range.
SIDEWAYS_STOP_MULT = 2.0

ENTRY_PLAN_VERSION = "entry-plan/v1"
PLANNING_IDENTITY_VERSION = "candidate-planning/v1"
ENTRY_PLAN_EXECUTION_MODES = ("RESTING_MAKER", "TRIGGERED")
ENTRY_PLAN_STATUSES = (
    "waiting",
    "risk_reserved",
    "triggered",
    "ordered",
    "partially_filled",
    "filled",
    "invalidated",
    "expired",
    "cancelled",
    "failed",
)

ENTRY_PLAN_TRANSITIONS = {
    "waiting": frozenset({"risk_reserved", "triggered", "invalidated", "expired", "cancelled"}),
    "triggered": frozenset({"risk_reserved", "invalidated", "expired", "cancelled", "failed"}),
    "risk_reserved": frozenset({"ordered", "invalidated", "expired", "cancelled", "failed"}),
    "ordered": frozenset({"partially_filled", "filled", "cancelled", "failed"}),
    "partially_filled": frozenset({"filled", "cancelled", "failed"}),
    "filled": frozenset(),
    "invalidated": frozenset(),
    "expired": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}


def _new_id() -> str:
    return uuid4().hex[:12]


def stop_distance_pct(entry_price: float | None, stop_price: float | None) -> float | None:
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
    return math.isfinite(value) and ENTRY_PLAN_MIN_RISK_REWARD <= value <= ENTRY_PLAN_MAX_RISK_REWARD


def _positive_finite(value: float, field: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field}_must_be_positive_finite")
    return number


class EntryPlan(BaseModel):
    """Immutable approved entry facts for one Candidate planning generation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str = Field(default_factory=_new_id)
    version: Literal["entry-plan/v1"] = ENTRY_PLAN_VERSION
    planning_generation_id: str
    candidate_id: str
    candidate_family: str
    venue: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    market_type: Literal["spot", "perp"]
    direction: Literal["long", "short"]
    thesis: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()
    scenario: str = Field(min_length=1)
    execution_mode: Literal["RESTING_MAKER", "TRIGGERED"]
    trigger_spec_id: str | None = None
    trigger_spec_version: str | None = None
    trigger_params: dict = Field(default_factory=dict)
    trigger_data_requirements: tuple[str, ...] = ()
    trigger_freshness_ms: int | None = Field(default=None, gt=0)
    entry_zone: tuple[float, float]
    invalidation_conditions: tuple[str, ...]
    invalidation_price: float
    stop_price: float
    target_price: float
    expected_horizon_hours: float = Field(gt=0, le=48)
    approved_at: str
    expires_at: str
    order_tactic: Literal["POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"]
    maker_required: bool
    time_in_force: Literal["POST_ONLY", "GTC", "IOC"]
    limit_price: float | None = None
    price_rule: Literal["EXACT_LIMIT", "PASSIVE_BBO", "OPPOSING_BBO"]
    bbo_reference: Literal["bid", "ask"] | None = None
    max_wait_sec: int = Field(ge=0)
    cancel_replace_policy: Literal["CANCEL_ONLY", "BOUNDED_REQUOTE"]
    max_requotes: int = Field(ge=0)
    partial_fill_policy: Literal["KEEP_PARTIAL_CANCEL_REMAINDER", "KEEP_RESTING"]
    taker_fallback_allowed: bool = False
    max_slippage_bps: float | None = None
    worst_price: float | None = None
    max_notional: float = Field(gt=0)
    max_risk_pct: float = Field(gt=0)
    max_leverage: int = Field(ge=1)
    required_permission: str = Field(min_length=1)
    execution_account_mode: str = Field(min_length=1)
    risk_policy_id: str = Field(min_length=1)
    risk_policy_revision: int = Field(ge=1)
    risk_policy_hash: str = Field(min_length=1)
    behavior_version: str = Field(min_length=1)
    runtime_session_id: str | None = None
    producer_commit: str | None = None
    behavior_fingerprint: str | None = None

    @field_validator(
        "stop_price",
        "target_price",
        "invalidation_price",
        mode="after",
    )
    @classmethod
    def _prices_positive(cls, value: float, info) -> float:
        return _positive_finite(value, info.field_name)

    @model_validator(mode="after")
    def _contract_is_coherent(self) -> EntryPlan:
        low, high = self.entry_zone
        if low <= 0 or high < low:
            raise ValueError("entry_plan_entry_zone_invalid")
        entry = self.limit_price if self.limit_price is not None else (low + high) / 2
        if self.direction == "long":
            if not (self.stop_price < entry < self.target_price):
                raise ValueError("long_entry_plan_geometry_invalid")
            if self.invalidation_price > high:
                raise ValueError("long_invalidation_above_entry")
        else:
            if not (self.target_price < entry < self.stop_price):
                raise ValueError("short_entry_plan_geometry_invalid")
            if self.invalidation_price < low:
                raise ValueError("short_invalidation_below_entry")
        computed = calculate_entry_plan_risk_reward(entry, self.stop_price, self.target_price)
        if computed is None or not entry_plan_risk_reward_is_plausible(computed):
            raise ValueError(RISK_REWARD_OUTSIDE_ENVELOPE)
        approved = _aware_instant(self.approved_at, "approved_at")
        expires = _aware_instant(self.expires_at, "expires_at")
        if expires <= approved:
            raise ValueError("entry_plan_expiry_invalid")
        if self.execution_mode == "TRIGGERED":
            if not self.trigger_spec_id or not self.trigger_spec_version:
                raise ValueError("triggered_entry_plan_requires_registered_trigger_spec")
            from aletheia.trading.trigger_specs import require_registered_trigger_spec

            contract, params = require_registered_trigger_spec(
                self.trigger_spec_id,
                self.trigger_spec_version,
                self.trigger_params,
            )
            if tuple(self.trigger_data_requirements) != contract.data_requirements:
                raise ValueError("trigger_data_requirements_mismatch")
            if self.trigger_freshness_ms != contract.freshness_ms:
                raise ValueError("trigger_freshness_mismatch")
            object.__setattr__(self, "trigger_params", params)
        elif self.trigger_spec_id is not None or self.trigger_spec_version is not None:
            raise ValueError("resting_maker_cannot_require_trigger")
        if self.order_tactic == "POST_ONLY_LIMIT":
            if not self.maker_required or self.time_in_force != "POST_ONLY":
                raise ValueError("maker_tactic_requires_post_only")
            if self.limit_price is None:
                raise ValueError("maker_tactic_requires_exact_limit_price")
        else:
            if self.execution_mode != "TRIGGERED":
                raise ValueError("urgent_taker_requires_triggered_plan")
            if self.maker_required or self.time_in_force != "IOC":
                raise ValueError("urgent_taker_requires_ioc")
            if self.price_rule != "OPPOSING_BBO" or self.bbo_reference is None:
                raise ValueError("urgent_taker_requires_opposing_bbo")
            if self.bbo_reference != ("ask" if self.direction == "long" else "bid"):
                raise ValueError("urgent_taker_bbo_side_mismatch")
            if self.max_slippage_bps is None or self.max_slippage_bps <= 0:
                raise ValueError("urgent_taker_requires_slippage_envelope")
        if self.taker_fallback_allowed:
            if self.max_slippage_bps is None or self.worst_price is None:
                raise ValueError("taker_fallback_requires_bounded_price")
        elif self.worst_price is not None:
            raise ValueError("worst_price_requires_taker_authority")
        return self


def _aware_instant(raw: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field}_timezone_required")
    return parsed


def candidate_planning_identity(candidate: dict) -> tuple[str, str]:
    """Return stable ownership and material hashes without snapshot/score/price churn."""

    ownership = {
        "venue": str(candidate.get("venue") or "").lower(),
        "symbol": str(candidate.get("symbol") or "").upper(),
        "market_type": str(candidate.get("market_type") or ""),
        "family": str(candidate.get("scanner_family") or ""),
        "direction": str(candidate.get("scanner_direction") or ""),
    }
    if not all(ownership.values()):
        raise ValueError("candidate_planning_identity_incomplete")
    risk_flags = candidate.get("risk_flags") or []
    if isinstance(risk_flags, str):
        try:
            risk_flags = json.loads(risk_flags)
        except json.JSONDecodeError:
            risk_flags = [risk_flags]
    material = {
        "identity_version": PLANNING_IDENTITY_VERSION,
        **ownership,
        "regime": str(candidate.get("regime_tag") or "unknown"),
        "execution_eligible": bool(candidate.get("execution_eligible", True)),
        "risk_flags": sorted(str(value) for value in risk_flags),
    }
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    return (
        hashlib.sha256(canonical(ownership).encode()).hexdigest(),
        hashlib.sha256(canonical(material).encode()).hexdigest(),
    )


def entry_plan_payload(plan: EntryPlan) -> tuple[str, str]:
    payload = json.dumps(plan.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def entry_plan_transition_allowed(current: str, target: str) -> bool:
    return target in ENTRY_PLAN_TRANSITIONS.get(current, ())


def opposing_bbo_price(
    *,
    direction: str,
    bid: float,
    ask: float,
    max_slippage_bps: float,
    worst_price: float | None = None,
) -> float:
    """Urgent Taker: long lifts the ask, short hits the bid, envelope fail-closed."""
    bid = _positive_finite(bid, "bid")
    ask = _positive_finite(ask, "ask")
    if ask < bid:
        raise ValueError("bbo_crossed")
    if direction == "long":
        price = ask
        mid = (bid + ask) / 2.0
        slip_bps = (price - mid) / mid * 10_000.0
    elif direction == "short":
        price = bid
        mid = (bid + ask) / 2.0
        slip_bps = (mid - price) / mid * 10_000.0
    else:
        raise ValueError("direction_invalid")
    if slip_bps - 1e-12 > float(max_slippage_bps):
        raise ValueError("bbo_slippage_envelope_exceeded")
    if worst_price is not None and (
        (direction == "long" and price > worst_price) or (direction == "short" and price < worst_price)
    ):
        raise ValueError("bbo_worst_price_exceeded")
    return price

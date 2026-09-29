"""Deterministic leverage resolution: target exposure -> minimal safe financing.

The third layer of the canonical money path (#3729 §7). ``SizingResolution``
already decided how much notional this trade should target; this module only
answers how to finance that target -- the *lowest* leverage that can realize
the *largest* achievable share of it, never more. Leverage can shrink the
realized exposure below target when no higher leverage is provably safe; it
can never enlarge the target.

Cross margin has no qualified V1 safety model (#1451): the isolated
liquidation estimate must never stand in for it, so cross is always a typed
refusal (``CROSS_MARGIN_SAFETY_UNAVAILABLE``), not a downgraded ``resolved``.

The ``envelope`` import is inside the function (not module top-level) so a
caller/test can pass any object shaped like ``VenueMarginEnvelope`` -- with
``tiers``, ``margin_mode``, ``leverage_step``, ``facts_fingerprint``, and
``tier_for_notional()`` -- without this module importing
``aletheia.trading.venue_margin`` at import time.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from aletheia.trading.entry_sizing import SizingResolution

if TYPE_CHECKING:
    from aletheia.trading.venue_margin import VenueMarginEnvelope

# Stable failure reasons (Issue #3729 §12). Never carry exchange exception text.
REASON_VENUE_MARGIN_TIERS_UNAVAILABLE = "VENUE_MARGIN_TIERS_UNAVAILABLE"
REASON_MARGIN_MODE_UNAVAILABLE = "MARGIN_MODE_UNAVAILABLE"
REASON_CROSS_MARGIN_SAFETY_UNAVAILABLE = "CROSS_MARGIN_SAFETY_UNAVAILABLE"
REASON_NO_FEASIBLE_FINANCING = "NO_FEASIBLE_FINANCING"

# Binding-constraint tokens: why execution_notional stopped short of target.
BINDING_MARGIN_BUDGET = "margin_budget"
BINDING_TIER_NOTIONAL_CAP = "tier_notional_cap"

_KNOWN_MARGIN_MODES = ("isolated", "cross")


class LeverageResolution(BaseModel):
    """Persisted output of the deterministic leverage resolver (#3729 §7.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["resolved", "unavailable", "no_feasible_financing", "not_applicable"]
    reason: str | None = None
    policy_leverage_cap: int
    venue_absolute_cap: float
    safe_leverage_cap: float
    required_leverage_for_target: float | None = None
    execution_leverage: int | None = None
    target_notional_usdt: float | None = None
    execution_notional_usdt: float | None = None
    target_fill_ratio: float | None = None
    margin_budget_usdt: float | None = None
    projected_position_notional_usdt: float | None = None
    tier: int | None = None
    maintenance_margin_rate: float | None = None
    margin_mode: str
    liquidation_buffer_pct: float
    binding_constraint: str | None = None
    facts_fingerprint: str


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(
    envelope_fingerprint: str | None,
    sizing_fingerprint: str,
    policy_leverage_cap: int,
    liquidation_buffer_pct: float,
    tag: str,
) -> str:
    payload = {
        "envelope_facts_fingerprint": envelope_fingerprint,
        "sizing_facts_fingerprint": sizing_fingerprint,
        "policy_leverage_cap": int(policy_leverage_cap),
        "liquidation_buffer_pct": round(float(liquidation_buffer_pct), 10),
        "tag": tag,
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _safe_cap(maintenance_margin_rate: float, liquidation_buffer_pct: float) -> float:
    """Exact highest leverage the buffer allows against one tier's MMR."""
    denominator = liquidation_buffer_pct / 100.0 + maintenance_margin_rate
    if denominator <= 0:
        return math.inf
    return 1.0 / denominator


def resolve_execution_financing(
    *,
    sizing: SizingResolution,
    envelope: "VenueMarginEnvelope | None" = None,
    policy_leverage_cap: int,
    liquidation_buffer_pct: float,
    existing_position_notional_usdt: float = 0.0,
    unresolved_order_notional_usdt: float = 0.0,
    risk_reservation_notional_usdt: float = 0.0,
    spot: bool = False,
) -> LeverageResolution:
    """The single financing authority shared by Compiler and Risk (#3729 §7).

    ``spot=True`` returns ``not_applicable``/1x without reading ``envelope``
    at all -- spot has no tier facts. For a perp, ``envelope is None`` is a
    typed ``unavailable`` (tiers unavailable), never a crash and never an
    isolated estimate.

    Otherwise enumerates admissible integer leverages low to high and keeps
    the one that finances the most notional without ever exceeding
    ``sizing.target_notional_usdt``, breaking ties toward the lowest
    leverage. Each candidate's tier (and therefore its ``max_leverage`` and
    MMR) is resolved from its own *projected* position notional -- existing
    exposure plus that candidate -- not from a tier picked before capacity
    capping, so a candidate that pushes the position into a higher tier is
    judged against that tier's limits, not a lower one's.
    """
    sizing_fp = sizing.facts_fingerprint
    committed = (
        existing_position_notional_usdt
        + unresolved_order_notional_usdt
        + risk_reservation_notional_usdt
    )

    if spot:
        target = sizing.target_notional_usdt
        return LeverageResolution(
            status="not_applicable",
            policy_leverage_cap=int(policy_leverage_cap),
            venue_absolute_cap=1.0,
            safe_leverage_cap=1.0,
            execution_leverage=1,
            target_notional_usdt=target,
            execution_notional_usdt=target,
            target_fill_ratio=1.0 if target else None,
            margin_budget_usdt=sizing.margin_budget_usdt,
            margin_mode="spot",
            liquidation_buffer_pct=float(liquidation_buffer_pct),
            facts_fingerprint=_fingerprint(
                None, sizing_fp, policy_leverage_cap, liquidation_buffer_pct, "spot"
            ),
        )

    if envelope is None or not getattr(envelope, "tiers", None):
        return LeverageResolution(
            status="unavailable",
            reason=REASON_VENUE_MARGIN_TIERS_UNAVAILABLE,
            policy_leverage_cap=int(policy_leverage_cap),
            venue_absolute_cap=0.0,
            safe_leverage_cap=0.0,
            target_notional_usdt=sizing.target_notional_usdt,
            margin_budget_usdt=sizing.margin_budget_usdt,
            margin_mode=str(getattr(envelope, "margin_mode", None) or "unknown"),
            liquidation_buffer_pct=float(liquidation_buffer_pct),
            facts_fingerprint=_fingerprint(
                getattr(envelope, "facts_fingerprint", None), sizing_fp,
                policy_leverage_cap, liquidation_buffer_pct,
                "venue_margin_tiers_unavailable",
            ),
        )

    tiers = list(envelope.tiers)
    venue_absolute_cap = float(max(tier.max_leverage for tier in tiers))
    margin_mode = envelope.margin_mode
    envelope_fp = envelope.facts_fingerprint

    if margin_mode not in _KNOWN_MARGIN_MODES:
        return LeverageResolution(
            status="unavailable",
            reason=REASON_MARGIN_MODE_UNAVAILABLE,
            policy_leverage_cap=int(policy_leverage_cap),
            venue_absolute_cap=venue_absolute_cap,
            safe_leverage_cap=0.0,
            target_notional_usdt=sizing.target_notional_usdt,
            margin_budget_usdt=sizing.margin_budget_usdt,
            margin_mode=str(margin_mode),
            liquidation_buffer_pct=float(liquidation_buffer_pct),
            facts_fingerprint=_fingerprint(
                envelope_fp, sizing_fp, policy_leverage_cap, liquidation_buffer_pct,
                "margin_mode_unavailable",
            ),
        )

    if margin_mode == "cross":
        # V1 has no account-aware cross safety model. The isolated estimate
        # below must never be reused here -- a typed refusal, not a guess.
        return LeverageResolution(
            status="unavailable",
            reason=REASON_CROSS_MARGIN_SAFETY_UNAVAILABLE,
            policy_leverage_cap=int(policy_leverage_cap),
            venue_absolute_cap=venue_absolute_cap,
            safe_leverage_cap=0.0,
            target_notional_usdt=sizing.target_notional_usdt,
            margin_budget_usdt=sizing.margin_budget_usdt,
            margin_mode="cross",
            liquidation_buffer_pct=float(liquidation_buffer_pct),
            facts_fingerprint=_fingerprint(
                envelope_fp, sizing_fp, policy_leverage_cap, liquidation_buffer_pct,
                "cross_margin_safety_unavailable",
            ),
        )

    target = sizing.target_notional_usdt
    margin_budget = sizing.margin_budget_usdt
    required_leverage_for_target = (
        math.ceil(target / margin_budget)
        if target and margin_budget and margin_budget > 0
        else None
    )

    def _no_feasible_financing() -> LeverageResolution:
        reference_total = committed + (target or 0.0)
        reference_tier = envelope.tier_for_notional(reference_total) if target else None
        reference_mmr = reference_tier.maintenance_margin_rate if reference_tier else None
        return LeverageResolution(
            status="no_feasible_financing",
            reason=REASON_NO_FEASIBLE_FINANCING,
            policy_leverage_cap=int(policy_leverage_cap),
            venue_absolute_cap=venue_absolute_cap,
            safe_leverage_cap=(
                _safe_cap(reference_mmr, liquidation_buffer_pct)
                if reference_mmr is not None else 0.0
            ),
            required_leverage_for_target=required_leverage_for_target,
            target_notional_usdt=target,
            margin_budget_usdt=margin_budget,
            tier=reference_tier.tier if reference_tier else None,
            maintenance_margin_rate=reference_mmr,
            margin_mode="isolated",
            liquidation_buffer_pct=float(liquidation_buffer_pct),
            facts_fingerprint=_fingerprint(
                envelope_fp, sizing_fp, policy_leverage_cap, liquidation_buffer_pct,
                "no_feasible_financing",
            ),
        )

    if not target or target <= 0 or not margin_budget or margin_budget <= 0:
        return _no_feasible_financing()

    step = max(int(getattr(envelope, "leverage_step", None) or 1), 1)
    ceiling = min(int(policy_leverage_cap), math.floor(venue_absolute_cap))
    if ceiling < 1:
        return _no_feasible_financing()

    # (execution_notional, leverage, tier, mmr, projected_total, binding)
    best: tuple[float, int, object, float, float, str | None] | None = None

    leverage = step
    while leverage <= ceiling:
        raw = min(target, margin_budget * leverage)
        if raw > 0:
            provisional_total = committed + raw
            provisional_tier = envelope.tier_for_notional(provisional_total)
            if provisional_tier is not None:
                max_notional = provisional_tier.max_notional
                capacity = (max_notional - committed) if max_notional is not None else raw
                if capacity > 0:
                    candidate = min(raw, capacity)
                    final_total = committed + candidate
                    # Re-resolve the tier at the capped total: capping the
                    # notional down can move the projected position back
                    # into a lower tier than the provisional one.
                    final_tier = envelope.tier_for_notional(final_total)
                    if final_tier is not None and leverage <= final_tier.max_leverage:
                        mmr = final_tier.maintenance_margin_rate
                        if (1.0 / leverage - mmr) * 100.0 >= liquidation_buffer_pct:
                            if best is None or candidate > best[0]:
                                if capacity < raw:
                                    binding = BINDING_TIER_NOTIONAL_CAP
                                elif raw < target:
                                    binding = BINDING_MARGIN_BUDGET
                                else:
                                    binding = None
                                best = (candidate, leverage, final_tier, mmr, final_total, binding)
        leverage += step

    if best is None:
        return _no_feasible_financing()

    execution_notional, execution_leverage, tier_obj, mmr, projected_total, binding = best
    return LeverageResolution(
        status="resolved",
        policy_leverage_cap=int(policy_leverage_cap),
        venue_absolute_cap=venue_absolute_cap,
        safe_leverage_cap=_safe_cap(mmr, liquidation_buffer_pct),
        required_leverage_for_target=required_leverage_for_target,
        execution_leverage=execution_leverage,
        target_notional_usdt=target,
        execution_notional_usdt=execution_notional,
        target_fill_ratio=execution_notional / target if target else None,
        margin_budget_usdt=margin_budget,
        projected_position_notional_usdt=projected_total,
        tier=tier_obj.tier,
        maintenance_margin_rate=mmr,
        margin_mode="isolated",
        liquidation_buffer_pct=float(liquidation_buffer_pct),
        binding_constraint=binding,
        facts_fingerprint=_fingerprint(
            envelope_fp, sizing_fp, policy_leverage_cap, liquidation_buffer_pct, "resolved"
        ),
    )

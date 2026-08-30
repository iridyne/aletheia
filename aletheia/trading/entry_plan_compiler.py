"""Compile bounded planner intent into an authoritative EntryPlan."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal, cast

from aletheia.risk.policy import UnknownRiskExecutionMode, resolve_risk_policy
from aletheia.trading.entry_plan import EntryPlan, SIDEWAYS_STOP_MULT
from aletheia.trading.instruments import get_max_leverage
from aletheia.trading.trigger_specs import (
    registered_trigger_specs,
    require_registered_trigger_spec,
)

if TYPE_CHECKING:
    from aletheia.agent_shell.entry_planner import PlanIntent


_RISK_REWARD_TARGET = {"LOW": 1.5, "MEDIUM": 2.5}
_RISK_TIER_LEVERAGE_CAP = {"LOW": 2, "MEDIUM": 3}
_RISK_TIER_MAX_RISK_PCT = {"LOW": 0.5, "MEDIUM": 1.0}
_HORIZON_HOURS = {"INTRADAY": 4.0, "SWING": 24.0}
_HORIZON_LIFETIME_MINUTES = {"INTRADAY": 60, "SWING": 240}


def _number(value: Any, code: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{code}_invalid") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{code}_invalid")
    return result


def _intent_error(code: str, detail: str = ""):
    from aletheia.agent_shell.entry_planner import PlanIntentError

    return PlanIntentError(code, detail)


def _stop_reference(feature: dict, cfg) -> tuple[float, float]:
    try:
        atr_pct = float(feature.get("atr_pct") or 0.0)
    except (TypeError, ValueError):
        atr_pct = 0.0
    try:
        vol_target_pct = float(getattr(cfg.trading, "vol_target_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        vol_target_pct = 0.0
    if not math.isfinite(atr_pct):
        atr_pct = 0.0
    if not math.isfinite(vol_target_pct):
        vol_target_pct = 0.0
    if atr_pct <= 0 and vol_target_pct <= 0:
        raise _intent_error("NO_STOP_REFERENCE")
    return atr_pct, vol_target_pct


def _require_trigger(
    intent: "PlanIntent",
    *,
    entry_zone: tuple[float, float],
    stop_price: float,
    entry_price: float,
):
    spec_id = str(intent.trigger_spec_id)
    spec_version = str(intent.trigger_spec_version)
    spec_key = (spec_id, spec_version)
    registered = {(spec.spec_id, spec.version) for spec in registered_trigger_specs()}
    if spec_key not in registered:
        raise _intent_error("TRIGGER_SPEC_NOT_REGISTERED")
    raw_params = {
        "breakout_level": entry_price,
        "pullback_low": entry_zone[0],
        "pullback_high": entry_zone[1],
        "entry_low": entry_zone[0],
        "entry_high": entry_zone[1],
        "invalidation_price": stop_price,
    }
    try:
        return require_registered_trigger_spec(spec_id, spec_version, raw_params)
    except ValueError as exc:
        raise _intent_error("COMPILER_TRIGGER_PARAMS_INVALID", str(exc)) from exc


def compile_entry_plan(
    generation: dict,
    candidate: dict,
    feature: dict,
    intent: "PlanIntent",
    cfg,
) -> EntryPlan:
    """Compile one validated PlanIntent using only current candidate and policy facts."""

    from aletheia.agent_shell.entry_planner import PlanIntentError

    try:

        if intent.decision != "ENTRY_PLAN":
            raise PlanIntentError("COMPILER_ENTRY_INTENT_REQUIRED")
        raw_market_type = generation.get("market_type")
        if raw_market_type not in {"spot", "perp"}:
            raise PlanIntentError("COMPILER_MARKET_TYPE_INVALID")
        market_type = cast(Literal["spot", "perp"], raw_market_type)
        raw_direction = generation.get("direction")
        if raw_direction not in {"long", "short"}:
            raise PlanIntentError("COMPILER_DIRECTION_INVALID")
        direction = cast(Literal["long", "short"], raw_direction)
        if intent.execution_mode not in {"RESTING_MAKER", "TRIGGERED"}:
            raise PlanIntentError("COMPILER_EXECUTION_MODE_INVALID")
        execution_mode = cast(
            Literal["RESTING_MAKER", "TRIGGERED"],
            intent.execution_mode,
        )
        if intent.risk_tier not in _RISK_REWARD_TARGET:
            raise PlanIntentError("COMPILER_RISK_TIER_INVALID")
        if intent.horizon not in _HORIZON_HOURS:
            raise PlanIntentError("COMPILER_HORIZON_INVALID")

        try:
            entry_price = _number(candidate.get("entry_price_usd"), "candidate_price", positive=True)
        except ValueError as exc:
            raise PlanIntentError("STALE_CANDIDATE_PRICE") from exc

        atr_pct, vol_target_pct = _stop_reference(feature or {}, cfg)
        stop_distance_pct = max(vol_target_pct, atr_pct) * SIDEWAYS_STOP_MULT
        if not math.isfinite(stop_distance_pct) or stop_distance_pct <= 0:
            raise PlanIntentError("NO_STOP_REFERENCE")
        if direction == "long":
            stop_price = entry_price * (1.0 - stop_distance_pct / 100.0)
            target_price = entry_price + _RISK_REWARD_TARGET[intent.risk_tier] * (entry_price - stop_price)
        else:
            stop_price = entry_price * (1.0 + stop_distance_pct / 100.0)
            target_price = entry_price - _RISK_REWARD_TARGET[intent.risk_tier] * (stop_price - entry_price)
        band_pct = 0.5 * vol_target_pct
        entry_zone = (
            entry_price * (1.0 - band_pct / 100.0),
            entry_price * (1.0 + band_pct / 100.0),
        )

        execution_account_mode = str(cfg.exchange.paper_execution_mode)
        try:
            resolved_policy = resolve_risk_policy(cfg, execution_account_mode)
        except UnknownRiskExecutionMode as exc:
            raise PlanIntentError("RISK_POLICY_UNKNOWN") from exc
        if execution_account_mode not in {"paper_internal", "bitget_demo"}:
            raise PlanIntentError("RISK_POLICY_UNKNOWN")
        policy = resolved_policy.policy

        try:
            venue_leverage_cap = int(get_max_leverage(generation["symbol"], False, cfg))
        except Exception as exc:
            raise PlanIntentError("COMPILER_LEVERAGE_UNAVAILABLE") from exc
        max_leverage = (
            1
            if market_type == "spot"
            else min(venue_leverage_cap, _RISK_TIER_LEVERAGE_CAP[intent.risk_tier])
        )
        max_risk_pct = min(
            float(policy.loss.max_single_trade_risk_pct),
            _RISK_TIER_MAX_RISK_PCT[intent.risk_tier],
        )
        try:
            from aletheia.risk.engine import (
                account_equity_for_execution,
                per_symbol_notional_ceiling,
            )

            capital_base = account_equity_for_execution(execution_account_mode, cfg)
        except Exception as exc:
            raise PlanIntentError("COMPILER_RISK_BUDGET_UNAVAILABLE") from exc
        if capital_base is None or not math.isfinite(float(capital_base)) or float(capital_base) <= 0:
            raise PlanIntentError("COMPILER_RISK_BUDGET_UNAVAILABLE")
        risk_budget = float(capital_base) * max_risk_pct / 100.0
        worst_entry_price = entry_zone[1] if direction == "long" else entry_zone[0]
        stop_fraction = abs(worst_entry_price - stop_price) / worst_entry_price
        if not math.isfinite(stop_fraction) or stop_fraction <= 0:
            raise PlanIntentError("COMPILER_RISK_LIMIT_INVALID")
        risk_bounded_notional = risk_budget / stop_fraction
        max_notional = min(
            float(policy.capital.max_position_margin_usdt) * max_leverage,
            risk_bounded_notional,
        )
        total_exposure_cap = (
            float(policy.capital.max_total_exposure_pct) / 100.0 * float(capital_base)
        )
        if total_exposure_cap > 0:
            max_notional = min(max_notional, total_exposure_cap)
        per_symbol_cap = per_symbol_notional_ceiling(
            execution_account_mode,
            leverage=max_leverage,
            cfg=cfg,
        )
        if per_symbol_cap > 0:
            max_notional = min(max_notional, per_symbol_cap)
        if max_leverage < 1 or max_risk_pct <= 0 or max_notional <= 0:
            raise PlanIntentError("COMPILER_RISK_LIMIT_INVALID")

        maker = intent.execution_mode == "RESTING_MAKER"
        trigger_spec_id = None
        trigger_spec_version = None
        trigger_params: dict = {}
        trigger_requirements: tuple[str, ...] = ()
        trigger_freshness_ms = None
        if not maker:
            contract, trigger_params = _require_trigger(
                intent,
                entry_zone=entry_zone,
                stop_price=stop_price,
                entry_price=entry_price,
            )
            trigger_spec_id = intent.trigger_spec_id
            trigger_spec_version = intent.trigger_spec_version
            trigger_requirements = contract.data_requirements
            trigger_freshness_ms = contract.freshness_ms

        current = datetime.now(timezone.utc)
        expires = current + timedelta(minutes=_HORIZON_LIFETIME_MINUTES[intent.horizon])
        try:
            return EntryPlan(
                planning_generation_id=generation["id"],
                candidate_id=generation["last_candidate_id"],
                candidate_family=generation["candidate_family"],
                venue=generation["venue"],
                symbol=generation["symbol"],
                market_type=market_type,
                direction=direction,
                thesis=intent.thesis,
                evidence_refs=tuple(intent.evidence_refs),
                scenario=intent.scenario,
                execution_mode=execution_mode,
                trigger_spec_id=trigger_spec_id,
                trigger_spec_version=trigger_spec_version,
                trigger_params=trigger_params,
                trigger_data_requirements=trigger_requirements,
                trigger_freshness_ms=trigger_freshness_ms,
                entry_zone=entry_zone,
                invalidation_conditions=(
                    ("close_below_stop",) if direction == "long" else ("close_above_stop",)
                ),
                invalidation_price=stop_price,
                stop_price=stop_price,
                target_price=target_price,
                expected_horizon_hours=_HORIZON_HOURS[intent.horizon],
                approved_at=current.isoformat(),
                expires_at=expires.isoformat(),
                order_tactic="POST_ONLY_LIMIT" if maker else "OPPOSING_BBO_IOC",
                maker_required=maker,
                time_in_force="POST_ONLY" if maker else "IOC",
                limit_price=entry_price if maker else None,
                price_rule="EXACT_LIMIT" if maker else "OPPOSING_BBO",
                bbo_reference=None if maker else ("ask" if direction == "long" else "bid"),
                max_wait_sec=0,
                cancel_replace_policy="CANCEL_ONLY",
                max_requotes=0,
                partial_fill_policy="KEEP_PARTIAL_CANCEL_REMAINDER",
                taker_fallback_allowed=False,
                max_slippage_bps=None if maker else 20.0,
                worst_price=None,
                max_notional=max_notional,
                max_risk_pct=max_risk_pct,
                max_leverage=max_leverage,
                required_permission=str(cfg.agent.permission_mode),
                execution_account_mode=execution_account_mode,
                risk_policy_id=str(policy.policy_id),
                risk_policy_revision=int(policy.revision),
                risk_policy_hash=resolved_policy.policy_hash,
                behavior_version="entry-planner/v2",
            )
        except ValueError as exc:
            raise PlanIntentError(
                f"COMPILER_GEOMETRY_UNSATISFIABLE:{str(exc)[:100]}"
            ) from exc
    except PlanIntentError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise PlanIntentError("COMPILER_INTERNAL", type(exc).__name__) from exc

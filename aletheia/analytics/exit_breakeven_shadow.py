"""Prospective, paired exit shadow for the MVP profit-capture question.

The treatment is frozen while a new protected local-paper Position is opened:
after a completed one-minute bar reaches +1.5% favorable return, an additional
shadow stop at the entry fill becomes active from the next bar.  Existing exit
rules remain the comparator.  The module never mutates a Position or grants
behavior authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from typing import Any

from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    ExitBreakevenShadowAssignment,
    ExitBreakevenShadowOutcome,
)


EXPERIMENT_VERSION = "exit-breakeven-shadow/v1"
TREATMENT_ID = "entry-lock-after-1.5pct/v1"
COST_MODEL_VERSION = "paper-fill-close-bar-v1"
TRIGGER_RETURN_PCT = 1.5
_BAR_MS = 60_000
_MAX_BARS = 10_000


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _hash_json(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _finite(value: object, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def assign_new_protected_position(position_id: str) -> dict[str, Any]:
    """Freeze one shadow rule in the same transaction as protection activation."""

    existing = repo.get_exit_breakeven_shadow_assignment(position_id)
    if existing is not None:
        return existing
    position = repo.get_position(position_id)
    state = repo.get_position_protection_state(position_id)
    plan = repo.get_current_position_exit_plan(position_id)
    if position is None or state is None or plan is None:
        return {"assigned": False, "reason": "protected_position_evidence_unavailable"}
    if (
        position.get("status") != "open"
        or position.get("execution_mode") != "paper_internal"
        or position.get("trade_school") != "short_term"
        or position.get("source") != "native"
    ):
        return {"assigned": False, "reason": "position_not_eligible"}

    raw_direction = str(position.get("direction") or "").lower()
    direction = "long" if raw_direction in {"long", "buy"} else "short" if raw_direction in {"short", "sell"} else None
    entry = _finite(position.get("entry_price"), positive=True)
    quantity = _finite(position.get("quantity"), positive=True)
    entry_fee = _finite(position.get("entry_fee"), nonnegative=True)
    assigned_at = datetime.now(timezone.utc).isoformat()
    if direction is None or entry is None or quantity is None or entry_fee is None:
        return {"assigned": False, "reason": "position_economics_unavailable"}

    from aletheia.config.loader import load_config

    cfg = load_config()
    exit_fee_rate = _finite(float(cfg.trading.execution_fee_pct) / 100.0, nonnegative=True)
    slippage_bps = _finite(cfg.trading.slippage_bps, nonnegative=True)
    if exit_fee_rate is None or slippage_bps is None:
        return {"assigned": False, "reason": "execution_cost_contract_unavailable"}
    trigger_price = entry * (
        1.0 + TRIGGER_RETURN_PCT / 100.0
        if direction == "long"
        else 1.0 - TRIGGER_RETURN_PCT / 100.0
    )
    if not math.isfinite(trigger_price) or trigger_price <= 0:
        return {"assigned": False, "reason": "trigger_price_unavailable"}

    rule = {
        "experiment_version": EXPERIMENT_VERSION,
        "treatment_id": TREATMENT_ID,
        "behavior_authority": False,
        "timeframe": "1m",
        "trigger_return_pct": TRIGGER_RETURN_PCT,
        "shadow_stop": "entry_fill",
        "trigger_observation": "completed_bar",
        "stop_activation": "next_completed_bar",
        "shadow_fill_reference": "stop_touch_bar_close",
        "collision_policy": "same_bar_no_activation_v1",
        "cost_model_version": COST_MODEL_VERSION,
        "exit_fee_rate": exit_fee_rate,
        "slippage_bps": slippage_bps,
    }
    rule_json = _canonical_json(rule)
    assignment = ExitBreakevenShadowAssignment(
        position_id=position_id,
        assigned_at=assigned_at,
        venue=str(state["venue"]),
        symbol=str(state["symbol"]),
        market_type=str(state["market_type"]),
        direction=direction,
        entry_price=entry,
        quantity=quantity,
        entry_fee=entry_fee,
        exit_fee_rate=exit_fee_rate,
        slippage_bps=slippage_bps,
        trigger_return_pct=TRIGGER_RETURN_PCT,
        trigger_price=trigger_price,
        shadow_stop_price=entry,
        exit_plan_version=int(plan["version"]),
        exit_plan_effective_at=str(plan["effective_at"]),
        exit_plan_hash=str(plan["plan_hash"]),
        rule_json=rule_json,
        rule_hash=_hash_json(rule_json),
        runtime_session_id=position.get("runtime_session_id"),
        code_commit=position.get("producer_commit"),
        behavior_fingerprint=position.get("behavior_fingerprint"),
        created_at=assigned_at,
    )
    repo.insert_exit_breakeven_shadow_assignment(assignment)
    return {**assignment.model_dump(), "assigned": True}


def _outcome(
    assignment: dict[str, Any],
    position: dict[str, Any],
    *,
    status: str,
    reason: str,
    evidence: dict[str, Any],
    **values: Any,
) -> dict[str, Any]:
    evidence_json = _canonical_json(evidence)
    outcome = ExitBreakevenShadowOutcome(
        position_id=str(position["id"]),
        status=status,
        reason=reason,
        treatment_changed=status == "counterfactual_close",
        actual_closed_at=str(position["closed_at"]),
        evidence_json=evidence_json,
        evidence_hash=_hash_json(evidence_json),
        **values,
    )
    repo.insert_exit_breakeven_shadow_outcome(outcome)
    return outcome.model_dump()


def _unavailable(
    assignment: dict[str, Any],
    position: dict[str, Any],
    reason: str,
    **evidence: Any,
) -> dict[str, Any]:
    return _outcome(
        assignment,
        position,
        status="unavailable",
        reason=reason,
        evidence={
            "experiment_version": assignment.get("experiment_version"),
            "rule_hash": assignment.get("rule_hash"),
            "reason": reason,
            **evidence,
        },
    )


def _validated_bars(
    assignment: dict[str, Any],
    *,
    start_ms: int,
    end_exclusive_ms: int,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    expected_ts = list(range(start_ms, end_exclusive_ms, _BAR_MS))
    if not expected_ts:
        return [], None
    if len(expected_ts) > _MAX_BARS:
        return None, "shadow_window_too_large"
    rows = repo.get_ohlcv_candles(
        str(assignment["symbol"]),
        "1m",
        limit=len(expected_ts),
        exchange=str(assignment["venue"]),
        market_type=str(assignment["market_type"]),
        before_ts=end_exclusive_ms,
    )
    if [int(row.get("ts") or -1) for row in rows] != expected_ts:
        return None, "exact_1m_coverage_unavailable"
    normalized: list[dict[str, Any]] = []
    for row in rows:
        values = {field: _finite(row.get(field), positive=True) for field in ("open", "high", "low", "close")}
        volume = _finite(row.get("volume"), nonnegative=True)
        if any(value is None for value in values.values()) or volume is None:
            return None, "invalid_1m_bar"
        if values["low"] > min(values["open"], values["close"], values["high"]):
            return None, "invalid_1m_bar"
        if values["high"] < max(values["open"], values["close"], values["low"]):
            return None, "invalid_1m_bar"
        normalized.append({**row, **values, "volume": volume})
    return normalized, None


def settle_position_shadow(position: dict[str, Any]) -> dict[str, Any]:
    """Settle one paired shadow after an eligible actual Position close."""

    position_id = str(position.get("id") or "")
    existing = repo.get_exit_breakeven_shadow_outcome(position_id)
    if existing is not None:
        return existing
    assignment = repo.get_exit_breakeven_shadow_assignment(position_id)
    if assignment is None:
        raise ValueError("exit breakeven shadow assignment is unavailable")
    if (
        position.get("status") != "closed"
        or position.get("execution_mode") != "paper_internal"
        or not bool(position.get("outcome_eligible"))
    ):
        return _unavailable(assignment, position, "closed_paper_position_required")

    actual_net = _finite(position.get("realized_pnl"))
    opened = _parse_utc(position.get("opened_at"))
    assigned = _parse_utc(assignment.get("assigned_at"))
    closed = _parse_utc(position.get("closed_at"))
    if actual_net is None or opened is None or assigned is None or closed is None or closed <= opened:
        return _unavailable(assignment, position, "actual_economics_or_window_unavailable")
    state = repo.get_position_protection_state(position_id)
    if state is None or any(
        str(state.get(field)) != str(assignment.get(field))
        for field in ("venue", "symbol", "market_type", "timeframe")
    ):
        return _unavailable(assignment, position, "instrument_identity_mismatch")
    for field in ("entry_price", "quantity", "entry_fee"):
        current = _finite(position.get(field), nonnegative=field == "entry_fee", positive=field != "entry_fee")
        assigned_value = _finite(assignment.get(field), nonnegative=field == "entry_fee", positive=field != "entry_fee")
        if current is None or assigned_value is None or not math.isclose(current, assigned_value, rel_tol=0.0, abs_tol=1e-10):
            return _unavailable(assignment, position, "position_changed_after_assignment", field=field)

    assigned_ms = int(assigned.timestamp() * 1000)
    closed_ms = int(closed.timestamp() * 1000)
    # The first eligible bar opens strictly after assignment.  The last bar must
    # close strictly before the real exit, so no hidden same-time ordering enters.
    start_ms = (assigned_ms // _BAR_MS + 1) * _BAR_MS
    end_exclusive_ms = ((closed_ms - 1) // _BAR_MS) * _BAR_MS
    bars, path_error = _validated_bars(
        assignment,
        start_ms=start_ms,
        end_exclusive_ms=end_exclusive_ms,
    )
    if bars is None:
        return _unavailable(
            assignment,
            position,
            path_error or "exact_1m_path_unavailable",
            start_ms=start_ms,
            end_exclusive_ms=end_exclusive_ms,
        )

    direction = str(assignment["direction"])
    trigger_price = float(assignment["trigger_price"])
    stop_price = float(assignment["shadow_stop_price"])
    trigger_index = next(
        (
            index
            for index, bar in enumerate(bars)
            if (direction == "long" and float(bar["high"]) >= trigger_price)
            or (direction == "short" and float(bar["low"]) <= trigger_price)
        ),
        None,
    )
    stop_index = None
    if trigger_index is not None:
        stop_index = next(
            (
                index
                for index, bar in enumerate(bars[trigger_index + 1 :], start=trigger_index + 1)
                if (direction == "long" and float(bar["low"]) <= stop_price)
                or (direction == "short" and float(bar["high"]) >= stop_price)
            ),
            None,
        )

    base_evidence = {
        "experiment_version": EXPERIMENT_VERSION,
        "treatment_id": TREATMENT_ID,
        "behavior_authority": False,
        "rule_hash": assignment["rule_hash"],
        "instrument": {
            "venue": assignment["venue"],
            "symbol": assignment["symbol"],
            "market_type": assignment["market_type"],
            "timeframe": "1m",
        },
        "eligible_bar_start_ms": start_ms,
        "eligible_bar_end_exclusive_ms": end_exclusive_ms,
        "bars_observed": len(bars),
        "trigger_bar_open_ms": int(bars[trigger_index]["ts"]) if trigger_index is not None else None,
        "same_bar_stop_not_active": True,
    }
    if stop_index is None:
        return _outcome(
            assignment,
            position,
            status="unchanged",
            reason="shadow_rule_did_not_preempt_actual_exit",
            evidence={**base_evidence, "shadow_exit_bar_open_ms": None},
            trigger_bar_open_ms=(int(bars[trigger_index]["ts"]) if trigger_index is not None else None),
            actual_net_pnl=actual_net,
            shadow_net_pnl=actual_net,
            incremental_net_pnl=0.0,
            cost_model_version=COST_MODEL_VERSION,
        )

    exit_bar = bars[stop_index]
    reference_price = float(exit_bar["close"])
    slip = float(assignment["slippage_bps"]) / 10_000.0
    fill_price = reference_price * (1.0 - slip if direction == "long" else 1.0 + slip)
    quantity = float(assignment["quantity"])
    entry_price = float(assignment["entry_price"])
    entry_fee = float(assignment["entry_fee"])
    exit_fee = fill_price * quantity * float(assignment["exit_fee_rate"])
    shadow_exit_ms = int(exit_bar["ts"]) + _BAR_MS
    shadow_exit_at = datetime.fromtimestamp(shadow_exit_ms / 1000.0, timezone.utc).isoformat()

    if assignment["market_type"] == "spot":
        funding_cost = 0.0
        funding_evidence: dict[str, Any] = {"status": "complete", "source": "not_applicable"}
    else:
        from aletheia.portfolio.local_funding import compute_local_funding

        funding = compute_local_funding(position, closed_at=shadow_exit_at)
        if not funding.complete or funding.funding_cost is None:
            return _unavailable(
                assignment,
                position,
                "shadow_funding_economics_unavailable",
                **base_evidence,
                shadow_exit_bar_open_ms=int(exit_bar["ts"]),
                funding=funding.evidence,
            )
        funding_cost = float(funding.funding_cost)
        funding_evidence = funding.evidence

    gross = (
        (fill_price - entry_price) * quantity
        if direction == "long"
        else (entry_price - fill_price) * quantity
    )
    shadow_net = gross - entry_fee - exit_fee - funding_cost
    incremental = shadow_net - actual_net
    if not all(math.isfinite(value) for value in (fill_price, exit_fee, gross, shadow_net, incremental)):
        return _unavailable(assignment, position, "shadow_arithmetic_unavailable")
    return _outcome(
        assignment,
        position,
        status="counterfactual_close",
        reason="shadow_entry_lock_preempted_actual_exit",
        evidence={
            **base_evidence,
            "shadow_exit_bar_open_ms": int(exit_bar["ts"]),
            "shadow_exit_at": shadow_exit_at,
            "fill_reference": "completed_stop_touch_bar_close",
            "funding": funding_evidence,
        },
        trigger_bar_open_ms=int(bars[trigger_index]["ts"]),
        shadow_exit_bar_open_ms=int(exit_bar["ts"]),
        shadow_exit_at=shadow_exit_at,
        shadow_reference_price=reference_price,
        shadow_fill_price=fill_price,
        shadow_entry_fee=entry_fee,
        shadow_exit_fee=exit_fee,
        shadow_funding_cost=funding_cost,
        actual_net_pnl=actual_net,
        shadow_net_pnl=shadow_net,
        incremental_net_pnl=incremental,
        cost_model_version=COST_MODEL_VERSION,
    )


def build_exit_breakeven_shadow_report(limit: int = 1000) -> dict[str, Any]:
    """Return descriptive evidence only; no promotion or parameter mutation."""

    rows = repo.list_exit_breakeven_shadow_outcomes(limit=limit)
    available = [row for row in rows if row["status"] != "unavailable"]
    changed = [row for row in available if bool(row["treatment_changed"])]
    deltas = [float(row["incremental_net_pnl"]) for row in available]
    return {
        "experiment_version": EXPERIMENT_VERSION,
        "behavior_authority": False,
        "outcome_count": len(rows),
        "available_count": len(available),
        "unavailable_count": len(rows) - len(available),
        "changed_count": len(changed),
        "incremental_net_pnl_total": sum(deltas) if deltas else None,
        "incremental_net_pnl_mean": (sum(deltas) / len(deltas)) if deltas else None,
        "positive_increment_count": sum(1 for value in deltas if value > 0),
        "promotion_eligible": False,
    }

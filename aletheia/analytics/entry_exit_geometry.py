"""Fail-closed Entry/Exit geometry reconstructed from durable evidence only.

This is a read-only audit model.  It deliberately does not import configuration,
Risk, Execution, or position-management code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
from aletheia.analytics.trade_path import build_trade_path_rows
from aletheia.storage import repositories as repo
from aletheia.storage.hashing import sha256_json
from aletheia.storage.models import TradeProposal

_ATOL = 1e-10
_SOURCE_LIMIT = 10_000
_PHYSICAL_MARKET_TYPES = {"spot": "spot", "perp": "perp", "usdt_futures": "perp"}


def build_entry_exit_geometry_audit(
    *, hours: int = 24, limit: int = 200, symbol: str | None = None
) -> dict[str, Any]:
    """Return eligible closed positions inside the requested close-time window."""
    if isinstance(hours, bool) or not isinstance(hours, int) or hours < 1:
        raise ValueError("hours must be a positive integer")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    clean_symbol = str(symbol).strip() if symbol is not None else None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    # trade_path's limit is applied before this view's close-time/symbol filters.
    # Read its complete supported source bound so a display limit never silently
    # excludes a matching row behind 500 unrelated outcomes.
    rows = build_trade_path_rows(limit=_SOURCE_LIMIT, post_exit_bars=0)
    rows.sort(
        key=lambda row: _utc(row.get("closed_at")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    selected_paths: list[dict[str, Any]] = []
    outside_window = 0
    symbol_mismatch = 0
    for path_row in rows:
        closed_at = _utc(path_row.get("closed_at"))
        if closed_at is None or closed_at < cutoff:
            outside_window += 1
            continue
        if clean_symbol is not None and path_row.get("symbol") != clean_symbol:
            symbol_mismatch += 1
            continue
        selected_paths.append(path_row)
    selected = [_build_row(row) for row in selected_paths[:limit]]
    # At the exact source cap we cannot distinguish exactly-10k from truncated;
    # report unknown rather than asserting either state.
    source_truncated: bool | None = None if len(rows) >= _SOURCE_LIMIT else False
    return {
        "view": "entry-exit",
        "hours": hours,
        "limit": limit,
        "symbol": clean_symbol,
        "position_count": len(selected),
        "cohort_eligible_count": len(selected_paths),
        "cohort_identity": {
            "basis": "current_eligible_strategy_outcomes_closed_in_window",
            "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
            "symbol": clean_symbol,
        },
        "cohort_exclusions": {
            "pre_path_outcome_exclusions": "not_included_by_eligible_strategy_outcomes",
            "outside_close_window": outside_window,
            "symbol_mismatch": symbol_mismatch,
            "source_truncated": source_truncated,
        },
        "rows": selected,
    }


def _build_row(path_row: dict[str, Any]) -> dict[str, Any]:
    position_id = str(path_row.get("position_id") or "")
    proposal = _safe_read(repo.get_trade_proposal, path_row.get("proposal_id"))
    position = _safe_read(repo.get_position, position_id)
    assignment = _safe_read(repo.get_exit_breakeven_shadow_assignment, position_id)
    state = _safe_read(repo.get_position_protection_state, position_id)
    trigger = _safe_read(repo.get_position_protection_trigger, position_id)
    plans = _read_plans(position_id)

    result: dict[str, Any] = {
        "position_id": position_id or None,
        "proposal_id": path_row.get("proposal_id"),
        "symbol": path_row.get("symbol"),
        "direction": path_row.get("direction"),
        "opened_at": path_row.get("opened_at"),
        "closed_at": path_row.get("closed_at"),
        "close_reason": path_row.get("close_reason"),
        "proposal_geometry_status": "unavailable",
        "proposal_geometry_reason": "proposal_evidence_unavailable",
        "proposal_price": None,
        "proposal_stop_price": None,
        "proposal_target_price": None,
        "proposal_gross_risk_reward": None,
        "proposal_stop_distance": None,
        "proposal_target_distance": None,
        "initial_plan_provenance": None,
        "entry_atr_status": "unavailable",
        "entry_atr_reason": "entry_atr_lineage_unavailable",
        "entry_atr": None,
        "stop_distance_entry_atr": None,
        "target_distance_entry_atr": None,
        "cost_assumptions_status": "unavailable",
        "cost_assumptions_reason": "cost_assignment_unavailable",
        "cost_contract_source": None,
        "round_trip_cost": None,
        "round_trip_cost_basis": None,
        "stop_round_trip_cost": None,
        "target_round_trip_cost": None,
        "stop_fill_price": None,
        "target_fill_price": None,
        "stop_exit_fee": None,
        "target_exit_fee": None,
        "break_even_return": None,
        "break_even_win_rate": None,
        "net_target_gain": None,
        "net_stop_loss": None,
        "initial_risk_status": "unavailable",
        "initial_risk_reason": "initial_plan_unavailable",
        "initial_plan_version": None,
        "initial_plan_hash": None,
        "initial_stop_price": None,
        "initial_target_price": None,
        "initial_risk_amount": None,
        "realized_net_r": None,
        "path_normalization_status": "unavailable",
        "path_normalization_reason": "initial_risk_unavailable",
        "mfe_r": None,
        "mae_r": None,
        "path_ratio_precision": None,
        "revision_history_status": "unavailable",
        "revision_history_reason": "exit_plan_history_unavailable",
        "trailing_plan_revisions": [],
        "protective_trigger_status": "unavailable",
        "protective_trigger_reason": "protective_trigger_unavailable",
        "protective_trigger": None,
        "protective_trigger_cross_evidence_status": "unavailable",
    }

    geometry = _proposal_geometry(path_row, proposal, position)
    result.update(geometry)
    history = _plan_history(plans, position)
    result.update(history)
    initial = _initial_risk(path_row, position, proposal, plans, geometry)
    result.update(initial)
    result.update(_cost_assumptions(path_row, assignment, state, plans, initial))
    result.update(_path_normalization(path_row, initial))
    result.update(_protective_trigger(path_row, state, trigger, plans))
    return result


def _proposal_geometry(
    row: dict[str, Any], proposal: dict[str, Any] | None, position: dict[str, Any] | None
) -> dict[str, Any]:
    unavailable = {
        "proposal_geometry_status": "unavailable",
        "proposal_geometry_reason": "proposal_evidence_unavailable",
    }
    if proposal is None or position is None:
        return unavailable
    direction = _direction(row.get("direction"))
    identity = (
        proposal.get("id") == row.get("proposal_id") == position.get("proposal_id")
        and proposal.get("opportunity_id") == position.get("opportunity_id") == row.get("opportunity_id")
        and proposal.get("symbol") == position.get("symbol") == row.get("symbol")
        and _direction(proposal.get("direction")) == direction
        and _direction(position.get("direction")) == direction
        and proposal.get("market_type") == position.get("market_type") == row.get("market_type")
        and proposal.get("source") == position.get("source") == "native"
    )
    if not identity:
        return {**unavailable, "proposal_geometry_reason": "proposal_position_identity_mismatch"}
    if not _validate_proposal_chain(proposal):
        return {
            **unavailable,
            "proposal_geometry_reason": "proposal_chain_identity_or_hash_invalid",
        }
    price = _positive(proposal.get("price"))
    stop = _positive(proposal.get("stop_price"))
    target = _positive(proposal.get("take_profit"))
    stored_rr = _positive(proposal.get("risk_reward"))
    if None in (price, stop, target, stored_rr) or direction is None:
        return {**unavailable, "proposal_geometry_reason": "proposal_geometry_malformed"}
    assert price is not None and stop is not None and target is not None and stored_rr is not None
    if not _barriers(direction, price, stop, target):
        return {**unavailable, "proposal_geometry_reason": "proposal_barriers_invalid"}
    stop_distance = abs(price - stop)
    target_distance = abs(target - price)
    rr = target_distance / stop_distance
    if not math.isclose(rr, stored_rr, rel_tol=1e-9, abs_tol=1e-9):
        return {**unavailable, "proposal_geometry_reason": "proposal_risk_reward_mismatch"}
    source = str(proposal.get("stop_source") or "").strip().lower()
    provenance = "Operator" if source == "operator" else "default" if source == "default" else None
    if provenance is None:
        return {**unavailable, "proposal_geometry_reason": "proposal_provenance_unavailable"}
    return {
        "proposal_geometry_status": "available",
        "proposal_geometry_reason": "validated_proposal_snapshot",
        "proposal_price": price,
        "proposal_stop_price": stop,
        "proposal_target_price": target,
        "proposal_gross_risk_reward": rr,
        "proposal_stop_distance": stop_distance,
        "proposal_target_distance": target_distance,
        "initial_plan_provenance": provenance,
    }


def _validate_proposal_chain(proposal: dict[str, Any]) -> bool:
    """Validate this exact snapshot and its immutable global chain prefix read-only."""
    proposal_id = str(proposal.get("id") or "")
    if not proposal_id:
        return False
    try:
        rows = repo.get_db().execute(
            "SELECT rowid AS _chain_rowid, * FROM trade_proposals "
            "WHERE rowid <= (SELECT rowid FROM trade_proposals WHERE id=?) "
            "ORDER BY rowid ASC",
            (proposal_id,),
        ).fetchall()
    except Exception:
        return False
    chain = [dict(row) for row in rows]
    if not chain or chain[-1].get("id") != proposal_id:
        return False
    previous_hash: str | None = None
    for stored in chain:
        try:
            model = TradeProposal.model_validate(stored)
            payload = model.model_dump(exclude={"status", "commit_hash"})
            expected_hash = sha256_json(payload)[:16]
        except Exception:
            return False
        if stored.get("parent_hash") != previous_hash or stored.get("commit_hash") != expected_hash:
            return False
        previous_hash = expected_hash
    try:
        supplied = TradeProposal.model_validate(proposal).model_dump()
        persisted = TradeProposal.model_validate(chain[-1]).model_dump()
    except Exception:
        return False
    # Status is mutable, but this exact read should still agree with its stored
    # snapshot; all immutable fields, including parent_hash, are hash-covered.
    return supplied == persisted


def _plan_history(plans: list[dict[str, Any]] | None, position: dict[str, Any] | None) -> dict[str, Any]:
    base = {
        "revision_history_status": "unavailable",
        "revision_history_reason": "exit_plan_history_unavailable",
        "trailing_plan_revisions": [],
    }
    if plans is None or not plans:
        return base
    parsed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    previous_time: datetime | None = None
    for expected, plan in enumerate(plans, start=1):
        if plan.get("version") != expected:
            return {**base, "revision_history_reason": "exit_plan_versions_non_contiguous"}
        effective = _utc(plan.get("effective_at"))
        created = _utc(plan.get("created_at"))
        closed = _utc((position or {}).get("closed_at"))
        if (
            effective is None
            or created is None
            or created < effective
            or (closed is not None and (effective > closed or created > closed))
            or (previous_time is not None and effective <= previous_time)
        ):
            return {**base, "revision_history_reason": "exit_plan_chronology_invalid"}
        # Runtime revisions (v2+) freeze created_at as their effective instant.
        # A later-created row backdated into the holding path is retrospective
        # evidence and cannot be presented as contemporaneous history.
        if expected > 1 and created != effective:
            return {**base, "revision_history_reason": "retrospective_exit_plan_revision"}
        payload = _plan_payload(plan)
        if payload is None:
            return {**base, "revision_history_reason": "exit_plan_json_or_hash_invalid"}
        parsed.append((plan, payload))
        previous_time = effective
    if position is None or position.get("exit_plan_effective_at") != plans[-1].get("effective_at"):
        return {**base, "revision_history_reason": "exit_plan_projection_mismatch"}
    revisions: list[dict[str, Any]] = []
    previous_plan, previous_payload = parsed[0]
    previous_trailing = previous_payload.get("trailing")
    for plan, payload in parsed[1:]:
        trailing = payload.get("trailing")
        trailing_revision = (
            trailing != previous_trailing
            or (
                str(plan.get("provenance") or "") == "deterministic_trailing_v2"
                and not _same(plan.get("stop_price"), previous_plan.get("stop_price"))
            )
        )
        if trailing_revision:
            revisions.append({
                "version": plan["version"],
                "effective_at": plan["effective_at"],
                "stop_price": _finite(plan.get("stop_price")),
                "take_profit_price": _finite(plan.get("take_profit_price")),
                "exit_limit_price": _finite(plan.get("exit_limit_price")),
                "provenance": plan.get("provenance"),
                "trailing": trailing,
                "plan_hash": plan.get("plan_hash"),
            })
        previous_plan = plan
        previous_trailing = trailing
    return {
        "revision_history_status": "available",
        "revision_history_reason": "complete_immutable_exit_plan_history",
        "trailing_plan_revisions": revisions,
    }


def _initial_risk(
    row: dict[str, Any],
    position: dict[str, Any] | None,
    proposal: dict[str, Any] | None,
    plans: list[dict[str, Any]] | None,
    proposal_geometry: dict[str, Any],
) -> dict[str, Any]:
    base = {"initial_risk_status": "unavailable", "initial_risk_reason": "initial_plan_unavailable"}
    if proposal_geometry.get("proposal_geometry_status") != "available":
        return {**base, "initial_risk_reason": "proposal_snapshot_unverified"}
    if not plans or position is None or proposal is None:
        return base
    # Version one is independently immutable evidence. Later revision integrity
    # is reported separately and never retroactively erases verified entry risk.
    first = plans[0]
    if first.get("version") != 1 or first.get("position_id") != position.get("id"):
        return {**base, "initial_risk_reason": "initial_plan_identity_mismatch"}
    if _plan_payload(first) is None:
        return {**base, "initial_risk_reason": "initial_plan_json_or_hash_invalid"}
    opened = _utc(position.get("opened_at"))
    effective = _utc(first.get("effective_at"))
    if opened is None or effective != opened:
        return {**base, "initial_risk_reason": "initial_plan_not_effective_at_entry"}
    state = _safe_read(repo.get_position_protection_state, position.get("id"))
    if state is None or _utc(state.get("activated_at")) != opened:
        return {**base, "initial_risk_reason": "forward_adopted_or_unlinked_initial_plan"}
    order = _safe_read(repo.get_order, position.get("order_id"))
    fill = _entry_fill(position)
    entry = _positive(position.get("entry_price"))
    quantity = _positive(position.get("quantity"))
    stop = _positive(first.get("stop_price"))
    target = _positive(first.get("take_profit_price"))
    direction = _direction(position.get("direction"))
    if (
        order is None
        or fill is None
        or order.get("id") != position.get("order_id")
        or order.get("position_id") not in (None, position.get("id"))
        or order.get("proposal_id") != proposal.get("id")
        or order.get("close_reason") not in (None, "")
        or order.get("symbol") != position.get("symbol")
        or _direction(order.get("direction")) != direction
        or None in (entry, quantity, stop, target)
        or direction is None
    ):
        return {**base, "initial_risk_reason": "entry_fill_or_initial_plan_malformed"}
    assert entry is not None and quantity is not None and stop is not None and target is not None
    fill_price = _positive(fill.get("fill_price"))
    fill_qty = _positive(fill.get("fill_quantity"))
    if (
        fill.get("filled_at") != position.get("opened_at")
        or fill_price is None
        or fill_qty is None
        or not _same(fill_price, entry)
        or not _same(fill_qty, quantity)
        or position.get("order_id") != fill.get("order_id")
    ):
        return {**base, "initial_risk_reason": "entry_fill_linkage_mismatch"}
    if not _barriers(direction, entry, stop, target):
        return {**base, "initial_risk_reason": "initial_plan_barriers_invalid"}
    # Version one must represent the Proposal's entry contract; mutable Position
    # projections are intentionally not consulted.
    if not _same(stop, proposal.get("stop_price")) or not _same(target, proposal.get("take_profit")):
        return {**base, "initial_risk_reason": "initial_plan_proposal_mismatch"}
    risk_amount = abs(entry - stop) * quantity
    realized = _finite(position.get("realized_pnl"))
    if risk_amount <= 0 or realized is None:
        return {**base, "initial_risk_reason": "initial_risk_or_realized_pnl_invalid"}
    return {
        "initial_risk_status": "available",
        "initial_risk_reason": "verified_version_1_entry_plan",
        "initial_plan_version": 1,
        "initial_plan_hash": first.get("plan_hash"),
        "initial_stop_price": stop,
        "initial_target_price": target,
        "initial_risk_amount": risk_amount,
        "realized_net_r": realized / risk_amount,
    }


def _cost_assumptions(
    row: dict[str, Any],
    assignment: dict[str, Any] | None,
    state: dict[str, Any] | None,
    plans: list[dict[str, Any]] | None,
    initial: dict[str, Any],
) -> dict[str, Any]:
    base = {"cost_assumptions_status": "unavailable", "cost_assumptions_reason": "cost_assignment_unavailable"}
    if assignment is None:
        return base
    if initial.get("initial_risk_status") != "available" or not plans:
        return {**base, "cost_assumptions_reason": "initial_risk_unavailable"}
    first = plans[0]
    entry = _positive(row.get("entry_price"))
    quantity = _positive(row.get("quantity"))
    entry_fee = _nonnegative(assignment.get("entry_fee"))
    fee_rate = _nonnegative(assignment.get("exit_fee_rate"))
    slippage_bps = _nonnegative(assignment.get("slippage_bps"))
    direction = _direction(row.get("direction"))
    physical_market = _physical_market(row.get("market_type"))
    identity = (
        state is not None
        and assignment.get("position_id") == row.get("position_id") == state.get("position_id")
        and assignment.get("symbol") == row.get("symbol") == state.get("symbol")
        and str(assignment.get("venue") or "").lower() == str(state.get("venue") or "").lower()
        and assignment.get("market_type") == physical_market == state.get("market_type")
        and assignment.get("timeframe") == state.get("timeframe") == "1m"
        and _direction(assignment.get("direction")) == direction
        and assignment.get("exit_plan_version") == 1
        and assignment.get("exit_plan_effective_at") == first.get("effective_at")
        and assignment.get("exit_plan_hash") == first.get("plan_hash")
        and assignment.get("behavior_authority") in (False, 0)
        and assignment.get("experiment_version") == "exit-breakeven-shadow/v1"
        and _same(assignment.get("entry_fee"), row.get("entry_fee"))
    )
    if not identity:
        return {**base, "cost_assumptions_reason": "cost_assignment_identity_mismatch"}
    cross_status = _path_identity_status(row, assignment)
    if cross_status == "mismatch":
        return {**base, "cost_assumptions_reason": "cost_assignment_path_identity_mismatch"}
    if None in (entry, quantity, entry_fee, fee_rate, slippage_bps) or direction is None:
        return {**base, "cost_assumptions_reason": "cost_assignment_malformed"}
    rule = _hashed_json(assignment.get("rule_json"), assignment.get("rule_hash"))
    if (
        not isinstance(rule, dict)
        or rule.get("cost_model_version") != "paper-fill-close-bar-v1"
        or not _same(rule.get("exit_fee_rate"), fee_rate)
        or not _same(rule.get("slippage_bps"), slippage_bps)
    ):
        return {**base, "cost_assumptions_reason": "cost_assignment_rule_invalid"}
    assert entry is not None and quantity is not None and entry_fee is not None
    assert fee_rate is not None and slippage_bps is not None
    if not _same(assignment.get("entry_price"), entry) or not _same(assignment.get("quantity"), quantity):
        return {**base, "cost_assumptions_reason": "cost_assignment_economics_mismatch"}
    stop = _paper_close_economics(
        direction=direction,
        reference_price=float(initial["initial_stop_price"]),
        entry_price=entry,
        quantity=quantity,
        entry_fee=entry_fee,
        exit_fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )
    target = _paper_close_economics(
        direction=direction,
        reference_price=float(initial["initial_target_price"]),
        entry_price=entry,
        quantity=quantity,
        entry_fee=entry_fee,
        exit_fee_rate=fee_rate,
        slippage_bps=slippage_bps,
    )
    net_stop = -stop["net_pnl"]
    net_target = target["net_pnl"]
    if net_stop <= 0 or net_target <= 0:
        return {**base, "cost_assumptions_reason": "net_barrier_economics_non_positive"}
    stop_cost = entry_fee + stop["exit_fee"] + stop["slippage_cost"]
    target_cost = entry_fee + target["exit_fee"] + target["slippage_cost"]
    notional = entry * quantity
    return {
        "cost_assumptions_status": "available",
        "cost_assumptions_reason": "exact_frozen_paper_fill_close_bar_v1",
        "cost_contract_source": {
            "experiment_version": assignment.get("experiment_version"),
            "position_id": assignment.get("position_id"),
            "rule_hash": assignment.get("rule_hash"),
            "exit_plan_hash": assignment.get("exit_plan_hash"),
            "path_identity_status": cross_status,
        },
        "round_trip_cost": target_cost,
        "round_trip_cost_basis": "target_barrier_fill",
        "stop_round_trip_cost": stop_cost,
        "target_round_trip_cost": target_cost,
        "stop_fill_price": stop["fill_price"],
        "target_fill_price": target["fill_price"],
        "stop_exit_fee": stop["exit_fee"],
        "target_exit_fee": target["exit_fee"],
        "break_even_return": target_cost / notional,
        "break_even_win_rate": net_stop / (net_target + net_stop),
        "net_target_gain": net_target,
        "net_stop_loss": net_stop,
    }


def _paper_close_economics(
    *,
    direction: str,
    reference_price: float,
    entry_price: float,
    quantity: float,
    entry_fee: float,
    exit_fee_rate: float,
    slippage_bps: float,
) -> dict[str, float]:
    """Exact paper-broker close arithmetic for one frozen barrier reference."""
    slip = slippage_bps / 10_000.0
    # A long closes by selling (lower fill); a short closes by buying (higher).
    fill_price = round(
        reference_price * (1.0 - slip if direction == "long" else 1.0 + slip),
        8,
    )
    exit_fee = round(quantity * fill_price * exit_fee_rate, 8)
    gross = (
        (fill_price - entry_price) * quantity
        if direction == "long"
        else (entry_price - fill_price) * quantity
    )
    return {
        "fill_price": fill_price,
        "exit_fee": exit_fee,
        "slippage_cost": abs(fill_price - reference_price) * quantity,
        "net_pnl": gross - entry_fee - exit_fee,
    }


def _path_normalization(row: dict[str, Any], initial: dict[str, Any]) -> dict[str, Any]:
    base = {"path_normalization_status": "unavailable", "path_normalization_reason": "initial_risk_unavailable"}
    if initial.get("initial_risk_status") != "available":
        return base
    if row.get("path_evidence_status") != "available":
        return {**base, "path_normalization_reason": "exact_trade_path_unavailable"}
    mfe = _nonnegative(row.get("mfe_pct"))
    mae = _finite(row.get("mae_pct"))
    entry = _positive(row.get("entry_price"))
    risk_distance = abs(float(initial["initial_stop_price"]) - float(entry or 0))
    if mfe is None or mae is None or mae > 0 or entry is None or risk_distance <= 0:
        return {**base, "path_normalization_reason": "exact_trade_path_malformed"}
    return {
        "path_normalization_status": "available",
        "path_normalization_reason": "rounded_path_ratios_over_verified_initial_risk",
        "mfe_r": (mfe / 100.0 * entry) / risk_distance,
        "mae_r": (mae / 100.0 * entry) / risk_distance,
        "path_ratio_precision": "derived_from_rounded_4dp_excursion_pct",
    }


def _protective_trigger(
    row: dict[str, Any], state: dict[str, Any] | None, trigger: dict[str, Any] | None, plans: list[dict[str, Any]] | None
) -> dict[str, Any]:
    base = {
        "protective_trigger_status": "unavailable",
        "protective_trigger_reason": "protective_trigger_unavailable",
        "protective_trigger": None,
        "protective_trigger_cross_evidence_status": "unavailable",
    }
    if state is None or trigger is None or not plans:
        return base
    version = trigger.get("plan_version")
    plan = next((item for item in plans if item.get("version") == version), None)
    bar_fields = {key: trigger.get(key) for key in ("venue", "symbol", "market_type", "timeframe", "bar_open_ms", "bar_close_ms", "bar_open", "bar_high", "bar_low", "bar_close", "bar_volume", "provider")}
    try:
        bar_hash = repo.hash_position_protection_bar(**bar_fields)
    except Exception:
        bar_hash = None
    direction = _direction(row.get("direction"))
    stop = _positive(trigger.get("stop_price"))
    upside = _positive(trigger.get("upside_price"))
    low = _positive(trigger.get("bar_low"))
    high = _positive(trigger.get("bar_high"))
    bar_open_ms = _integer(trigger.get("bar_open_ms"))
    bar_close_ms = _integer(trigger.get("bar_close_ms"))
    stop_hit = bool(trigger.get("stop_hit"))
    target_hit = bool(trigger.get("target_hit"))
    stop_crossed = direction is not None and stop is not None and low is not None and high is not None and (
        low <= stop if direction == "long" else high >= stop
    )
    target_crossed = direction is not None and upside is not None and low is not None and high is not None and (
        high >= upside if direction == "long" else low <= upside
    )
    reason_matches = (
        trigger.get("reason") == "stop_loss"
        if stop_hit
        else trigger.get("reason") in {"take_profit", "exit_limit"}
    )
    plan_index = plans.index(plan) if plan in plans else -1
    successor = plans[plan_index + 1] if 0 <= plan_index < len(plans) - 1 else None
    plan_effective = _utc((plan or {}).get("effective_at"))
    successor_effective = _utc((successor or {}).get("effective_at"))
    bar_open_at = (
        datetime.fromtimestamp(bar_open_ms / 1000, timezone.utc)
        if bar_open_ms is not None
        else None
    )
    bar_close_at = (
        datetime.fromtimestamp(bar_close_ms / 1000, timezone.utc)
        if bar_close_ms is not None
        else None
    )
    if stop_hit != bool(stop_crossed) or target_hit != bool(target_crossed):
        return {
            **base,
            "protective_trigger_reason": "protective_trigger_crossing_flags_mismatch",
        }
    cross_status = _path_identity_status(row, trigger)
    if cross_status == "mismatch":
        return {
            **base,
            "protective_trigger_reason": "protective_trigger_path_identity_mismatch",
        }
    matches = (
        plan is not None
        and trigger.get("position_id") == row.get("position_id") == state.get("position_id")
        and state.get("trigger_id") == trigger.get("trigger_id")
        and trigger.get("symbol") == row.get("symbol") == state.get("symbol")
        and all(trigger.get(field) == state.get(field) for field in ("venue", "market_type", "timeframe"))
        and trigger.get("plan_effective_at") == plan.get("effective_at")
        and trigger.get("plan_hash") == plan.get("plan_hash")
        and trigger.get("bar_hash") == bar_hash
        and trigger.get("collision_policy") == "stop_first_v1"
        and (stop_hit or target_hit)
        and reason_matches
        and stop_hit == bool(stop_crossed)
        and target_hit == bool(target_crossed)
        and _same(trigger.get("stop_price"), plan.get("stop_price"))
        and _same(trigger.get("upside_price"), plan.get("exit_limit_price") or plan.get("take_profit_price"))
        and bar_open_ms is not None
        and bar_close_ms == bar_open_ms + 60_000
        and state.get("last_inspected_bar_open_ms") == bar_open_ms
        and state.get("last_evaluated_bar_open_ms") == bar_open_ms
        and state.get("next_bar_open_ms") == bar_close_ms
        and plan_effective is not None
        and bar_open_at is not None
        and bar_close_at is not None
        and plan_effective <= bar_open_at
        and (successor_effective is None or successor_effective >= bar_close_at)
    )
    if not matches:
        return {**base, "protective_trigger_reason": "protective_trigger_evidence_mismatch"}
    return {
        "protective_trigger_status": "available",
        "protective_trigger_reason": "validated_immutable_first_trigger",
        "protective_trigger_cross_evidence_status": cross_status,
        "protective_trigger": {
            "trigger_id": trigger.get("trigger_id"),
            "bar_open_ms": trigger.get("bar_open_ms"),
            "bar_close_ms": trigger.get("bar_close_ms"),
            "bar_hash": trigger.get("bar_hash"),
            "plan_version": version,
            "plan_hash": trigger.get("plan_hash"),
            "stop_hit": bool(trigger.get("stop_hit")),
            "target_hit": bool(trigger.get("target_hit")),
            "dual_hit": bool(trigger.get("stop_hit") and trigger.get("target_hit")),
            "reason": trigger.get("reason"),
            "collision_policy": "stop_first_v1",
        },
    }


def render_entry_exit_geometry_audit(report: dict[str, Any]) -> str:
    """Render honest human-readable geometry without replacing JSON values."""
    cohort = report.get("cohort_identity") or {}
    lines = [
        "Entry/Exit Geometry Audit",
        f"Window: {report.get('hours')}h | Positions: {report.get('position_count', 0)}",
        (
            "Outcome cohort: "
            f"{cohort.get('basis', 'unavailable')} | "
            f"semantics={cohort.get('outcome_semantics_version', 'unavailable')} | "
            f"eligible={report.get('cohort_eligible_count', 'unavailable')}"
        ),
        f"Cohort exclusions: {json.dumps(report.get('cohort_exclusions'), sort_keys=True)}",
    ]
    for row in report.get("rows", []):
        lines.append(f"\n{row.get('position_id')}  {row.get('symbol')}  {row.get('direction')}")
        lines.append(_human_section("Proposal geometry", row, "proposal_geometry", (
            ("R:R", "proposal_gross_risk_reward"), ("stop distance", "proposal_stop_distance"), ("target distance", "proposal_target_distance")
        )))
        lines.append(_human_section("Entry ATR", row, "entry_atr", (("stop/ATR", "stop_distance_entry_atr"), ("target/ATR", "target_distance_entry_atr"))))
        lines.append(_human_section("Costs", row, "cost_assumptions", (("round trip", "round_trip_cost"), ("break-even return", "break_even_return"), ("break-even win rate", "break_even_win_rate"))))
        lines.append(_human_section("Initial risk", row, "initial_risk", (("realized net R", "realized_net_r"),)))
        lines.append(_human_section("Rounded path ratios", row, "path_normalization", (("MFE R", "mfe_r"), ("MAE R", "mae_r"))))
        revisions = row.get("trailing_plan_revisions") or []
        lines.append(f"Revisions: {len(revisions)} ({row.get('revision_history_status')}: {row.get('revision_history_reason')})")
        for revision in revisions:
            lines.append(f"  v{revision['version']} {revision['effective_at']} stop={revision['stop_price']} target={revision['take_profit_price']} trailing={json.dumps(revision['trailing'], sort_keys=True)} hash={revision['plan_hash']}")
        trigger = row.get("protective_trigger")
        if trigger is None:
            lines.append(f"Protective trigger: unavailable ({row.get('protective_trigger_reason')})")
        else:
            lines.append(f"Protective trigger: bar={trigger['bar_open_ms']} v{trigger['plan_version']} stop_hit={trigger['stop_hit']} target_hit={trigger['target_hit']} dual_hit={trigger['dual_hit']} reason={trigger['reason']} policy={trigger['collision_policy']}")
    return "\n".join(lines)


def _human_section(label: str, row: dict[str, Any], prefix: str, fields: tuple[tuple[str, str], ...]) -> str:
    status = row.get(f"{prefix}_status")
    reason = row.get(f"{prefix}_reason")
    if status != "available":
        return f"{label}: unavailable ({reason})"
    values = ", ".join(f"{name}={row.get(key)}" for name, key in fields)
    return f"{label}: {values}"


def _read_plans(position_id: str) -> list[dict[str, Any]] | None:
    if not position_id:
        return None
    try:
        rows = repo.get_db().execute(
            "SELECT * FROM position_exit_plan_versions WHERE position_id=? ORDER BY version ASC",
            (position_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    except Exception:
        return None


def _entry_fill(position: dict[str, Any]) -> dict[str, Any] | None:
    order_id = position.get("order_id")
    if not order_id:
        return None
    try:
        rows = repo.get_db().execute(
            "SELECT * FROM fills WHERE order_id=? ORDER BY rowid ASC", (order_id,)
        ).fetchall()
    except Exception:
        return None
    return dict(rows[0]) if len(rows) == 1 else None


def _plan_payload(plan: dict[str, Any]) -> dict[str, Any] | None:
    raw = plan.get("plan_json")
    expected_hash = plan.get("plan_hash")
    if not isinstance(raw, str) or not isinstance(expected_hash, str):
        return None
    try:
        if repo.hash_position_exit_plan_json(raw) != expected_hash:
            return None
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for field in ("stop_price", "take_profit_price", "exit_limit_price", "exit_limit_source"):
        if field not in payload:
            return None
        if field == "exit_limit_source":
            if payload[field] != plan.get(field):
                return None
        elif not _same(payload[field], plan.get(field)):
            return None
    trailing = payload.get("trailing")
    if trailing is not None and not isinstance(trailing, dict):
        return None
    return payload


def _hashed_json(raw: Any, expected_hash: Any) -> Any:
    if not isinstance(raw, str) or not isinstance(expected_hash, str):
        return None
    if hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected_hash:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _safe_read(fn: Any, identity: Any) -> dict[str, Any] | None:
    if identity is None:
        return None
    try:
        value = fn(identity)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _direction(value: Any) -> str | None:
    clean = str(value or "").strip().lower()
    return "long" if clean in {"long", "buy"} else "short" if clean in {"short", "sell"} else None


def _physical_market(value: Any) -> str | None:
    return _PHYSICAL_MARKET_TYPES.get(str(value or "").strip().lower())


def _path_identity_status(row: dict[str, Any], evidence: dict[str, Any]) -> str:
    """Return aligned, independent, or mismatch for separately sourced OHLCV."""
    path_values = (
        str(row.get("ohlcv_exchange") or "").strip().lower(),
        _physical_market(row.get("ohlcv_market_type")),
        str(row.get("timeframe") or "").strip().lower(),
    )
    if not any(path_values):
        return "independent"
    evidence_values = (
        str(evidence.get("venue") or "").strip().lower(),
        _physical_market(evidence.get("market_type")),
        str(evidence.get("timeframe") or "").strip().lower(),
    )
    if not all(path_values) or not all(evidence_values) or path_values != evidence_values:
        return "mismatch"
    return "aligned"


def _barriers(direction: str, entry: float, stop: float, target: float) -> bool:
    return stop < entry < target if direction == "long" else target < entry < stop


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _nonnegative(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number >= 0 else None


def _same(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    left_number = _finite(left)
    right_number = _finite(right)
    return left_number is not None and right_number is not None and math.isclose(left_number, right_number, rel_tol=0.0, abs_tol=_ATOL)

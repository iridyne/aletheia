"""Deterministic EntryPlan to canonical Risk/Gateway Order Intent handoff."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.models import TradeProposal
from aletheia.trading.entry_plan import (
    EntryPlan,
    calculate_entry_plan_risk_reward,
    opposing_bbo_price,
)
from aletheia.trading.instruments import instrument_market_to_db_market


def _instant(raw: str) -> datetime:
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("entry_exact_state_timezone_required")
    return parsed.astimezone(timezone.utc)


def build_entry_plan_order_intent(
    plan: EntryPlan,
    *,
    bid: float,
    ask: float,
    observed_at: str,
    source_observed_at: str,
    now: datetime | None = None,
) -> TradeProposal:
    """Build one bounded immutable intent; no Risk, broker, or LLM work occurs here."""

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if _instant(plan.expires_at) <= current:
        raise ValueError("entry_plan_expired")
    freshness_ms = plan.trigger_freshness_ms or 2_000
    for timestamp in (observed_at, source_observed_at):
        age_ms = (current - _instant(timestamp)).total_seconds() * 1_000
        if age_ms < 0 or age_ms > freshness_ms:
            raise ValueError("entry_exact_state_stale")
    bid, ask = float(bid), float(ask)
    if bid <= 0 or ask < bid:
        raise ValueError("entry_exact_bbo_invalid")

    if plan.order_tactic == "POST_ONLY_LIMIT":
        price = float(plan.limit_price)
        if (plan.direction == "long" and price >= ask) or (plan.direction == "short" and price <= bid):
            raise ValueError("post_only_would_cross_bbo")
        time_in_force = "post_only"
        post_only = True
    else:
        price = opposing_bbo_price(
            direction=plan.direction,
            bid=bid,
            ask=ask,
            max_slippage_bps=float(plan.max_slippage_bps),
            worst_price=plan.worst_price,
        )
        time_in_force = "ioc"
        post_only = False

    if not plan.entry_zone[0] <= price <= plan.entry_zone[1]:
        raise ValueError("entry_price_outside_plan_zone")

    quantity = float(plan.max_notional) / price
    risk_reward = calculate_entry_plan_risk_reward(price, plan.stop_price, plan.target_price)
    if quantity <= 0 or risk_reward is None:
        raise ValueError("entry_order_intent_geometry_invalid")
    return TradeProposal(
        opportunity_id=None,
        symbol=plan.symbol,
        market_type=instrument_market_to_db_market(plan.market_type),
        direction=plan.direction,
        order_type="limit",
        quantity=quantity,
        price=price,
        leverage=plan.max_leverage,
        stop_price=plan.stop_price,
        take_profit=plan.target_price,
        risk_reward=risk_reward,
        expected_horizon_hours=plan.expected_horizon_hours,
        stop_source="operator",
        execution_mode=plan.execution_account_mode,
        agent_rationale=plan.thesis,
        attribution_snapshot=json.dumps(
            {
                "entry_plan_id": plan.plan_id,
                "planning_generation_id": plan.planning_generation_id,
                "market_received_at": observed_at,
                "market_source_at": source_observed_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        entry_plan_id=plan.plan_id,
        order_tactic=plan.order_tactic,
        time_in_force=time_in_force,
        post_only=post_only,
        bbo_reference=plan.bbo_reference,
        max_slippage_bps=plan.max_slippage_bps,
        worst_price=plan.worst_price,
    )


def execute_entry_plan(
    plan_id: str,
    *,
    bid: float,
    ask: float,
    observed_at: str,
    source_observed_at: str,
    now: datetime | None = None,
) -> dict:
    """Idempotently submit one approved plan through the existing Gateway."""

    row = repo.get_entry_plan(plan_id)
    if row is None:
        return {"status": "not_found", "entry_plan_id": plan_id}
    payload = str(row["plan_json"])
    if hashlib.sha256(payload.encode()).hexdigest() != row["plan_hash"]:
        raise ValueError("entry_plan_hash_mismatch")
    plan = EntryPlan.model_validate_json(payload)
    if plan.execution_account_mode not in {"paper_internal", "bitget_demo"}:
        raise ValueError("entry_plan_tactic_capability_unavailable")

    existing = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    if existing is not None:
        from aletheia.execution.gateway import execute_existing_proposal

        return execute_existing_proposal(existing["id"], existing["execution_mode"])

    proposal = build_entry_plan_order_intent(
        plan,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        source_observed_at=source_observed_at,
        now=now,
    )
    if plan.execution_mode == "TRIGGERED" and row["status"] == "waiting":
        if not repo.transition_entry_plan(plan.plan_id, expected_status="waiting", status="triggered", now=now):
            return {"status": "already_claimed", "entry_plan_id": plan.plan_id}
    elif plan.execution_mode == "RESTING_MAKER" and row["status"] != "waiting":
        return {"status": row["status"], "entry_plan_id": plan.plan_id}

    try:
        repo.insert_trade_proposal(proposal)
    except Exception:
        existing = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
        if existing is None:
            raise
        proposal = TradeProposal.model_validate(existing)

    from aletheia.execution.gateway import execute_existing_proposal

    return execute_existing_proposal(proposal.id, proposal.execution_mode)

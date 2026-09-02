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


_MOMENTUM_V2_TRIGGER = ("momentum-retrace-reclaim", "v2")


def _require_momentum_v2_authority(plan: EntryPlan) -> None:
    if _trigger_identity(plan) != _MOMENTUM_V2_TRIGGER:
        return
    if plan.execution_account_mode not in {"paper_internal", "bitget_demo"}:
        raise ValueError("entry_plan_tactic_capability_unavailable")
    if not plan.treatment_exposure_id:
        raise ValueError("entry_plan_treatment_exposure_required")
    from aletheia.learning.ledger import get_exposure

    exposure = get_exposure(plan.treatment_exposure_id)
    if (
        exposure is None
        or exposure.get("path_id") != "momentum_entry_geometry"
        or exposure.get("assignment") != "treatment"
        or json.loads(str(exposure.get("after_json") or "{}")).get("value") != 1.0
        or not json.loads(str(exposure.get("manifest_json") or "{}")).get("geometry_available")
    ):
        raise ValueError("entry_plan_treatment_exposure_invalid")


def _require_ai_entry_authority(plan: EntryPlan) -> None:
    if plan.version != "entry-plan/v2":
        return
    generation = repo.get_candidate_planning_generation(plan.planning_generation_id)
    if generation is None or generation.get("planner_contract_version") != "entry-planner/v3":
        raise ValueError("ai_entry_generation_contract_mismatch")
    from aletheia.trading.entry_analysis import EntryAnalysisContextV1, fingerprint

    try:
        context = EntryAnalysisContextV1.model_validate_json(generation.get("planner_input_json") or "")
    except Exception as exc:
        raise ValueError("ai_entry_context_invalid") from exc
    if (
        generation.get("planner_input_hash") != fingerprint(context.model_dump(mode="json"))
        or plan.analysis_context_fingerprint != context.context_fingerprint
        or plan.candidate_id != context.planning_generation.get("candidate_id")
        or plan.venue != context.instrument.get("venue")
        or plan.symbol != context.instrument.get("symbol")
        or plan.market_type != context.instrument.get("market_type")
        or plan.direction != context.instrument.get("direction")
    ):
        raise ValueError("ai_entry_context_identity_mismatch")
    from aletheia.config.loader import load_config

    cfg = load_config(persist_migrations=False)
    if (
        cfg.agent.permission_mode != "Paper"
        or plan.required_permission != "Paper"
        or plan.execution_account_mode != cfg.exchange.paper_execution_mode
        or cfg.exchange.paper_execution_mode not in {"paper_internal", "bitget_demo"}
        or cfg.live.state != "live_disabled"
    ):
        raise ValueError("ai_entry_runtime_authority_unavailable")


def require_entry_plan_runtime_authority(plan: EntryPlan) -> None:
    """Revalidate persisted plan lineage and global authority without creating an Order."""

    _require_momentum_v2_authority(plan)
    _require_ai_entry_authority(plan)





def _trigger_identity(plan: EntryPlan) -> tuple[str, str] | None:
    if plan.trigger_spec_id is None or plan.trigger_spec_version is None:
        return None
    return str(plan.trigger_spec_id), str(plan.trigger_spec_version)


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
    trigger_evidence: dict | None = None,
    now: datetime | None = None,
) -> TradeProposal:
    """Build one bounded immutable intent; no Risk, broker, or LLM work occurs here."""
    require_entry_plan_runtime_authority(plan)
    if plan.version == "entry-plan/v2" and plan.execution_mode == "TRIGGERED":
        if trigger_evidence is None:
            raise ValueError("ai_entry_trigger_evidence_required")
        if (
            trigger_evidence.get("trigger_spec_id") != plan.trigger_spec_id
            or trigger_evidence.get("trigger_spec_version") != plan.trigger_spec_version
            or trigger_evidence.get("geometry_fingerprint")
            != plan.trigger_params.get("geometry_fingerprint")
        ):
            raise ValueError("ai_entry_trigger_identity_mismatch")

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
        if plan.limit_price is None:
            raise ValueError("entry_plan_limit_price_required")
        price = float(plan.limit_price)
        if (plan.direction == "long" and price >= ask) or (plan.direction == "short" and price <= bid):
            raise ValueError("post_only_would_cross_bbo")
        time_in_force = "post_only"
        post_only = True
    else:
        if plan.max_slippage_bps is None or plan.worst_price is None:
            raise ValueError("entry_plan_taker_bounds_required")
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
                "treatment_exposure_id": plan.treatment_exposure_id,
                "geometry_version": plan.trigger_params.get("geometry_version"),
                "geometry_fingerprint": plan.trigger_params.get("geometry_fingerprint"),
                "planner_contract_version": "entry-planner/v3" if plan.version == "entry-plan/v2" else None,
                "entry_plan_version": plan.version,
                "entry_behavior_version": plan.behavior_version,
                "analysis_context_version": plan.analysis_context_version,
                "analysis_context_fingerprint": plan.analysis_context_fingerprint,
                "entry_hypothesis_version": plan.entry_hypothesis_version,
                "entry_hypothesis_hash": plan.entry_hypothesis_hash,
                "primary_anchor_id": (plan.primary_anchor_snapshot or {}).get("anchor_id"),
                "invalidation_anchor_id": (plan.invalidation_anchor_snapshot or {}).get("anchor_id"),
                "entry_profile_id": plan.entry_profile_id,
                "entry_profile_version": plan.entry_profile_version,
                "compiled_geometry_fingerprint": plan.compiled_geometry_fingerprint,
                "required_permission": plan.required_permission,
                "execution_account_mode": plan.execution_account_mode,
                "risk_policy_id": plan.risk_policy_id,
                "risk_policy_revision": plan.risk_policy_revision,
                "risk_policy_hash": plan.risk_policy_hash,
                "market_received_at": observed_at,
                "market_source_at": source_observed_at,
                **({"entry_trigger": dict(trigger_evidence)} if trigger_evidence is not None else {}),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
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
    trigger_evidence: dict | None = None,
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
    require_entry_plan_runtime_authority(plan)
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
        trigger_evidence=trigger_evidence,
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

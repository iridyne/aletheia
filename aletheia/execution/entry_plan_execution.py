"""Deterministic EntryPlan to canonical Risk/Gateway Order Intent handoff."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from aletheia.storage import repositories as repo
from aletheia.storage.models import TradeProposal
from aletheia.trading.entry_compat import decode_entry_plan
from aletheia.trading.entry_plan import (
    EntryPlan,
    calculate_entry_plan_risk_reward,
    opposing_bbo_price,
    post_only_price_is_passive,
)
from aletheia.trading.instruments import instrument_market_to_db_market


_RECEIPT_VERSION = "entry-execution-receipt/v1"
_RECEIPT_CHANNELS = frozenset({"quote", "trade", "depth", "candle"})


class EntryExecutionReceiptV1(BaseModel):
    """Bounded, expiring market evidence for one pre-Order attempt."""

    model_config = ConfigDict(frozen=True)

    version: Literal["entry-execution-receipt/v1"] = _RECEIPT_VERSION
    attempt_id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    entry_plan_id: str = Field(min_length=1)
    instrument: dict[str, str]
    stream_generation_id: str = Field(min_length=1)
    subscription_generation_id: str = Field(min_length=1)
    required_channels: tuple[Literal["quote", "trade", "depth", "candle"], ...]
    quote: dict[str, object]
    depth: dict[str, object] | None = None
    trigger_activation_id: str | None = None
    trigger_evidence_hash: str | None = None
    captured_at: str
    expires_at: str
    receipt_hash: str

    def unsigned_payload(self) -> dict:
        return self.model_dump(exclude={"receipt_hash"}, mode="json")

    def canonical_json(self) -> str:
        return json.dumps(
            self.unsigned_payload(),
            sort_keys=True,
            separators=(",", ":"),
        )

    def hash_is_valid(self) -> bool:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest() == self.receipt_hash


def _receipt_timestamp_ms(value: str) -> int:
    return int(_instant(value).timestamp() * 1_000)


def _bounded_depth(
    levels: tuple[tuple[float, float], ...] | list[tuple[float, float]] | None,
) -> list[list[float]]:
    bounded: list[list[float]] = []
    for raw_price, raw_size in tuple(levels or ())[:5]:
        price, size = float(raw_price), float(raw_size)
        if not math.isfinite(price) or price <= 0 or not math.isfinite(size) or size < 0:
            raise ValueError("entry_execution_receipt_depth_invalid")
        bounded.append([price, size])
    return bounded


def build_entry_execution_receipt(
    plan: EntryPlan,
    proposal: TradeProposal,
    *,
    bid: float,
    ask: float,
    observed_at: str,
    source_observed_at: str,
    stream_generation_id: str,
    subscription_generation_id: str,
    quote_sequence: int,
    required_channels: tuple[str, ...],
    depth_bids: tuple[tuple[float, float], ...] | list[tuple[float, float]] | None = None,
    depth_asks: tuple[tuple[float, float], ...] | list[tuple[float, float]] | None = None,
    depth_exchange_timestamp_ms: int | None = None,
    depth_received_timestamp_ms: int | None = None,
    depth_sequence: int | None = None,
    trigger_activation_id: str | None = None,
    trigger_activation_hash: str | None = None,
) -> EntryExecutionReceiptV1:
    """Bind exact runtime evidence to one immutable Proposal without mutating it."""

    from aletheia.trading.entry_activation import require_entry_trigger_activation

    activation = require_entry_trigger_activation(
        plan, trigger_activation_id, trigger_activation_hash, now=datetime.now(timezone.utc),
        stream_generation_id=stream_generation_id, subscription_generation_id=subscription_generation_id,
        quote_sequence=quote_sequence, entry_leg_index=proposal.entry_leg_index or 0,
    )
    if not proposal.post_only:
        required_channels = (*required_channels, "depth")
    channels = tuple(dict.fromkeys(str(item) for item in required_channels))
    if not channels or any(item not in _RECEIPT_CHANNELS for item in channels):
        raise ValueError("entry_execution_receipt_channels_invalid")
    if not str(stream_generation_id).strip() or not str(subscription_generation_id).strip():
        raise ValueError("entry_execution_receipt_generation_required")
    if isinstance(quote_sequence, bool) or int(quote_sequence) < 0:
        raise ValueError("entry_execution_receipt_quote_sequence_invalid")
    bid_value, ask_value = float(bid), float(ask)
    if (
        not math.isfinite(bid_value)
        or not math.isfinite(ask_value)
        or bid_value <= 0
        or ask_value < bid_value
    ):
        raise ValueError("entry_execution_receipt_quote_invalid")

    bids, asks = _bounded_depth(depth_bids), _bounded_depth(depth_asks)
    depth_required = "depth" in channels
    if depth_required and (
        not bids
        or not asks
        or depth_exchange_timestamp_ms is None
        or depth_received_timestamp_ms is None
        or depth_sequence is None
    ):
        raise ValueError("entry_execution_receipt_depth_required")
    if depth_sequence is not None and (isinstance(depth_sequence, bool) or int(depth_sequence) < 0):
        raise ValueError("entry_execution_receipt_depth_sequence_invalid")

    if depth_required:
        from aletheia.universe.fast_stream import _CHANNEL_FRESHNESS_MS
        now_ms = datetime.now(timezone.utc).timestamp() * 1000
        if any(not 0 <= now_ms - timestamp <= _CHANNEL_FRESHNESS_MS["depth"]
               for timestamp in (depth_exchange_timestamp_ms, depth_received_timestamp_ms)):
            raise ValueError("entry_execution_receipt_depth_stale")
    captured = _instant(observed_at)
    trigger_hash = activation.activation_hash if activation else None
    payload = {
        "version": _RECEIPT_VERSION,
        "attempt_id": uuid4().hex,
        "proposal_id": proposal.id,
        "entry_plan_id": plan.plan_id,
        "instrument": {
            "venue": plan.venue,
            "symbol": plan.symbol,
            "market_type": plan.market_type,
        },
        "stream_generation_id": str(stream_generation_id),
        "subscription_generation_id": str(subscription_generation_id),
        "required_channels": channels,
        "quote": {
            "bid": bid_value,
            "ask": ask_value,
            "exchange_timestamp_ms": _receipt_timestamp_ms(source_observed_at),
            "received_timestamp_ms": _receipt_timestamp_ms(observed_at),
            "sequence": int(quote_sequence),
        },
        "depth": (
            {
                "bids": bids,
                "asks": asks,
                "exchange_timestamp_ms": int(depth_exchange_timestamp_ms),
                "received_timestamp_ms": int(depth_received_timestamp_ms),
                "sequence": int(depth_sequence),
            }
            if bids or asks or depth_required
            else None
        ),
        "trigger_activation_id": activation.activation_id if activation else None,
        "trigger_evidence_hash": trigger_hash,
        "captured_at": captured.isoformat(),
        "expires_at": activation.expires_at if activation and proposal.entry_leg_index != 1 else plan.expires_at,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return EntryExecutionReceiptV1(
        **payload,
        receipt_hash=hashlib.sha256(canonical.encode()).hexdigest(),
    )


def _require_ai_entry_authority(plan: EntryPlan) -> None:
    from aletheia.trading.entry_contract import entry_contract_fingerprints
    from aletheia.trading.entry_compat import historical_plan_version

    if historical_plan_version(plan) is not None or (
        plan.behavior_fingerprint, plan.schema_fingerprint
    ) != entry_contract_fingerprints():
        raise ValueError("ai_entry_running_contract_mismatch")
    if not plan.capture_policy_id:
        raise ValueError("entry_capture_policy_required")
    if plan.entry_hypothesis_json is None or plan.source_kind is None:
        raise ValueError("ai_entry_plan_provenance_required")
    generation = repo.get_candidate_planning_generation(plan.planning_generation_id)
    if (
        generation is None
        or not bool(generation.get("is_current", 1))
        or not generation.get("entry_behavior_fingerprint")
        or not generation.get("schema_fingerprint")
    ):
        raise ValueError("ai_entry_generation_contract_mismatch")
    if (
        generation["entry_behavior_fingerprint"], generation["schema_fingerprint"]
    ) != entry_contract_fingerprints():
        raise ValueError("ai_entry_running_contract_mismatch")
    from aletheia.trading.entry_analysis import EntryAnalysisContext, fingerprint

    try:
        context = EntryAnalysisContext.model_validate_json(generation.get("planner_input_json") or "")
    except Exception as exc:
        raise ValueError("ai_entry_context_invalid") from exc
    if (
        generation.get("planner_input_hash") != fingerprint(context.model_dump(mode="json"))
        or context.planning_generation.get("entry_behavior_fingerprint")
        != generation.get("entry_behavior_fingerprint")
        or context.planning_generation.get("schema_fingerprint")
        != generation.get("schema_fingerprint")
        or plan.analysis_context_fingerprint != context.context_fingerprint
        or plan.candidate_id != context.planning_generation.get("candidate_id")
        or plan.venue != context.instrument.get("venue")
        or plan.symbol != context.instrument.get("symbol")
        or plan.market_type != context.instrument.get("market_type")
        or plan.direction != context.instrument.get("direction")
    ):
        raise ValueError("ai_entry_context_identity_mismatch")
    candidate = repo.get_candidate(str(plan.candidate_id))
    opportunity = context.candidate_opportunity_context
    source = opportunity.get("source", {})
    if (plan.source_kind, plan.source_id, plan.trade_school) != (
        source.get("kind"), source.get("id"), source.get("trade_school"),
    ):
        raise ValueError("ai_entry_source_identity_mismatch")
    if opportunity.get("opportunity", {}).get("capability_status") != "supported":
        raise ValueError("ai_entry_candidate_capability_unavailable")
    if (
        candidate is None
        or candidate.get("candidate_opportunity_context_hash") != opportunity.get("context_hash")
        or candidate.get("opportunity_mechanism") != generation.get("opportunity_mechanism")
        or candidate.get("candidate_selection_policy") != generation.get("candidate_selection_policy")
        or candidate.get("candidate_selection_policy_epoch") != generation.get("candidate_selection_policy_epoch")
        or opportunity.get("opportunity", {}).get("mechanism_version") != generation.get("mechanism_version")
    ):
        raise ValueError("ai_entry_candidate_authority_mismatch")
    from aletheia.config.loader import load_config

    cfg = load_config(persist_migrations=False)
    from aletheia.trading.entry_authority import require_entry_runtime_authority

    try:
        require_entry_runtime_authority(
            cfg,
            execution_route=plan.execution_account_mode,
            required_permission=plan.required_permission,
        )
    except ValueError as exc:
        raise ValueError(f"ai_entry_runtime_authority_unavailable:{exc}") from exc


def require_entry_plan_runtime_authority(plan: EntryPlan) -> None:
    """Revalidate persisted plan lineage and global authority without creating an Order."""

    _require_ai_entry_authority(plan)


def _instant(raw: str) -> datetime:
    parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("entry_exact_state_timezone_required")
    return parsed.astimezone(timezone.utc)



def bounded_entry_ioc_price(plan: EntryPlan, *, bid: float, ask: float) -> float:
    """A frozen limit; changing BBO is only a current eligibility reference."""
    if plan.max_slippage_bps is None or plan.worst_price is None:
        raise ValueError("entry_plan_taker_bounds_required")
    reference = opposing_bbo_price(direction=plan.direction, bid=bid, ask=ask,
        max_slippage_bps=plan.max_slippage_bps, worst_price=plan.worst_price)
    limit = plan.ioc_limit_price
    long = plan.direction == "long"
    if (not plan.entry_zone[0] <= reference <= plan.entry_zone[1]
        or (reference > limit if long else reference < limit)):
        raise ValueError("entry_ioc_outside_frozen_bounds")
    return limit


def build_entry_plan_order_intent(
    plan: EntryPlan,
    *,
    bid: float,
    ask: float,
    observed_at: str,
    source_observed_at: str,
    trigger_activation_id: str | None = None,
    trigger_activation_hash: str | None = None,
    now: datetime | None = None,
    entry_leg_index: int = 0,
    order_tactic: str | None = None,
    quantity: float | None = None,
) -> TradeProposal:
    """Build one bounded immutable intent; no Risk, broker, or LLM work occurs here."""
    require_entry_plan_runtime_authority(plan)
    from aletheia.trading.entry_activation import require_entry_trigger_activation

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    activation = require_entry_trigger_activation(plan, trigger_activation_id, trigger_activation_hash, now=current, entry_leg_index=entry_leg_index)
    if _instant(plan.expires_at) <= current:
        raise ValueError("entry_plan_expired")
    from aletheia.universe.fast_stream import _CHANNEL_FRESHNESS_MS
    freshness_ms = _CHANNEL_FRESHNESS_MS["quote"]
    for timestamp in (observed_at, source_observed_at):
        age_ms = (current - _instant(timestamp)).total_seconds() * 1_000
        if age_ms < 0 or (freshness_ms is not None and age_ms > freshness_ms):
            raise ValueError("entry_exact_state_stale")
    bid, ask = float(bid), float(ask)
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
        raise ValueError("entry_exact_bbo_invalid")

    tactic = order_tactic or plan.order_tactic
    if tactic == "POST_ONLY_LIMIT":
        if plan.limit_price is None:
            raise ValueError("entry_plan_limit_price_required")
        price = float(plan.limit_price)
        if not post_only_price_is_passive(plan.direction, price, bid, ask):
            raise ValueError("post_only_would_cross_bbo")
        time_in_force = "post_only"
        post_only = True
    else:
        if plan.max_slippage_bps is None or plan.worst_price is None:
            raise ValueError("entry_plan_taker_bounds_required")
        price = bounded_entry_ioc_price(plan, bid=bid, ask=ask)
        time_in_force = "ioc"
        post_only = False

    if not plan.entry_zone[0] <= price <= plan.entry_zone[1]:
        raise ValueError("entry_price_outside_plan_zone")

    if quantity is None:
        quantity = plan.target_quantity
    risk_reward = calculate_entry_plan_risk_reward(price, plan.stop_price, plan.target_price)
    if quantity <= 0 or risk_reward is None:
        raise ValueError("entry_order_intent_geometry_invalid")
    return TradeProposal(
        opportunity_id=plan.source_id if plan.source_kind == "serenity" else None,
        trade_school=plan.trade_school,
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
        expected_horizon_hours=plan.holding_horizon_hours,
        stop_source="operator",
        execution_mode=plan.execution_account_mode,
        agent_rationale=plan.thesis,
        attribution_snapshot=json.dumps(
            {
                "entry_plan_id": plan.plan_id,
                "source_kind": plan.source_kind,
                "source_id": plan.source_id,
                "trade_school": plan.trade_school,
                "planning_generation_id": plan.planning_generation_id,
                "treatment_exposure_id": plan.treatment_exposure_id,
                "geometry_version": plan.trigger_params.get("geometry_version"),
                "geometry_fingerprint": plan.trigger_params.get("geometry_fingerprint"),
                "entry_behavior_fingerprint": plan.behavior_fingerprint,
                "analysis_context_fingerprint": plan.analysis_context_fingerprint,
                "entry_hypothesis_hash": plan.entry_hypothesis_hash,
                "primary_anchor_id": (plan.primary_anchor_snapshot or {}).get("anchor_id"),
                "invalidation_anchor_id": (plan.invalidation_anchor_snapshot or {}).get("anchor_id"),
                "entry_profile_id": plan.entry_profile_id,
                "compiled_geometry_fingerprint": plan.compiled_geometry_fingerprint,
                "required_permission": plan.required_permission,
                "execution_account_mode": plan.execution_account_mode,
                "risk_policy_id": plan.risk_policy_id,
                "risk_policy_revision": plan.risk_policy_revision,
                "risk_policy_hash": plan.risk_policy_hash,
                "market_received_at": observed_at,
                "market_source_at": source_observed_at,
                **({"entry_trigger_activation_id": activation.activation_id,
                    "entry_trigger_activation_hash": activation.activation_hash} if activation else {}),
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        entry_plan_id=plan.plan_id,
        entry_leg_index=entry_leg_index,
        order_tactic=tactic,
        time_in_force=time_in_force,
        post_only=post_only,
        bbo_reference=None if post_only else ("ask" if plan.direction == "long" else "bid"),
        max_slippage_bps=plan.max_slippage_bps,
        worst_price=plan.worst_price,
    )


def require_execution_receipt_activation(proposal: TradeProposal, receipt, *, now: datetime) -> None:
    """Recheck current Plan/quote/activation at claims and before the broker."""
    from aletheia.trading.entry_activation import require_entry_trigger_activation

    row = repo.get_entry_plan(proposal.entry_plan_id)
    if row is None:
        raise ValueError("entry_plan_not_found")
    if hashlib.sha256(row["plan_json"].encode()).hexdigest() != row["plan_hash"]:
        raise ValueError("entry_plan_hash_mismatch")
    plan = decode_entry_plan(row["plan_json"])
    if receipt is None:
        raise ValueError("entry_execution_receipt_required")
    if (not receipt.hash_is_valid() or receipt.proposal_id != proposal.id
        or receipt.entry_plan_id != plan.plan_id):
        raise ValueError("entry_execution_receipt_integrity_failed")
    if _instant(plan.expires_at) <= now:
        raise ValueError("entry_plan_expired")
    intent = build_entry_plan_order_intent(plan, bid=receipt.quote["bid"], ask=receipt.quote["ask"],
        observed_at=datetime.fromtimestamp(receipt.quote["received_timestamp_ms"] / 1000, timezone.utc).isoformat(),
        source_observed_at=datetime.fromtimestamp(receipt.quote["exchange_timestamp_ms"] / 1000, timezone.utc).isoformat(),
        trigger_activation_id=receipt.trigger_activation_id, trigger_activation_hash=receipt.trigger_evidence_hash,
        now=_instant(receipt.captured_at), entry_leg_index=proposal.entry_leg_index or 0, order_tactic=proposal.order_tactic, quantity=proposal.quantity)
    if intent.price != proposal.price:
        raise ValueError("entry_execution_receipt_intent_price_mismatch")
    require_entry_trigger_activation(plan,
        receipt.trigger_activation_id, receipt.trigger_evidence_hash, now=now,
        stream_generation_id=receipt.stream_generation_id,
        subscription_generation_id=receipt.subscription_generation_id,
        quote_sequence=receipt.quote.get("sequence"), entry_leg_index=proposal.entry_leg_index or 0)


def _capture_facts(plan: EntryPlan, *, bid: float | None, ask: float | None, now: datetime):
    """One coherent read of existing Plan/Proposal/Order/Fill owners."""
    from aletheia.trading.entry_activation import require_entry_trigger_activation
    from aletheia.trading.entry_execution_projection import EntryExecutionFacts

    with repo.transaction():
        row = repo.get_entry_plan(plan.plan_id)
        proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
        orders = [dict(item) for item in repo.get_db().execute(
            "SELECT o.*, COALESCE(p.entry_leg_index,0) AS entry_leg_index FROM orders o "
            "LEFT JOIN trade_proposals p ON p.id=o.proposal_id "
            "WHERE o.entry_plan_id=? AND o.close_reason IS NULL ORDER BY entry_leg_index, o.created_at, o.id",
            (plan.plan_id,),
        ).fetchall()]
        latest = orders[-1] if orders else {}
        filled = 0.0
        authority_error = "entry_plan_expired" if _instant(plan.expires_at) <= now else None
        for order in orders:
            fills = repo.list_fills(order_id=order["id"], limit=None)
            quantity = sum(float(item["fill_quantity"]) for item in fills)
            if (any(not math.isfinite(float(item[key])) or float(item[key]) <= 0
                    for item in fills for key in ("fill_quantity", "fill_price"))
                or quantity > float(order["quantity"]) + 1e-12
                or (order["status"] == "filled" and not math.isclose(quantity, float(order["quantity"]), rel_tol=1e-9, abs_tol=1e-12))
                or (order["status"] in {"partial", "partially_filled"} and quantity == 0)):
                filled = None
                break
            if quantity:
                position = repo.get_position(order.get("position_id")) if order.get("position_id") else None
                if (position is None or not position.get("entry_fill_verified") or position.get("entry_price_source") != "broker_fill"
                    or any(position[key] != order[key] for key in ("symbol", "market_type", "direction", "execution_mode"))):
                    filled = None
                    break
                if position["status"] == "closed" or position.get("pending_close_order_id"):
                    authority_error = "entry_position_closing_or_closed"
            filled += quantity
        activation_current = plan.activation_policy == "IMMEDIATE"
        if not orders and not activation_current and row.get("trigger_activation_id"):
            activation = repo.get_entry_trigger_activation(row["trigger_activation_id"])
            if activation:
                try:
                    require_entry_trigger_activation(plan, row["trigger_activation_id"], activation["activation_hash"], now=now)
                    activation_current = True
                except ValueError as exc:
                    if str(exc) not in {"entry_trigger_activation_expired", "entry_trigger_activation_monitoring_mismatch",
                                        "entry_trigger_activation_required", "entry_trigger_activation_plan_not_executable"}:
                        authority_error = str(exc)
        claim = repo.get_proposal_execution_claim(proposal["id"]) if proposal else None
        passive = bid is not None and ask is not None and plan.limit_price is not None and post_only_price_is_passive(plan.direction, plan.limit_price, bid, ask)
        try:
            if bid is None or ask is None:
                raise ValueError("entry_market_unavailable")
            bounded_entry_ioc_price(plan, bid=bid, ask=ask)
            ioc = True
        except ValueError:
            ioc = False
        return EntryExecutionFacts(plan_status=row["status"], authority_error=authority_error,
            activation_current=activation_current, filled_quantity=filled,
            preparation_claim_held=bool(claim and repo.proposal_execution_claim_is_fresh(claim, now)),
            proposal_status=proposal["status"] if proposal else None,
            proposal_leg_index=(proposal.get("entry_leg_index") or 0) if proposal else None,
            proposal_tactic=proposal.get("order_tactic") if proposal else None,
            proposal_has_order=bool(proposal and any(order["proposal_id"] == proposal["id"] for order in orders)),
            order_status=latest.get("status"), order_leg_index=latest.get("entry_leg_index", 0),
            order_tactic=latest.get("order_tactic"),
            order_age_sec=(now-_instant(latest["created_at"])).total_seconds() if latest else None,
            cancellation_unresolved=any(str(order.get("error_reason") or "").startswith("entry_cancel_pending:") for order in orders),
            passive_eligible=passive, ioc_eligible=ioc)


def execute_entry_plan(
    plan_id: str,
    *,
    bid: float,
    ask: float,
    observed_at: str,
    source_observed_at: str,
    stream_generation_id: str,
    subscription_generation_id: str,
    quote_sequence: int,
    required_channels: tuple[str, ...] = ("quote",),
    depth_bids: tuple[tuple[float, float], ...] | None = None,
    depth_asks: tuple[tuple[float, float], ...] | None = None,
    depth_exchange_timestamp_ms: int | None = None,
    depth_received_timestamp_ms: int | None = None,
    depth_sequence: int | None = None,
    trigger_activation_id: str | None = None,
    trigger_activation_hash: str | None = None,
    now: datetime | None = None,
    current_market=None,
) -> dict:
    """Idempotently submit one approved plan through the existing Gateway."""

    row = repo.get_entry_plan(plan_id)
    if row is None:
        return {"status": "not_found", "entry_plan_id": plan_id}
    payload = str(row["plan_json"])
    if hashlib.sha256(payload.encode()).hexdigest() != row["plan_hash"]:
        raise ValueError("entry_plan_hash_mismatch")
    plan = decode_entry_plan(payload)
    require_entry_plan_runtime_authority(plan)
    if plan.execution_account_mode != "bitget_demo":
        raise ValueError("entry_plan_tactic_capability_unavailable")

    from aletheia.trading.entry_execution_projection import resolve_entry_execution
    from aletheia.execution.gateway import cancel_entry_plan_order, execute_existing_proposal
    current = now or datetime.now(timezone.utc)
    state = _capture_facts(plan, bid=bid, ask=ask, now=current)
    directive = resolve_entry_execution(plan, state)
    if directive.action == "WAIT":
        return {"status": "waiting_market", "reason_code": directive.reason, "entry_plan_id": plan.plan_id}
    if directive.action == "RECONCILE":
        latest = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
        if state.cancellation_unresolved:
            return cancel_entry_plan_order(plan.plan_id, reason="entry_cancel_reconciliation")
        if latest and latest["status"] == "executing":
            execute_existing_proposal(latest["id"], latest["execution_mode"])
        return {"status": "reconciliation_required", "reason_code": directive.reason, "entry_plan_id": plan.plan_id}
    if directive.action in {"STOP", "DONE"}:
        terminal = ("filled" if directive.reason == "entry_target_filled" else "cancelled" if directive.action == "DONE"
                    else "expired" if directive.reason == "entry_plan_expired" else "failed")
        if row["status"] in {"waiting", "active"}:
            repo.transition_entry_plan(plan.plan_id, expected_status=row["status"], status=terminal, reason=directive.reason, now=current)
        return {"status": terminal, "reason_code": directive.reason, "entry_plan_id": plan.plan_id}
    if directive.command == "CANCEL_PASSIVE":
        return cancel_entry_plan_order(plan.plan_id, reason=directive.reason)
    leg_index = directive.leg_index
    quantity = directive.quantity
    tactic = "POST_ONLY_LIMIT" if directive.command == "SUBMIT_PASSIVE" else "OPPOSING_BBO_IOC"
    if trigger_activation_id is None and row.get("trigger_activation_id"):
        activation = repo.get_entry_trigger_activation(row["trigger_activation_id"])
        trigger_activation_id = row["trigger_activation_id"]
        trigger_activation_hash = activation["activation_hash"] if activation else None

    existing = repo.get_trade_proposal_by_entry_plan(plan.plan_id, leg_index=leg_index)
    if existing is not None:
        from aletheia.execution.gateway import execute_existing_proposal

        proposal = TradeProposal.model_validate(existing)
        if proposal.status not in {"pending", "approved"}:
            # Observing/reconciling an existing handoff must not earn a new
            # trigger. Gateway owns these states and never resubmits them.
            return execute_existing_proposal(proposal.id, proposal.execution_mode)
        if trigger_activation_id is None:
            attribution = json.loads(proposal.attribution_snapshot or "{}")
            trigger_activation_id = attribution.get("entry_trigger_activation_id")
            trigger_activation_hash = attribution.get("entry_trigger_activation_hash")
        receipt = build_entry_execution_receipt(
            plan,
            proposal,
            bid=bid,
            ask=ask,
            observed_at=observed_at,
            source_observed_at=source_observed_at,
            stream_generation_id=stream_generation_id,
            subscription_generation_id=subscription_generation_id,
            quote_sequence=quote_sequence,
            required_channels=required_channels,
            depth_bids=depth_bids,
            depth_asks=depth_asks,
            depth_exchange_timestamp_ms=depth_exchange_timestamp_ms,
            depth_received_timestamp_ms=depth_received_timestamp_ms,
            depth_sequence=depth_sequence,
            trigger_activation_id=trigger_activation_id,
            trigger_activation_hash=trigger_activation_hash,
        )
        return execute_existing_proposal(
            existing["id"], existing["execution_mode"], execution_receipt=receipt, current_market=current_market
        )

    proposal = build_entry_plan_order_intent(
        plan,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        source_observed_at=source_observed_at,
        trigger_activation_id=trigger_activation_id,
        trigger_activation_hash=trigger_activation_hash,
        now=now, entry_leg_index=leg_index, order_tactic=tactic, quantity=quantity,
    )
    receipt = build_entry_execution_receipt(
        plan,
        proposal,
        bid=bid,
        ask=ask,
        observed_at=observed_at,
        source_observed_at=source_observed_at,
        stream_generation_id=stream_generation_id,
        subscription_generation_id=subscription_generation_id,
        quote_sequence=quote_sequence,
        required_channels=required_channels,
        depth_bids=depth_bids,
        depth_asks=depth_asks,
        depth_exchange_timestamp_ms=depth_exchange_timestamp_ms,
        depth_received_timestamp_ms=depth_received_timestamp_ms,
        depth_sequence=depth_sequence,
        trigger_activation_id=trigger_activation_id,
        trigger_activation_hash=trigger_activation_hash,
    )
    if row["status"] != "active":
        return {"status": row["status"], "entry_plan_id": plan.plan_id}

    try:
        repo.insert_trade_proposal(proposal)
    except sqlite3.IntegrityError as exc:
        if str(exc) != "UNIQUE constraint failed: index 'idx_trade_proposals_entry_plan_leg'":
            raise
        existing = repo.get_trade_proposal_by_entry_plan(plan.plan_id, leg_index=leg_index)
        if existing is None:
            raise
        proposal = TradeProposal.model_validate(existing)
        receipt = receipt.model_copy(update={"proposal_id": proposal.id})
        receipt = receipt.model_copy(update={
            "receipt_hash": hashlib.sha256(receipt.canonical_json().encode()).hexdigest(),
        })

    from aletheia.execution.gateway import execute_existing_proposal

    return execute_existing_proposal(
        proposal.id,
        proposal.execution_mode,
        execution_receipt=receipt,
        current_market=current_market,
    )


class EntryMarketWaiting(ValueError):
    """Temporary pre-broker ineligibility; never an external submission result."""


def require_current_submission_market(proposal, receipt, current_market, *, now):
    """Read the watcher's current exact quote without changing immutable intent."""
    require_execution_receipt_activation(proposal, receipt, now=now)
    if current_market is None:
        raise EntryMarketWaiting("entry_submission_market_unavailable")
    event, monitoring = current_market()
    if event is None or monitoring.get("state") != "ready":
        raise EntryMarketWaiting("entry_submission_market_unavailable")
    from aletheia.data.symbols import InstrumentId
    plan = decode_entry_plan(repo.get_entry_plan(proposal.entry_plan_id)["plan_json"])
    if event.instrument != InstrumentId.from_parts(plan.venue, plan.symbol, plan.market_type):
        raise ValueError("entry_submission_market_identity_mismatch")
    if (event.stream_generation_id != receipt.stream_generation_id
        or event.subscription_generation_id != receipt.subscription_generation_id):
        raise EntryMarketWaiting("entry_submission_market_generation_changed")
    if event.sequence < receipt.quote["sequence"]:
        raise EntryMarketWaiting("entry_submission_market_stale")
    # Monitoring owns current quote freshness; Trigger TTL never supplies it.
    from aletheia.universe.fast_stream import _CHANNEL_FRESHNESS_MS
    for timestamp in (event.exchange_timestamp_ms, event.received_timestamp_ms):
        age = now.timestamp() * 1000 - timestamp
        if not 0 <= age <= _CHANNEL_FRESHNESS_MS["quote"]:
            raise EntryMarketWaiting("entry_submission_market_stale")
    bid, ask = float(event.bid), float(event.ask)
    if not math.isfinite(bid) or not math.isfinite(ask) or bid <= 0 or ask < bid:
        raise EntryMarketWaiting("entry_submission_market_invalid")
    if ((plan.direction == "long" and bid <= plan.invalidation_price)
        or (plan.direction == "short" and ask >= plan.invalidation_price)):
        raise ValueError("entry_plan_price_invalidated")
    if proposal.post_only and not post_only_price_is_passive(plan.direction, proposal.price, bid, ask):
        raise EntryMarketWaiting("post_only_would_cross_bbo")
    if not proposal.post_only:
        if not receipt.depth or any(not 0 <= now.timestamp()*1000 - receipt.depth[clock] <= _CHANNEL_FRESHNESS_MS["depth"]
                                    for clock in ("exchange_timestamp_ms", "received_timestamp_ms")):
            raise EntryMarketWaiting("entry_submission_depth_stale")
        try:
            price = bounded_entry_ioc_price(plan, bid=bid, ask=ask)
        except ValueError as exc:
            if str(exc) in {"entry_ioc_outside_frozen_bounds", "bbo_slippage_envelope_exceeded", "bbo_worst_price_exceeded"}:
                raise EntryMarketWaiting(str(exc)) from exc
            raise
        if price != proposal.price:
            raise ValueError("entry_execution_receipt_intent_price_mismatch")
    return event

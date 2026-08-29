"""On-demand System Operator planning over durable Candidate generations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from aletheia.storage import repositories as repo
from aletheia.trading.entry_plan import EntryPlan
from aletheia.trading.trigger_specs import (
    registered_trigger_specs,
    require_registered_trigger_spec,
)


class EntryPlanDraft(BaseModel):
    result: Literal["ENTRY_PLAN", "NO_PLAN"]
    reasoning: str = Field(min_length=1, max_length=4_000)
    direction: Literal["long", "short"] | None = None
    thesis: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    scenario: str | None = None
    execution_mode: Literal["RESTING_MAKER", "TRIGGERED"] | None = None
    trigger_spec_id: str | None = None
    trigger_spec_version: str | None = None
    trigger_params: dict = Field(default_factory=dict)
    entry_zone: tuple[float, float] | None = None
    invalidation_conditions: list[str] = Field(default_factory=list)
    invalidation_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    expected_horizon_hours: float | None = None
    plan_lifetime_minutes: int | None = Field(default=None, ge=1, le=1_440)
    order_tactic: Literal["POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"] | None = None
    limit_price: float | None = None
    max_wait_sec: int = Field(default=0, ge=0, le=86_400)
    taker_fallback_allowed: Literal[False] = False
    max_slippage_bps: float | None = None
    worst_price: float | None = None
    max_notional: float | None = None
    max_risk_pct: float | None = None
    max_leverage: int | None = None


PlannerFn = Callable[[dict], Awaitable[EntryPlanDraft | dict | None]]

ENTRY_PLANNER_MIN_CONCURRENCY = 3
ENTRY_PLANNER_MAX_CONCURRENCY = 15


class PlannerBackpressure(RuntimeError):
    """Local broker admission refusal; no physical model attempt was consumed."""


def _candidate_context(generation: dict) -> dict:
    candidate = repo.get_candidate(generation["last_candidate_id"])
    if candidate is None:
        raise ValueError("candidate_not_found")
    feature = repo.get_latest_feature_snapshot(candidate["asset_id"]) or {}
    return {
        "planning_generation": {
            "id": generation["id"],
            "identity_version": generation["identity_version"],
        },
        "candidate": candidate,
        "latest_feature_snapshot": feature,
        "registered_trigger_specs": [
            {
                "id": spec.spec_id,
                "version": spec.version,
                "data_requirements": spec.data_requirements,
                "freshness_ms": spec.freshness_ms,
            }
            for spec in registered_trigger_specs()
        ],
    }


def _build_plan(generation: dict, draft: EntryPlanDraft, cfg) -> EntryPlan:
    if draft.result != "ENTRY_PLAN":
        raise ValueError("entry_plan_draft_required")
    required = {
        "direction": draft.direction,
        "thesis": draft.thesis,
        "scenario": draft.scenario,
        "execution_mode": draft.execution_mode,
        "entry_zone": draft.entry_zone,
        "invalidation_price": draft.invalidation_price,
        "stop_price": draft.stop_price,
        "target_price": draft.target_price,
        "expected_horizon_hours": draft.expected_horizon_hours,
        "plan_lifetime_minutes": draft.plan_lifetime_minutes,
        "order_tactic": draft.order_tactic,
        "max_notional": draft.max_notional,
        "max_risk_pct": draft.max_risk_pct,
        "max_leverage": draft.max_leverage,
    }
    missing = [key for key, value in required.items() if value is None]
    if missing:
        raise ValueError("entry_plan_draft_incomplete:" + ",".join(missing))
    if draft.direction != generation["direction"]:
        raise ValueError("entry_plan_direction_mismatch")

    trigger_requirements: tuple[str, ...] = ()
    trigger_freshness_ms = None
    trigger_params: dict = {}
    if draft.execution_mode == "TRIGGERED":
        contract, trigger_params = require_registered_trigger_spec(
            str(draft.trigger_spec_id or ""),
            str(draft.trigger_spec_version or ""),
            draft.trigger_params,
        )
        trigger_requirements = contract.data_requirements
        trigger_freshness_ms = contract.freshness_ms

    from aletheia.risk.policy import resolve_risk_policy

    execution_account_mode = str(cfg.exchange.paper_execution_mode)
    if execution_account_mode != "bitget_demo":
        raise ValueError("entry_plan_bitget_demo_required")
    resolved_policy = resolve_risk_policy(cfg, execution_account_mode)
    policy = resolved_policy.policy
    if float(draft.max_risk_pct) > float(policy.loss.max_single_trade_risk_pct):
        raise ValueError("entry_plan_exceeds_risk_policy")
    from aletheia.trading.instruments import get_max_leverage

    leverage_cap = get_max_leverage(generation["symbol"], False, cfg)
    if int(draft.max_leverage) > leverage_cap:
        raise ValueError("entry_plan_exceeds_leverage_policy")
    if float(draft.max_notional) > (float(policy.capital.max_position_margin_usdt) * int(draft.max_leverage)):
        raise ValueError("entry_plan_exceeds_notional_policy")
    current = datetime.now(timezone.utc)
    expires = current + timedelta(minutes=int(draft.plan_lifetime_minutes))
    maker = draft.order_tactic == "POST_ONLY_LIMIT"
    return EntryPlan(
        planning_generation_id=generation["id"],
        candidate_id=generation["last_candidate_id"],
        candidate_family=generation["candidate_family"],
        venue=generation["venue"],
        symbol=generation["symbol"],
        market_type=generation["market_type"],
        direction=draft.direction,
        thesis=str(draft.thesis),
        evidence_refs=tuple(draft.evidence_refs),
        scenario=str(draft.scenario),
        execution_mode=draft.execution_mode,
        trigger_spec_id=draft.trigger_spec_id,
        trigger_spec_version=draft.trigger_spec_version,
        trigger_params=trigger_params,
        trigger_data_requirements=trigger_requirements,
        trigger_freshness_ms=trigger_freshness_ms,
        entry_zone=draft.entry_zone,
        invalidation_conditions=tuple(draft.invalidation_conditions),
        invalidation_price=float(draft.invalidation_price),
        stop_price=float(draft.stop_price),
        target_price=float(draft.target_price),
        expected_horizon_hours=float(draft.expected_horizon_hours),
        approved_at=current.isoformat(),
        expires_at=expires.isoformat(),
        order_tactic=draft.order_tactic,
        maker_required=maker,
        time_in_force="POST_ONLY" if maker else "IOC",
        limit_price=draft.limit_price,
        price_rule="EXACT_LIMIT" if maker else "OPPOSING_BBO",
        bbo_reference=None if maker else ("ask" if draft.direction == "long" else "bid"),
        max_wait_sec=draft.max_wait_sec,
        cancel_replace_policy="CANCEL_ONLY",
        max_requotes=0,
        partial_fill_policy="KEEP_PARTIAL_CANCEL_REMAINDER",
        taker_fallback_allowed=draft.taker_fallback_allowed,
        max_slippage_bps=draft.max_slippage_bps,
        worst_price=draft.worst_price,
        max_notional=float(draft.max_notional),
        max_risk_pct=float(draft.max_risk_pct),
        max_leverage=int(draft.max_leverage),
        required_permission=str(cfg.agent.permission_mode),
        execution_account_mode=execution_account_mode,
        risk_policy_id=str(policy.policy_id),
        risk_policy_revision=int(policy.revision),
        risk_policy_hash=resolved_policy.policy_hash,
        behavior_version="entry-planner/v1",
    )


async def plan_next_candidate(
    planner: PlannerFn,
    *,
    cfg=None,
    owner_token: str | None = None,
) -> dict[str, Any]:
    """Claim, reason once, and CAS the result against the current generation."""

    from aletheia.config.loader import load_config

    cfg = cfg or load_config(persist_migrations=False)
    owner = owner_token or f"entry-planner-{uuid4().hex}"
    generation = repo.claim_next_candidate_planning(owner)
    if generation is None:
        return {"status": "idle", "model_invoked": False}
    if not repo.candidate_attention_is_active(generation["id"]):
        return {
            "status": "stale",
            "planning_generation_id": generation["id"],
            "model_invoked": False,
        }
    try:
        context = _candidate_context(generation)
    except Exception as exc:
        repo.defer_candidate_planning(generation["id"], owner, type(exc).__name__)
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": False,
        }
    if not repo.candidate_attention_is_active(generation["id"]):
        return {
            "status": "stale",
            "planning_generation_id": generation["id"],
            "model_invoked": False,
        }
    try:
        raw = await planner(context)
    except asyncio.CancelledError:
        repo.release_candidate_planning_claim(
            generation["id"], owner, "planner_preempted"
        )
        raise
    except PlannerBackpressure as exc:
        repo.release_candidate_planning_claim(
            generation["id"], owner, str(exc), retry_seconds=1
        )
        return {
            "status": "backpressure",
            "planning_generation_id": generation["id"],
            "model_invoked": False,
        }
    except Exception as exc:
        repo.defer_candidate_planning(generation["id"], owner, type(exc).__name__)
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    if raw is None:
        repo.defer_candidate_planning(generation["id"], owner, "planner_unavailable")
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    draft = EntryPlanDraft.model_validate(raw)
    if draft.result == "NO_PLAN":
        stored = repo.complete_candidate_planning_no_plan(generation["id"], owner, draft.model_dump(mode="json"))
        return {
            "status": "no_plan" if stored else "stale",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    try:
        plan = _build_plan(generation, draft, cfg)
    except Exception as exc:
        repo.defer_candidate_planning(generation["id"], owner, f"invalid_entry_plan:{type(exc).__name__}")
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    status, record = repo.complete_candidate_planning_with_plan(generation["id"], owner, plan)
    return {
        "status": status,
        "planning_generation_id": generation["id"],
        "entry_plan_id": record["id"] if record else None,
        "model_invoked": True,
    }


async def system_operator_entry_planner(context: dict) -> EntryPlanDraft | None:
    """Canonical slow AI producer; all output is validated before persistence."""

    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.broker import InferencePriority, inference_priority

    with inference_priority(InferencePriority.DEFAULT):
        result, error = await llm_structured(
            "You are Aletheia System Operator acting before any time-critical event. "
            "Return NO_PLAN when the bounded facts cannot support a complete plan. "
            "Otherwise select RESTING_MAKER or TRIGGERED, and for TRIGGERED select only "
            "a registered TriggerSpec id/version with bounded numeric params. Maker means "
            "true Post-Only; urgent execution means opposing-BBO IOC with a bounded envelope. "
            "Never authorize Live, invent another trigger language, or weaken Risk.",
            json.dumps(context, sort_keys=True, default=str),
            schema=EntryPlanDraft,
            fallback_to_defaults=False,
            caller="agent_shell.entry_planner",
            context_symbol=str((context.get("candidate") or {}).get("symbol") or ""),
            reasoning_effort="medium",
        )
    if error and "InferenceQueueFull" in error:
        raise PlannerBackpressure("inference_queue_full")
    return EntryPlanDraft.model_validate(result) if result is not None else None


async def run_entry_planner_once(cfg=None) -> dict[str, Any]:
    return await plan_next_candidate(system_operator_entry_planner, cfg=cfg)


async def run_entry_planner_batch(cfg=None) -> dict[str, Any]:
    """Plan one rank-ordered durable batch with bounded model concurrency."""

    from aletheia.config.loader import load_config
    from aletheia.model_provider.broker import (
        InferencePriority,
        inference_broker,
    )

    cfg = cfg or load_config(persist_migrations=False)
    pending = repo.count_pending_candidate_planning()
    provider_name = str(cfg.models.default_provider)
    available = await inference_broker.available_capacity(
        provider_name,
        priority=InferencePriority.DEFAULT,
    )
    worker_count = min(
        max(pending, ENTRY_PLANNER_MIN_CONCURRENCY),
        ENTRY_PLANNER_MAX_CONCURRENCY,
        available,
    )
    if worker_count <= 0:
        return {
            "status": "backpressure" if pending else "idle",
            "results": [],
            "planning_attempts": 0,
            "model_invoked": False,
            "pending_queue_remaining": pending,
        }
    tasks: list[asyncio.Task[dict[str, Any]]] = []
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(run_entry_planner_once(cfg=cfg))
            for _ in range(worker_count)
        ]
    results = [task.result() for task in tasks]
    claimed = [result for result in results if result.get("status") != "idle"]
    return {
        "status": "batch" if claimed else "idle",
        "results": claimed,
        "planning_attempts": len(claimed),
        "model_invoked": any(result.get("model_invoked") for result in claimed),
        "pending_queue_remaining": repo.count_pending_candidate_planning(),
    }

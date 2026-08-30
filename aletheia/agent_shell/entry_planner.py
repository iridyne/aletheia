"""On-demand System Operator planning over durable Candidate generations."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aletheia.storage import repositories as repo
from aletheia.trading.trigger_specs import (
    compatible_trigger_specs,
    require_compatible_trigger_spec,
)
from aletheia.universe.fast_stream import fast_market_data_unavailability_reason


class PlanIntentError(ValueError):
    """A bounded, machine-readable planner or compiler rejection."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        message = self.code if not detail else f"{self.code}:{detail}"
        super().__init__(message)


class _PlanIntentWire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["ENTRY_PLAN", "NO_PLAN"]
    reason_code: Literal[
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_SIGNALS",
        "STALE_FACTS",
        "NO_VALID_TRIGGER",
        "OK",
    ]
    thesis: str = Field(max_length=400)
    scenario: str = Field(max_length=200)
    evidence_refs: list[str] = Field(max_length=8)
    execution_mode: Literal["RESTING_MAKER", "TRIGGERED"] | None
    order_tactic: Literal["POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"] | None
    trigger_spec_id: str | None
    trigger_spec_version: str | None
    risk_tier: Literal["LOW", "MEDIUM", "NONE"]
    horizon: Literal["INTRADAY", "SWING", "NONE"]


class PlanIntent(_PlanIntentWire):
    @model_validator(mode="after")
    def _validate_semantics(self) -> "PlanIntent":
        trigger_fields_empty = self.trigger_spec_id is None and self.trigger_spec_version is None
        if self.decision == "NO_PLAN":
            if self.reason_code == "OK":
                raise PlanIntentError("NO_PLAN_REASON_REQUIRED")
            if (
                self.execution_mode is not None
                or self.order_tactic is not None
                or self.risk_tier != "NONE"
                or self.horizon != "NONE"
                or not trigger_fields_empty
            ):
                raise PlanIntentError("NO_PLAN_EXECUTION_FIELDS_FORBIDDEN")
            return self

        if self.reason_code != "OK":
            raise PlanIntentError("ENTRY_PLAN_REASON_MUST_BE_OK")
        if self.execution_mode is None:
            raise PlanIntentError("ENTRY_PLAN_EXECUTION_MODE_REQUIRED")
        if self.order_tactic is None:
            raise PlanIntentError("ENTRY_PLAN_ORDER_TACTIC_REQUIRED")
        if self.risk_tier == "NONE":
            raise PlanIntentError("ENTRY_PLAN_RISK_TIER_REQUIRED")
        if self.horizon == "NONE":
            raise PlanIntentError("ENTRY_PLAN_HORIZON_REQUIRED")
        if not self.thesis.strip():
            raise PlanIntentError("ENTRY_PLAN_THESIS_REQUIRED")
        if not self.scenario.strip():
            raise PlanIntentError("ENTRY_PLAN_SCENARIO_REQUIRED")
        if self.execution_mode == "RESTING_MAKER":
            if self.order_tactic != "POST_ONLY_LIMIT":
                raise PlanIntentError("RESTING_MAKER_TACTIC_INVALID")
            if not trigger_fields_empty:
                raise PlanIntentError("RESTING_MAKER_TRIGGER_FIELDS_FORBIDDEN")
            return self
        if self.order_tactic not in {"POST_ONLY_LIMIT", "OPPOSING_BBO_IOC"}:
            raise PlanIntentError("TRIGGERED_TACTIC_INVALID")
        return self


PlannerFn = Callable[[dict], Awaitable[PlanIntent | dict | None]]

ENTRY_PLANNER_MIN_CONCURRENCY = 3
ENTRY_PLANNER_MAX_CONCURRENCY = 15


class PlannerBackpressure(RuntimeError):
    """Local broker admission refusal; no physical model attempt was consumed."""


def _candidate_context(
    generation: dict,
    *,
    candidate: dict | None = None,
    feature: dict | None = None,
) -> dict:
    candidate = candidate or repo.get_candidate(generation["last_candidate_id"])
    if candidate is None:
        raise ValueError("candidate_not_found")
    if feature is None:
        feature = repo.get_latest_feature_snapshot(candidate["asset_id"]) or {}
    return {
        "planning_generation": {
            "id": generation["id"],
            "identity_version": generation["identity_version"],
        },
        "candidate": candidate,
        "latest_feature_snapshot": feature,
        "compatible_trigger_specs": [
            {
                "id": spec.spec_id,
                "version": spec.version,
                "data_requirements": spec.data_requirements,
                "freshness_ms": spec.freshness_ms,
            }
            for spec in compatible_trigger_specs(str(candidate.get("scanner_family") or ""))
        ],
    }


def _canonicalize_no_plan(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("decision") != "NO_PLAN":
        return payload
    return {
        **payload,
        "execution_mode": None,
        "order_tactic": None,
        "trigger_spec_id": None,
        "trigger_spec_version": None,
        "risk_tier": "NONE",
        "horizon": "NONE",
    }


def _validate_intent_against_candidate(
    intent: PlanIntent,
    generation: dict,
    candidate: dict,
    feature: dict,
    cfg,
) -> None:
    if intent.decision != "ENTRY_PLAN":
        raise PlanIntentError("COMPILER_ENTRY_INTENT_REQUIRED")
    if intent.execution_mode == "TRIGGERED":
        if not intent.trigger_spec_id or not intent.trigger_spec_version:
            raise PlanIntentError("TRIGGER_SPEC_NOT_REGISTERED")
        try:
            require_compatible_trigger_spec(
                str(candidate.get("scanner_family") or ""),
                str(intent.trigger_spec_id),
                str(intent.trigger_spec_version),
            )
        except ValueError as exc:
            code = (
                "NO_COMPATIBLE_TRIGGER_SPEC"
                if str(exc) == "trigger_spec_candidate_family_unsupported"
                else "UNSUPPORTED_TRIGGER_SPEC"
            )
            raise PlanIntentError(code) from exc
    if candidate.get("id") is not None and candidate.get("id") != generation.get("last_candidate_id"):
        raise PlanIntentError("STALE_CANDIDATE_PRICE")
    price_value: Any = candidate.get("entry_price_usd")
    try:
        entry_price = float(price_value)
    except (TypeError, ValueError) as exc:
        raise PlanIntentError("STALE_CANDIDATE_PRICE") from exc
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise PlanIntentError("STALE_CANDIDATE_PRICE")
    try:
        atr_pct = float((feature or {}).get("atr_pct") or 0.0)
    except (TypeError, ValueError):
        atr_pct = 0.0
    try:
        vol_target_pct = float(getattr(cfg.trading, "vol_target_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        vol_target_pct = 0.0
    if (not math.isfinite(atr_pct) or atr_pct <= 0) and (not math.isfinite(vol_target_pct) or vol_target_pct <= 0):
        raise PlanIntentError("NO_STOP_REFERENCE")


def _intent_validation_code(exc: BaseException) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            items = errors()
        except Exception:
            items = []
        for item in items if isinstance(items, list) else []:
            nested = (item.get("ctx") or {}).get("error") if isinstance(item, dict) else None
            if isinstance(nested, PlanIntentError):
                return nested.code
            if isinstance(item, dict):
                raw_type = str(item.get("type") or "")
                if raw_type in {"literal_error", "enum"}:
                    return "OUTSIDE_ENUM"
        return "VALIDATION_FAILED"
    return "VALIDATION_FAILED"


def _complete_semantic_no_plan(
    generation_id: str,
    owner: str,
    code: str,
    *,
    detail: str = "",
) -> bool:
    result = {
        "decision": "NO_PLAN",
        "reason_code": str(code),
        "detail": str(detail)[:160],
        "source": "deterministic_validation",
    }
    return repo.complete_candidate_planning_no_plan(generation_id, owner, result)


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
        candidate = repo.get_candidate(generation["last_candidate_id"])
        if candidate is None:
            raise ValueError("candidate_not_found")
        venue_reason = fast_market_data_unavailability_reason(
            cfg,
            str(candidate["venue"]),
        )
        if venue_reason is not None:
            stored = _complete_semantic_no_plan(
                generation["id"],
                owner,
                f"EXECUTION_VENUE_UNAVAILABLE:{venue_reason}",
            )
            return {
                "status": "no_plan" if stored else "stale",
                "planning_generation_id": generation["id"],
                "model_invoked": False,
            }
        feature = repo.get_latest_feature_snapshot(candidate["asset_id"]) or {}
        context = _candidate_context(
            generation,
            candidate=candidate,
            feature=feature,
        )
    except Exception as exc:
        repo.defer_candidate_planning_with_code(
            generation["id"],
            owner,
            "PLANNER_INTERNAL",
            type(exc).__name__,
        )
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
        repo.release_candidate_planning_claim(generation["id"], owner, "planner_preempted")
        raise
    except PlannerBackpressure as exc:
        repo.release_candidate_planning_claim(generation["id"], owner, str(exc), retry_seconds=1)
        return {
            "status": "backpressure",
            "planning_generation_id": generation["id"],
            "model_invoked": False,
        }
    except PlanIntentError as exc:
        repo.defer_candidate_planning_with_code(generation["id"], owner, exc.code, str(exc))
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    except Exception as exc:
        repo.defer_candidate_planning_with_code(generation["id"], owner, "PLANNER_INTERNAL", type(exc).__name__)
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    if raw is None:
        repo.defer_candidate_planning_with_code(generation["id"], owner, "PLANNER_UNAVAILABLE")
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    try:
        intent = PlanIntent.model_validate(raw)
    except ValidationError as exc:
        code = _intent_validation_code(exc)
        repo.defer_candidate_planning_with_code(
            generation["id"],
            owner,
            f"INTENT_SEMANTIC:{code}",
        )
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    if intent.decision == "NO_PLAN":
        stored = repo.complete_candidate_planning_no_plan(
            generation["id"],
            owner,
            intent.model_dump(mode="json"),
        )
        return {
            "status": "no_plan" if stored else "stale",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    try:
        _validate_intent_against_candidate(
            intent,
            generation,
            candidate,
            feature,
            cfg,
        )
        from aletheia.trading.entry_plan_compiler import compile_entry_plan

        plan = compile_entry_plan(generation, candidate, feature, intent, cfg)
    except PlanIntentError as exc:
        if exc.code in {
            "NO_COMPATIBLE_TRIGGER_SPEC",
            "TRIGGER_SPEC_NOT_REGISTERED",
            "UNSUPPORTED_TRIGGER_SPEC",
        }:
            stored = _complete_semantic_no_plan(
                generation["id"],
                owner,
                exc.code,
                detail=str(exc),
            )
            return {
                "status": "no_plan" if stored else "stale",
                "planning_generation_id": generation["id"],
                "model_invoked": True,
            }
        repo.defer_candidate_planning_with_code(generation["id"], owner, exc.code, str(exc))
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    except Exception as exc:
        repo.defer_candidate_planning_with_code(generation["id"], owner, "PLANNER_INTERNAL", type(exc).__name__)
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    status, record = repo.complete_candidate_planning_with_plan(
        generation["id"],
        owner,
        plan,
    )
    return {
        "status": status,
        "planning_generation_id": generation["id"],
        "entry_plan_id": record["id"] if record else None,
        "model_invoked": True,
    }


async def system_operator_entry_planner(
    context: dict,
    provider_name: str | None = None,
) -> PlanIntent | None:
    """Canonical slow AI producer; all output is validated before persistence."""

    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.broker import InferencePriority, inference_priority
    from aletheia.model_provider.router import get_provider_for_tier

    provider_name = provider_name or get_provider_for_tier("deep")
    system_prompt = """You are Aletheia System Operator. Return one strict PlanIntent.
You may choose only these lifecycle branches:
  1. RESTING_MAKER + POST_ONLY_LIMIT: passive maker Order without a trigger.
  2. TRIGGERED + POST_ONLY_LIMIT: wait for a compatible trigger, then maker Order.
  3. TRIGGERED + OPPOSING_BBO_IOC: wait for a compatible trigger, then bounded taker IOC.
TRIGGERED may select only an id/version listed in compatible_trigger_specs.
When that list is empty, choose RESTING_MAKER only if passive entry is justified;
otherwise return NO_PLAN. Never invent a trigger.
For NO_PLAN, execution_mode, order_tactic, trigger_spec_id and
trigger_spec_version are null; risk_tier and horizon are NONE.
For an Entry Plan, choose LOW or MEDIUM risk_tier and INTRADAY or SWING horizon.
LOW is required unless every evidence_ref is directly attributable to the
Candidate and internally consistent. Keep thesis, scenario, and evidence_refs short.
Prices, sizes, stop, target, leverage, expiry, slippage, timeout, and trigger
parameters are deterministic code output; do not emit them.
Return NO_PLAN with reason_code=INSUFFICIENT_EVIDENCE, CONFLICTING_SIGNALS,
STALE_FACTS, or NO_VALID_TRIGGER when bounded facts do not support entry."""
    with inference_priority(InferencePriority.DEFAULT):
        result, error = await llm_structured(
            system_prompt,
            json.dumps(context, sort_keys=True, default=str),
            schema=_PlanIntentWire,
            max_tokens=768,
            provider=provider_name,
            fallback_to_defaults=False,
            caller="agent_shell.entry_planner",
            context_symbol=str((context.get("candidate") or {}).get("symbol") or ""),
            reasoning_effort="none",
        )
    if error:
        error_text = str(error)
        if "InferenceQueueFull" in error_text:
            raise PlannerBackpressure("inference_queue_full")
        if error_text.startswith("structured_empty_content"):
            raise PlanIntentError("PLANNER_EMPTY_CONTENT")
        if error_text.startswith("structured_parse_error"):
            raise PlanIntentError("PLANNER_INVALID_JSON")
        if error_text.startswith("structured_schema_invalid"):
            detail = error_text.split(":", 1)[1][:80] if ":" in error_text else ""
            raise PlanIntentError(f"PLANNER_SCHEMA_INVALID:{detail}")
        if error_text.startswith("llm_unavailable"):
            if "structured_timeout" in error_text:
                raise PlanIntentError("PLANNER_TIMEOUT")
            raise PlanIntentError("PLANNER_UNAVAILABLE")
        raise PlanIntentError("PLANNER_UNAVAILABLE")
    if result is None:
        raise PlanIntentError("PLANNER_UNAVAILABLE")
    result = _canonicalize_no_plan(result)
    try:
        return PlanIntent.model_validate(result)
    except ValidationError as exc:
        raise PlanIntentError(f"PLANNER_SCHEMA_INVALID:{_intent_validation_code(exc)}") from exc


async def run_entry_planner_once(
    cfg=None,
    *,
    provider_name: str | None = None,
) -> dict[str, Any]:
    if provider_name is None:
        from aletheia.model_provider.router import get_provider_for_tier

        provider_name = get_provider_for_tier("deep")

    async def planner(context: dict) -> PlanIntent | None:
        return await system_operator_entry_planner(context, provider_name)

    return await plan_next_candidate(planner, cfg=cfg)


async def run_entry_planner_batch(cfg=None) -> dict[str, Any]:
    """Plan one rank-ordered durable batch with bounded model concurrency."""

    from aletheia.config.loader import load_config
    from aletheia.model_provider.broker import (
        InferencePriority,
        inference_broker,
    )
    from aletheia.model_provider.router import get_provider_for_tier

    cfg = cfg or load_config(persist_migrations=False)
    pending = repo.count_pending_candidate_planning()
    provider_name = get_provider_for_tier("deep")
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
            group.create_task(run_entry_planner_once(cfg=cfg, provider_name=provider_name)) for _ in range(worker_count)
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

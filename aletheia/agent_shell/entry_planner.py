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
    registered_trigger_specs,
    require_registered_trigger_spec,
)


class PlanIntentError(ValueError):
    """A bounded, machine-readable planner or compiler rejection."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        message = self.code if not detail else f"{self.code}:{detail}"
        super().__init__(message)


class PlanIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["ENTRY_PLAN", "NO_PLAN"]
    reason_code: Literal[
        "INSUFFICIENT_EVIDENCE",
        "CONFLICTING_SIGNALS",
        "STALE_FACTS",
        "NO_VALID_TRIGGER",
        "OK",
    ]
    thesis: str = Field(default="", max_length=400)
    scenario: str = Field(default="", max_length=200)
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)
    execution_mode: Literal["RESTING_MAKER", "TRIGGERED", "NONE"] = "NONE"
    trigger_spec_id: str = ""
    trigger_spec_version: str = ""
    risk_tier: Literal["LOW", "MEDIUM", "NONE"] = "NONE"
    horizon: Literal["INTRADAY", "SWING", "NONE"] = "NONE"

    @model_validator(mode="after")
    def _validate_semantics(self) -> "PlanIntent":
        trigger_fields_empty = not self.trigger_spec_id and not self.trigger_spec_version
        if self.decision == "NO_PLAN":
            if self.reason_code == "OK":
                raise PlanIntentError("NO_PLAN_REASON_REQUIRED")
            if (
                self.execution_mode != "NONE"
                or self.risk_tier != "NONE"
                or self.horizon != "NONE"
                or not trigger_fields_empty
            ):
                raise PlanIntentError("NO_PLAN_EXECUTION_FIELDS_FORBIDDEN")
            return self

        if self.reason_code != "OK":
            raise PlanIntentError("ENTRY_PLAN_REASON_MUST_BE_OK")
        if self.execution_mode == "NONE":
            raise PlanIntentError("ENTRY_PLAN_EXECUTION_MODE_REQUIRED")
        if self.risk_tier == "NONE":
            raise PlanIntentError("ENTRY_PLAN_RISK_TIER_REQUIRED")
        if self.horizon == "NONE":
            raise PlanIntentError("ENTRY_PLAN_HORIZON_REQUIRED")
        if not self.thesis.strip():
            raise PlanIntentError("ENTRY_PLAN_THESIS_REQUIRED")
        if not self.scenario.strip():
            raise PlanIntentError("ENTRY_PLAN_SCENARIO_REQUIRED")
        if self.execution_mode == "TRIGGERED":
            if not self.trigger_spec_id or not self.trigger_spec_version:
                raise PlanIntentError("TRIGGER_SPEC_NOT_REGISTERED")
            try:
                require_registered_trigger_spec(
                    self.trigger_spec_id,
                    self.trigger_spec_version,
                    {},
                )
            except ValueError as exc:
                if str(exc) != "momentum_trigger_params_incomplete":
                    raise PlanIntentError("TRIGGER_SPEC_NOT_REGISTERED") from exc
        elif not trigger_fields_empty:
            raise PlanIntentError("RESTING_MAKER_TRIGGER_FIELDS_FORBIDDEN")
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


def _validate_intent_against_candidate(
    intent: PlanIntent,
    generation: dict,
    candidate: dict,
    feature: dict,
    cfg,
) -> None:
    if intent.decision != "ENTRY_PLAN":
        raise PlanIntentError("COMPILER_ENTRY_INTENT_REQUIRED")
    if intent.execution_mode == "TRIGGERED" and candidate.get("scanner_family") != "momentum":
        raise PlanIntentError("NO_VALID_TRIGGER")
    if (
        candidate.get("id") is not None
        and candidate.get("id") != generation.get("last_candidate_id")
    ):
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
    if (
        (not math.isfinite(atr_pct) or atr_pct <= 0)
        and (not math.isfinite(vol_target_pct) or vol_target_pct <= 0)
    ):
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
    except PlanIntentError as exc:
        repo.defer_candidate_planning_with_code(
            generation["id"], owner, exc.code, str(exc)
        )
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    except Exception as exc:
        repo.defer_candidate_planning_with_code(
            generation["id"], owner, "PLANNER_INTERNAL", type(exc).__name__
        )
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    if raw is None:
        repo.defer_candidate_planning_with_code(
            generation["id"], owner, "PLANNER_UNAVAILABLE"
        )
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    try:
        intent = PlanIntent.model_validate(raw)
    except ValidationError as exc:
        repo.defer_candidate_planning_with_code(
            generation["id"],
            owner,
            f"INTENT_SEMANTIC:{_intent_validation_code(exc)}",
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
        repo.defer_candidate_planning_with_code(
            generation["id"], owner, exc.code, str(exc)
        )
        return {
            "status": "unavailable",
            "planning_generation_id": generation["id"],
            "model_invoked": True,
        }
    except Exception as exc:
        repo.defer_candidate_planning_with_code(
            generation["id"], owner, "PLANNER_INTERNAL", type(exc).__name__
        )
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


async def system_operator_entry_planner(context: dict) -> PlanIntent | None:
    """Canonical slow AI producer; all output is validated before persistence."""

    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.broker import InferencePriority, inference_priority
    from aletheia.model_provider.router import get_provider_for_tier

    system_prompt = """You are Aletheia System Operator. Return a PlanIntent describing whether to
enter the given Candidate. You choose only:
  - decision: ENTRY_PLAN or NO_PLAN.
  - execution_mode: RESTING_MAKER for a passive maker limit; TRIGGERED for an
    urgent opposing-BBO IOC gated on a registered TriggerSpec.
  - trigger_spec_id / trigger_spec_version: MUST match a registered spec from
    the context; leave empty for RESTING_MAKER.
  - risk_tier: LOW or MEDIUM. LOW is required unless every listed evidence_ref
    is directly attributable to the Candidate and internally consistent.
  - horizon: INTRADAY or SWING.
  - thesis, scenario, evidence_refs: short human-readable rationale.
Prices, sizes, stop, target, leverage, expiry, and slippage are computed
deterministically from the Candidate and policy; do NOT emit them.
Return NO_PLAN with reason_code=INSUFFICIENT_EVIDENCE, CONFLICTING_SIGNALS,
STALE_FACTS, or NO_VALID_TRIGGER when the bounded facts do not support entry."""
    with inference_priority(InferencePriority.DEFAULT):
        result, error = await llm_structured(
            system_prompt,
            json.dumps(context, sort_keys=True, default=str),
            schema=PlanIntent,
            max_tokens=768,
            provider=get_provider_for_tier("deep"),
            fallback_to_defaults=False,
            caller="agent_shell.entry_planner",
            context_symbol=str((context.get("candidate") or {}).get("symbol") or ""),
            reasoning_effort=None,
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
    try:
        return PlanIntent.model_validate(result)
    except ValidationError as exc:
        raise PlanIntentError(
            f"PLANNER_SCHEMA_INVALID:{_intent_validation_code(exc)}"
        ) from exc


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

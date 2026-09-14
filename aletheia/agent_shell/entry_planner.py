"""On-demand System Operator planning over durable Candidate generations."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from aletheia.config.schema import ModelSelection

from aletheia.storage import repositories as repo
from aletheia.trading.entry_analysis import (
    EntryAnalysisContext,
    EntryHypothesis,
    build_entry_analysis_context,
    canonical_json,
    entry_model_view,
    entry_orderbook_available,
    fingerprint,
    validate_hypothesis_references,
)
from aletheia.universe.fast_stream import (
    fast_market_data_unavailability_reason,
)

logger = logging.getLogger(__name__)


class PlanIntentError(ValueError):
    """A bounded, machine-readable planner or compiler rejection."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        message = self.code if not detail else f"{self.code}:{detail}"
        super().__init__(message)


PlannerFn = Callable[
    [dict], Awaitable[EntryHypothesis | dict | None]
]

ENTRY_PLANNER_MIN_CONCURRENCY = 3
ENTRY_PLANNER_MAX_CONCURRENCY = 15


class PlannerBackpressure(RuntimeError):
    """Local broker admission refusal; no physical model attempt was consumed."""


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
    model_provider: str | None = "custom_planner",
    resume_only: bool = False,
) -> dict[str, Any]:
    """Claim, reason once, and CAS the result against the current generation."""

    from aletheia.config.loader import load_config

    cfg = cfg or load_config(persist_migrations=False)
    owner = owner_token or f"entry-planner-{uuid4().hex}"
    generation = repo.claim_next_candidate_planning(owner, resume_only=resume_only)
    if generation is None:
        return {"status": "idle", "model_invoked": False}
    dispatched = False
    cancelled = False
    try:
        if not repo.candidate_attention_is_active(generation["id"]):
            return {
                "status": "stale",
                "planning_generation_id": generation["id"],
                "model_invoked": False,
            }
        from aletheia.trading.entry_plan import entry_instrument_violation

        instrument_violation = entry_instrument_violation(generation["market_type"], generation["direction"])
        if instrument_violation:
            stored = repo.fail_candidate_planning(
                generation["id"], owner, instrument_violation, stage="runtime_preflight",
                model_invoked=False, compiler_invoked=False,
            )
            return {"status": "failed" if stored else "stale", "planning_generation_id": generation["id"],
                    "reason": instrument_violation, "model_invoked": False, "compiler_invoked": False}
        try:
            venue_reason = fast_market_data_unavailability_reason(
                cfg,
                str(generation["venue"]),
                str(generation["market_type"]),
            )
            if venue_reason is not None:
                stored = repo.block_candidate_planning(
                    generation["id"],
                    owner,
                    f"EXECUTION_VENUE_UNAVAILABLE:{venue_reason}",
                    stage="runtime_preflight",
                )
                return {
                    "status": "blocked" if stored else "stale",
                    "planning_generation_id": generation["id"],
                    "model_invoked": False,
                }
            candidate: dict | None = None
            feature: dict = {}
            if generation.get("entry_behavior_fingerprint"):
                from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

                candidate = repo.get_candidate(generation["last_candidate_id"])
                if candidate is None:
                    raise ValueError("candidate_not_found")
                opportunity = CandidateOpportunityContextV1.model_validate_json(
                    candidate.get("candidate_opportunity_context_json") or ""
                )
                if (opportunity.candidate_id != candidate["id"]
                    or opportunity.context_hash != candidate.get("candidate_opportunity_context_hash")):
                    raise ValueError("candidate_opportunity_context_identity_mismatch")
                capability = opportunity.opportunity["capability_status"]
                if capability != "supported":
                    stored = repo.block_candidate_planning(
                        generation["id"], owner, f"CANDIDATE_CAPABILITY:{capability}",
                        stage="runtime_preflight",
                    )
                    return {"status": "blocked" if stored else "stale",
                            "planning_generation_id": generation["id"], "model_invoked": False}
                from aletheia.trading.entry_profiles import compatible_entry_profiles

                if not compatible_entry_profiles(opportunity.opportunity["primary_mechanism"]):
                    stored = repo.block_candidate_planning(
                        generation["id"], owner, "NO_VALID_ENTRY_PROFILE", stage="runtime_preflight",
                    )
                    return {"status": "blocked" if stored else "stale",
                            "planning_generation_id": generation["id"], "model_invoked": False}
                from aletheia.trading.entry_authority import resolve_entry_runtime_authority

                authority = resolve_entry_runtime_authority(cfg)
                if not authority.allowed:
                    stored = repo.block_candidate_planning(
                        generation["id"], owner, authority.reason or "entry_runtime_authority_unavailable",
                        stage="runtime_preflight",
                    )
                    return {"status": "blocked" if stored else "stale", "planning_generation_id": generation["id"], "model_invoked": False, "compiler_invoked": False}
                if generation.get("planner_input_json"):
                    context_model = EntryAnalysisContext.model_validate_json(generation["planner_input_json"])
                else:
                    from aletheia.data.collectors.microstructure import MicrostructureCollector
                    from aletheia.data.symbols import InstrumentId

                    orderbook = repo.latest_orderbook(
                        generation["symbol"], generation["venue"], generation["market_type"]
                    )
                    if not entry_orderbook_available(orderbook, datetime.now(timezone.utc)):
                        instrument = InstrumentId(
                            generation["venue"], generation["symbol"], generation["market_type"]
                        )
                        started = asyncio.get_running_loop().time()
                        category, phase, exception_type = "invalid_payload", "parse", None
                        book_age_ms = None
                        orderbook = None
                        try:
                            async with asyncio.timeout(5):
                                result = await MicrostructureCollector().collect(
                                    [instrument], venue=generation["venue"]
                                )
                            for error in result.get("errors", ()):
                                error_phase, _, error_type = error.partition(":")
                                if error_phase in {"provider_setup", "load_markets", "market_metadata", "fetch_order_book", "parse"}:
                                    phase = error_phase
                                    if error_type.isascii() and error_type.isidentifier() and len(error_type) <= 80:
                                        exception_type = error_type
                                    category = "invalid_payload" if error_type == "invalid_orderbook" else "collector_error"
                                    break
                            for snapshot in result["snapshots"]:
                                if (snapshot.exchange, snapshot.symbol, snapshot.market_type) != (
                                    generation["venue"], generation["symbol"], generation["market_type"]
                                ):
                                    category = "identity_mismatch"
                                    continue
                                candidate_book = snapshot.model_dump()
                                now = datetime.now(timezone.utc)
                                observed = datetime.fromisoformat(candidate_book["observed_at"].replace("Z", "+00:00"))
                                if observed.tzinfo is not None:
                                    book_age_ms = int((now - observed).total_seconds() * 1000)
                                if entry_orderbook_available(candidate_book, now):
                                    orderbook = candidate_book
                                elif book_age_ms is not None:
                                    category = "future" if book_age_ms < 0 else "stale"
                                break
                        except Exception as exc:
                            category = "timeout" if isinstance(exc, TimeoutError) else "collector_error"
                            phase, exception_type = "collect", type(exc).__name__
                        if orderbook is not None:
                            try:
                                repo.insert_orderbook_snapshot(orderbook)
                            except Exception as exc:
                                orderbook = None
                                category, phase, exception_type = "persistence_error", "persistence", type(exc).__name__
                        if not entry_orderbook_available(orderbook, datetime.now(timezone.utc)):
                            if orderbook is not None:
                                book_age_ms = int((datetime.now(timezone.utc) - observed).total_seconds() * 1000)
                                category, phase, exception_type = "stale", "freshness", None
                            logger.warning("entry_orderbook_unavailable", extra={
                                "generation_id": generation["id"], "venue": generation["venue"],
                                "symbol": generation["symbol"], "market_type": generation["market_type"],
                                "category": category, "phase": phase, "exception_type": exception_type,
                                "elapsed_ms": int((asyncio.get_running_loop().time() - started) * 1000),
                                "book_age_ms": book_age_ms,
                            })
                    if not entry_orderbook_available(orderbook, datetime.now(timezone.utc)):
                        repo.release_candidate_planning_claim(
                            generation["id"], owner, "ENTRY_ANALYSIS_EXECUTION_CONTEXT_UNAVAILABLE",
                            retry_seconds=60,
                        )
                        return {"status": "unavailable", "planning_generation_id": generation["id"], "model_invoked": False}
                    feature = repo.get_latest_feature_snapshot(candidate["asset_id"]) or {}
                    cutoff = datetime.now(timezone.utc)
                    cutoff_ms = int(cutoff.timestamp() * 1000)
                    from aletheia.data.providers import timeframe_ms

                    windows = {
                        timeframe: repo.get_ohlcv_candles(
                            generation["symbol"], timeframe, limit, generation["venue"],
                            generation["market_type"], cutoff_ms - timeframe_ms(timeframe) + 1,
                            include_provenance=True,
                        )
                        for timeframe, limit in (("4h", 30), ("1h", 48), ("15m", 96))
                    }
                    context_model = build_entry_analysis_context(
                        generation, candidate, feature, windows, cutoff_at=cutoff,
                        candidate_detected_at=repo.get_universe_snapshot_created_at(
                            str(candidate["snapshot_id"])
                        ),
                        positions=repo.list_all_open_positions(),
                        orderbook=orderbook,
                        model_provider=model_provider,
                    )
                    if (
                        context_model.candle_windows["15m"]["availability"] != "available"
                        or not context_model.anchors
                        or context_model.execution_context["availability"] != "available"
                    ):
                        repo.release_candidate_planning_claim(
                            generation["id"], owner, "ENTRY_ANALYSIS_REQUIRED_CONTEXT_UNAVAILABLE",
                            retry_seconds=60,
                        )
                        return {"status": "unavailable", "planning_generation_id": generation["id"], "model_invoked": False}
                    from aletheia.trading.entry_plan_compiler import require_entry_market_quality

                    try:
                        require_entry_market_quality(context_model.execution_context, cfg)
                    except PlanIntentError:
                        repo.release_candidate_planning_claim(
                            generation["id"], owner, "ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE",
                            retry_seconds=60,
                        )
                        return {"status": "unavailable", "planning_generation_id": generation["id"],
                                "reason": "ENTRY_ANALYSIS_MARKET_QUALITY_UNAVAILABLE",
                                "model_invoked": False, "compiler_invoked": False}
                    payload = canonical_json(context_model.model_dump(mode="json"))
                    if not repo.bind_candidate_planner_input(
                        generation["id"], owner, payload,
                        fingerprint(context_model.model_dump(mode="json")),
                    ):
                        return {"status": "stale", "planning_generation_id": generation["id"], "model_invoked": False}
                if not context_model.entry_profiles:
                    stored = repo.block_candidate_planning(
                        generation["id"], owner, "NO_VALID_ENTRY_PROFILE", stage="runtime_preflight"
                    )
                    return {
                        "status": "blocked" if stored else "stale",
                        "planning_generation_id": generation["id"],
                        "model_invoked": False,
                    }
                context = context_model.model_dump(mode="json")
            else:
                raise ValueError("legacy_generation_not_current_runtime")
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
        if generation.get("validated_hypothesis_json"):
            raw = json.loads(generation["validated_hypothesis_json"])
        else:
            if not repo.mark_candidate_planning_stage(
                generation["id"], owner, "model_invoking"
            ):
                return {"status": "stale", "planning_generation_id": generation["id"], "model_invoked": False}
            try:
                dispatched = True
                raw = await planner(context)
            except PlannerBackpressure as exc:
                repo.release_candidate_planning_claim(generation["id"], owner, str(exc), retry_seconds=1)
                return {
                    "status": "backpressure",
                    "planning_generation_id": generation["id"],
                    "model_invoked": False,
                }
            except PlanIntentError as exc:
                if exc.code.startswith((
                    "PLANNER_SCHEMA_INVALID", "PLANNER_INVALID_JSON",
                    "PLANNER_SEMANTIC_INVALID",
                )):
                    repo.fail_candidate_planning(
                        generation["id"], owner, exc.code, stage="validation",
                        model_invoked=True, compiler_invoked=False,
                    )
                    status = "failed"
                else:
                    repo.defer_candidate_planning(
                        generation["id"], owner, str(exc), stage="provider", model_invoked=True,
                    )
                    status = "unavailable"
                return {
                    "status": status,
                    "planning_generation_id": generation["id"],
                    "model_invoked": True,
                }
            except Exception as exc:
                repo.defer_candidate_planning_with_code(
                    generation["id"], owner, "PLANNER_INTERNAL", type(exc).__name__,
                    model_invoked=True,
                )
                return {
                    "status": "unavailable",
                    "planning_generation_id": generation["id"],
                    "model_invoked": True,
                }
            if not repo.mark_candidate_planning_stage(
                generation["id"], owner, "model_complete", model_invoked=True
            ):
                return {
                    "status": "stale",
                    "planning_generation_id": generation["id"],
                    "model_invoked": True,
                }
            if raw is None:
                repo.defer_candidate_planning_with_code(
                    generation["id"], owner, "PLANNER_UNAVAILABLE", model_invoked=True
                )
                return {
                    "status": "unavailable",
                    "planning_generation_id": generation["id"],
                    "model_invoked": True,
                }
        try:
            intent = EntryHypothesis.model_validate(raw)
        except ValidationError as exc:
            code = _intent_validation_code(exc)
            repo.fail_candidate_planning(
                generation["id"], owner, f"INTENT_SEMANTIC:{code}",
                stage="validation", model_invoked=dispatched, compiler_invoked=False,
            )
            return {
                "status": "failed",
                "planning_generation_id": generation["id"],
                "model_invoked": dispatched,
            }
        try:
            validate_hypothesis_references(intent, context_model)
        except ValueError as exc:
            stored = repo.fail_candidate_planning(
                generation["id"], owner, f"INVALID_HYPOTHESIS_REFERENCE:{str(exc)[:100]}",
                stage="validation", model_invoked=dispatched, compiler_invoked=False,
            )
            return {"status": "failed" if stored else "stale", "planning_generation_id": generation["id"], "model_invoked": dispatched, "compiler_invoked": False}
        if not generation.get("validated_hypothesis_json"):
            if not repo.bind_candidate_planning_hypothesis(
                generation["id"], owner, canonical_json(intent.model_dump(mode="json")),
            ):
                return {"status": "stale", "planning_generation_id": generation["id"], "model_invoked": dispatched}
        if intent.decision == "NO_PLAN":
            stored = repo.complete_candidate_planning_no_plan(
                generation["id"], owner, intent.model_dump(mode="json"),
            )
            return {"status": "no_plan" if stored else "stale",
                    "planning_generation_id": generation["id"], "model_invoked": dispatched}
        from aletheia.trading.entry_authority import resolve_entry_runtime_authority

        authority = resolve_entry_runtime_authority(cfg)
        if not authority.allowed:
            stored = repo.block_candidate_planning(
                generation["id"], owner, authority.reason or "entry_runtime_authority_unavailable",
                stage="post_inference_authority", model_invoked=dispatched,
            )
            return {"status": "blocked" if stored else "stale", "planning_generation_id": generation["id"], "model_invoked": dispatched, "compiler_invoked": False}
        from aletheia.trading.entry_plan_compiler import compile_entry_plan

        if not repo.mark_candidate_planning_stage(
            generation["id"], owner, "compiler", model_invoked=dispatched, compiler_invoked=True
        ):
            return {"status": "stale", "planning_generation_id": generation["id"], "model_invoked": dispatched, "compiler_invoked": False}
        try:
            plan = compile_entry_plan(generation, context_model, intent, cfg)
        except ValueError as exc:
            code = exc.code if isinstance(exc, PlanIntentError) else type(exc).__name__
            if code in {
                "RISK_POLICY_UNKNOWN", "COMPILER_LEVERAGE_UNAVAILABLE",
                "COMPILER_RISK_BUDGET_UNAVAILABLE", "COMPILER_INTERNAL",
            }:
                stored = repo.defer_candidate_planning(
                    generation["id"], owner, f"ENTRY_PLAN_COMPILE_UNAVAILABLE:{code}",
                    stage="compiler", model_invoked=dispatched, compiler_invoked=True,
                )
                return {"status": "unavailable" if stored else "stale",
                        "planning_generation_id": generation["id"],
                        "model_invoked": dispatched, "compiler_invoked": True}
            repo.fail_candidate_planning(
                generation["id"], owner, f"ENTRY_PLAN_COMPILE_INVALID:{code}",
                stage="compiler", model_invoked=dispatched, compiler_invoked=True,
            )
            return {"status": "failed", "planning_generation_id": generation["id"], "model_invoked": dispatched, "compiler_invoked": True}
        except Exception as exc:
            logger.exception(
                "entry_plan_compile_unexpected generation_id=%s", generation["id"]
            )
            repo.defer_candidate_planning(
                generation["id"], owner, f"COMPILER_INTERNAL:{type(exc).__name__}",
                stage="compiler", model_invoked=dispatched, compiler_invoked=True,
            )
            return {"status": "unavailable", "planning_generation_id": generation["id"], "model_invoked": dispatched, "compiler_invoked": True}
        try:
            status, record = repo.complete_candidate_planning_with_plan(
                generation["id"], owner, plan
            )
        except ValueError as exc:
            from aletheia.trading.entry_plan import EntryPlanActivationError

            code = exc.code if isinstance(exc, EntryPlanActivationError) else (
                "entry_plan_schema_invalid" if isinstance(exc, ValidationError) else "entry_plan_activation_unknown"
            )
            repo.fail_candidate_planning(
                generation["id"], owner,
                f"ENTRY_PLAN_ACTIVATION_INVALID:{code}",
                stage="activation", model_invoked=dispatched, compiler_invoked=True,
            )
            return {"status": "failed", "planning_generation_id": generation["id"], "model_invoked": dispatched, "compiler_invoked": True}
        return {"status": status, "planning_generation_id": generation["id"], "entry_plan_id": record["id"] if record else None, "model_invoked": dispatched, "compiler_invoked": True}
    except asyncio.CancelledError:
        cancelled = True
        raise
    finally:
        if cancelled:
            async def recover_cancelled_claim() -> None:
                current = repo.get_candidate_planning_generation(generation["id"])
                if not current or current["status"] != "planning" or current["owner_token"] != owner:
                    return
                if dispatched:
                    repo.defer_candidate_planning(
                        generation["id"], owner, "PLANNER_CANCELLED_DISPATCH_UNKNOWN",
                        stage="provider",
                    )
                else:
                    repo.release_candidate_planning_claim(
                        generation["id"], owner, "PLANNER_CANCELLED_BEFORE_DISPATCH",
                        retry_seconds=60,
                    )

            recovery = asyncio.create_task(recover_cancelled_claim())
            while not recovery.done():
                try:
                    await asyncio.shield(recovery)
                except asyncio.CancelledError:
                    continue
            recovery.result()


async def system_operator_entry_planner(
    context: dict,
    provider_name: str | ModelSelection | None = None,
) -> EntryHypothesis | None:
    """Canonical slow AI producer; all output is validated before persistence."""

    from aletheia.agents.helpers import llm_structured
    from aletheia.model_provider.broker import InferencePriority, inference_priority
    from aletheia.model_provider.router import get_provider_for_tier

    context_model = EntryAnalysisContext.model_validate(context)
    model_view = entry_model_view(context_model)
    provider_name = provider_name or get_provider_for_tier("deep")
    planning_generation_id = str(context_model.planning_generation.get("id") or "")
    from aletheia.trading.entry_contract import CANONICAL_ENTRY_SYSTEM_PROMPT

    opportunity = context_model.candidate_opportunity_context["opportunity"]

    class BoundEntryHypothesis(EntryHypothesis):
        @classmethod
        def model_json_schema(cls, **kwargs):
            schema = super().model_json_schema(**kwargs)
            schema["properties"]["mechanism"]["enum"] = [opportunity["primary_mechanism"]]
            schema["properties"]["direction"]["enum"] = [opportunity["direction"]]
            return schema

    def _validate_semantics(data: dict[str, object]) -> str | None:
        hypothesis = EntryHypothesis.model_validate(data)
        try:
            validate_hypothesis_references(hypothesis, context_model)
        except ValueError as exc:
            return str(exc).splitlines()[0][:100]
        return None

    call_kwargs = {
        "schema": BoundEntryHypothesis,
        "max_tokens": 3072,
        "provider": provider_name,
        "fallback_to_defaults": False,
        "context_symbol": str(context_model.instrument.get("symbol") or ""),
        "context_artifacts": (
            [{
                "kind": "environment",
                "stable_id": f"planning-generation:{planning_generation_id}",
                "source": "candidate_planning_generations",
                "selected_reason": "exact_grounded_entry_input",
                "trust_class": "internal_fact",
                "freshness_status": "fresh",
                "observed_at": model_view.get("analysis_cutoff_at"),
                "rendered_chars": len(canonical_json(model_view)),
            }]
            if planning_generation_id else None
        ),
        "reasoning_effort": "medium",
        "semantic_validator": _validate_semantics,
    }
    prompt_payload = canonical_json(model_view)
    with inference_priority(InferencePriority.DEFAULT):
        result, error = await llm_structured(
            CANONICAL_ENTRY_SYSTEM_PROMPT, prompt_payload,
            caller="agent_shell.entry_planner",
            **call_kwargs,
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
        if error_text.startswith("structured_semantic_invalid"):
            detail = error_text.split(":", 1)[1][:100] if ":" in error_text else "unknown"
            raise PlanIntentError(f"PLANNER_SEMANTIC_INVALID:{detail}")
        if error_text.startswith("llm_unavailable"):
            if "structured_timeout" in error_text:
                raise PlanIntentError("PLANNER_TIMEOUT")
            raise PlanIntentError("PLANNER_UNAVAILABLE")
        raise PlanIntentError("PLANNER_UNAVAILABLE")
    if result is None:
        raise PlanIntentError("PLANNER_UNAVAILABLE")
    try:
        return EntryHypothesis.model_validate(result)
    except ValidationError as exc:
        raise PlanIntentError(f"PLANNER_SCHEMA_INVALID:{_intent_validation_code(exc)}") from exc


async def run_entry_planner_once(
    cfg=None,
    *,
    provider_name: str | ModelSelection | None = None,
    resume_only: bool = False,
) -> dict[str, Any]:
    if provider_name is None and not resume_only:
        from aletheia.model_provider.router import get_provider_for_tier

        provider_name = get_provider_for_tier("deep")

    async def planner(context: dict) -> EntryHypothesis | None:
        return await system_operator_entry_planner(context, provider_name)

    endpoint_id = provider_name.endpoint_id if isinstance(provider_name, ModelSelection) else provider_name
    return await plan_next_candidate(planner, cfg=cfg, model_provider=endpoint_id, resume_only=resume_only)


async def run_entry_planner_batch(cfg=None) -> dict[str, Any]:
    """Plan one rank-ordered durable batch with bounded model concurrency."""

    from aletheia.config.loader import load_config
    from aletheia.model_provider.broker import (
        InferencePriority,
        inference_broker,
    )
    from aletheia.model_provider.router import ProviderNotConfigured, get_provider_for_tier

    cfg = cfg or load_config(persist_migrations=False)
    pending = repo.count_pending_candidate_planning()
    try:
        selection = get_provider_for_tier("deep")
    except ProviderNotConfigured:
        selection = None
    if selection is None:
        available = 0
    else:
        provider_name = selection.endpoint_id
        available = await inference_broker.available_capacity(
            provider_name,
            priority=InferencePriority.DEFAULT,
        )
    worker_count = min(
        max(pending, ENTRY_PLANNER_MIN_CONCURRENCY),
        ENTRY_PLANNER_MAX_CONCURRENCY,
        available,
    )
    resume_only = worker_count <= 0
    if resume_only:
        worker_count = min(repo.count_pending_candidate_planning(resume_only=True), ENTRY_PLANNER_MAX_CONCURRENCY)
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
            group.create_task(run_entry_planner_once(cfg=cfg, provider_name=selection, resume_only=resume_only)) for _ in range(worker_count)
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

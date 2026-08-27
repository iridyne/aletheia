"""Focused runtime proof for the logical LLM inference envelope."""

from __future__ import annotations


async def _make_async_provider(provider):
    """Helper for tests: wrap sync mock provider in async."""
    return provider


import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from aletheia.model_provider.base import ModelProvider, ModelResponse
from aletheia.storage.sqlite import get_db


class _ValueSchema(BaseModel):
    value: int


class _RepairProvider(ModelProvider):
    name = "harness_test"

    def __init__(self, responses: list[dict]) -> None:
        self.model = "fixed-test-model"
        self._responses = iter(responses)

    async def complete(self, request):
        payload = next(self._responses)
        return ModelResponse(
            content=json.dumps(payload),
            model=self.model,
            usage={"input_tokens": 10, "output_tokens": 3},
            raw={"usage": {"input_tokens": 10, "output_tokens": 3}},
        )


class _FailingProvider(ModelProvider):
    name = "harness_failing"

    def __init__(self, provider_name: str, model: str) -> None:
        self.model = model
        self._aletheia_provider_name = provider_name

    async def complete(self, _request):
        raise RuntimeError("429 transient failure")


async def _physical_complete(request, *, provider=None, provider_name=None):
    assert provider is not None
    return await provider.complete(request)


async def _direct_brokered_complete(_provider_name, provider, request, **_kwargs):
    return await provider.complete(request)


def _fallback_cfg(default_provider: str, fallback_models: list[str]):
    return SimpleNamespace(
        economics=SimpleNamespace(
            enabled=False,
            budgets_shadow_enabled=False,
            prompt_guardrail_detection_enabled=False,
            enforcement_mode="warn_only",
        ),
        models=SimpleNamespace(
            default_provider=default_provider,
            providers={
                default_provider: SimpleNamespace(
                    model_fallback_enabled=True,
                    fallback_models=fallback_models,
                )
            },
        ),
    )


@pytest.mark.asyncio
async def test_structured_repair_attempts_share_one_logical_call():
    from aletheia.agents import helpers

    provider = _RepairProvider([{"value": "wrong"}, {"value": 7}])
    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(helpers, "complete_with_fallback", _physical_complete),
    ):
        data, error = await helpers.llm_structured(
            "system",
            "user",
            schema=_ValueSchema,
            caller="market_observer.market_read",
        )

    assert error is None
    assert data == {"value": 7}
    db = get_db()
    logical = db.execute("SELECT * FROM llm_inference_calls WHERE caller='market_observer.market_read'").fetchone()
    assert logical is not None
    assert logical["status"] == "success"
    assert logical["parse_valid"] == 1
    assert logical["schema_valid"] == 1
    assert logical["attempt_count"] == 2
    attempts = db.execute(
        "SELECT call_id,attempt_index FROM runtime_cost_events WHERE event_type='llm' ORDER BY attempt_index"
    ).fetchall()
    assert [row["call_id"] for row in attempts] == [
        logical["logical_call_id"],
        logical["logical_call_id"],
    ]
    assert [row["attempt_index"] for row in attempts] == [0, 1]
    outcomes = db.execute(
        "SELECT structured_attempt_index,outcome FROM llm_structured_attempt_outcomes "
        "WHERE logical_call_id=? ORDER BY structured_attempt_index",
        (logical["logical_call_id"],),
    ).fetchall()
    assert [tuple(row) for row in outcomes] == [(0, "schema_invalid"), (1, "accepted")]


@pytest.mark.asyncio
async def test_default_provider_failure_keeps_final_attempt_identity():
    from aletheia.agents import helpers
    from aletheia.model_provider import fallback

    provider = _FailingProvider("default-api", "primary-model")
    cfg = _fallback_cfg("default-api", [])
    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(fallback, "load_config", lambda: cfg),
        patch.object(fallback, "brokered_complete", _direct_brokered_complete),
    ):
        content, error = await helpers.llm_complete(
            "system",
            "user",
            caller="research_worker.lens",
            allow_model_fallback=False,
        )

    assert content is None
    assert error is not None
    db = get_db()
    logical = db.execute(
        "SELECT logical_call_id,status,attempt_count,final_provider,final_model,error_type "
        "FROM llm_inference_calls WHERE caller='research_worker.lens' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert logical is not None
    assert dict(logical) == {
        "logical_call_id": logical["logical_call_id"],
        "status": "error",
        "attempt_count": 1,
        "final_provider": "default-api",
        "final_model": "primary-model",
        "error_type": "RuntimeError",
    }
    physical = db.execute(
        "SELECT provider,model_or_tool,status FROM runtime_cost_events WHERE call_id=?",
        (logical["logical_call_id"],),
    ).fetchone()
    assert dict(physical) == {
        "provider": "default-api",
        "model_or_tool": "primary-model",
        "status": "error",
    }


@pytest.mark.asyncio
@pytest.mark.real_llm
async def test_exhausted_model_fallback_keeps_last_attempted_model():
    from aletheia.economics.inference_calls import (
        record_inference_finished,
        record_inference_started,
    )
    from aletheia.model_provider import fallback
    from aletheia.model_provider.base import ModelRequest
    from aletheia.model_provider.inference_contract import build_inference_call

    provider = _FailingProvider("default-api", "primary-model")
    cfg = _fallback_cfg("default-api", ["fallback-a", "fallback-b"])
    call = build_inference_call(
        caller="research_worker.claims",
        system="system",
        messages=[{"role": "user", "content": "user"}],
        context_adapter_id="none",
    )
    assert record_inference_started(call)
    with (
        patch.object(fallback, "load_config", lambda: cfg),
        patch.object(fallback, "brokered_complete", _direct_brokered_complete),
        patch.object(fallback, "emit_infra_event", lambda **_kwargs: True),
    ):
        with pytest.raises(RuntimeError):
            await fallback.complete_with_fallback(
                ModelRequest(
                    messages=[{"role": "user", "content": "user"}],
                    system="system",
                    inference=call,
                ),
                provider=provider,
                provider_name=None,
            )
    assert record_inference_finished(
        call,
        status="error",
        error_type="RuntimeError",
    )

    db = get_db()
    logical = db.execute(
        "SELECT logical_call_id,status,attempt_count,final_provider,final_model,error_type "
        "FROM llm_inference_calls WHERE logical_call_id=?",
        (call.logical_call_id,),
    ).fetchone()
    assert logical is not None
    assert dict(logical) == {
        "logical_call_id": logical["logical_call_id"],
        "status": "error",
        "attempt_count": 3,
        "final_provider": "default-api",
        "final_model": "fallback-b",
        "error_type": "RuntimeError",
    }
    attempts = db.execute(
        "SELECT model_or_tool,status FROM runtime_cost_events WHERE call_id=? ORDER BY attempt_index",
        (logical["logical_call_id"],),
    ).fetchall()
    assert [tuple(row) for row in attempts] == [
        ("primary-model", "error"),
        ("fallback-a", "error"),
        ("fallback-b", "error"),
    ]


def test_pre_provider_denial_does_not_fabricate_final_model():
    from aletheia.economics.inference_calls import (
        record_inference_finished,
        record_inference_started,
    )
    from aletheia.model_provider.inference_contract import build_inference_call

    call = build_inference_call(
        caller="research_worker.themes",
        system="system",
        messages=[{"role": "user", "content": "user"}],
        context_adapter_id="none",
    )
    call.set_resolved_provider("default-api")
    assert record_inference_started(call)
    assert record_inference_finished(call, status="error", error_type="llm_budget_exhausted")

    logical = (
        get_db()
        .execute(
            "SELECT status,attempt_count,final_provider,final_model,error_type "
            "FROM llm_inference_calls WHERE logical_call_id=?",
            (call.logical_call_id,),
        )
        .fetchone()
    )
    assert dict(logical) == {
        "status": "error",
        "attempt_count": 0,
        "final_provider": "default-api",
        "final_model": None,
        "error_type": "llm_budget_exhausted",
    }


@pytest.mark.asyncio
async def test_schema_invalid_object_never_becomes_success():
    from aletheia.agents import helpers

    provider = _RepairProvider([{"value": "wrong"}, {"value": "still-wrong"}, {"value": None}])
    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(helpers, "complete_with_fallback", _physical_complete),
    ):
        data, error = await helpers.llm_structured(
            "system",
            "user",
            schema=_ValueSchema,
            caller="market_observer.market_read",
        )

    assert data is None
    assert error is not None and error.startswith("structured_schema_invalid")
    logical = (
        get_db()
        .execute(
            "SELECT status,parse_valid,schema_valid,attempt_count,error_type "
            "FROM llm_inference_calls WHERE caller='market_observer.market_read'"
        )
        .fetchone()
    )
    assert dict(logical) == {
        "status": "error",
        "parse_valid": 1,
        "schema_valid": 0,
        "attempt_count": 3,
        "error_type": "structured_schema_invalid",
    }
    outcomes = (
        get_db()
        .execute(
            "SELECT structured_attempt_index,outcome FROM llm_structured_attempt_outcomes "
            "WHERE logical_call_id=(SELECT logical_call_id FROM llm_inference_calls "
            "WHERE caller='market_observer.market_read') ORDER BY structured_attempt_index"
        )
        .fetchall()
    )
    assert [tuple(row) for row in outcomes] == [
        (0, "schema_invalid"),
        (1, "schema_invalid"),
        (2, "schema_invalid"),
    ]


@pytest.mark.asyncio
async def test_external_cancellation_finalizes_structured_logical_call_and_propagates():
    from aletheia.agents import helpers

    entered_provider = asyncio.Event()

    async def hanging_complete(*_args, **_kwargs):
        entered_provider.set()
        await asyncio.Future()

    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(object())),
        patch.object(helpers, "complete_with_fallback", hanging_complete),
    ):
        task = asyncio.create_task(
            helpers.llm_structured(
                "system",
                "user",
                schema=_ValueSchema,
                caller="news_analyzer.analysis",
                fallback_to_defaults=True,
            )
        )
        await entered_provider.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    logical = (
        get_db()
        .execute(
            "SELECT status,finished_at,parse_valid,schema_valid,error_type "
            "FROM llm_inference_calls WHERE caller='news_analyzer.analysis'"
        )
        .fetchone()
    )
    assert logical is not None
    assert logical["status"] == "error"
    assert logical["finished_at"] is not None
    assert logical["parse_valid"] is None
    assert logical["schema_valid"] is None
    assert logical["error_type"] == "cancelled"
    outcomes = (
        get_db()
        .execute(
            "SELECT outcome FROM llm_structured_attempt_outcomes "
            "WHERE logical_call_id=(SELECT logical_call_id FROM llm_inference_calls "
            "WHERE caller='news_analyzer.analysis')"
        )
        .fetchall()
    )
    assert [row["outcome"] for row in outcomes] == ["cancelled"]


@pytest.mark.asyncio
async def test_external_cancellation_finalizes_complete_logical_call_and_propagates():
    from aletheia.agents import helpers

    entered_provider = asyncio.Event()

    async def hanging_complete(*_args, **_kwargs):
        entered_provider.set()
        await asyncio.Future()

    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(object())),
        patch.object(helpers, "complete_with_fallback", hanging_complete),
    ):
        task = asyncio.create_task(helpers.llm_complete("system", "user", caller="research_worker.lens"))
        await entered_provider.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    logical = (
        get_db()
        .execute("SELECT status,finished_at,error_type FROM llm_inference_calls WHERE caller='research_worker.lens'")
        .fetchone()
    )
    assert dict(logical) == {
        "status": "error",
        "finished_at": logical["finished_at"],
        "error_type": "cancelled",
    }
    assert logical["finished_at"] is not None


@pytest.mark.asyncio
async def test_external_cancellation_finalizes_stream_logical_call_and_propagates():
    from aletheia.agents import helpers
    from aletheia.model_provider import broker

    entered_provider = asyncio.Event()

    async def hanging_stream(*_args, **_kwargs):
        entered_provider.set()
        await asyncio.Future()
        yield None

    async def consume():
        async for _ in helpers.llm_stream("system", "user", caller="unregistered.stream"):
            pass

    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(object())),
        patch.object(broker, "brokered_stream", hanging_stream),
    ):
        task = asyncio.create_task(consume())
        await entered_provider.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    logical = (
        get_db()
        .execute("SELECT status,finished_at,error_type FROM llm_inference_calls WHERE caller='unregistered.stream'")
        .fetchone()
    )
    assert logical["status"] == "error"
    assert logical["finished_at"] is not None
    assert logical["error_type"] == "cancelled"


def test_startup_reconciliation_finalizes_interrupted_logical_call():
    from aletheia.economics.inference_calls import (
        reconcile_interrupted_inferences,
        record_inference_started,
    )
    from aletheia.model_provider.inference_contract import build_inference_call

    call = build_inference_call(
        caller="unregistered",
        system="system",
        messages=[{"role": "user", "content": "user"}],
        context_adapter_id="none",
    )
    record_inference_started(call, started_at="2026-01-01T00:00:00+00:00")

    assert reconcile_interrupted_inferences("new-runtime") == 1
    logical = (
        get_db()
        .execute(
            "SELECT status,attempt_count,error_type,finished_at FROM llm_inference_calls WHERE logical_call_id=?",
            (call.logical_call_id,),
        )
        .fetchone()
    )
    assert logical["status"] == "error"
    assert logical["attempt_count"] == 0
    assert logical["error_type"] == "interrupted"
    assert logical["finished_at"] is not None


@pytest.mark.asyncio
async def test_committee_records_one_logical_call_and_one_physical_attempt():
    from aletheia.agents.committee import llm_agent
    from aletheia.model_provider import fallback

    provider = _RepairProvider(
        [
            {
                "direction": "neutral",
                "confidence": "low",
                "confidence_score": 30,
                "reasoning": "mixed",
                "evidence": [],
                "risks": [],
                "position_size_factor": 0.3,
            }
        ]
    )

    async def direct(request, **_kwargs):
        return await provider.complete(request)

    with patch.object(fallback, "complete_with_fallback", direct):
        result = await llm_agent.analyze_with_llm("BTC/USDT", "perp", {})

    assert result is not None
    logical = (
        get_db()
        .execute(
            "SELECT logical_call_id,status,attempt_count,final_model "
            "FROM llm_inference_calls WHERE caller='committee.llm_analysis'"
        )
        .fetchone()
    )
    assert logical["status"] == "success"
    assert logical["attempt_count"] == 1
    assert logical["final_model"] == "fixed-test-model"
    attempts = (
        get_db()
        .execute(
            "SELECT COUNT(*) FROM runtime_cost_events WHERE call_id=?",
            (logical["logical_call_id"],),
        )
        .fetchone()[0]
    )
    assert attempts == 1


@pytest.mark.asyncio
async def test_committee_rejects_invalid_schema_before_recording_success():
    from aletheia.agents.committee import llm_agent
    from aletheia.model_provider import fallback

    provider = _RepairProvider([{"direction": "sideways", "evidence": "text"}])

    async def direct(request, **_kwargs):
        return await provider.complete(request)

    with patch.object(fallback, "complete_with_fallback", direct):
        result = await llm_agent.analyze_with_llm("BTC/USDT", "perp", {})

    assert result is None
    logical = get_db().execute(
        "SELECT status,parse_valid,schema_valid,error_type "
        "FROM llm_inference_calls WHERE caller='committee.llm_analysis'"
    ).fetchone()
    assert dict(logical) == {
        "status": "error",
        "parse_valid": 1,
        "schema_valid": 0,
        "error_type": "structured_schema_invalid",
    }


@pytest.mark.asyncio
async def test_context_artifacts_link_to_exact_evaluation_call():
    from aletheia.agents import helpers

    provider = _RepairProvider([{"value": 3}])
    evaluation = {
        "evaluation_pair_id": "pair-test",
        "evaluation_arm": "treatment",
        "fixture_id": "fixture-test",
    }
    artifacts = [
        {
            "kind": "learning",
            "stable_id": "learning:test-bundle",
            "source": "evaluation_fixture",
            "selected_reason": "paired_treatment",
            "trust_class": "learned_advisory",
            "freshness_status": "fresh",
            "rendered_chars": 42,
        }
    ]
    with (
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(helpers, "complete_with_fallback", _physical_complete),
    ):
        data, error = await helpers.llm_structured(
            "system",
            "user",
            schema=_ValueSchema,
            caller="evaluation.learning_b.treatment",
            allow_model_fallback=False,
            context_artifacts=artifacts,
            context_symbol="BTC/USDT",
            evaluation=evaluation,
        )

    assert error is None and data == {"value": 3}
    db = get_db()
    call = db.execute(
        "SELECT logical_call_id,allow_model_fallback FROM llm_inference_calls "
        "WHERE caller='evaluation.learning_b.treatment'"
    ).fetchone()
    artifact = db.execute(
        "SELECT logical_call_id,evaluation_pair_id,evaluation_arm,fixture_id,rendered_chars FROM llm_context_artifacts"
    ).fetchone()
    assert call["allow_model_fallback"] == 0
    assert dict(artifact) == {
        "logical_call_id": call["logical_call_id"],
        "evaluation_pair_id": "pair-test",
        "evaluation_arm": "treatment",
        "fixture_id": "fixture-test",
        "rendered_chars": 42,
    }


@pytest.mark.asyncio
async def test_operator_inference_records_existing_causal_identity_without_fabricating_proposal():
    from aletheia.agent_runtime.run_context import bind_economic_attribution
    from aletheia.agents import helpers

    provider = _RepairProvider([{"value": 3}])
    with (
        bind_economic_attribution(
            logical_agent="pi_trading_decision",
            evaluation_id="operator_eval_exact",
            opportunity_id="opp-exact",
            decision_id="pending-exact",
            arbitrary_text="must-not-persist",
        ),
        patch.object(helpers, "get_provider_async", lambda _name: _make_async_provider(provider)),
        patch.object(helpers, "complete_with_fallback", _physical_complete),
    ):
        data, error = await helpers.llm_structured(
            "system",
            "user",
            schema=_ValueSchema,
            caller="system_operator.assess_opportunity",
        )

    assert error is None and data == {"value": 3}
    db = get_db()
    logical = db.execute(
        "SELECT run_id,metadata_json FROM llm_inference_calls WHERE caller='system_operator.assess_opportunity'"
    ).fetchone()
    assert logical is not None
    assert logical["run_id"] is None
    environment = json.loads(logical["metadata_json"])["environment"]
    assert environment["evaluation_id"] == "operator_eval_exact"
    assert environment["opportunity_id"] == "opp-exact"
    assert environment["decision_id"] == "pending-exact"
    assert "proposal_id" not in environment
    assert "arbitrary_text" not in environment

    physical = db.execute(
        "SELECT run_id,opportunity_id,decision_id,proposal_id,metadata_json "
        "FROM runtime_cost_events WHERE call_id=("
        "SELECT logical_call_id FROM llm_inference_calls "
        "WHERE caller='system_operator.assess_opportunity')"
    ).fetchone()
    assert physical is not None
    assert physical["run_id"] is None
    assert physical["opportunity_id"] == "opp-exact"
    assert physical["decision_id"] == "pending-exact"
    assert physical["proposal_id"] is None
    metadata = json.loads(physical["metadata_json"])
    assert metadata["evaluation_id"] == "operator_eval_exact"
    assert "arbitrary_text" not in metadata


@pytest.mark.asyncio
async def test_no_fallback_request_skips_candidate_discovery(monkeypatch):
    from aletheia.model_provider import fallback
    from aletheia.model_provider.base import ModelRequest

    provider = _RepairProvider([{"value": 1}])

    async def should_not_run(*_args, **_kwargs):
        raise AssertionError("candidate discovery must be skipped")

    async def direct(_name, target, request):
        return await target.complete(request)

    monkeypatch.setattr(fallback, "_candidate_models", should_not_run)
    monkeypatch.setattr(fallback, "brokered_complete", direct)
    response = await fallback.complete_with_fallback(
        ModelRequest(
            messages=[{"role": "user", "content": "x"}],
            allow_model_fallback=False,
        ),
        provider_name="harness_test",
        provider=provider,
    )
    assert response.model == "fixed-test-model"


def test_prompt_template_hash_is_separate_from_rendered_context():
    from aletheia.model_provider.inference_contract import build_inference_call

    common = {
        "caller": "agent_shell.general_query",
        "prompt_static_text": "stable system template",
        "messages": [{"role": "user", "content": "same request"}],
        "context_adapter_id": "minimal-v1",
    }
    first = build_inference_call(
        **common,
        system="runtime header A\nstable system template",
    )
    second = build_inference_call(
        **common,
        system="runtime header B\nstable system template",
    )

    assert first.prompt_static_sha256 == second.prompt_static_sha256
    assert first.rendered_prompt_sha256 != second.rendered_prompt_sha256


def test_economics_schema_tolerates_pre_duration_tool_table():
    from aletheia.economics.schema import ensure_economics_schema

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE agent_tool_calls ("
        "id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool_name TEXT NOT NULL, "
        "tool_class TEXT NOT NULL, input_data TEXT, output_data TEXT, "
        "permitted INTEGER NOT NULL, inference_call_id TEXT, created_at TEXT NOT NULL)"
    )

    ensure_economics_schema(db)

    assert db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_inference_calls'").fetchone()
    assert db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_structured_attempt_outcomes'"
    ).fetchone()
    assert not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='trg_agent_tool_call_cost_event'"
    ).fetchone()

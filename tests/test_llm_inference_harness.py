"""Focused runtime proof for the logical LLM inference envelope."""

from __future__ import annotations

import asyncio
import json
import sqlite3
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


async def _physical_complete(request, *, provider=None, provider_name=None):
    assert provider is not None
    return await provider.complete(request)


@pytest.mark.asyncio
async def test_structured_repair_attempts_share_one_logical_call():
    from aletheia.agents import helpers

    provider = _RepairProvider([{"value": "wrong"}, {"value": 7}])
    with (
        patch.object(helpers, "get_provider", lambda _name: provider),
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
    logical = db.execute(
        "SELECT * FROM llm_inference_calls WHERE caller='market_observer.market_read'"
    ).fetchone()
    assert logical is not None
    assert logical["status"] == "success"
    assert logical["parse_valid"] == 1
    assert logical["schema_valid"] == 1
    assert logical["attempt_count"] == 2
    attempts = db.execute(
        "SELECT call_id,attempt_index FROM runtime_cost_events "
        "WHERE event_type='llm' ORDER BY attempt_index"
    ).fetchall()
    assert [row["call_id"] for row in attempts] == [
        logical["logical_call_id"],
        logical["logical_call_id"],
    ]
    assert [row["attempt_index"] for row in attempts] == [0, 1]


@pytest.mark.asyncio
async def test_schema_invalid_object_never_becomes_success():
    from aletheia.agents import helpers

    provider = _RepairProvider(
        [{"value": "wrong"}, {"value": "still-wrong"}, {"value": None}]
    )
    with (
        patch.object(helpers, "get_provider", lambda _name: provider),
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
    logical = get_db().execute(
        "SELECT status,parse_valid,schema_valid,attempt_count,error_type "
        "FROM llm_inference_calls WHERE caller='market_observer.market_read'"
    ).fetchone()
    assert dict(logical) == {
        "status": "error",
        "parse_valid": 1,
        "schema_valid": 0,
        "attempt_count": 3,
        "error_type": "structured_schema_invalid",
    }


@pytest.mark.asyncio
async def test_external_cancellation_finalizes_structured_logical_call_and_propagates():
    from aletheia.agents import helpers

    entered_provider = asyncio.Event()

    async def hanging_complete(*_args, **_kwargs):
        entered_provider.set()
        await asyncio.Future()

    with (
        patch.object(helpers, "get_provider", lambda _name: object()),
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

    logical = get_db().execute(
        "SELECT status,finished_at,parse_valid,schema_valid,error_type "
        "FROM llm_inference_calls WHERE caller='news_analyzer.analysis'"
    ).fetchone()
    assert logical is not None
    assert logical["status"] == "error"
    assert logical["finished_at"] is not None
    assert logical["parse_valid"] is None
    assert logical["schema_valid"] is None
    assert logical["error_type"] == "cancelled"


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
        patch.object(helpers, "get_provider", lambda _name: provider),
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
        "SELECT logical_call_id,evaluation_pair_id,evaluation_arm,fixture_id,rendered_chars "
        "FROM llm_context_artifacts"
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
        patch.object(helpers, "get_provider", lambda _name: provider),
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
        "SELECT run_id,metadata_json FROM llm_inference_calls "
        "WHERE caller='system_operator.assess_opportunity'"
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


def test_economics_schema_tolerates_pre_migration_tool_table():
    from aletheia.economics.schema import ensure_economics_schema

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE agent_tool_calls ("
        "id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tool_name TEXT NOT NULL, "
        "tool_class TEXT NOT NULL, permitted INTEGER NOT NULL, created_at TEXT NOT NULL)"
    )

    ensure_economics_schema(db)

    assert db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_inference_calls'"
    ).fetchone()
    assert not db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' "
        "AND name='trg_agent_tool_call_cost_event'"
    ).fetchone()

"""Runtime cost facts: pricing, physical attempts, tools, and AgentRun aggregation."""

from __future__ import annotations

import pytest

from aletheia.agent_runtime.run_context import (
    reset_current_agent_run_id,
    set_current_agent_run_id,
)
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import (
    add_pricing_snapshot,
    add_tool_cost_profile,
    aggregate_agent_run_cost,
    initialize_runtime_tracing,
)
from aletheia.model_provider.base import ModelProvider, ModelRequest, ModelResponse
from aletheia.storage.models import AgentRun
from aletheia.storage.repositories import insert_agent_run
from aletheia.storage.sqlite import get_db


class _MeasuredProvider(ModelProvider):
    name = "measured_test"

    def __init__(self, *, fail: bool = False):
        self.model = "measured-model"
        self.fail = fail

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if self.fail:
            raise RuntimeError("provider failed")
        return ModelResponse(
            content="ok",
            model=self.model,
            usage={"prompt_tokens": 1_000, "completion_tokens": 500},
        )

    async def structured(self, request, schema):  # pragma: no cover - contract stub
        return {}

    async def healthcheck(self):  # pragma: no cover - contract stub
        return None


@pytest.mark.asyncio
async def test_physical_provider_attempt_persists_usage_and_cost():
    ensure_economics_schema()
    run = AgentRun(
        agent_name="pi_trading_decision",
        trigger_type="test",
        permission_mode="Paper",
    )
    insert_agent_run(run)
    add_pricing_snapshot(
        provider="measured_test",
        model="measured-model",
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="test",
    )

    token = set_current_agent_run_id(run.id)
    try:
        await _MeasuredProvider().complete(ModelRequest(messages=[]))
    finally:
        reset_current_agent_run_id(token)

    row = get_db().execute(
        "SELECT * FROM runtime_cost_events WHERE run_id=? AND event_type='llm'",
        (run.id,),
    ).fetchone()
    assert row is not None
    assert row["status"] == "success"
    assert row["cost_status"] == "estimated"
    assert row["estimated_cost_usd"] == pytest.approx(0.002)

    aggregate = aggregate_agent_run_cost(run.id)
    assert aggregate["cost_complete"] is True
    assert aggregate["cost_estimate_usd"] == pytest.approx(0.002)
    assert aggregate["final_model"] == "measured-model"


@pytest.mark.asyncio
async def test_failed_attempt_is_not_assumed_free():
    ensure_economics_schema()
    run = AgentRun(
        agent_name="pi_trading_decision",
        trigger_type="test",
        permission_mode="Paper",
    )
    insert_agent_run(run)
    token = set_current_agent_run_id(run.id)
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            await _MeasuredProvider(fail=True).complete(ModelRequest(messages=[]))
    finally:
        reset_current_agent_run_id(token)

    aggregate = aggregate_agent_run_cost(run.id)
    assert aggregate["event_count"] == 1
    assert aggregate["unknown_event_count"] == 1
    assert aggregate["cost_complete"] is False
    assert aggregate["cost_estimate_usd"] is None


@pytest.mark.asyncio
async def test_fallback_attempts_are_accounted_separately_on_one_request():
    ensure_economics_schema()
    run = AgentRun(
        agent_name="pi_trading_decision",
        trigger_type="test",
        permission_mode="Paper",
    )
    insert_agent_run(run)
    add_pricing_snapshot(
        provider="measured_test",
        model="measured-model",
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="test",
    )
    request = ModelRequest(messages=[])
    token = set_current_agent_run_id(run.id)
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            await _MeasuredProvider(fail=True).complete(request)
        await _MeasuredProvider().complete(request)
    finally:
        reset_current_agent_run_id(token)

    rows = get_db().execute(
        "SELECT attempt_index,status,cost_status FROM runtime_cost_events "
        "WHERE run_id=? AND event_type='llm' ORDER BY attempt_index",
        (run.id,),
    ).fetchall()
    assert [(row["attempt_index"], row["status"]) for row in rows] == [
        (0, "error"),
        (1, "success"),
    ]
    assert rows[0]["cost_status"] == "unknown"
    assert rows[1]["cost_status"] == "estimated"
    aggregate = aggregate_agent_run_cost(run.id)
    assert aggregate["known_cost_subtotal_usd"] == pytest.approx(0.002)
    assert aggregate["cost_estimate_usd"] is None


def test_existing_tool_ledger_is_mirrored_as_unknown_not_zero():
    initialize_runtime_tracing()
    run = AgentRun(
        agent_name="pi_trading_decision",
        trigger_type="test",
        permission_mode="Paper",
    )
    insert_agent_run(run)
    get_db().execute(
        """INSERT INTO agent_tool_calls
           (id,run_id,tool_name,tool_class,input_data,output_data,permitted,created_at)
           VALUES ('tool-economics-test',?,'market_lookup','read','{}','{}',1,
                   '2026-07-16T00:00:00+00:00')""",
        (run.id,),
    )
    row = get_db().execute(
        "SELECT * FROM runtime_cost_events WHERE id='toolcost_tool-economics-test'"
    ).fetchone()
    assert row is not None
    assert row["event_type"] == "tool"
    assert row["cost_status"] == "unknown"
    assert row["estimated_cost_usd"] is None
    assert row["latency_ms"] is None


def test_tool_cost_profile_can_explicitly_price_local_or_external_tool():
    initialize_runtime_tracing()
    run = AgentRun(
        agent_name="pi_trading_decision",
        trigger_type="test",
        permission_mode="Paper",
    )
    insert_agent_run(run)
    add_tool_cost_profile(
        tool_name="local_lookup",
        usd_per_call=0.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="test-explicit-free",
    )
    get_db().execute(
        """INSERT INTO agent_tool_calls
           (id,run_id,tool_name,tool_class,input_data,output_data,permitted,created_at)
           VALUES ('tool-profile-test',?,'local_lookup','read','{}','{}',1,
                   '2026-07-16T00:00:00+00:00')""",
        (run.id,),
    )
    row = get_db().execute(
        "SELECT * FROM runtime_cost_events WHERE id='toolcost_tool-profile-test'"
    ).fetchone()
    assert row is not None
    assert row["cost_status"] == "estimated"
    assert row["estimated_cost_usd"] == pytest.approx(0.0)
    assert row["cost_profile_id"] is not None

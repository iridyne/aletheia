"""Runtime cost facts: pricing, physical attempts, tools, and AgentRun aggregation."""

from __future__ import annotations

import asyncio
import threading

async def _make_async_provider(provider):
    """Helper for tests: wrap sync mock provider in async."""
    return provider


import json

import pytest

pytestmark = pytest.mark.domain_runtime


@pytest.fixture(autouse=True)
def _cost_file_db(_inmemory_db, tmp_path, monkeypatch):
    """Use a file DB because tracing is intentionally persisted off-loop."""
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    reset_db()
    sq._db_conn = None
    run_migrations()
    yield
    reset_db()

from aletheia.agent_runtime.run_context import (
    bind_economic_attribution,
    reset_current_agent_run_id,
    set_current_agent_run_id,
)
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import (
    add_catalog_pricing_snapshot,
    aggregate_agent_run_cost,
    initialize_runtime_tracing,
    normalize_usage,
    record_llm_attempt,
)
from aletheia.model_provider.base import (
    ModelHealth,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    StructuredModelRequest,
    StreamChunk,
)
from aletheia.storage.models import AgentRun
from tests.helpers.repo_test_helpers import insert_agent_run
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

    async def structured(self, request: StructuredModelRequest, schema: type) -> dict:
        response = await self.complete(
            ModelRequest(
                messages=request.messages,
                system=request.system,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        )
        return {"content": response.content}

    async def healthcheck(self) -> ModelHealth:  # pragma: no cover - contract stub
        return ModelHealth(provider="measured", available=True, model=self.model)


def _insert_run() -> AgentRun:
    run = AgentRun(
        agent_name="pi_trading_decision",
        trigger_type="test",
        permission_mode="Paper",
    )
    insert_agent_run(run)
    return run


def _add_test_pricing() -> None:
    add_catalog_pricing_snapshot(
        provider="measured_test",
        model="measured-model",
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="litellm_public_median:test",
        version="catalog-model-family-test",
    )


def test_catalog_pricing_rejects_non_catalog_source():
    with pytest.raises(ValueError, match="LiteLLM public catalog"):
        add_catalog_pricing_snapshot(
            provider="p",
            model="m",
            input_usd_per_million=1.0,
            output_usd_per_million=2.0,
            source="manual",
            version="catalog-model-family-test",
        )


def test_normalize_usage_top_level_cache_read_falls_back_to_cached():
    """DeepSeek-style providers report cache hits at the top level as
    cache_read_input_tokens; those must normalize into cached_input_tokens
    instead of being silently dropped."""
    usage = normalize_usage(
        {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 768,
            "cache_creation_input_tokens": 0,
        }
    )
    assert usage["cached_input_tokens"] == 768
    assert usage["provider_cache_read_input_tokens"] == 768
    assert usage["provider_cache_creation_input_tokens"] == 0


def test_normalize_usage_prompt_details_cache_still_preferred():
    """OpenAI/Anthropic style prompt_tokens_details.cached_tokens remains the
    primary source and is not displaced by the top-level fallback."""
    usage = normalize_usage(
        {
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    )
    assert usage["cached_input_tokens"] == 40
    assert usage["provider_cache_read_input_tokens"] == 40


@pytest.mark.asyncio
async def test_physical_provider_attempt_persists_usage_and_cost():
    ensure_economics_schema()
    run = _insert_run()
    _add_test_pricing()

    token = set_current_agent_run_id(run.id)
    try:
        await _MeasuredProvider().complete(ModelRequest(messages=[]))
    finally:
        reset_current_agent_run_id(token)

    row = (
        get_db()
        .execute(
            "SELECT * FROM runtime_cost_events WHERE run_id=? AND event_type='llm'",
            (run.id,),
        )
        .fetchone()
    )
    assert row is not None
    assert row["status"] == "success"
    assert row["cost_status"] == "estimated"
    assert row["estimated_cost_usd"] == pytest.approx(0.002)
    assert json.loads(row["metadata_json"])["token_integrity_observations"] == []

    aggregate = aggregate_agent_run_cost(run.id)
    assert aggregate["cost_complete"] is True
    assert aggregate["cost_estimate_usd"] == pytest.approx(0.002)
    assert aggregate["final_model"] == "measured-model"


@pytest.mark.asyncio
async def test_cost_tracing_write_does_not_block_provider_loop(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def blocked_record(**_kwargs):
        entered.set()
        release.wait(timeout=2.0)

    monkeypatch.setattr(
        "aletheia.economics.tracing.record_llm_attempt", blocked_record
    )
    provider_task = asyncio.create_task(
        _MeasuredProvider().complete(ModelRequest(messages=[]))
    )
    assert await asyncio.to_thread(entered.wait, 1.0)

    loop_tick = asyncio.Event()
    asyncio.get_running_loop().call_soon(loop_tick.set)
    await asyncio.wait_for(loop_tick.wait(), timeout=0.2)

    release.set()
    await provider_task


@pytest.mark.asyncio
async def test_failed_attempt_is_not_assumed_free():
    ensure_economics_schema()
    run = _insert_run()
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


def test_provider_reported_cost_wins_without_model_pricing():
    event_id = record_llm_attempt(
        call_id="provider-reported",
        provider="unknown-provider",
        model="hidden-model",
        requested_model="requested-model",
        attempt_index=0,
        status="success",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        latency_ms=1000,
        raw_usage={"provider_cost_usd": 0.0123},
    )

    row = (
        get_db()
        .execute(
            "SELECT estimated_cost_usd,cost_status,metadata_json FROM runtime_cost_events WHERE id=?",
            (event_id,),
        )
        .fetchone()
    )
    assert row["estimated_cost_usd"] == pytest.approx(0.0123)
    assert row["cost_status"] == "known"
    assert json.loads(row["metadata_json"])["cost_calculation"]["reason"] == "provider_reported"


@pytest.mark.asyncio
async def test_fallback_attempts_are_accounted_separately_on_one_request():
    ensure_economics_schema()
    run = _insert_run()
    _add_test_pricing()
    request = ModelRequest(messages=[])
    token = set_current_agent_run_id(run.id)
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            await _MeasuredProvider(fail=True).complete(request)
        await _MeasuredProvider().complete(request)
    finally:
        reset_current_agent_run_id(token)

    rows = (
        get_db()
        .execute(
            "SELECT attempt_index,status,cost_status FROM runtime_cost_events "
            "WHERE run_id=? AND event_type='llm' ORDER BY attempt_index",
            (run.id,),
        )
        .fetchall()
    )
    assert [(row["attempt_index"], row["status"]) for row in rows] == [
        (0, "error"),
        (1, "success"),
    ]
    assert rows[0]["cost_status"] == "unknown"
    assert rows[1]["cost_status"] == "estimated"
    aggregate = aggregate_agent_run_cost(run.id)
    assert aggregate["known_cost_subtotal_usd"] == pytest.approx(0.002)
    assert aggregate["cost_estimate_usd"] is None


@pytest.mark.asyncio
async def test_structured_fallbacks_share_logical_call_identity():
    ensure_economics_schema()
    run = _insert_run()
    _add_test_pricing()
    request = StructuredModelRequest(messages=[])
    token = set_current_agent_run_id(run.id)
    try:
        with pytest.raises(RuntimeError, match="provider failed"):
            await _MeasuredProvider(fail=True).structured(request, dict)
        await _MeasuredProvider().structured(request, dict)
    finally:
        reset_current_agent_run_id(token)

    rows = (
        get_db()
        .execute(
            "SELECT call_id,attempt_index,status,requested_model,metadata_json "
            "FROM runtime_cost_events WHERE run_id=? AND event_type='llm' "
            "ORDER BY attempt_index",
            (run.id,),
        )
        .fetchall()
    )
    assert len(rows) == 2
    assert len({row["call_id"] for row in rows}) == 1
    assert [row["attempt_index"] for row in rows] == [0, 1]
    assert [row["status"] for row in rows] == ["error", "success"]
    assert {row["requested_model"] for row in rows} == {"measured-model"}
    assert all(json.loads(row["metadata_json"])["structured"] for row in rows)


@pytest.mark.asyncio
async def test_direct_operator_call_uses_explicit_evaluation_identity():
    ensure_economics_schema()
    _add_test_pricing()

    with bind_economic_attribution(
        logical_agent="pi_trading_decision",
        evaluation_id="operator_eval_test",
        trigger="signal",
    ):
        await _MeasuredProvider().complete(ModelRequest(messages=[]))

    row = (
        get_db()
        .execute("SELECT * FROM runtime_cost_events WHERE event_type='llm' ORDER BY rowid DESC LIMIT 1")
        .fetchone()
    )
    assert row is not None
    assert row["run_id"] is None
    metadata = json.loads(row["metadata_json"])
    assert metadata["logical_agent"] == "pi_trading_decision"
    assert metadata["evaluation_id"] == "operator_eval_test"
    assert metadata["identity_source"] == "explicit_economic_context"




def test_economic_attribution_rejects_unregistered_metadata_keys():
    from aletheia.agent_runtime.run_context import (
        bind_economic_attribution,
        current_economic_attribution,
    )

    with bind_economic_attribution(
        logical_agent="pi_trading_decision",
        evaluation_id="operator_eval_exact",
        arbitrary_text="must-not-persist",
        response_body="must-not-persist",
    ):
        assert current_economic_attribution() == {
            "logical_agent": "pi_trading_decision",
            "evaluation_id": "operator_eval_exact",
        }


@pytest.mark.asyncio
async def test_inherited_stream_records_one_physical_attempt_not_two():
    ensure_economics_schema()
    run = _insert_run()
    _add_test_pricing()
    token = set_current_agent_run_id(run.id)
    try:
        chunks = [chunk async for chunk in _MeasuredProvider().stream(ModelRequest(messages=[]))]
    finally:
        reset_current_agent_run_id(token)
    assert chunks[-1].done is True
    count = (
        get_db()
        .execute(
            "SELECT COUNT(*) FROM runtime_cost_events WHERE run_id=? AND event_type='llm'",
            (run.id,),
        )
        .fetchone()[0]
    )
    assert count == 1


@pytest.mark.asyncio
async def test_direct_stream_records_final_usage_once_with_actual_model():
    class _DirectStreamProvider(ModelProvider):
        name = "measured_test"
        model = "requested-model"

        async def stream(self, request: ModelRequest):
            yield StreamChunk(content="ok", model="actual-model")
            yield StreamChunk(
                content="",
                done=True,
                model="actual-model",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
            )

    ensure_economics_schema()
    add_catalog_pricing_snapshot(
        provider="measured_test",
        model="actual-model",
        input_usd_per_million=1,
        output_usd_per_million=2,
        effective_from="2020-01-01T00:00:00+00:00",
        source="litellm_public_median:test",
        version="catalog-model-family-stream",
    )

    chunks = [chunk async for chunk in _DirectStreamProvider().stream(ModelRequest(messages=[]))]
    rows = (
        get_db().execute("SELECT model_or_tool,usage_json FROM runtime_cost_events WHERE event_type='llm'").fetchall()
    )

    assert chunks[-1].done is True
    assert len(rows) == 1
    assert rows[0]["model_or_tool"] == "actual-model"
    assert json.loads(rows[0]["usage_json"])["normalized"]["input_tokens"] == 10


def test_local_tool_ledger_is_mirrored_as_zero_external_cost():
    initialize_runtime_tracing()
    run = _insert_run()
    get_db().execute(
        """INSERT INTO agent_tool_calls
           (id,run_id,tool_name,tool_class,input_data,output_data,permitted,duration_ms,created_at)
           VALUES ('tool-economics-test',?,'market_lookup','read','{}','{}',1,125,
                   '2026-07-16T00:00:00+00:00')""",
        (run.id,),
    )
    row = get_db().execute("SELECT * FROM runtime_cost_events WHERE id='toolcost_tool-economics-test'").fetchone()
    assert row is not None
    assert row["event_type"] == "tool"
    assert row["cost_status"] == "estimated"
    assert row["estimated_cost_usd"] == 0
    assert row["latency_ms"] == 125


def test_preexisting_tool_rows_are_backfilled_idempotently():
    initialize_runtime_tracing()
    run = _insert_run()
    db = get_db()
    db.execute(
        """INSERT INTO agent_tool_calls
           (id,run_id,tool_name,tool_class,input_data,output_data,permitted,created_at)
           VALUES ('tool-backfill-test',?,'historical_lookup','read','{}','{}',1,
                   '2026-07-15T00:00:00+00:00')""",
        (run.id,),
    )
    db.execute("DELETE FROM runtime_cost_events WHERE id='toolcost_tool-backfill-test'")
    db.execute("DELETE FROM economics_settings WHERE key='tool_cost_backfill_v1'")

    ensure_economics_schema(db)
    ensure_economics_schema(db)

    rows = db.execute("SELECT * FROM runtime_cost_events WHERE id='toolcost_tool-backfill-test'").fetchall()
    assert len(rows) == 1
    assert rows[0]["cost_status"] == "estimated"
    assert rows[0]["estimated_cost_usd"] == 0
    assert rows[0]["latency_ms"] is None
    metadata = json.loads(rows[0]["metadata_json"])
    assert metadata["cost_source"] == "catalog_model_tool_cost_v1"
    assert metadata["legacy_backfill"] == 1


def test_denied_tool_call_is_known_zero_cost():
    initialize_runtime_tracing()
    run = _insert_run()
    get_db().execute(
        """INSERT INTO agent_tool_calls
           (id,run_id,tool_name,tool_class,input_data,output_data,permitted,created_at)
           VALUES ('tool-denied-test',?,'paid_external_api','write','{}','{}',0,
                   '2026-07-16T00:00:00+00:00')""",
        (run.id,),
    )
    row = get_db().execute("SELECT * FROM runtime_cost_events WHERE id='toolcost_tool-denied-test'").fetchone()
    assert row is not None
    assert row["status"] == "denied"
    assert row["cost_status"] == "known"
    assert row["estimated_cost_usd"] == pytest.approx(0.0)


def test_local_agent_tool_has_zero_external_cost_without_manual_profile():
    initialize_runtime_tracing()
    run = _insert_run()
    get_db().execute(
        """INSERT INTO agent_tool_calls
           (id,run_id,tool_name,tool_class,input_data,output_data,permitted,created_at)
           VALUES ('tool-profile-test',?,'local_lookup','read','{}','{}',1,
                   '2026-07-16T00:00:00+00:00')""",
        (run.id,),
    )
    row = get_db().execute("SELECT * FROM runtime_cost_events WHERE id='toolcost_tool-profile-test'").fetchone()
    assert row is not None
    assert row["cost_status"] == "estimated"
    assert row["estimated_cost_usd"] == pytest.approx(0.0)
    assert row["cost_profile_id"] is None
    assert json.loads(row["metadata_json"])["cost_source"] == "catalog_model_tool_cost_v1"


def test_first_schema_install_refuses_to_commit_an_active_transaction():
    db = get_db()
    # get_db() / injected test connections always use isolation_level=None
    # (autocommit). A plain INSERT commits immediately; in_transaction stays
    # False. Issue an explicit BEGIN to open a real transaction so the
    # ensure_economics_schema() guard can fire.
    db.execute("BEGIN")
    db.execute("INSERT INTO settings(key,value) VALUES ('economics-tx-test','pending')")
    assert db.in_transaction is True
    with pytest.raises(RuntimeError, match="active transaction"):
        ensure_economics_schema(db)
    db.rollback()
    assert db.execute("SELECT value FROM settings WHERE key='economics-tx-test'").fetchone() is None


def test_initialized_schema_probe_does_not_commit_an_active_transaction():
    db = ensure_economics_schema()
    # See note in test_first_schema_install_refuses_to_commit_an_active_transaction:
    # autocommit connections require an explicit BEGIN before in_transaction is True.
    db.execute("BEGIN")
    db.execute("INSERT INTO settings(key,value) VALUES ('economics-tx-ready','pending')")
    assert db.in_transaction is True
    assert ensure_economics_schema(db) is db
    assert db.in_transaction is True
    db.rollback()
    assert db.execute("SELECT value FROM settings WHERE key='economics-tx-ready'").fetchone() is None


def test_settings_repair_does_not_backfill_when_tracing_is_disabled(monkeypatch):
    initialize_runtime_tracing()
    run = _insert_run()
    db = get_db()
    db.execute(
        "INSERT INTO agent_tool_calls "
        "(id,run_id,tool_name,tool_class,input_data,output_data,permitted,created_at) "
        "VALUES ('repair-tool',?,'historical_lookup','read','{}','{}',1,'2026-07-15T00:00:00+00:00')",
        (run.id,),
    )
    db.execute("DELETE FROM runtime_cost_events WHERE id='toolcost_repair-tool'")
    db.execute("DROP TABLE economics_settings")
    monkeypatch.setenv("ALETHEIA_COST_TRACING", "0")
    assert initialize_runtime_tracing() is False
    assert db.execute("SELECT COUNT(*) FROM runtime_cost_events WHERE id='toolcost_repair-tool'").fetchone()[0] == 0
    assert db.execute("SELECT value FROM economics_settings WHERE key='cost_tracing_enabled'").fetchone()[0] == "0"

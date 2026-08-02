"""Read-only /api/economics/* API contract.

These tests exercise the seven Phase 1 GET endpoints defined in
`docs/design/proposals/tokenomics_harness_phase_plan_2026-07-30.md`
§4 Phase 1. They only assert route shape, kill-switch honesty, and
empty/seeded behaviour — the underlying economics logic is already
covered by `test_economic_cost_tracing`, `test_economic_viability`,
and `test_economic_cli`.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.economics.budgets import add_budget
from aletheia.economics.schema import ensure_economics_schema
from aletheia.economics.tracing import add_pricing_snapshot, add_tool_cost_profile
from aletheia.model_provider.fallback import reset_model_health
from aletheia.server.app import app
from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    setattr(sq, "_db_conn", None)
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    setattr(sq, "_db_conn", conn)
    run_migrations()
    ensure_economics_schema()
    reset_model_health()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    reset_db()
    reset_model_health()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_llm_cost(
    db: sqlite3.Connection,
    *,
    event_id: str,
    cost: float | None,
    run_id: str | None = None,
    started_at: str | None = None,
    usage_json: str = "{}",
    metadata_json: str = "{}",
) -> None:
    now = started_at or _now()
    status = "known" if cost is not None else "unknown"
    db.execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,run_id,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,cost_status,
            metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, "llm", run_id, event_id, "test", "model", 0, "success",
            now, now, usage_json, cost, "USD", status, metadata_json, now,
        ),
    )


# ── /api/economics/status ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_status_returns_kill_switch_state(client):
    resp = await client.get("/api/economics/status")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["tracing_enabled"], bool)
    assert isinstance(data["audit_enabled"], bool)


@pytest.mark.anyio
async def test_status_reflects_tracing_kill_switch(client, monkeypatch):
    monkeypatch.setenv("ALETHEIA_COST_TRACING", "0")
    resp = await client.get("/api/economics/status")
    assert resp.status_code == 200
    assert resp.json()["tracing_enabled"] is False


# ── /api/economics/costs ─────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_costs_empty_ledger(client):
    resp = await client.get("/api/economics/costs?days=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_count"] == 0
    assert data["known_cost_subtotal_usd"] == 0
    assert data["unknown_event_count"] == 0
    assert data["by_type"] == {}
    assert "events" not in data  # include_events default False


@pytest.mark.anyio
async def test_costs_known_and_unknown(client):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_llm_cost(db, event_id="known-1", cost=0.25)
    _seed_llm_cost(db, event_id="unknown-1", cost=None)

    resp = await client.get("/api/economics/costs?days=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_count"] == 2
    assert data["known_cost_subtotal_usd"] == pytest.approx(0.25)
    assert data["unknown_event_count"] == 1
    assert data["by_type"]["llm"]["event_count"] == 2
    assert data["by_type"]["llm"]["unknown_count"] == 1
    assert data["by_type"]["llm"]["known_cost_usd"] == pytest.approx(0.25)


@pytest.mark.anyio
async def test_costs_include_events_capped_by_limit(client):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(3):
        _seed_llm_cost(db, event_id=f"ev-{i}", cost=0.1)

    resp = await client.get(
        "/api/economics/costs?days=1&include_events=true&event_limit=2"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_count"] == 3
    assert len(data["events"]) == 2


@pytest.mark.anyio
async def test_costs_run_id_filter(client):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_llm_cost(db, event_id="run-a", cost=0.1, run_id="agent_run_A")
    _seed_llm_cost(db, event_id="run-b", cost=0.2, run_id="agent_run_B")

    resp = await client.get("/api/economics/costs?days=1&run_id=agent_run_A")
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_count"] == 1
    assert data["known_cost_subtotal_usd"] == pytest.approx(0.1)


@pytest.mark.anyio
async def test_costs_rejects_invalid_days(client):
    resp = await client.get("/api/economics/costs?days=0")
    assert resp.status_code == 422


# ── /api/economics/tokens ────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_tokens_summarize_provider_usage_without_cost(client):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_llm_cost(
        db,
        event_id="token-known",
        cost=None,
        usage_json=(
            '{"normalized":{"input_tokens":10,"output_tokens":4,'
            '"total_tokens":14,"cached_input_tokens":3,'
            '"provider_cache_read_input_tokens":2,'
            '"provider_cache_creation_input_tokens":1,"reasoning_tokens":null}}'
        ),
        metadata_json='{"logical_agent":"market_observer"}',
    )
    _seed_llm_cost(db, event_id="token-unavailable", cost=None)

    resp = await client.get("/api/economics/tokens?days=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["llm_attempt_count"] == 2
    assert data["input_output_available_attempt_count"] == 1
    assert data["input_output_unavailable_attempt_count"] == 1
    assert data["tokens"] == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "reasoning_tokens": 0,
        "cached_input_tokens": 3,
        "provider_cache_read_input_tokens": 2,
        "provider_cache_creation_input_tokens": 1,
    }
    assert data["field_availability"]["cached_input_tokens"] == 1
    assert data["by_logical_agent"]["market_observer"]["input_tokens"] == 10


@pytest.mark.anyio
async def test_tokens_filters_by_run_id(client):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_llm_cost(
        db,
        event_id="token-run-a",
        cost=None,
        run_id="run-a",
        usage_json='{"normalized":{"input_tokens":5,"output_tokens":2}}',
    )
    _seed_llm_cost(
        db,
        event_id="token-run-b",
        cost=None,
        run_id="run-b",
        usage_json='{"normalized":{"input_tokens":9,"output_tokens":3}}',
    )

    resp = await client.get("/api/economics/tokens?days=1&run_id=run-a")
    assert resp.status_code == 200
    assert resp.json()["tokens"]["input_tokens"] == 5


# ── /api/economics/viability ─────────────────────────────────────────────────


@pytest.mark.anyio
async def test_viability_disabled_returns_503(client, monkeypatch):
    monkeypatch.setenv("ALETHEIA_VIABILITY_AUDIT", "0")
    resp = await client.get("/api/economics/viability?scope=system&days=1")
    assert resp.status_code == 503
    assert "ALETHEIA_VIABILITY_AUDIT" in resp.json()["detail"]


@pytest.mark.anyio
async def test_viability_get_does_not_persist(client, monkeypatch):
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda **kwargs: [],
    )
    resp = await client.get(
        "/api/economics/viability?scope=system&days=1"
        "&data_cost_usd=0&infrastructure_cost_usd=0&other_cost_usd=0"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["scope"] == "system"
    assert data["verdict"] in {"insufficient_data", "invalid"}

    persisted = get_db().execute(
        "SELECT COUNT(*) FROM viability_reports"
    ).fetchone()[0]
    assert persisted == 0


@pytest.mark.anyio
async def test_viability_rejects_invalid_scope(client):
    resp = await client.get("/api/economics/viability?scope=invalid&days=1")
    assert resp.status_code == 422


# ── /api/economics/viability/reports ─────────────────────────────────────────


def _seed_viability_report(
    db: sqlite3.Connection, *, report_id: str, scope: str = "system"
) -> None:
    now = _now()
    db.execute(
        """INSERT INTO viability_reports (
            id,dedup_key,scope,window_start,window_end,execution_mode,
            metric_version,net_trading_result_usd,deployment_cost_usd,
            viability_amount_usd,verdict,cost_completeness,sample_count,
            exclusion_counts_json,assumptions_json,reason,generated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            report_id, f"dedup-{report_id}", scope, now, now, "paper_internal",
            "test", 1.0, 0.5, 0.5, "positive", "complete", 1, "{}", "[]",
            "test", now,
        ),
    )


@pytest.mark.anyio
async def test_viability_reports_empty(client):
    resp = await client.get("/api/economics/viability/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert data["reports"] == []
    assert data["total"] == 0


@pytest.mark.anyio
async def test_viability_reports_pagination(client):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    for i in range(5):
        _seed_viability_report(db, report_id=f"rep-{i}")

    resp = await client.get("/api/economics/viability/reports?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["reports"]) == 2
    assert data["limit"] == 2
    assert data["offset"] == 0
    assert data["total"] == 5


@pytest.mark.anyio
async def test_viability_reports_scope_filter(client):
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _seed_viability_report(db, report_id="sys-1", scope="system")
    _seed_viability_report(db, report_id="op-1", scope="operator")

    resp = await client.get("/api/economics/viability/reports?scope=system")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["reports"][0]["scope"] == "system"


# ── /api/economics/model-health ──────────────────────────────────────────────


@pytest.mark.anyio
async def test_model_health_empty(client):
    resp = await client.get("/api/economics/model-health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["snapshot"] == []


@pytest.mark.anyio
async def test_model_health_reflects_cooldown(client):
    from aletheia.model_provider.fallback import _record_model_failure

    for _ in range(2):
        _record_model_failure(
            "test_provider", "test_model", RuntimeError("simulated 503")
        )

    resp = await client.get("/api/economics/model-health")
    assert resp.status_code == 200
    entries = resp.json()["snapshot"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["provider"] == "test_provider"
    assert entry["model"] == "test_model"
    assert entry["cooling_down"] is True
    assert entry["cooldown_remaining_sec"] > 0


# ── /api/economics/pricing ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_pricing_empty(client):
    resp = await client.get("/api/economics/pricing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert data["tools"] == []


# ── /api/economics/budgets ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_budgets_empty(client):
    resp = await client.get("/api/economics/budgets")
    assert resp.status_code == 200
    assert resp.json()["budgets"] == []


@pytest.mark.anyio
async def test_budgets_returns_declared_policies(client):
    add_budget(scope="system", subject=None, usd_cap=10.0, window="daily")
    add_budget(
        scope="logical_agent",
        subject="market_observer",
        usd_cap=1.0,
        window="daily",
    )

    resp = await client.get("/api/economics/budgets")
    assert resp.status_code == 200
    rows = resp.json()["budgets"]
    assert len(rows) == 2


@pytest.mark.anyio
async def test_budgets_scope_filter(client):
    add_budget(scope="system", subject=None, usd_cap=10.0, window="daily")
    add_budget(
        scope="logical_agent",
        subject="market_observer",
        usd_cap=1.0,
        window="daily",
    )

    resp = await client.get("/api/economics/budgets?scope=logical_agent")
    assert resp.status_code == 200
    rows = resp.json()["budgets"]
    assert len(rows) == 1
    assert rows[0]["subject"] == "market_observer"


@pytest.mark.anyio
async def test_budgets_enabled_only(client):
    add_budget(
        scope="system", subject=None, usd_cap=10.0, window="daily", enabled=False
    )
    resp = await client.get("/api/economics/budgets?enabled_only=true")
    assert resp.status_code == 200
    assert resp.json()["budgets"] == []


# ── /api/economics/budgets/would-block ───────────────────────────────────────


def _seed_would_block_event(
    db,
    *,
    event_id: str,
    observations: list[dict],
    scope: str | None = None,
) -> None:
    now = _now()
    metadata = {"would_block": observations}
    db.execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,run_id,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,cost_status,
            metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, "llm", None, event_id, "test", "model", 0, "success",
            now, now, "{}", 0.1, "USD", "known",
            __import__("json").dumps(metadata), now,
        ),
    )


@pytest.mark.anyio
async def test_budgets_would_block_empty(client):
    resp = await client.get("/api/economics/budgets/would-block")
    assert resp.status_code == 200
    body = resp.json()
    assert body["events"] == []
    assert body["window_days"] == 7


@pytest.mark.anyio
async def test_budgets_would_block_returns_events_with_observations(client):
    from aletheia.storage.sqlite import get_db

    _seed_would_block_event(
        get_db(),
        event_id="wb-1",
        observations=[
            {"policy_id": "p1", "scope": "system", "usd_cap": 1.0,
             "spent_before_call_usd": 0.5, "projected_after_call_usd": 1.2,
             "over_by_usd": 0.2, "subject": None, "window": "daily"}
        ],
    )

    resp = await client.get("/api/economics/budgets/would-block?days=7")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 1
    assert events[0]["would_block"][0]["policy_id"] == "p1"


@pytest.mark.anyio
async def test_budgets_would_block_scope_filter(client):
    from aletheia.storage.sqlite import get_db

    _seed_would_block_event(
        get_db(),
        event_id="sys-wb",
        observations=[{"policy_id": "p_sys", "scope": "system"}],
    )
    _seed_would_block_event(
        get_db(),
        event_id="la-wb",
        observations=[{"policy_id": "p_la", "scope": "logical_agent"}],
    )

    resp = await client.get("/api/economics/budgets/would-block?scope=logical_agent")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 1
    assert events[0]["would_block"][0]["policy_id"] == "p_la"


# ── /api/economics/pricing ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_pricing_returns_seeded_snapshots(client):
    add_pricing_snapshot(
        provider="test",
        model="model-a",
        input_usd_per_million=1.0,
        output_usd_per_million=2.0,
        effective_from="2020-01-01T00:00:00+00:00",
        source="test",
    )
    add_tool_cost_profile(
        tool_name="test_tool",
        usd_per_call=0.001,
        effective_from="2020-01-01T00:00:00+00:00",
        source="test",
    )

    resp = await client.get("/api/economics/pricing")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["models"]) == 1
    assert data["models"][0]["provider"] == "test"
    assert data["models"][0]["model"] == "model-a"
    assert len(data["tools"]) == 1
    assert data["tools"][0]["tool_name"] == "test_tool"

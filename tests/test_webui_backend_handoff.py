"""Tests for the WEBUI_BACKEND_HANDOFF backend gaps that were implemented.

#1 position → proposal/order causal back-link schema.
Execution lineage is covered by spec_pipeline_exchange_materialization.
#2 GET /api/agent-runs/{id}/steps read-only reasoning-trace endpoint.

(#3 exchange equity touches change-controlled client.py — not built, needs auth.
 #4 news ingestion is banned scope — not built.)
"""

from tests.fixtures.repo_test_helpers import insert_agent_run, insert_run_step

import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


# --- #1 position causal back-link ---


def test_positions_migration_has_fk_columns(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    cols = {r["name"] for r in sq.get_db().execute("PRAGMA table_info(positions)").fetchall()}
    assert "proposal_id" in cols
    assert "order_id" in cols


# --- #2 reasoning-steps endpoint ---

def test_list_run_steps_ordered(tmp_path, monkeypatch):
    """Phase 3 A8 contract: `list_run_steps` UNIONs steps + tool_calls and
    orders by insertion (created_at). The caller-supplied step_number is not
    authoritative for sort — it's reassigned by merged execution order — because
    the tool-call side of the UNION has no such column. Callers that write in
    execution order (via helpers.log_tool / record_step) see a monotonic 1..N.
    """
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import AgentRun
    run = AgentRun(agent_name="market_observer", permission_mode="Observe")
    insert_agent_run(run)
    # Insert in execution order; caller-supplied step_number is ignored on read.
    insert_run_step(run.id, 1, "first", input_data="i1", output_data="o1")
    insert_run_step(run.id, 2, "second", input_data="i2", output_data="o2")
    steps = repo.list_run_steps(run.id)
    assert [s["step_number"] for s in steps] == [1, 2]
    assert steps[0]["action"] == "first"
    assert steps[1]["action"] == "second"


def test_list_run_steps_empty_is_honest(tmp_path, monkeypatch):
    """A run with no recorded steps returns [] (UI renders that honestly)."""
    setup_env(tmp_path, monkeypatch)
    assert repo.list_run_steps("nonexistent-run") == []


@pytest.mark.anyio
async def test_steps_endpoint_returns_ordered(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import AgentRun
    run = AgentRun(agent_name="market_observer", permission_mode="Observe")
    insert_agent_run(run)
    insert_run_step(run.id, 1, "observe", output_data="snapshot read")

    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/api/agent-runs/{run.id}/steps")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["action"] == "observe"

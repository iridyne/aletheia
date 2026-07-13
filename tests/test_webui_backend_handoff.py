"""Tests for the WEBUI_BACKEND_HANDOFF backend gaps that were implemented.

#1 position → proposal/order causal back-link (schema + execution path).
#2 GET /api/agent-runs/{id}/steps read-only reasoning-trace endpoint.

(#3 exchange equity touches change-controlled client.py — not built, needs auth.
 #4 news ingestion is banned scope — not built.)
"""

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

def test_paper_position_carries_proposal_and_order_fk(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
        proposal_id="prop-1", risk_decision_id="rd-1",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60000.0,
        execution_mode="paper_internal", strategy_id="strat-1",
        stop_price=58800.0,
    )
    r = execute_paper_order(
        proposal_id="prop-1", risk_decision_id="rd-1", symbol="BTC/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=60000.0, execution_mode="paper_internal",
        strategy_id="strat-1", stop_price=58800.0,
    )
    pos = repo.list_positions(status="open")
    assert len(pos) == 1
    assert pos[0]["proposal_id"] == "prop-1"
    assert pos[0]["order_id"] == r["order_id"]


def test_averaging_in_preserves_original_opener(tmp_path, monkeypatch):
    """Averaging into an existing position must keep the proposal/order that OPENED it."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
        proposal_id="prop-open", risk_decision_id="rd-1",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60000.0,
        execution_mode="paper_internal", strategy_id="strat-1",
        stop_price=58800.0,
    )
    r1 = execute_paper_order(
        proposal_id="prop-open", risk_decision_id="rd-1", symbol="BTC/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=60000.0, execution_mode="paper_internal",
        strategy_id="strat-1", stop_price=58800.0,
    )
    # Second fill, same symbol/direction/strategy → averages into the same position.
    seed_order_authority(
        proposal_id="prop-add", risk_decision_id="rd-2",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=61000.0,
        execution_mode="paper_internal", strategy_id="strat-1",
        stop_price=59000.0,
    )
    execute_paper_order(
        proposal_id="prop-add", risk_decision_id="rd-2", symbol="BTC/USDT",
        market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=61000.0, execution_mode="paper_internal",
        strategy_id="strat-1", stop_price=59000.0,
    )
    pos = repo.list_positions(status="open")
    assert len(pos) == 1  # averaged, not a second position
    assert pos[0]["proposal_id"] == "prop-open"  # original opener preserved
    assert pos[0]["order_id"] == r1["order_id"]


def test_positions_migration_has_fk_columns(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import aletheia.storage.sqlite as sq
    cols = {r["name"] for r in sq.get_db().execute("PRAGMA table_info(positions)").fetchall()}
    assert "proposal_id" in cols
    assert "order_id" in cols


# --- #2 reasoning-steps endpoint ---

def test_list_run_steps_ordered(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import AgentRun
    run = AgentRun(agent_name="market_observer", permission_mode="Observe")
    repo.insert_agent_run(run)
    # Insert out of order; list must return them sorted by step_number.
    repo.insert_run_step(run.id, 2, "second", input_data="i2", output_data="o2")
    repo.insert_run_step(run.id, 1, "first", input_data="i1", output_data="o1")
    steps = repo.list_run_steps(run.id)
    assert [s["step_number"] for s in steps] == [1, 2]
    assert steps[0]["action"] == "first"


def test_list_run_steps_empty_is_honest(tmp_path, monkeypatch):
    """A run with no recorded steps returns [] (UI renders that honestly)."""
    setup_env(tmp_path, monkeypatch)
    assert repo.list_run_steps("nonexistent-run") == []


@pytest.mark.anyio
async def test_steps_endpoint_returns_ordered(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import AgentRun
    run = AgentRun(agent_name="market_observer", permission_mode="Observe")
    repo.insert_agent_run(run)
    repo.insert_run_step(run.id, 1, "observe", output_data="snapshot read")

    from httpx import AsyncClient, ASGITransport
    from aletheia.server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/api/agent-runs/{run.id}/steps")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["action"] == "observe"

"""Tests for the risk API surface: kill switch + risk boundaries."""

import sqlite3

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.agent_shell.operator_store import create_artifact
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import RiskDecision, TradeProposal


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_db()


@pytest.mark.anyio
async def test_get_risk_boundaries(client):
    resp = await client.get("/api/risk/boundaries")
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_daily_loss_pct"] == 1.0
    assert data["allowed_symbols"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@pytest.mark.anyio
async def test_kill_switch_status_default_off(client):
    resp = await client.get("/api/risk/kill-switch")
    assert resp.status_code == 200
    assert resp.json()["active"] is False


@pytest.mark.anyio
async def test_kill_switch_activate_and_release(client):
    resp = await client.post("/api/risk/kill-switch", json={"active": True, "reason": "panic"})
    assert resp.status_code == 200
    assert resp.json()["active"] is True

    # status reflects it
    resp = await client.get("/api/risk/kill-switch")
    assert resp.json()["active"] is True

    # release
    resp = await client.post("/api/risk/kill-switch", json={"active": False})
    assert resp.status_code == 200
    assert resp.json()["active"] is False


@pytest.mark.anyio
async def test_risk_status_blocked_when_kill_switch_active(client):
    await client.post("/api/risk/kill-switch", json={"active": True, "reason": "panic"})
    resp = await client.get("/api/risk/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "blocked"
    assert "kill_switch_active" in data["violations"]


@pytest.mark.anyio
async def test_risk_status_warns_when_exchange_live_but_live_disabled(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.mode = "live"
    cfg.live.state = "live_disabled"
    save_config(cfg)

    resp = await client.get("/api/risk/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warning"
    assert "live_disabled_while_exchange_live" in data["violations"]


@pytest.mark.anyio
async def test_risk_status_warns_on_latest_rejected_decision(client):
    proposal = TradeProposal(symbol="DOGEUSDT", market_type="spot", direction="long")
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(RiskDecision(
        proposal_id=proposal.id,
        approved=False,
        violations='["symbol_not_allowed:DOGEUSDT"]',
        message="Rejected",
    ))

    resp = await client.get("/api/risk/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "warning"
    assert "symbol_not_allowed:DOGEUSDT" in data["violations"]


@pytest.mark.anyio
async def test_risk_summary_surfaces_due_boundary_change_after_repeated_adjustable_rejections(client):
    first = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.005,
        price=60000,
    )
    second = TradeProposal(
        symbol="ETHUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.1,
        price=3000,
    )
    repo.insert_trade_proposal(first)
    repo.insert_trade_proposal(second)
    repo.insert_risk_decision(
        RiskDecision(
            proposal_id=first.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected",
        )
    )
    repo.insert_risk_decision(
        RiskDecision(
            proposal_id=second.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected",
        )
    )

    resp = await client.get("/api/risk/summary")
    assert resp.status_code == 200

    payload = resp.json()
    due = payload["needs_operator_risk_boundary_proposals"]
    assert len(due) == 1
    assert due[0]["boundary_key"] == "max_demo_position_notional_usdt"
    assert due[0]["changes"]["max_demo_position_notional_usdt"] == 300.0


@pytest.mark.anyio
async def test_risk_summary_suppresses_due_boundary_change_when_recent_proposal_exists(client):
    proposal = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.005,
        price=60000,
    )
    repo.insert_trade_proposal(proposal)
    repo.insert_risk_decision(
        RiskDecision(
            proposal_id=proposal.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected",
        )
    )
    repo.insert_risk_decision(
        RiskDecision(
            proposal_id=proposal.id,
            approved=False,
            violations='["exceeds_demo_position_notional:300.0>200.0"]',
            message="Rejected again",
        )
    )
    create_artifact(
        task_id=None,
        session_id="operator-main",
        artifact_type="risk_boundary_change_proposal",
        title="Risk boundary proposal",
        content={
            "changes": {
                "max_demo_position_notional_usdt": 300.0,
            },
            "requires_human_approval": True,
        },
    )

    resp = await client.get("/api/risk/summary")
    assert resp.status_code == 200
    assert resp.json()["needs_operator_risk_boundary_proposals"] == []

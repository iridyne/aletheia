"""Phase 4 tests: proposal approve/reject, strategy promote/retire, portfolio, playbooks."""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import TradeProposal, Position, MarketSnapshot
from aletheia.storage import repositories as repo


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    reset_db()


@pytest.mark.anyio
async def test_approve_proposal_executes_paper(client, monkeypatch):
    # PaperAuto so paper execution is permitted
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "PaperAuto"; save_config(cfg)

    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      quantity=0.01, price=60000, execution_mode="paper_internal")
    repo.insert_trade_proposal(p)

    resp = await client.post(f"/api/proposals/{p.id}/approve-paper")
    assert resp.status_code == 200
    assert resp.json()["execution"]["status"] == "executed"
    # a position now exists
    assert len(repo.list_positions(status="open")) == 1


@pytest.mark.anyio
async def test_reject_proposal(client):
    p = TradeProposal(symbol="ETHUSDT", market_type="spot", direction="long")
    repo.insert_trade_proposal(p)
    resp = await client.post(f"/api/proposals/{p.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.anyio
async def test_approve_missing_proposal_404(client):
    resp = await client.post("/api/proposals/nonexistent/approve-paper")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_strategy_promote_and_retire(client):
    from aletheia.trading.strategy_lifecycle import create_strategy
    s = create_strategy("Test Strategy")
    resp = await client.post(f"/api/strategies/{s.id}/promote", json={"target_state": "research_note"})
    assert resp.status_code == 200
    assert resp.json()["new_state"] == "research_note"

    resp = await client.get(f"/api/strategies/{s.id}")
    assert resp.json()["state"] == "research_note"

    resp = await client.post(f"/api/strategies/{s.id}/retire")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_illegal_promotion_rejected(client):
    from aletheia.trading.strategy_lifecycle import create_strategy
    s = create_strategy("Jump")
    resp = await client.post(f"/api/strategies/{s.id}/promote", json={"target_state": "live_limited"})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_portfolio_endpoint(client):
    repo.insert_position(Position(symbol="SOLUSDT", market_type="spot", direction="long",
                                  quantity=10.0, entry_price=150.0, execution_mode="paper_internal"))
    repo.insert_market_snapshot(MarketSnapshot(symbol="SOLUSDT", market_type="spot", price=160.0))
    resp = await client.get("/api/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    assert data["positions_count"] == 1
    assert data["unrealized_pnl"] == 100.0  # (160-150)*10


@pytest.mark.anyio
async def test_paper_order_persists_with_fk_enforcement(tmp_path, monkeypatch):
    """Regression: orders.risk_decision_id must be a valid FK (was '' -> IntegrityError).

    Uses a connection with PRAGMA foreign_keys=ON, like the real app.
    """
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    from aletheia.storage.sqlite import reset_db as _reset
    _reset()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "fk.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")  # enforce FKs like production
    sq._db_conn = conn
    run_migrations()

    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "PaperAuto"; save_config(cfg)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(symbol="BTCUSDT", market_type="spot", direction="long",
                             quantity=0.01, price=60000, execution_mode="paper_internal")
    assert result["status"] == "executed"
    assert len(repo.list_orders()) == 1
    _reset()


def test_playbooks_seeded(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    loader.bootstrap()
    pb_dir = tmp_path / "playbooks"
    files = list(pb_dir.glob("*.md"))
    names = {f.stem for f in files}
    assert "core_crypto_regime_observation" in names
    assert "trend_following_hypothesis" in names
    assert len(files) == 5

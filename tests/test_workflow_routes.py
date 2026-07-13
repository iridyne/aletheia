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
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
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
    cfg = load_config(); cfg.agent.permission_mode = "Paper"; save_config(cfg)

    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      quantity=0.001, price=60000, execution_mode="paper_internal")
    repo.insert_trade_proposal(p)

    resp = await client.post(f"/api/proposals/{p.id}/approve-paper")
    assert resp.status_code == 200
    assert resp.json()["execution"]["status"] == "executed"
    assert resp.json()["execution"]["proposal_id"] == p.id
    proposals = repo.list_trade_proposals(limit=10)
    assert len(proposals) == 1
    orders = repo.list_orders()
    assert len(orders) == 1
    assert orders[0]["proposal_id"] == p.id
    # a position now exists
    assert len(repo.list_positions(status="open")) == 1


@pytest.mark.anyio
async def test_approve_proposal_idempotent_after_execute(client, monkeypatch):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)

    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(p)

    first = await client.post(f"/api/proposals/{p.id}/approve-paper")
    assert first.status_code == 200
    assert first.json()["execution"]["status"] == "executed"
    assert len(repo.list_orders()) == 1

    second = await client.post(f"/api/proposals/{p.id}/approve-paper")
    assert second.status_code == 200
    assert second.json()["execution"]["status"] == "already_executed"
    assert len(repo.list_orders()) == 1


@pytest.mark.anyio
async def test_reject_proposal(client):
    p = TradeProposal(symbol="ETHUSDT", market_type="spot", direction="long")
    repo.insert_trade_proposal(p)
    resp = await client.post(f"/api/proposals/{p.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


@pytest.mark.anyio
async def test_reject_executed_proposal_returns_409(client):
    """#112: cannot reject a proposal after it has been executed."""
    p = TradeProposal(symbol="BTCUSDT", market_type="usdt_futures", direction="long")
    repo.insert_trade_proposal(p)
    repo.update_trade_proposal(p.id, status="executed")
    resp = await client.post(f"/api/proposals/{p.id}/reject")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_approve_missing_proposal_404(client):
    resp = await client.post("/api/proposals/nonexistent/approve-paper")
    assert resp.status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize("execution_mode", ["bitget_demo", "okx_demo", "bitget_live_limited"])
async def test_approve_paper_rejects_non_paper_proposal_without_mutation(
    client, monkeypatch, execution_mode
):
    """The paper approval endpoint must never rewrite stored execution intent."""
    import aletheia.execution.gateway as gateway

    calls = []
    monkeypatch.setattr(
        gateway,
        "execute_existing_proposal",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000,
        execution_mode=execution_mode,
    )
    repo.insert_trade_proposal(p)

    resp = await client.post(f"/api/proposals/{p.id}/approve-paper")

    assert resp.status_code == 409
    stored = repo.get_trade_proposal(p.id)
    assert stored["status"] == "pending"
    assert stored["execution_mode"] == execution_mode
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "unresolved_status",
    [
        "approved",
        "executing",
        "submitted",
        "partially_filled",
        "submission_unknown",
        "materialization_missing",
    ],
)
async def test_approve_paper_does_not_reapprove_unresolved_execution(
    client, monkeypatch, unresolved_status
):
    """A stale approval request must not resend an order with an unknown outcome."""
    import aletheia.execution.gateway as gateway

    calls = []
    monkeypatch.setattr(
        gateway,
        "execute_existing_proposal",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(p)
    repo.update_trade_proposal(p.id, status=unresolved_status)

    resp = await client.post(f"/api/proposals/{p.id}/approve-paper")

    assert resp.status_code == 409
    assert repo.get_trade_proposal(p.id)["status"] == unresolved_status
    assert calls == []


@pytest.mark.anyio
async def test_approve_paper_sanitizes_unexpected_execution_exception(client, monkeypatch):
    """Raw exception text must not reach the API response or durable audit log."""
    import aletheia.execution.gateway as gateway

    sensitive = "api_secret=do-not-leak-this-value"

    def _raise_unexpected(*args, **kwargs):
        raise RuntimeError(sensitive)

    monkeypatch.setattr(gateway, "execute_existing_proposal", _raise_unexpected)
    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(p)

    resp = await client.post(f"/api/proposals/{p.id}/approve-paper")

    assert resp.status_code == 200
    execution = resp.json()["execution"]
    assert execution == {
        "status": "error",
        "reason_code": "paper_execution_failed",
        "message": "Paper execution could not be completed.",
    }
    assert repo.get_trade_proposal(p.id)["status"] == "error"
    assert sensitive not in resp.text
    from aletheia.config.loader import get_data_dir
    audit_text = (get_data_dir() / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert sensitive not in audit_text
    assert "paper_execution_failed" in audit_text


@pytest.mark.anyio
async def test_portfolio_endpoint(client):
    repo.insert_position(Position(symbol="SOLUSDT", market_type="spot", direction="long",
                                  quantity=10.0, entry_price=150.0, execution_mode="bitget_demo"))
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
    cfg = load_config(); cfg.agent.permission_mode = "Paper"; save_config(cfg)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(symbol="BTC/USDT", market_type="spot", direction="long",
                             quantity=0.001, price=60000, execution_mode="paper_internal")
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

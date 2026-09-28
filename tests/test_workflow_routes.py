"""Phase 4 tests: proposal reject, strategy promote/retire, portfolio, playbooks."""

import json

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import TradeProposal, Position, MarketSnapshot, Order, RiskDecision
from aletheia.storage import repositories as repo


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.server.routes.data as data_routes
    import aletheia.storage.sqlite as sq

    def _clear_portfolio_cache():
        with data_routes._portfolio_cache_lock:
            data_routes._portfolio_cache = None

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    _clear_portfolio_cache()
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
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        _clear_portfolio_cache()
        reset_db()


@pytest.mark.anyio
async def test_reject_proposal(client):
    p = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=3_000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(p)
    repo.acquire_entry_risk_reservation(
        proposal_id=p.id,
        proposal_commit_hash=p.commit_hash,
        symbol=p.symbol,
        execution_mode=p.execution_mode,
        quantity=p.quantity,
        price=p.price,
    )
    assert repo.get_entry_risk_reservation(p.id) is not None

    resp = await client.post(f"/api/proposals/{p.id}/reject")

    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert repo.get_entry_risk_reservation(p.id) is None


@pytest.mark.anyio
async def test_reject_commits_state_reservation_and_structured_audit_together(
    client, monkeypatch
):
    proposal = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=3_000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(proposal)
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        execution_mode=proposal.execution_mode,
        quantity=proposal.quantity,
        price=proposal.price,
    )
    monkeypatch.setattr(
        "aletheia.files.jsonl.write_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("jsonl unavailable")),
    )

    response = await client.post(f"/api/proposals/{proposal.id}/reject")

    assert response.status_code == 200
    assert repo.get_trade_proposal(proposal.id)["status"] == "rejected"
    assert repo.get_entry_risk_reservation(proposal.id) is None
    events = [
        event for event in repo.list_audit_events(limit=20)
        if event["event_type"] == "proposal_rejected"
        and event["entity_id"] == proposal.id
    ]
    assert len(events) == 1
    assert json.loads(events[0]["before_state"]) == {"status": "pending"}
    assert json.loads(events[0]["after_state"]) == {"status": "rejected"}


@pytest.mark.anyio
async def test_reject_rolls_back_state_and_reservation_when_structured_audit_fails(
    client, monkeypatch
):
    proposal = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=3_000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(proposal)
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        execution_mode=proposal.execution_mode,
        quantity=proposal.quantity,
        price=proposal.price,
    )
    monkeypatch.setattr(
        repo,
        "insert_audit_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("sqlite audit unavailable")),
    )

    with pytest.raises(RuntimeError, match="sqlite audit unavailable"):
        await client.post(f"/api/proposals/{proposal.id}/reject")

    assert repo.get_trade_proposal(proposal.id)["status"] == "pending"
    assert repo.get_entry_risk_reservation(proposal.id) is not None


@pytest.mark.anyio
async def test_reject_executed_proposal_returns_409(client):
    """#112: cannot reject a proposal after it has been executed."""
    p = TradeProposal(symbol="BTCUSDT", market_type="usdt_futures", direction="long", execution_mode="bitget_demo")
    repo.insert_trade_proposal(p)
    repo.update_trade_proposal(p.id, status="executed")
    resp = await client.post(f"/api/proposals/{p.id}/reject")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_reject_submitted_exchange_proposal_preserves_live_order_state(client):
    """A dashboard reject must not lie about an already-submitted venue order."""
    from tests.helpers.execution_authority import seed_order_authority

    proposal, decision = seed_order_authority(
        proposal_id="submitted-proposal",
        risk_decision_id="submitted-risk",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    order = Order(
        id="submitted-order",
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        client_order_id=proposal.id,
        exchange_order_id="venue-order-123",
        status="submitted",
    )
    repo.insert_order(order)
    repo.update_trade_proposal(proposal.id, status="submitted")

    resp = await client.post(f"/api/proposals/{proposal.id}/reject")

    assert resp.status_code == 409
    assert repo.get_trade_proposal(proposal.id)["status"] == "submitted"
    persisted_order = repo.get_order(order.id)
    assert persisted_order["status"] == "submitted"
    assert persisted_order["exchange_order_id"] == "venue-order-123"


@pytest.mark.anyio
async def test_order_risk_decision_uses_exact_authoritative_fk(client):
    """Causal Chain must not replace an Order's consumed authority with newer preflight."""
    from tests.helpers.execution_authority import seed_order_authority

    proposal, decision = seed_order_authority(
        proposal_id="risk-chain-proposal",
        risk_decision_id="risk-authoritative-a",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    repo._update(
        "risk_decisions",
        decision.id,
        {"risk_score": 12, "message": "authoritative A"},
    )
    repo.insert_order(Order(
        id="risk-chain-order",
        client_order_id=proposal.id,
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        direction=proposal.direction,
        order_type=proposal.order_type,
        quantity=proposal.quantity,
        price=proposal.price,
        execution_mode=proposal.execution_mode,
        status="filled",
    ))
    repo.insert_risk_decision(RiskDecision(
        id="risk-preflight-b",
        proposal_id=proposal.id,
        approved=False,
        risk_score=99,
        decision_role="preflight",
        message="newer preflight B",
    ))

    by_order = await client.get("/api/orders/risk-chain-order/risk-decision")
    by_proposal = await client.get(f"/api/proposals/{proposal.id}/risk-decision")

    assert by_order.status_code == 200
    assert by_order.json()["status"] == "available"
    assert by_order.json()["decision"]["id"] == "risk-authoritative-a"
    assert by_order.json()["decision"]["message"] == "authoritative A"
    assert by_proposal.status_code == 200
    assert by_proposal.json()["status"] == "available"
    assert by_proposal.json()["decision"]["id"] == "risk-authoritative-a"


@pytest.mark.anyio
async def test_order_risk_decision_rejects_preflight_authority(client):
    proposal = TradeProposal(
        id="risk-chain-preflight-proposal",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_trade_proposal(proposal)
    preflight = RiskDecision(
        id="risk-chain-preflight-only",
        proposal_id=proposal.id,
        approved=True,
        decision_role="preflight",
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(preflight)
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "INSERT INTO orders "
        "(id,proposal_id,risk_decision_id,symbol,market_type,direction,order_type,"
        "quantity,price,execution_mode) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "risk-chain-preflight-order",
            proposal.id,
            preflight.id,
            proposal.symbol,
            proposal.market_type,
            proposal.direction,
            proposal.order_type,
            proposal.quantity,
            proposal.price,
            proposal.execution_mode,
        ),
    )

    response = await client.get("/api/orders/risk-chain-preflight-order/risk-decision")

    assert response.status_code == 200
    assert response.json()["status"] == "integrity_unavailable"
    assert response.json()["reason_code"] == "risk_decision_not_authoritative"
    assert response.json()["decision"] is None


@pytest.mark.anyio
async def test_portfolio_endpoint(client, monkeypatch):
    from aletheia.config.loader import load_config, save_config

    # Select Bitget demo explicitly so only that account mode is in scope.
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)

    repo.insert_position(Position(symbol="SOLUSDT", market_type="spot", direction="long",
                                  quantity=10.0, entry_price=150.0, execution_mode="bitget_demo"))
    repo._insert("positions", Position(symbol="BTCUSDT", market_type="spot", direction="long",
                                  quantity=1.0, entry_price=60_000.0, execution_mode="paper_internal").model_dump())
    repo.insert_market_snapshot(MarketSnapshot(symbol="SOLUSDT", market_type="spot", price=160.0, exchange="bitget"))
    resp = await client.get("/api/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_mode"] == "bitget_demo"
    assert data["execution_mode_status"] == "resolved"
    assert data["positions_count"] == 1
    assert [position["execution_mode"] for position in data["positions"]] == ["bitget_demo"]
    # No exchange cache in test env → unrealized_pnl is None per-position → aggregate 0.0
    assert data["unrealized_pnl"] == 0.0


@pytest.mark.anyio
async def test_portfolio_cache_is_bound_to_resolved_execution_mode(client, monkeypatch):
    import aletheia.analytics.execution_mode as execution_mode_module
    import aletheia.server.routes.data as data_routes

    modes = iter(("bitget_demo", "okx_demo", None))
    monkeypatch.setattr(
        execution_mode_module,
        "resolve_selected_execution_mode",
        lambda: next(modes),
    )
    monkeypatch.setattr(
        data_routes,
        "compute_portfolio",
        lambda execution_mode: {"positions_count": 0, "positions": []},
    )

    first = await client.get("/api/portfolio")
    second = await client.get("/api/portfolio")
    unresolved = await client.get("/api/portfolio")

    assert first.json()["execution_mode"] == "bitget_demo"
    assert second.json()["execution_mode"] == "okx_demo"
    assert unresolved.status_code == 503
    assert unresolved.json()["detail"] == "execution_mode_unresolved"


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

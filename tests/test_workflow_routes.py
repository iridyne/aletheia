"""Phase 4 tests: proposal approve/reject, strategy promote/retire, portfolio, playbooks."""

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
async def test_approve_proposal_executes_paper(client, monkeypatch):
    # PaperAuto so paper execution is permitted
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Paper"; save_config(cfg)
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch, symbol="BTC/USDT", market_type="spot", direction="long", price=60000,
    )

    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      quantity=0.001, price=60000, execution_mode="paper_internal", **trade)
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
async def test_preview_mode_blocks_approval_before_proposal_mutation(client, monkeypatch):
    """A dev API may render a proposal, but cannot approve or execute it."""
    import aletheia.server.routes.data as data_routes

    proposal = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=0.001, price=60_000, execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    monkeypatch.setattr(data_routes, "is_preview_mode", lambda: True)

    response = await client.post(f"/api/proposals/{proposal.id}/approve-paper")

    assert response.status_code == 403
    assert response.json()["detail"]["reason_code"] == "preview_execution_blocked"
    assert repo.get_trade_proposal(proposal.id)["status"] == "pending"
    assert repo.list_orders() == []


@pytest.mark.anyio
async def test_approve_paper_rechecks_status_inside_writer_transaction(client, monkeypatch):
    """A stale unlocked approval snapshot cannot overwrite a terminal rejection."""
    import aletheia.execution.gateway as gateway
    from aletheia.storage.sqlite import get_db

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    repo.update_trade_proposal(proposal.id, status="rejected")

    real_get = repo.get_trade_proposal
    observed_transactions = []
    gateway_calls = []

    def stale_unlocked_read(proposal_id):
        row = real_get(proposal_id)
        observed_transactions.append(get_db().in_transaction)
        if not observed_transactions[-1]:
            stale = dict(row)
            stale["status"] = "pending"
            return stale
        return row

    monkeypatch.setattr(repo, "get_trade_proposal", stale_unlocked_read)
    monkeypatch.setattr(
        gateway,
        "execute_existing_proposal",
        lambda *args, **kwargs: gateway_calls.append((args, kwargs)),
    )

    response = await client.post(f"/api/proposals/{proposal.id}/approve-paper")

    assert response.status_code == 200
    assert response.json()["execution"]["status"] == "rejected"
    assert observed_transactions == [True]
    assert gateway_calls == []
    assert real_get(proposal.id)["status"] == "rejected"
    assert repo.list_orders() == []
    assert repo.list_positions(status="open") == []


@pytest.mark.anyio
async def test_approve_commits_structured_audit_before_best_effort_jsonl(
    client, monkeypatch
):
    import aletheia.execution.gateway as gateway

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    gateway_calls = []
    monkeypatch.setattr(
        gateway,
        "execute_existing_proposal",
        lambda *args, **kwargs: gateway_calls.append((args, kwargs)) or {"status": "accepted"},
    )
    monkeypatch.setattr("aletheia.files.jsonl.write_audit", lambda *_args, **_kwargs: False)

    response = await client.post(f"/api/proposals/{proposal.id}/approve-paper")

    assert response.status_code == 200
    assert repo.get_trade_proposal(proposal.id)["status"] == "approved"
    assert len(gateway_calls) == 1
    events = [
        event for event in repo.list_audit_events(limit=20)
        if event["event_type"] == "proposal_approved"
        and event["entity_id"] == proposal.id
    ]
    assert len(events) == 1
    assert json.loads(events[0]["before_state"]) == {"status": "pending"}
    assert json.loads(events[0]["after_state"]) == {"status": "approved"}


@pytest.mark.anyio
async def test_approve_rolls_back_when_structured_audit_fails(client, monkeypatch):
    import aletheia.execution.gateway as gateway

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)
    gateway_calls = []
    monkeypatch.setattr(
        gateway,
        "execute_existing_proposal",
        lambda *args, **kwargs: gateway_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        repo,
        "insert_audit_event",
        lambda _event: (_ for _ in ()).throw(RuntimeError("sqlite audit unavailable")),
    )

    with pytest.raises(RuntimeError, match="sqlite audit unavailable"):
        await client.post(f"/api/proposals/{proposal.id}/approve-paper")

    assert repo.get_trade_proposal(proposal.id)["status"] == "pending"
    assert gateway_calls == []


@pytest.mark.anyio
async def test_approve_proposal_idempotent_after_execute(client, monkeypatch):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch, symbol="BTC/USDT", market_type="spot", direction="long", price=60_000,
    )

    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000,
        execution_mode="paper_internal",
        **trade,
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
@pytest.mark.parametrize("initial_status", ["pending", "approved", "executing"])
async def test_approve_recovers_persisted_approved_gap_and_ignores_audit_sink_failure(
    client,
    monkeypatch,
    initial_status,
):
    """A crash/audit failure after approval cannot strand a no-Order Proposal."""
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        price=60_000.0,
        opportunity_id=f"approval-opportunity-{initial_status}",
    )

    proposal = TradeProposal(
        id=f"approval-recovery-{initial_status}",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
        status=initial_status,
        **trade,
    )
    repo.insert_trade_proposal(proposal)
    monkeypatch.setattr(
        "aletheia.files.jsonl.write_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("audit down")),
    )

    recovered = await client.post(f"/api/proposals/{proposal.id}/approve-paper")

    assert recovered.status_code == 200
    assert recovered.json()["execution"]["status"] == "executed"
    assert repo.get_trade_proposal(proposal.id)["status"] == "executed"
    assert len([
        row for row in repo.list_orders(limit=20)
        if row["proposal_id"] == proposal.id
    ]) == 1


@pytest.mark.anyio
async def test_reject_proposal(client):
    p = TradeProposal(
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        quantity=0.01,
        price=3_000.0,
        execution_mode="paper_internal",
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
        execution_mode="paper_internal",
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
        execution_mode="paper_internal",
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
    p = TradeProposal(symbol="BTCUSDT", market_type="usdt_futures", direction="long")
    repo.insert_trade_proposal(p)
    repo.update_trade_proposal(p.id, status="executed")
    resp = await client.post(f"/api/proposals/{p.id}/reject")
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_reject_submitted_exchange_proposal_preserves_live_order_state(client):
    """A dashboard reject must not lie about an already-submitted venue order."""
    from tests.execution_authority import seed_order_authority

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
    from tests.execution_authority import seed_order_authority

    proposal, decision = seed_order_authority(
        proposal_id="risk-chain-proposal",
        risk_decision_id="risk-authoritative-a",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
    )
    repo._update(
        "risk_decisions",
        decision.id,
        {"risk_score": 12, "message": "authoritative A"},
    )
    repo.insert_order(Order(
        id="risk-chain-order",
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
        execution_mode="paper_internal",
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
        "submitted",
        "partially_filled",
        "submission_unknown",
        "materialization_missing",
    ],
)
async def test_approve_paper_does_not_reapprove_unresolved_execution(
    client, monkeypatch, unresolved_status
):
    """A venue/materialization outcome state must not re-enter execution."""
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
async def test_portfolio_endpoint(client, monkeypatch):
    from datetime import datetime, timezone
    from aletheia.data import price_feed
    from aletheia.config.loader import load_config, save_config

    # Select Bitget demo explicitly so only that account mode is in scope.
    cfg = load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)

    # #1174: mark_positions_to_market now reads _instrument_price_cache (exact
    # venue/symbol/market_type) instead of the DB snapshot table.
    # bitget_demo → venue "bitget"; seed the exact key so the PnL is computed.
    fake_cache = {
        ("bitget", "SOL/USDT", "spot"): {
            "price": 160.0, "ts": datetime.now(timezone.utc),
            "market_type": "spot", "exchange": "bitget",
        },
    }
    monkeypatch.setattr(price_feed, "_instrument_price_cache", fake_cache)

    repo.insert_position(Position(symbol="SOLUSDT", market_type="spot", direction="long",
                                  quantity=10.0, entry_price=150.0, execution_mode="bitget_demo"))
    repo.insert_position(Position(symbol="BTCUSDT", market_type="spot", direction="long",
                                  quantity=1.0, entry_price=60_000.0, execution_mode="paper_internal"))
    repo.insert_market_snapshot(MarketSnapshot(symbol="SOLUSDT", market_type="spot", price=160.0, exchange="bitget"))
    resp = await client.get("/api/portfolio")
    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_mode"] == "bitget_demo"
    assert data["execution_mode_status"] == "resolved"
    assert data["positions_count"] == 1
    assert [position["execution_mode"] for position in data["positions"]] == ["bitget_demo"]
    assert data["unrealized_pnl"] == 100.0  # (160-150)*10


@pytest.mark.anyio
async def test_portfolio_cache_is_bound_to_resolved_execution_mode(client, monkeypatch):
    import aletheia.analytics.execution_mode as execution_mode_module
    import aletheia.server.routes.data as data_routes

    modes = iter(("paper_internal", "okx_demo", None))
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

    assert first.json()["execution_mode"] == "paper_internal"
    assert second.json()["execution_mode"] == "okx_demo"
    assert unresolved.status_code == 503
    assert unresolved.json()["detail"] == "execution_mode_unresolved"


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
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch, symbol="BTC/USDT", market_type="spot", direction="long", price=60000,
    )
    result = submit_proposal(symbol="BTC/USDT", market_type="spot", direction="long",
                             quantity=0.001, price=60000, execution_mode="paper_internal", **trade)
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

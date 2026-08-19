"""Tests for the live execution path (execution/bitget_broker.py + gateway wiring).

The actual Bitget HTTP call is mocked — we verify the durable acknowledgement,
private-fill reconciliation, and exchange_order_id, plus gateway routing for a
fully-approved live proposal while live-disabled remains blocked.
"""

import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.exchanges.bitget.client import OrderResponse
from aletheia.storage.models import Position, RiskDecision, TradeProposal


def setup_env(tmp_path, monkeypatch):
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


def _patch_place_order(monkeypatch, captured):
    """Patch BitgetClient.place_order to record the request and return a fake id."""
    from aletheia.exchanges.bitget import client as bg

    async def fake_place_order(self, order):
        captured.append(order)
        # Exchange confirms the fill; broker only records a fill/position when
        # the order is reported filled/partially_filled (no fabricated fills).
        return OrderResponse(
            order_id="EX-123",
            client_oid="cli-1",
            status="filled",
            fill_price=order.price or 60000.0,
            filled_quantity=order.quantity,
        )

    monkeypatch.setattr(bg.BitgetClient, "place_order", fake_place_order)


def _seed_authority(
    proposal_id: str,
    *,
    quantity: float,
    strategy_id: str | None = None,
) -> tuple[str, str]:
    proposal = TradeProposal(
        id=proposal_id,
        strategy_id=strategy_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=quantity,
        price=60000.0,
        execution_mode="bitget_live_limited",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=proposal.execution_mode,
        proposal_execution_mode=proposal.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    return proposal.id, decision.id


def test_bitget_broker_waits_for_private_fill_before_position(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)
    from tests.specs.spec_pipeline_stop_propagation import (
        _patch_private_fill_history,
    )
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "aletheia.exchanges.bitget.client",
        fill_price=60000.0,
    )
    proposal_id, decision_id = _seed_authority("p1", quantity=0.001)

    from aletheia.execution.bitget_broker import execute_live_order
    result = execute_live_order(
        proposal_id=proposal_id, risk_decision_id=decision_id,
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60000.0,
    )
    assert result["exchange_order_id"] == "EX-123"

    orders = repo.list_orders()
    assert len(orders) == 1
    assert orders[0]["exchange_order_id"] == "EX-123"
    assert orders[0]["execution_mode"] == "bitget_live_limited"
    assert orders[0]["status"] == "materialization_missing"
    assert repo.list_fills() == []
    assert repo.list_positions(status="open") == []

    from aletheia.execution.reconciliation import reconcile
    reconciled = reconcile()
    assert reconciled["filled"] == 1

    positions = repo.list_positions(status="open")
    assert len(positions) == 1
    assert positions[0]["execution_mode"] == "bitget_live_limited"
    # the order request actually reached the (mocked) exchange
    assert len(captured) == 1
    assert captured[0].symbol == "BTCUSDT"


def test_place_order_carries_client_oid_from_proposal(tmp_path, monkeypatch):
    """§2.3: a stable clientOid (derived from proposal_id) must reach the exchange
    so a retried submission is deduplicated."""
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)
    proposal_id, decision_id = _seed_authority("prop-xyz", quantity=0.001)

    from aletheia.execution.bitget_broker import execute_live_order
    execute_live_order(
        proposal_id=proposal_id, risk_decision_id=decision_id,
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60000.0,
    )
    assert len(captured) == 1
    assert captured[0].client_oid == "prop-xyz"


def test_live_order_db_trace_when_exchange_call_fails(tmp_path, monkeypatch):
    """§1.3 hazard: if place_order fails, a local order row must still exist (marked
    error) and NO fill/position may be fabricated."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.exchanges.bitget import client as bg

    async def boom_place_order(self, order):
        raise RuntimeError("exchange timeout")

    monkeypatch.setattr(bg.BitgetClient, "place_order", boom_place_order)
    proposal_id, decision_id = _seed_authority("p-fail", quantity=0.001)

    from aletheia.execution.bitget_broker import execute_live_order
    with pytest.raises(RuntimeError):
        execute_live_order(
            proposal_id=proposal_id, risk_decision_id=decision_id,
            symbol="BTC/USDT", market_type="spot", direction="long",
            order_type="market", quantity=0.001, price=60000.0,
        )

    orders = repo.list_orders()
    assert len(orders) == 1  # pending row was written before the exchange call
    assert orders[0]["status"] == "error"
    assert orders[0]["proposal_id"] == proposal_id
    assert len(repo.list_fills()) == 0
    assert len(repo.list_positions(status="open")) == 0


def test_gateway_routes_approved_live_order_to_broker(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)
    # This test owns the exchange submission mock, so also provide the
    # executable venue's published constraints instead of touching Bitget.
    from aletheia.execution import gateway
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", lambda *args: (0.00001, None))

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.live.state = "live_limited"
    cfg.exchange.mode = "live"
    # This gateway-routing test does not own an account-equity adapter. Give
    # Risk an explicit test denominator so approval proves the routing path
    # instead of being skipped behind a missing capital authority.
    cfg.risk.live.capital.base_source = "custom"
    cfg.risk.live.capital.base_usdt = 100.0
    save_config(cfg)

    # EX-04 (#1004): the routing path also requires a valid live authorization
    # ref at both Proposal creation and execution. This test owns the broker
    # mock; a stable stubbed authorization identity keeps focus on routing.
    import aletheia.trust.live_unlock as trust_mod

    monkeypatch.setattr(trust_mod, "current_authorization_ref", lambda _cfg: "sha256:test-ok")
    monkeypatch.setattr(
        trust_mod, "authorization_matches_ref",
        lambda _cfg, ref: (bool(ref), "ok" if ref else "missing_live_authorization_ref"),
    )
    monkeypatch.setattr(
        trust_mod, "evidence_matches_config", lambda _cfg, _ev: (True, "ok"),
    )

    # fresh market data so the data-freshness check passes
    from datetime import datetime, timezone
    from aletheia.storage.models import MarketSnapshot, Opportunity, UniverseForwardOutcome

    # #14 scanner-family evidence gate (Position C 2026-07-02): a LIVE order is
    # live-eligible only if its scanner_family has a proven clean-window edge. Seed
    # 30 winning settled outcomes for (momentum, long) + an opportunity carrying that
    # family so the proposal passes gate #14 and routes to the broker.
    now = datetime.now(timezone.utc).isoformat()
    for i in range(30):
        repo.insert_forward_outcome(UniverseForwardOutcome(
            member_id=f"m{i}", snapshot_id="snap1", symbol="BTC/USDT",
            scanner_family="momentum", direction="long", horizon="24h",
            entry_price_usd=100.0, due_at=now, settled_at=now,
            exit_price_usd=105.0, forward_return_pct=5.0,
            gross_return_pct=5.0, directional_return_pct=5.0,
            estimated_fee_pct=0.12, estimated_spread_slippage_pct=0.15,
            estimated_funding_pct=0.0, net_return_pct=4.73,
            min_tradable_edge_pct=0.05, cost_model_version="v1",
            market_type="spot", status="settled",
        ))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long", scanner_family="momentum")
    repo.insert_opportunity(opp)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=0.00001, price=60000.0, execution_mode="bitget_live_limited", stop_price=59000.0,
        opportunity_id=opp.id,
    )
    assert result["status"] == "materialization_missing"
    assert result["exchange_order_id"] == "EX-123"
    assert len(captured) == 1


def test_gateway_still_blocks_live_when_disabled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"  # but live stays disabled
    save_config(cfg)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        quantity=0.0001, price=60000.0, execution_mode="bitget_live_limited",
    )
    assert result["status"] == "rejected"
    # no live order was sent
    assert len(captured) == 0


def test_private_fill_materialization_keeps_strategy_positions_separate(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)
    from tests.specs.spec_pipeline_stop_propagation import (
        _patch_private_fill_history,
    )
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "aletheia.exchanges.bitget.client",
        fill_price=60000.0,
    )

    from aletheia.execution.bitget_broker import execute_live_order

    repo.insert_position(Position(
        strategy_id="strategy-alpha",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        entry_price=60000.0,
        current_price=60000.0,
        execution_mode="bitget_live_limited",
        status="open",
    ))
    proposal_id, decision_id = _seed_authority(
        "live-two", quantity=0.002, strategy_id="strategy-beta"
    )

    result = execute_live_order(
        proposal_id=proposal_id,
        risk_decision_id=decision_id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.002,
        price=60000.0,
        strategy_id="strategy-beta",
    )

    assert len(captured) == 1
    assert result["fill_quantity"] == 0
    assert result["order_status"] == "materialization_missing"
    assert len(repo.list_positions(status="open")) == 1

    from aletheia.execution.reconciliation import reconcile
    reconciled = reconcile()
    assert reconciled["filled"] == 1

    positions = repo.list_positions(status="open")
    assert len(positions) == 2
    alpha_pos = next(p for p in positions if p["strategy_id"] == "strategy-alpha")
    beta_pos = next(p for p in positions if p["strategy_id"] == "strategy-beta")
    assert alpha_pos["quantity"] == 0.001
    assert beta_pos["quantity"] == 0.002

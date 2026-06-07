"""Tests for the live execution path (execution/bitget_broker.py + gateway wiring).

The actual Bitget HTTP call is mocked — we verify the order/fill/position records
and exchange_order_id, and that the gateway routes a fully-approved live proposal
to the live broker while still blocking when live is disabled.
"""

import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.exchanges.bitget.client import OrderResponse
from aletheia.storage.models import Position


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
        return OrderResponse(order_id="EX-123", client_oid="cli-1", status="filled")

    monkeypatch.setattr(bg.BitgetClient, "place_order", fake_place_order)


def test_bitget_broker_records_order_fill_position(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)

    from aletheia.execution.bitget_broker import execute_live_order
    result = execute_live_order(
        proposal_id="p1", risk_decision_id=None,
        symbol="BTCUSDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60000.0,
    )
    assert result["exchange_order_id"] == "EX-123"

    orders = repo.list_orders()
    assert len(orders) == 1
    assert orders[0]["exchange_order_id"] == "EX-123"
    assert orders[0]["execution_mode"] == "bitget_live_limited"

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

    from aletheia.execution.bitget_broker import execute_live_order
    execute_live_order(
        proposal_id="prop-xyz", risk_decision_id=None,
        symbol="BTCUSDT", market_type="spot", direction="long",
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

    from aletheia.execution.bitget_broker import execute_live_order
    with pytest.raises(RuntimeError):
        execute_live_order(
            proposal_id="p-fail", risk_decision_id=None,
            symbol="BTCUSDT", market_type="spot", direction="long",
            order_type="market", quantity=0.001, price=60000.0,
        )

    orders = repo.list_orders()
    assert len(orders) == 1  # pending row was written before the exchange call
    assert orders[0]["status"] == "error"
    assert orders[0]["proposal_id"] == "p-fail"
    assert len(repo.list_fills()) == 0
    assert len(repo.list_positions(status="open")) == 0


def test_gateway_routes_approved_live_order_to_broker(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Auto"
    cfg.live.state = "live_limited"
    cfg.exchange.mode = "live"
    save_config(cfg)

    # fresh market data so the data-freshness check passes
    from aletheia.storage.models import MarketSnapshot
    from aletheia.storage.repositories import insert_strategy
    from aletheia.storage.models import StrategyRecord

    repo.insert_strategy(StrategyRecord(name="Live Strategy", state="live_candidate"))
    strategy_rows = repo.list_strategies()
    strategy_id = strategy_rows[0]["id"]
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000))

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        quantity=0.00001, price=60000.0, execution_mode="bitget_live_limited", stop_price=59000.0,
        strategy_id=strategy_id,
    )
    assert result["status"] == "executed"
    assert result["exchange_order_id"] == "EX-123"
    assert len(captured) == 1


def test_gateway_still_blocks_live_when_disabled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Auto"  # but live stays disabled
    save_config(cfg)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        quantity=0.0001, price=60000.0, execution_mode="bitget_live_limited",
    )
    assert result["status"] == "rejected"
    # no live order was sent
    assert len(captured) == 0


def test_live_broker_keeps_strategy_positions_separate(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    captured = []
    _patch_place_order(monkeypatch, captured)

    from aletheia.execution.bitget_broker import execute_live_order

    repo.insert_position(Position(
        strategy_id="strategy-alpha",
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        entry_price=60000.0,
        current_price=60000.0,
        execution_mode="bitget_live_limited",
        status="open",
    ))

    result = execute_live_order(
        proposal_id="live-two",
        risk_decision_id=None,
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.002,
        price=60000.0,
        strategy_id="strategy-beta",
    )

    assert len(captured) == 1
    assert result["fill_quantity"] == 0.002
    positions = repo.list_positions(status="open")
    assert len(positions) == 2
    alpha_pos = next(p for p in positions if p["strategy_id"] == "strategy-alpha")
    beta_pos = next(p for p in positions if p["strategy_id"] == "strategy-beta")
    assert alpha_pos["quantity"] == 0.001
    assert beta_pos["quantity"] == 0.002

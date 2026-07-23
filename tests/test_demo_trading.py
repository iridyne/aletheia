"""Tests for the Bitget demo trading path (real demo/simulated-trading API).

Architectural principle (user-confirmed 2026-06-09): paper trading PREFERS the
exchange's own demo API (real API, exchange-side record); local simulation
(paper_internal) is the fallback when no demo endpoint exists.

The Bitget HTTP call is always mocked here — CI never touches the real API (the
autouse guard in conftest.py also blocks unmocked calls). These verify:
- gateway routes an approved bitget_demo proposal to the broker (real-exchange path),
  tags execution_mode="bitget_demo", and is NOT blocked by live_disabled;
- a bitget_demo proposal is rejected when exchange.mode != demo (account_mode_mismatch
  trust-boundary check) — would otherwise place a REAL live order;
- the paper_internal fallback never calls the exchange;
- a failed demo order leaves a DB trace marked error and fabricates NO fill;
- aggregate responses create no Fill/Position; private history supplies exact
  price/quantity/fee/time before local accounting changes;
- place_order builds the mix body with marginCoin + tradeSide (hedge mode);
- a hedge-mode close places a real close order (tradeSide:close + holdSide), then
  realizes PnL only after exact private fill reconciliation.
"""

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import MarketSnapshot, Position
from aletheia.exchanges.bitget.client import OrderResponse, OrderRequest


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


def _demo_config(monkeypatch, permission="Paper"):
    from aletheia.config.loader import load_config, save_config
    from aletheia.execution import order_constraints
    from aletheia.execution.order_constraints import OrderConstraints
    from aletheia.storage.sqlite import get_db
    from decimal import Decimal

    cfg = load_config()
    cfg.agent.permission_mode = permission
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    # These tests exercise the broker/materialization path, not missing-account
    # handling.  Seed the same fresh local cache that Risk #17 requires in
    # production so the fixture does not depend on configured starting capital.
    state = {
        "mode": "demo",
        "equity_usdt": 10_000.0,
        "available_usdt": 10_000.0,
        "frozen_usdt": 0.0,
        "futures_unrealized_pnl": 0.0,
        "open_positions_count": 0,
        "open_orders_count": 0,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "status": "healthy",
        "asset_breakdown": [],
        "positions": [],
    }
    db = get_db()
    db.execute(
        "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at",
        ("exchange_account_state.demo", json.dumps(state)),
    )
    db.commit()
    # The same broker-path tests must not reach CCXT for executable rules.  Supply
    # a realistic Bitget BTC futures rule set while retaining the real quantity
    # normalization and Gateway re-check behavior.
    monkeypatch.setattr(
        order_constraints,
        "get_order_constraints",
        lambda _mode, symbol, market_type: OrderConstraints(
            venue="bitget",
            symbol=symbol,
            market_type=market_type,
            min_quantity=Decimal("0.0001"),
            max_quantity=None,
            amount_step=Decimal("0.0001"),
            min_notional=Decimal("5"),
        ),
    )
    return cfg


def _patch_place_order(monkeypatch, captured, *, status="filled", fill_price=None, filled_quantity=None):
    """Override the conftest guard with a recording fake that confirms a fill."""
    from aletheia.exchanges.bitget import client as bg

    async def fake_place_order(self, order):
        captured.append(order)
        confirmed = status in ("filled", "partially_filled")
        return OrderResponse(
            order_id="EX-DEMO-1", client_oid=order.client_oid, status=status,
            fill_price=(fill_price if fill_price is not None else 60000.0) if confirmed else None,
            filled_quantity=(
                filled_quantity if filled_quantity is not None else order.quantity
            ) if confirmed else None,
        )

    monkeypatch.setattr(bg.BitgetClient, "place_order", fake_place_order)


def _fresh_snapshot(symbol="BTC/USDT", price=60000.0):
    repo.insert_market_snapshot(MarketSnapshot(symbol=symbol, market_type="usdt_futures", price=price))
    # latest_snapshot matches by symbol only, but seed canonical (pipeline format).
    repo.insert_market_snapshot(MarketSnapshot(symbol=symbol, market_type="spot", price=price))


# --- gateway routing ------------------------------------------------------

def test_gateway_routes_demo_order_to_broker(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot()
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

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="bitget_demo",
        leverage=5,
    )
    assert result["status"] == "materialization_missing"
    assert result["exchange_order_id"] == "EX-DEMO-1"
    assert len(captured) == 1
    # The exchange call uses Bitget execution symbol format...
    assert captured[0].symbol == "BTCUSDT"
    # ...but the local order row keeps canonical format + bitget_demo tag.
    orders = repo.list_orders()
    assert len(orders) == 1
    assert orders[0]["execution_mode"] == "bitget_demo"
    assert orders[0]["symbol"] == "BTC/USDT"
    assert repo.list_fills() == []
    assert repo.list_positions(status="open") == []

    from aletheia.execution.reconciliation import reconcile
    assert reconcile()["filled"] == 1
    positions = repo.list_positions(status="open")
    assert len(positions) == 1
    assert positions[0]["execution_mode"] == "bitget_demo"


def test_demo_order_not_blocked_by_live_disabled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    cfg = _demo_config(monkeypatch)
    # live stays disabled (default) — must NOT block a demo order.
    assert cfg.live.state == "live_disabled"
    _fresh_snapshot()
    captured = []
    _patch_place_order(monkeypatch, captured)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="bitget_demo",
        leverage=5,
    )
    assert result["status"] == "materialization_missing"
    assert len(captured) == 1


def test_demo_requires_demo_exchange_mode(tmp_path, monkeypatch):
    """A bitget_demo proposal while exchange.mode == live must be rejected — otherwise
    it would place a REAL live order through the demo path (trust-boundary hole)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.exchange.mode = "live"  # mismatch
    save_config(cfg)
    _fresh_snapshot()
    captured = []
    _patch_place_order(monkeypatch, captured)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="bitget_demo",
        leverage=5,
    )
    assert result["status"] == "rejected"
    assert any("account_mode_mismatch" in v for v in result["risk_violations"])
    # No order reached the (mocked) exchange.
    assert len(captured) == 0


def test_paper_internal_fallback_never_calls_exchange(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    captured = []
    _patch_place_order(monkeypatch, captured)
    from tests.paper_trade_fixture import approvable_paper_trade
    trade = approvable_paper_trade(
        monkeypatch,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        price=60000.0,
    )

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="paper_internal", **trade,
    )
    assert result["status"] == "executed"
    assert len(captured) == 0  # local sim, no exchange call


def test_demo_order_db_trace_when_exchange_fails(tmp_path, monkeypatch):
    """A failed demo order leaves an order row marked error and fabricates NO fill."""
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot()
    from aletheia.exchanges.bitget import client as bg

    async def boom(self, order):
        raise RuntimeError("demo exchange timeout")

    monkeypatch.setattr(bg.BitgetClient, "place_order", boom)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="bitget_demo",
        leverage=5,
    )
    assert result["status"] == "error"
    orders = repo.list_orders()
    assert len(orders) == 1  # pending row written before the exchange call
    assert orders[0]["status"] == "error"
    assert len(repo.list_fills()) == 0
    assert len(repo.list_positions(status="open")) == 0


def test_demo_records_private_fill_price_not_aggregate_or_request(tmp_path, monkeypatch):
    """Only private history may provide the durable fill price."""
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot()
    captured = []
    _patch_place_order(monkeypatch, captured, fill_price=59999.0, filled_quantity=0.0004)
    from tests.specs.spec_pipeline_stop_propagation import (
        _patch_private_fill_history,
    )
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "aletheia.exchanges.bitget.client",
        fill_price=60123.5,
    )

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="bitget_demo",
        leverage=5,
    )
    assert result["status"] == "materialization_missing"
    assert repo.list_fills() == []

    from aletheia.execution.reconciliation import reconcile
    assert reconcile()["filled"] == 1
    fills = repo.list_fills()
    assert len(fills) == 1
    assert fills[0]["fill_price"] == 60123.5  # confirmed, not the 60000 request
    positions = repo.list_positions(status="open")
    assert positions[0]["entry_price"] == 60123.5


# --- place_order mix body shape -------------------------------------------

@pytest.mark.real_place_order
def test_place_order_mix_body_has_margin_coin_and_tradeside(tmp_path, monkeypatch):
    """The mix order body must carry marginCoin + tradeSide:open (hedge mode).
    Captures the JSON body via a mocked httpx post."""
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    import json
    from aletheia.exchanges.bitget import client as bg

    sent = {}

    class _Resp:
        def json(self):
            return {"code": "00000", "data": {"orderId": "EX-9", "clientOid": "c"}}

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, content=None, headers=None):
            sent["body"] = json.loads(content)
            return _Resp()
        async def get(self, url, headers=None):
            # order/detail follow-up — report not-filled so no fabrication
            class _D:
                def json(self_inner):
                    return {"code": "00000", "data": {"state": "live"}}
            return _D()

    monkeypatch.setattr(bg, "httpx_client", lambda **kwargs: _FakeAsyncClient())
    monkeypatch.setattr(bg.BitgetClient, "_headers", lambda self, *a, **k: {})

    client = bg.BitgetClient(mode="demo")
    import asyncio
    asyncio.run(client.place_order(OrderRequest(
        symbol="BTCUSDT", market_type="usdt_futures", side="buy",
        order_type="market", quantity=0.001, client_oid="p1",
    )))
    assert sent["body"]["marginCoin"] == "USDT"
    assert sent["body"]["tradeSide"] == "open"
    assert sent["body"]["productType"] == "USDT-FUTURES"


@pytest.mark.real_place_order
def test_place_order_hedge_close_body_has_holdside(tmp_path, monkeypatch):
    """A close OrderRequest (hold_side set) builds tradeSide:close + holdSide."""
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    import json
    from aletheia.exchanges.bitget import client as bg

    sent = {}

    class _Resp:
        def json(self):
            return {"code": "00000", "data": {"orderId": "EX-C", "clientOid": "c"}}

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, content=None, headers=None):
            sent["body"] = json.loads(content)
            return _Resp()
        async def get(self, url, headers=None):
            class _D:
                def json(self_inner):
                    return {"code": "00000", "data": {"state": "filled", "priceAvg": "60000", "baseVolume": "0.001"}}
            return _D()

    monkeypatch.setattr(bg, "httpx_client", lambda **kwargs: _FakeAsyncClient())
    monkeypatch.setattr(bg.BitgetClient, "_headers", lambda self, *a, **k: {})

    client = bg.BitgetClient(mode="demo")
    import asyncio
    asyncio.run(client.place_order(OrderRequest(
        symbol="BTCUSDT", market_type="usdt_futures", side="buy",
        order_type="market", quantity=0.001, hold_side="long",
    )))
    assert sent["body"]["tradeSide"] == "close"
    assert sent["body"]["holdSide"] == "long"


# --- demo close (hedge-mode reduce) ---------------------------------------

def test_close_demo_position_places_real_close_and_realizes_pnl(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot(price=61000.0)
    captured = []
    _patch_place_order(monkeypatch, captured, fill_price=61000.0, filled_quantity=0.001)
    from tests.specs.spec_pipeline_stop_propagation import (
        _patch_private_fill_history,
    )
    _patch_private_fill_history(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "aletheia.exchanges.bitget.client",
        fill_price=61000.0,
    )

    # Open demo position (long @ 60000).
    pos = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.001, entry_price=60000.0, current_price=61000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_position(pos)

    from aletheia.execution.bitget_broker import close_demo_position
    result = close_demo_position(pos.id, reason="take_profit")

    # A real close order was placed (hedge-mode: side=buy mirrors open, holdSide=long).
    assert len(captured) == 1
    assert captured[0].hold_side == "long"
    assert captured[0].side == "buy"
    assert captured[0].symbol == "BTCUSDT"
    # Aggregate acknowledgement does not change local economics.
    pending = repo._select_one("positions", pos.id)
    assert pending["status"] == "open"
    assert pending["pending_close_status"] == "materialization_missing"
    assert result["closed"] is False
    assert repo.list_fills() == []

    from aletheia.execution.reconciliation import reconcile
    assert reconcile()["filled"] == 1
    closed = repo._select_one("positions", pos.id)
    assert closed["status"] == "closed"
    # The private fill proves the close, but this fixture supplies no trusted
    # funding ledger. Do not treat unknown futures funding as zero net PnL.
    assert closed["realized_pnl"] is None
    assert closed["funding_source"] == "unavailable"
    assert closed["outcome_eligible"] == 0
    assert closed["outcome_exclusion_reason"] == "incomplete_funding_economics"
    assert result["exchange_order_id"] == "EX-DEMO-1"


def test_close_demo_position_keeps_books_consistent_on_failure(tmp_path, monkeypatch):
    """If the close order fails on the exchange, the local position must STAY open
    (a local-only close would dangle a real exchange position)."""
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot()
    from aletheia.exchanges.bitget import client as bg

    async def boom(self, order):
        raise RuntimeError("close timeout")

    monkeypatch.setattr(bg.BitgetClient, "place_order", boom)

    pos = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.001, entry_price=60000.0, current_price=60000.0,
        execution_mode="bitget_demo",
    )
    repo.insert_position(pos)

    from aletheia.execution.bitget_broker import close_demo_position
    with pytest.raises(RuntimeError):
        close_demo_position(pos.id, reason="stop_loss")

    still = repo._select_one("positions", pos.id)
    assert still["status"] == "open"  # NOT closed — books stay consistent

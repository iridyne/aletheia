"""Tests for the Bitget demo trading path (real demo/simulated-trading API).

Demo orders use the explicitly selected exchange account. There is no local
simulator fallback; missing or retired execution modes cannot execute.

The Bitget HTTP call is always mocked here — CI never touches the real API (the
autouse guard in conftest.py also blocks unmocked calls). These verify:
- gateway routes an approved bitget_demo proposal to the broker (real-exchange path),
  tags execution_mode="bitget_demo", and is NOT blocked by live_disabled;
- a bitget_demo proposal is rejected when exchange.mode != demo (account_mode_mismatch
  trust-boundary check) — would otherwise place a REAL live order;
- a failed demo order leaves a DB trace marked error and fabricates NO fill;
- aggregate responses create no Fill/Position; private history supplies exact
  price/quantity/fee/time before local accounting changes;
- place_order builds the mix body with marginCoin + tradeSide (hedge mode);
- a hedge-mode close places a real close order (tradeSide:close + holdSide), then
  realizes PnL only after exact private fill reconciliation.
"""

import sqlite3
from datetime import datetime, timezone

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import MarketSnapshot, Position
from extensions.exchanges.bitget.client import OrderRequest


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
    from decimal import Decimal

    cfg = load_config()
    cfg.agent.permission_mode = permission
    cfg.exchange.mode = "demo"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    save_config(cfg)
    # These tests exercise the broker/materialization path, not missing-account
    # handling.  Seed the same fresh local cache that Risk #17 requires in
    # production so the fixture does not depend on configured starting capital.
    from tests.uta.test_execution_coordinator import _identity
    from aletheia.portfolio import exchange_sync
    from types import SimpleNamespace
    from aletheia.uta import manager
    from extensions.exchanges.composition import funding_history_retention_contract

    # Broker fixtures bind credential-free profile metadata explicitly, without
    # relying on another test importing the application composition root first.
    monkeypatch.setattr(manager, "_retention_contract", funding_history_retention_contract)
    identity = _identity("bitget_demo")
    monkeypatch.setattr(manager, "build_uta_registry", lambda *args, **kwargs: SimpleNamespace(configured_identities=lambda: (identity,)))
    exchange_sync._store_unified({
        "total_equity_usdt": 10000.0, "total_available_usdt": 10000.0,
        "accounts": [{"broker_id": "bitget_demo", "equity_usdt": 10000.0, "available_usdt": 10000.0,
                      "identity": identity.model_dump(mode="json")}],
        "connected_brokers": ["bitget_demo"], "execution_modes": list(manager.DEFAULT_UNIFIED_EXECUTION_MODES),
        "fetched_at": datetime.now(timezone.utc).isoformat(), "positions": [],
    })
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
    """Record normalized commands; acknowledgements never supply private fills."""
    from tests.uta.test_execution_coordinator import _Adapter
    from aletheia.uta.models import SubmissionReport, CommandEvidence, OrderStatusReport, OrderLifecycle, UTACapabilities

    async def submit(command):
        captured.append(command)
        if status == "timeout":
            raise TimeoutError("exchange response unavailable")
        now = datetime.now(timezone.utc)
        if status == "rejected":
            return SubmissionReport(identity=command.expected_account, command_id=command.command_id,
                evidence=CommandEvidence.VENUE_REJECTED, reason_code="venue_rejected",
                received_at=now, profile_version="1")
        return SubmissionReport(identity=command.expected_account, command_id=command.command_id,
            evidence=CommandEvidence.ACKNOWLEDGED, received_at=now, profile_version="1",
            client_order_id=command.client_order_id, venue_order_id="EX-DEMO-1",
            order=OrderStatusReport(identity=command.expected_account, instrument=command.instrument,
                side=command.side, order_type=command.order_type, original_quantity=command.quantity,
                filled_quantity=str(filled_quantity) if filled_quantity is not None else command.quantity,
                state=OrderLifecycle(status), price=None if fill_price is None else str(fill_price), received_at=now))

    def adapter(mode):
        instance = _Adapter(submit, mode)
        instance.capabilities = UTACapabilities(source="isolated_test")
        return instance

    monkeypatch.setattr("aletheia.execution.coordinator.create_uta_adapter", adapter)


def _fresh_snapshot(symbol="BTC/USDT", price=60000.0):
    repo.insert_market_snapshot(MarketSnapshot(
        symbol=symbol, market_type="usdt_futures", exchange="bitget", price=price,
    ))
    repo.insert_market_snapshot(MarketSnapshot(
        symbol=symbol, market_type="spot", exchange="bitget", price=price,
    ))


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
    _patch_private_fill_history(monkeypatch, fill_price=60000.0,)

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="bitget_demo",
        leverage=5,
    )
    assert result["status"] == "materialization_missing"
    assert result["exchange_order_id"] == "EX-DEMO-1"
    assert len(captured) == 1
    # UTA commands and local rows retain canonical instrument identity.
    assert captured[0].instrument.canonical_symbol == "BTC/USDT"
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


def test_demo_order_db_trace_when_exchange_fails(tmp_path, monkeypatch):
    """An ambiguous timeout stays unresolved and fabricates no private fill."""
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot()
    _patch_place_order(monkeypatch, [], status="timeout")

    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.0004, price=60000.0, execution_mode="bitget_demo",
        leverage=5,
    )
    assert result["status"] == "submission_unknown"
    orders = repo.list_orders()
    assert len(orders) == 1  # pending row written before the exchange call
    assert orders[0]["status"] == "submission_unknown"
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
    _patch_private_fill_history(monkeypatch, fill_price=60123.5,)

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
    from extensions.exchanges.bitget import client as bg

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
    from extensions.exchanges.bitget import client as bg

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
    _patch_private_fill_history(monkeypatch, fill_price=61000.0,)

    # Open demo position (long @ 60000).
    pos = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.001, entry_price=60000.0, current_price=61000.0,
        execution_mode="bitget_demo",
    )
    from tests.uta.test_execution_coordinator import _insert_bound_position
    _insert_bound_position(pos)

    from aletheia.execution.coordinator import close_position as close_demo_position
    result = close_demo_position(pos.id, reason="take_profit")

    # Closing a long sends a reduce-only sell through the same UTA boundary.
    assert len(captured) == 1
    assert captured[0].reduce_only is True
    assert captured[0].side == "sell"
    assert captured[0].instrument.canonical_symbol == "BTC/USDT"
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
    # The private fill proves the close, but this manually seeded position has
    # neither a trusted entry fee nor funding. Do not fabricate net PnL.
    assert closed["realized_pnl"] is None
    assert closed["funding_source"] == "unavailable"
    assert closed["outcome_eligible"] == 0
    assert closed["outcome_exclusion_reason"] == "incomplete_realized_economics"
    assert result["exchange_order_id"] == "EX-DEMO-1"


def test_close_demo_position_keeps_books_consistent_on_failure(tmp_path, monkeypatch):
    """If the close order fails on the exchange, the local position must STAY open
    (a local-only close would dangle a real exchange position)."""
    setup_env(tmp_path, monkeypatch)
    _demo_config(monkeypatch)
    _fresh_snapshot()
    _patch_place_order(monkeypatch, [], status="timeout")

    pos = Position(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.001, entry_price=60000.0, current_price=60000.0,
        execution_mode="bitget_demo",
    )
    from tests.uta.test_execution_coordinator import _insert_bound_position
    _insert_bound_position(pos)

    from aletheia.execution.coordinator import close_position as close_demo_position
    with pytest.raises(RuntimeError):
        close_demo_position(pos.id, reason="stop_loss")

    still = repo._select_one("positions", pos.id)
    assert still["status"] == "open"  # NOT closed — books stay consistent

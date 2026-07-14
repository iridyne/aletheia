"""Tests for order reconciliation (execution/reconciliation.py, guide §7/§20.2).

Compares local live orders against the exchange's reported open orders and marks
locally-submitted orders that no longer appear open as filled. Demo/paper orders
are untouched. Network is mocked.
"""

import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, Position


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


def _patch_open_orders(monkeypatch, open_ids, fill_rows=()):
    from aletheia.exchanges.bitget import client as bg

    async def fake_open_orders(self, market_type="spot"):
        return [{"orderId": oid} for oid in open_ids]

    async def fake_fills(self, symbol=None, market_type="spot"):
        return list(fill_rows)

    monkeypatch.setattr(bg.BitgetClient, "get_open_orders", fake_open_orders)
    monkeypatch.setattr(bg.BitgetClient, "get_fills", fake_fills)


def test_submitted_order_no_longer_open_marked_filled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{
            "orderId": "EX-1",
            "fillQuantity": "0.001",
        }],  # private history directly confirms the complete fill
    )

    order = Order(
        source="adopted",
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-1", status="submitted",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=60000, fill_quantity=0.001, fee=0.036,
    ))
    repo.insert_position(Position(
        proposal_id=order.proposal_id,
        order_id=order.id,
        symbol=order.symbol,
        market_type=order.market_type,
        direction=order.direction,
        quantity=order.quantity,
        entry_price=order.price,
        current_price=order.price,
        execution_mode=order.execution_mode,
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()
    assert result["filled"] == 1

    orders = repo.list_orders()
    assert orders[0]["status"] == "filled"


def test_missing_open_order_without_private_fill_stays_unresolved(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(monkeypatch, open_ids=[], fill_rows=[])

    repo.insert_order(Order(
        source="adopted",
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-NO-EVIDENCE", status="submitted",
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.list_orders()[0]["status"] == "submitted"


def test_exchange_fill_without_local_materialization_stays_unresolved(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-REMOTE-ONLY", "fillQuantity": "0.001"}],
    )

    order = Order(
        source="adopted",
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-REMOTE-ONLY", status="materialization_missing",
    )
    repo.insert_order(order)

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "materialization_missing"
    audit = repo.list_audit_events(limit=1)[0]
    assert audit["event_type"] == "order_reconciliation_inconclusive"
    assert "exchange_fill_seen_local_materialization_missing" in audit["after_state"]


def test_incomplete_private_fill_does_not_invent_full_fill(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-PARTIAL-HISTORY", "fillQuantity": "0.0004"}],
    )

    repo.insert_order(Order(
        source="adopted",
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="limit",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-PARTIAL-HISTORY", status="partially_filled",
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.list_orders()[0]["status"] == "partially_filled"


def test_remote_full_fill_does_not_hide_partial_local_materialization(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-LOCAL-PARTIAL", "fillQuantity": "0.001"}],
    )
    order = Order(
        source="adopted",
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="limit",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-LOCAL-PARTIAL", status="partially_filled",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=60000, fill_quantity=0.0004, fee=0.0144,
    ))
    repo.insert_position(Position(
        order_id=order.id,
        symbol=order.symbol,
        market_type=order.market_type,
        direction=order.direction,
        quantity=0.0004,
        entry_price=order.price,
        current_price=order.price,
        execution_mode=order.execution_mode,
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "partially_filled"


def test_averaging_entry_uses_direct_position_identity(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-AVERAGING", "fillQuantity": "0.001"}],
    )
    position = Position(
        order_id="original-entry-order",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.011,
        entry_price=60000,
        current_price=60000,
        execution_mode="bitget_live_limited",
    )
    repo.insert_position(position)
    order = Order(
        source="adopted",
        position_id=position.id,
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=0.001,
        price=60000,
        execution_mode=position.execution_mode,
        exchange_order_id="EX-AVERAGING",
        status="materialization_missing",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=60000, fill_quantity=0.001, fee=0.036,
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 1
    assert repo.get_order(order.id)["status"] == "filled"


def test_close_direct_position_identity_requires_terminal_position(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-CLOSE-DIRECT", "fillQuantity": "0.01"}],
    )
    position = Position(
        order_id="entry-order",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100,
        current_price=101,
        execution_mode="bitget_live_limited",
    )
    repo.insert_position(position)
    order = Order(
        source="adopted",
        client_order_id=f"close-{position.id}",
        position_id=position.id,
        close_reason="stop_loss",
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=position.quantity,
        price=position.current_price,
        execution_mode=position.execution_mode,
        exchange_order_id="EX-CLOSE-DIRECT",
        status="materialization_missing",
    )
    repo.insert_order(order)
    repo.insert_fill(Fill(
        order_id=order.id, fill_price=101, fill_quantity=0.01, fee=0.000606,
    ))

    from aletheia.execution.reconciliation import reconcile
    first = reconcile()

    assert first["filled"] == 0
    assert first["inconclusive"] == 1
    assert repo.get_order(order.id)["status"] == "materialization_missing"

    repo.update_position(position.id, status="closed")
    second = reconcile()

    assert second["filled"] == 1
    assert repo.get_order(order.id)["status"] == "filled"


def test_still_open_order_left_alone(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(monkeypatch, open_ids=["EX-2"])  # still open on exchange

    repo.insert_order(Order(
        source="adopted",
        symbol="ETH/USDT", market_type="spot", direction="long", order_type="limit",
        quantity=1.0, price=3000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-2", status="submitted",
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()
    assert result["filled"] == 0
    assert repo.list_orders()[0]["status"] == "submitted"


def test_paper_orders_ignored(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(monkeypatch, open_ids=[])

    repo.insert_order(Order(
        source="adopted",
        symbol="SOL/USDT", market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=150, execution_mode="paper_internal", status="filled",
    ))
    from aletheia.execution.reconciliation import reconcile
    result = reconcile()
    # paper order untouched and not counted
    assert result["filled"] == 0
    assert result["checked"] == 0


def test_reconciliation_scans_all_unresolved_orders_without_fixed_limit(
    tmp_path, monkeypatch,
):
    setup_env(tmp_path, monkeypatch)
    order_ids = [f"EX-BULK-{index}" for index in range(501)]
    _patch_open_orders(monkeypatch, open_ids=order_ids)
    for exchange_order_id in order_ids:
        repo.insert_order(Order(
            source="adopted",
            symbol="BTC/USDT", market_type="spot", direction="long",
            order_type="limit", quantity=0.001, price=60000,
            execution_mode="bitget_live_limited",
            exchange_order_id=exchange_order_id, status="submitted",
        ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["checked"] == 501
    assert result["filled"] == 0

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
from aletheia.storage.models import Order


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

    repo.insert_order(Order(
        symbol="BTCUSDT", market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-1", status="submitted",
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
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="market",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-NO-EVIDENCE", status="submitted",
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.list_orders()[0]["status"] == "submitted"


def test_incomplete_private_fill_does_not_invent_full_fill(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(
        monkeypatch,
        open_ids=[],
        fill_rows=[{"orderId": "EX-PARTIAL-HISTORY", "fillQuantity": "0.0004"}],
    )

    repo.insert_order(Order(
        symbol="BTC/USDT", market_type="spot", direction="long", order_type="limit",
        quantity=0.001, price=60000, execution_mode="bitget_live_limited",
        exchange_order_id="EX-PARTIAL-HISTORY", status="partially_filled",
    ))

    from aletheia.execution.reconciliation import reconcile
    result = reconcile()

    assert result["filled"] == 0
    assert result["inconclusive"] == 1
    assert repo.list_orders()[0]["status"] == "partially_filled"


def test_still_open_order_left_alone(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _patch_open_orders(monkeypatch, open_ids=["EX-2"])  # still open on exchange

    repo.insert_order(Order(
        symbol="ETHUSDT", market_type="spot", direction="long", order_type="limit",
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
        symbol="SOLUSDT", market_type="spot", direction="long", order_type="market",
        quantity=1.0, price=150, execution_mode="paper_internal", status="filled",
    ))
    from aletheia.execution.reconciliation import reconcile
    result = reconcile()
    # paper order untouched and not counted
    assert result["filled"] == 0
    assert result["checked"] == 0

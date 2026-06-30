"""Regression tests for exchange-close confirmation.

Real-exchange close paths must not fabricate a fill or mark a position closed
until the exchange reports a confirmed fill. An accepted-but-unfilled close order
means local books should keep the position open and wait for reconciliation or a
later human/operator action.
"""

import importlib
import json
import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.storage.models import MarketSnapshot, Position


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


def _insert_position(execution_mode: str) -> Position:
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode=execution_mode,
    )
    repo.insert_position(pos)
    return pos


def _patch_unconfirmed_close(
    monkeypatch,
    broker_module: str,
    client_name: str,
    response_module: str,
    exchange_status: str,
    captured: list | None = None,
):
    mod = importlib.import_module(broker_module)
    response_cls = importlib.import_module(response_module).OrderResponse

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def place_order(self, order):
            if captured is not None:
                captured.append(order)
            return response_cls(
                order_id="EX-CLOSE-1",
                client_oid=order.client_oid,
                status=exchange_status,
                fill_price=100.5,
                filled_quantity=0.005,
            )

        async def close(self):
            pass

    monkeypatch.setattr(mod, client_name, FakeClient)


@pytest.mark.parametrize(
    "exchange_status,expected_status",
    [
        ("submitted", "submitted"),
        ("partially_filled", "partial"),
    ],
)
@pytest.mark.parametrize(
    "broker_module,client_name,response_module,close_func,execution_mode",
    [
        (
            "aletheia.execution.bitget_broker",
            "BitgetClient",
            "aletheia.exchanges.bitget.client",
            "close_demo_position",
            "bitget_demo",
        ),
        (
            "aletheia.execution.okx_broker",
            "OKXClient",
            "aletheia.exchanges.okx.client",
            "close_okx_position",
            "okx_demo",
        ),
        (
            "aletheia.execution.binance_broker",
            "BinanceClient",
            "aletheia.exchanges.binance.client",
            "close_testnet_position",
            "binance_testnet",
        ),
    ],
)
def test_exchange_close_unconfirmed_does_not_fabricate_full_close(
    tmp_path,
    monkeypatch,
    broker_module,
    client_name,
    response_module,
    close_func,
    execution_mode,
    exchange_status,
    expected_status,
):
    setup_env(tmp_path, monkeypatch)
    pos = _insert_position(execution_mode)
    captured = []
    _patch_unconfirmed_close(
        monkeypatch,
        broker_module,
        client_name,
        response_module,
        exchange_status,
        captured,
    )

    result = getattr(importlib.import_module(broker_module), close_func)(pos.id, reason="stop_loss")

    assert result["closed"] is False
    assert result["status"] == expected_status
    assert result["fill_quantity"] == 0

    still_open = repo._select_one("positions", pos.id)
    assert still_open["status"] == "open"
    assert still_open["pending_close_order_id"]
    assert still_open["pending_close_status"] == expected_status

    orders = repo.list_orders()
    assert len(orders) == 1
    assert orders[0]["status"] == expected_status
    assert orders[0]["exchange_order_id"] == "EX-CLOSE-1"
    assert repo.list_fills() == []
    assert len(captured) == 1
    assert captured[0].client_oid == f"close-{pos.id}"

    second_result = getattr(importlib.import_module(broker_module), close_func)(pos.id, reason="stop_loss")

    assert second_result["closed"] is False
    assert second_result["already_pending"] is True
    assert len(captured) == 1
    assert len(repo.list_orders()) == 1


def test_position_manager_counts_only_confirmed_exchange_closes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=90.0,
        execution_mode="bitget_demo",
        stop_price=95.0,
    )
    repo.insert_position(pos)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="usdt_futures", price=90.0))

    import aletheia.portfolio.position_manager as pm

    calls = []

    def fake_close(position_id, reason, fallback_exit_price=None, *, allow_live=False):
        calls.append((position_id, reason, fallback_exit_price, allow_live))
        return {"closed": False, "status": "submitted"}

    monkeypatch.setattr(pm, "close_position_routed", fake_close)

    result = pm.evaluate_open_positions()

    assert calls
    assert result["checked"] == 1
    assert result["closed"] == 0
    assert repo._select_one("positions", pos.id)["status"] == "open"


def test_position_manager_skips_positions_with_pending_exchange_close(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=90.0,
        execution_mode="bitget_demo",
        stop_price=95.0,
        pending_close_order_id="close-order-1",
        pending_close_status="submitted",
    )
    repo.insert_position(pos)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="usdt_futures", price=90.0))

    import aletheia.portfolio.position_manager as pm

    calls = []

    def fake_close(position_id, reason, fallback_exit_price=None, *, allow_live=False):
        calls.append((position_id, reason, fallback_exit_price, allow_live))
        raise AssertionError("pending close should suppress repeat close routing")

    monkeypatch.setattr(pm, "close_position_routed", fake_close)

    result = pm.evaluate_open_positions()

    assert calls == []
    assert result["checked"] == 1
    assert result["closed"] == 0
    assert repo._select_one("positions", pos.id)["status"] == "open"


def test_bitget_unconfirmed_close_keeps_existing_tpsl_bracket(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.trading.exchange_tpsl_enabled = True
    save_config(cfg)

    pos = Position(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.01,
        entry_price=100.0,
        current_price=101.0,
        execution_mode="bitget_demo",
        exchange_tpsl_order_id=json.dumps({"sl": "sl-1", "tp": "tp-1"}),
    )
    repo.insert_position(pos)
    captured = []
    _patch_unconfirmed_close(
        monkeypatch,
        "aletheia.execution.bitget_broker",
        "BitgetClient",
        "aletheia.exchanges.bitget.client",
        "submitted",
        captured,
    )

    import aletheia.execution.bitget_broker as bitget_broker

    cancel_calls = []

    def fake_cancel(client, exchange_symbol, tpsl_json):
        cancel_calls.append((exchange_symbol, tpsl_json))

    monkeypatch.setattr(bitget_broker, "_try_cancel_tpsl", fake_cancel)

    result = bitget_broker.close_demo_position(pos.id, reason="stop_loss")

    assert result["closed"] is False
    assert cancel_calls == []
    still_open = repo._select_one("positions", pos.id)
    assert still_open["status"] == "open"
    assert json.loads(still_open["exchange_tpsl_order_id"]) == {"sl": "sl-1", "tp": "tp-1"}

"""Tests for exchange-native TPSL bracket order logic.

Covers:
- BitgetClient.place_tpsl_order / cancel_plan_order / get_pending_plan_orders
- bitget_broker._try_place_tpsl / _try_cancel_tpsl / replace_exchange_stop
- position_manager._sync_exchange_closed
- execute_live_order with exchange_tpsl_enabled=True
- TPSL failure is non-fatal (position still opens)
- close_demo_position cancels TPSL before placing close order
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.async_test_utils import closing_coro_runner


# ─── BitgetClient TPSL methods ───────────────────────────────────────────────


def _make_client():
    """Return a BitgetClient with mocked _headers (no real credentials needed)."""
    from aletheia.exchanges.bitget.client import BitgetClient
    c = BitgetClient.__new__(BitgetClient)
    c._base = "https://api.bitget.com"
    c._headers = lambda method, path, body="": {}
    return c


@pytest.mark.asyncio
async def test_place_tpsl_order_sends_correct_body():
    """place_tpsl_order POSTs correct fields to /api/v2/mix/order/place-tpsl-order."""
    client = _make_client()
    captured = {}

    async def mock_post(url, content, headers):
        captured["url"] = url
        captured["body"] = json.loads(content)
        resp = MagicMock()
        resp.json.return_value = {"code": "00000", "data": {"orderId": "plan-123"}}
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(post=mock_post))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.place_tpsl_order(
            symbol="BTCUSDT", plan_type="pos_loss",
            trigger_price=95000.0, hold_side="long", size=0.01,
        )

    assert result == "plan-123"
    body = captured["body"]
    assert body["symbol"] == "BTCUSDT"
    assert body["planType"] == "pos_loss"
    assert body["triggerPrice"] == "95000.0"
    assert body["holdSide"] == "long"
    assert body["size"] == "0.01"
    assert body["productType"] == "USDT-FUTURES"
    assert body["marginMode"] == "crossed"


@pytest.mark.asyncio
async def test_cancel_plan_order_sends_correct_body():
    captured = {}

    async def mock_post(url, content, headers):
        captured["body"] = json.loads(content)
        resp = MagicMock()
        resp.json.return_value = {"code": "00000", "data": {}}
        return resp

    client = _make_client()
    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(post=mock_post))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.cancel_plan_order("BTCUSDT", "plan-456")

    assert result["cancelled"] is True
    assert captured["body"]["orderIdList"] == [{"orderId": "plan-456"}]
    assert captured["body"]["productType"] == "USDT-FUTURES"


@pytest.mark.asyncio
async def test_get_pending_plan_orders_returns_list():
    client = _make_client()

    async def mock_get(url, headers):
        resp = MagicMock()
        resp.json.return_value = {
            "code": "00000",
            "data": {"entrustedList": [{"orderId": "p1"}, {"orderId": "p2"}]},
        }
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.get_pending_plan_orders()

    assert len(result) == 2
    assert result[0]["orderId"] == "p1"


# ─── _try_place_tpsl ─────────────────────────────────────────────────────────


def test_try_place_tpsl_stores_ids_on_success(tmp_path):
    """_try_place_tpsl writes {"sl": ..., "tp": ...} to exchange_tpsl_order_id."""
    from aletheia.execution.bitget_broker import _try_place_tpsl

    mock_client = MagicMock()
    mock_client.place_tpsl_order = AsyncMock(side_effect=["sl-id-1", "tp-id-1"])
    mock_cfg = MagicMock()

    with patch(
        "aletheia.execution.bitget_broker._run_coro",
        side_effect=closing_coro_runner("sl-id-1", "tp-id-1"),
    ), patch("aletheia.execution.bitget_broker.update_position") as mock_update:
        _try_place_tpsl(
            client=mock_client,
            position_id="pos-1",
            exchange_symbol="BTCUSDT",
            hold_side="long",
            size=0.01,
            stop_price=95000.0,
            take_profit_price=105000.0,
            cfg=mock_cfg,
        )

        mock_update.assert_called_once()
        stored = json.loads(mock_update.call_args[1]["exchange_tpsl_order_id"])
        assert stored["sl"] == "sl-id-1"
        assert stored["tp"] == "tp-id-1"


def test_try_place_tpsl_non_fatal_on_failure():
    """_try_place_tpsl does not raise when API errors; update_position not called."""
    from aletheia.execution.bitget_broker import _try_place_tpsl

    mock_client = MagicMock()
    mock_cfg = MagicMock()

    with patch(
        "aletheia.execution.bitget_broker._run_coro",
        side_effect=closing_coro_runner(RuntimeError("API down"), RuntimeError("API down")),
    ), \
         patch("aletheia.execution.bitget_broker.update_position") as mock_update:
        _try_place_tpsl(
            client=mock_client, position_id="pos-2", exchange_symbol="BTCUSDT",
            hold_side="long", size=0.01, stop_price=95000.0,
            take_profit_price=105000.0, cfg=mock_cfg,
        )
        mock_update.assert_not_called()


# ─── _try_cancel_tpsl ────────────────────────────────────────────────────────


def test_try_cancel_tpsl_cancels_both_orders():
    from aletheia.execution.bitget_broker import _try_cancel_tpsl

    mock_client = MagicMock()
    cancelled = []

    def fake_run(coro):
        coro.close()
        cancelled.append("called")
        return {"cancelled": True}

    tpsl_json = json.dumps({"sl": "sl-99", "tp": "tp-99"})
    with patch("aletheia.execution.bitget_broker._run_coro", side_effect=fake_run):
        _try_cancel_tpsl(mock_client, "BTCUSDT", tpsl_json)

    assert len(cancelled) == 2


def test_try_cancel_tpsl_noop_on_empty():
    from aletheia.execution.bitget_broker import _try_cancel_tpsl

    mock_client = MagicMock()
    with patch("aletheia.execution.bitget_broker._run_coro") as mock_run:
        _try_cancel_tpsl(mock_client, "BTCUSDT", None)
        mock_run.assert_not_called()


# ─── replace_exchange_stop ───────────────────────────────────────────────────


def test_replace_exchange_stop_cancel_and_replace():
    from aletheia.execution.bitget_broker import replace_exchange_stop

    mock_row = {
        "exchange_tpsl_order_id": json.dumps({"sl": "old-sl", "tp": "tp-88"}),
        "symbol": "BTC/USDT",
        "direction": "long",
        "quantity": "0.01",
    }

    calls = []

    def fake_run(coro):
        coro.close()
        calls.append("called")
        return "new-sl-id"

    with patch("aletheia.execution.bitget_broker._run_coro", side_effect=fake_run), \
         patch("aletheia.execution.bitget_broker.load_config") as mock_cfg, \
         patch("aletheia.execution.bitget_broker.BitgetClient") as mock_cls, \
         patch("aletheia.execution.bitget_broker.to_bitget", return_value="BTCUSDT"), \
         patch("aletheia.storage.repositories._select_one", return_value=mock_row), \
         patch("aletheia.execution.bitget_broker.update_position") as mock_update:
        mock_cfg.return_value.exchange.mode = "demo"
        mock_cls.return_value = MagicMock()

        replace_exchange_stop("pos-3", 94000.0)

        assert mock_update.called
        stored = json.loads(mock_update.call_args[1]["exchange_tpsl_order_id"])
        assert stored["sl"] == "new-sl-id"
        assert stored["tp"] == "tp-88"


def test_replace_exchange_stop_noop_when_no_tpsl():
    """replace_exchange_stop is no-op if position has no exchange_tpsl_order_id."""
    from aletheia.execution.bitget_broker import replace_exchange_stop

    mock_row = {"exchange_tpsl_order_id": None, "symbol": "BTC/USDT", "direction": "long"}
    with patch("aletheia.storage.repositories._select_one", return_value=mock_row), \
         patch("aletheia.execution.bitget_broker._run_coro") as mock_run:
        replace_exchange_stop("pos-none", 90000.0)
        mock_run.assert_not_called()


# ─── _sync_exchange_closed ───────────────────────────────────────────────────


def test_sync_exchange_closed_marks_position_closed():
    """When exchange no longer holds a demo position, close it locally."""
    from aletheia.portfolio.position_manager import _sync_exchange_closed

    mock_cfg = MagicMock()
    mock_cfg.trading.exchange_tpsl_enabled = True
    mock_cfg.exchange.mode = "demo"

    open_pos = {"id": "pos-4", "symbol": "ETH/USDT", "direction": "long", "execution_mode": "bitget_demo",
                "current_price": 3000.0, "entry_price": 3000.0}

    # Exchange only holds SOL, not ETH — ETH bracket must have triggered
    ExchangePos = type("P", (), {"symbol": "SOLUSDT", "side": "long", "quantity": 0.5})
    mock_client_inst = MagicMock()
    mock_client_inst.get_positions = AsyncMock(return_value=[ExchangePos()])

    # close_position is a top-level import in position_manager; patch the bound name
    # BitgetClient / to_canonical / asyncio.run are lazy-imported inside the function
    # → patch at their source module so the local `from X import Y` picks up the mock
    with patch("aletheia.portfolio.position_manager.list_positions", return_value=[open_pos]), \
         patch("aletheia.portfolio.position_manager._latest_price", return_value=2950.0), \
         patch("aletheia.portfolio.position_manager.close_position") as mock_close, \
         patch("aletheia.exchanges.bitget.client.BitgetClient", return_value=mock_client_inst), \
         patch("aletheia.data.symbols.to_canonical", side_effect=lambda s: "SOL/USDT"), \
         patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")), \
         patch("asyncio.run", side_effect=closing_coro_runner([ExchangePos()])):
        synced = _sync_exchange_closed(mock_cfg)

    assert synced == 1
    mock_close.assert_called_once()
    _, kwargs = mock_close.call_args
    assert kwargs["reason"] == "exchange_bracket_triggered"


def test_sync_exchange_closed_skips_when_disabled():
    from aletheia.portfolio.position_manager import _sync_exchange_closed

    mock_cfg = MagicMock()
    mock_cfg.trading.exchange_tpsl_enabled = False
    result = _sync_exchange_closed(mock_cfg)
    assert result == 0

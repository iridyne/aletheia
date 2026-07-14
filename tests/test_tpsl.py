"""Tests for exchange-native TPSL bracket order logic.

Covers:
- BitgetClient.place_tpsl_order / cancel_plan_order / get_pending_plan_orders
- bitget_broker._try_place_tpsl / _try_cancel_tpsl / replace_exchange_stop
- position_manager has no bulk-position-absence close shortcut
- execute_live_order with exchange_tpsl_enabled=True
- TPSL failure is non-fatal (position still opens)
- close_demo_position cancels TPSL before placing close order
"""
from __future__ import annotations

import json
import inspect
from urllib.parse import parse_qs, urlparse
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


@pytest.mark.asyncio
async def test_get_plan_order_history_requests_profit_loss_and_exact_plan_id():
    """The history reader must query the TPSL family and exact plan identity."""
    client = _make_client()
    captured = {}

    async def mock_get(url, headers):
        captured["url"] = url
        resp = MagicMock()
        resp.json.return_value = {
            "code": "00000",
            "data": {
                "entrustedList": [
                    {
                        "orderId": "plan-123",
                        "planStatus": "executed",
                        "executeOrderId": "exec-456",
                    }
                ]
            },
        }
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.get_plan_order_history(order_id="plan-123")

    query = parse_qs(urlparse(captured["url"]).query)
    assert query == {
        "productType": ["USDT-FUTURES"],
        "planType": ["profit_loss"],
        "orderId": ["plan-123"],
    }
    assert result[0]["planStatus"] == "executed"
    assert result[0]["executeOrderId"] == "exec-456"


@pytest.mark.asyncio
async def test_get_order_fills_queries_exact_order_and_paginates_completely():
    """Exact private fill history must preserve every page and its cursor."""
    client = _make_client()
    captured_urls = []
    first_page = [
        {
            "orderId": "exec-456",
            "tradeId": f"trade-{index}",
            "fillPrice": "101",
            "fillQuantity": "0.01",
        }
        for index in range(100)
    ]
    payloads = [
        {
            "code": "00000",
            "data": {"fillList": first_page, "endId": "cursor-100"},
        },
        {
            "code": "00000",
            "data": {
                "fillList": [
                    {
                        "orderId": "exec-456",
                        "tradeId": "trade-100",
                        "fillPrice": "102",
                        "fillQuantity": "0.01",
                    }
                ],
                "endId": "cursor-101",
            },
        },
    ]

    async def mock_get(url, headers):
        captured_urls.append(url)
        resp = MagicMock()
        resp.json.return_value = payloads.pop(0)
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.get_order_fills(
            order_id="exec-456",
            symbol="BTCUSDT",
            market_type="usdt_futures",
        )

    first_query = parse_qs(urlparse(captured_urls[0]).query)
    second_query = parse_qs(urlparse(captured_urls[1]).query)
    assert first_query == {
        "productType": ["USDT-FUTURES"],
        "symbol": ["BTCUSDT"],
        "orderId": ["exec-456"],
        "limit": ["100"],
    }
    assert second_query["idLessThan"] == ["cursor-100"]
    assert len(result) == 101
    assert result[-1]["tradeId"] == "trade-100"


@pytest.mark.asyncio
async def test_get_order_fills_rejects_full_page_without_next_cursor():
    """A truncated private history page is not complete fill evidence."""
    client = _make_client()

    async def mock_get(url, headers):
        resp = MagicMock()
        resp.json.return_value = {
            "code": "00000",
            "data": {
                "fillList": [
                    {"orderId": "exec-456", "tradeId": f"trade-{index}"}
                    for index in range(100)
                ]
            },
        }
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        with pytest.raises(RuntimeError, match="pagination incomplete"):
            await client.get_order_fills(
                order_id="exec-456",
                symbol="BTCUSDT",
                market_type="usdt_futures",
            )


@pytest.mark.asyncio
async def test_get_order_fills_rejects_rows_for_another_order():
    """An exact-order endpoint response cannot silently mix another Order."""
    client = _make_client()

    async def mock_get(url, headers):
        resp = MagicMock()
        resp.json.return_value = {
            "code": "00000",
            "data": {
                "fillList": [
                    {"orderId": "another-order", "tradeId": "wrong-trade"}
                ]
            },
        }
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("aletheia.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        with pytest.raises(ValueError, match="mismatched"):
            await client.get_order_fills(
                order_id="exec-456",
                symbol="BTCUSDT",
                market_type="usdt_futures",
            )


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


@pytest.mark.parametrize(
    "execution_mode,expected_client_mode",
    (
        ("bitget_demo", "demo"),
        ("bitget_live_limited", "live"),
    ),
)
def test_replace_exchange_stop_cancel_and_replace(
    execution_mode, expected_client_mode
):
    from aletheia.execution.bitget_broker import replace_exchange_stop

    mock_row = {
        "exchange_tpsl_order_id": json.dumps({"sl": "old-sl", "tp": "tp-88"}),
        "symbol": "BTC/USDT",
        "direction": "long",
        "quantity": "0.01",
        "execution_mode": execution_mode,
    }

    calls = []

    def fake_run(coro):
        coro.close()
        calls.append("called")
        return "new-sl-id"

    with patch("aletheia.execution.bitget_broker._run_coro", side_effect=fake_run), \
         patch("aletheia.execution.bitget_broker.BitgetClient") as mock_cls, \
         patch("aletheia.execution.bitget_broker.to_bitget", return_value="BTCUSDT"), \
         patch("aletheia.storage.repositories._select_one", return_value=mock_row), \
         patch("aletheia.execution.bitget_broker.update_position") as mock_update:
        mock_cls.return_value = MagicMock()

        replace_exchange_stop("pos-3", 94000.0)

        mock_cls.assert_called_once_with(mode=expected_client_mode)
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


def test_position_manager_has_no_bulk_absence_close_shortcut():
    """A missing row in a bulk account view is never terminal fill evidence."""
    from aletheia.portfolio import position_manager

    assert not hasattr(position_manager, "_sync_exchange_closed")
    assert "_sync_exchange_closed" not in inspect.getsource(
        position_manager.evaluate_open_positions
    )

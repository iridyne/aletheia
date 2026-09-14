"""Native Bitget protocol and nonfatal qualified UTA protection failures."""
from __future__ import annotations

import json
import inspect
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.async_test_utils import closing_coro_runner


# ─── BitgetClient TPSL methods ───────────────────────────────────────────────


def _make_client():
    """Return a BitgetClient with mocked _headers (no real credentials needed).

    classic_v2 profile: exercises the /api/v2/ mix TPSL endpoints directly.
    """
    from extensions.exchanges.bitget.client import BitgetClient
    c = BitgetClient.__new__(BitgetClient)
    c._base = "https://api.bitget.com"
    c._headers = lambda method, path, body="": {}
    c.profile = "classic_v2"
    c.mode = "demo"
    c.demo = True
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

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
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

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
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

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
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

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
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

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
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

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
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

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.get_order_fills(
            order_id="exec-456",
            symbol="BTCUSDT",
            market_type="usdt_futures",
        )
    assert result == []


@pytest.mark.asyncio
async def test_get_order_fills_uses_uta_v3_when_unified():
    client = _make_client()
    client.profile = "uta_v3"
    captured = {}

    async def mock_get(url, headers):
        captured["url"] = url
        resp = MagicMock()
        resp.json.return_value = {
            "code": "00000",
            "data": {
                "list": [
                    {"orderId": "uta-1", "execId": "fill-1", "fillPx": "1.12"},
                    {"orderId": "other", "execId": "fill-x", "fillPx": "9"},
                ]
            },
        }
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.get_order_fills(
            order_id="uta-1",
            symbol="XRPUSDT",
            market_type="usdt_futures",
        )

    assert "/api/v3/trade/fills" in captured["url"]
    query = parse_qs(urlparse(captured["url"]).query)
    assert query["category"] == ["USDT-FUTURES"]
    assert query["orderId"] == ["uta-1"]
    assert [row["execId"] for row in result] == ["fill-1"]


@pytest.mark.asyncio
async def test_get_order_fills_uta_treats_null_list_as_empty():
    client = _make_client()
    client.profile = "uta_v3"

    async def mock_get(url, headers):
        resp = MagicMock()
        resp.json.return_value = {
            "code": "00000",
            "data": {"cursor": None, "list": None},
        }
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.get_order_fills(
            order_id="uta-empty",
            symbol="XRPUSDT",
            market_type="usdt_futures",
        )
    assert result == []


@pytest.mark.asyncio
async def test_close_uta_position_requires_symbol_and_pos_side():
    client = _make_client()
    captured = {}

    async def mock_post(url, content, headers):
        captured["url"] = url
        captured["body"] = json.loads(content)
        resp = MagicMock()
        resp.json.return_value = {"code": "00000", "data": {"orderId": "flash-1"}}
        return resp

    mock_httpx = MagicMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=MagicMock(post=mock_post))
    mock_httpx.__aexit__ = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="pos_side"):
        await client.close_uta_position(symbol="XRP/USDT", pos_side="both")

    with patch("extensions.exchanges.bitget.client.httpx_client", return_value=mock_httpx):
        result = await client.close_uta_position(symbol="XRP/USDT", pos_side="short")

    assert "/api/v3/trade/close-positions" in captured["url"]
    assert captured["body"] == {
        "category": "USDT-FUTURES",
        "symbol": "XRPUSDT",
        "posSide": "short",
    }
    assert result["orderId"] == "flash-1"


def test_uta_protection_failure_preserves_position_without_fabricating_ids(tmp_path, monkeypatch):
    from tests.uta.test_execution_coordinator import _setup_db, _insert_bound_position, _Adapter
    from aletheia.storage.models import Position
    from aletheia.storage import repositories as repo
    from aletheia.uta.models import UTACapabilities, CapabilitySupport
    from aletheia.execution import gateway, coordinator
    _setup_db(tmp_path, monkeypatch)
    position = Position(symbol="BTC/USDT", market_type="usdt_futures", direction="long", quantity=0.01, entry_price=100, execution_mode="bitget_demo", stop_price=95, take_profit_price=110)
    _insert_bound_position(position)
    async def submit(command):
        raise RuntimeError("exchange unavailable")
    adapter = _Adapter(submit, "bitget_demo")
    adapter.capabilities = UTACapabilities(native_tpsl=CapabilitySupport.SUPPORTED, source="isolated_test")
    monkeypatch.setattr(coordinator, "create_uta_adapter", lambda _mode: adapter)
    assert gateway.place_exchange_protection(position.id) == {}
    stored = repo.get_position(position.id)
    assert stored["status"] == "open"
    assert stored["exchange_tpsl_order_id"] is None
    assert repo.list_fills() == []
    protection = [order for order in repo.list_orders() if order["position_id"] == position.id]
    assert {order["close_reason"]: order["status"] for order in protection} == {"protective_stop": "submission_unknown", "take_profit": "submission_unknown"}

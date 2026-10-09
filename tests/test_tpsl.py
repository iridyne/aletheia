"""Native Bitget protocol and nonfatal qualified UTA protection failures."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


# Native plan history and exact fills remain; protection mutations use UTA.


class _FakeUTAExchange:
    def __init__(self, *, fills=None, sub_orders=None):
        self.fills = fills
        self.sub_orders = sub_orders
        self.requests = []

    async def privateUtaGetV3TradeFills(self, params):
        self.requests.append(("fills", params))
        return self.fills

    async def privateUtaGetV3TradeStrategySubOrders(self, params):
        self.requests.append(("sub_orders", params))
        return self.sub_orders


def _make_client(exchange):
    """Return a BitgetClient whose UTA CCXT exchange is the given fake."""
    from extensions.exchanges.bitget.client import BitgetClient
    c = BitgetClient(mode="demo")
    c._make_exchange = lambda: (exchange, MagicMock())  # type: ignore[method-assign]
    return c


@pytest.fixture(autouse=True)
def _no_ccxt_close(monkeypatch):
    monkeypatch.setattr("extensions.exchanges.bitget.client.close_ccxt", AsyncMock())


@pytest.mark.asyncio
async def test_get_plan_order_history_maps_uta_strategy_sub_orders_to_exact_plan_id():
    """The history reader must query the exact parent plan and expose the executed child."""
    exchange = _FakeUTAExchange(sub_orders={
        "code": "00000",
        "data": {"list": [{"subOrderId": "exec-456", "status": "filled"}]},
    })
    result = await _make_client(exchange).get_plan_order_history(order_id="plan-123")

    assert exchange.requests == [("sub_orders", {"orderId": "plan-123", "limit": "100"})]
    assert result[0]["orderId"] == "plan-123"
    assert result[0]["planStatus"] == "executed"
    assert result[0]["executeOrderId"] == "exec-456"


@pytest.mark.asyncio
async def test_get_order_fills_uses_uta_v3_when_unified():
    exchange = _FakeUTAExchange(fills={
        "code": "00000",
        "data": {
            "list": [
                {"orderId": "uta-1", "execId": "fill-1", "fillPx": "1.12"},
                {"orderId": "other", "execId": "fill-x", "fillPx": "9"},
            ]
        },
    })
    result = await _make_client(exchange).get_order_fills(
        order_id="uta-1",
        symbol="XRPUSDT",
        market_type="usdt_futures",
    )

    assert exchange.requests == [("fills", {
        "category": "USDT-FUTURES",
        "symbol": "XRPUSDT",
        "orderId": "uta-1",
        "limit": "100",
    })]
    assert [row["execId"] for row in result] == ["fill-1"]


@pytest.mark.asyncio
async def test_get_order_fills_uta_treats_null_list_as_empty():
    exchange = _FakeUTAExchange(fills={
        "code": "00000",
        "data": {"cursor": None, "list": None},
    })
    result = await _make_client(exchange).get_order_fills(
        order_id="uta-empty",
        symbol="XRPUSDT",
        market_type="usdt_futures",
    )
    assert result == []

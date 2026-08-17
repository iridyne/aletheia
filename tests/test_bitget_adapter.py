"""Tests for the Bitget adapter methods added to satisfy §19.3 ExchangeAdapter.

Covers: get_account, get_positions, get_open_orders, cancel_order, get_fills.
HTTP is faked (no network); auth/creds are stubbed.
"""

import pytest

from aletheia.exchanges.bitget import client as bg


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Minimal async-context httpx.AsyncClient replacement returning canned JSON.

    `routes` maps a substring of the request path -> payload dict.
    Records (method, url) into `calls`.
    """

    routes: dict = {}
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _match(self, url):
        for frag, payload in self.routes.items():
            if frag in url:
                return payload
        return {"code": "00000", "data": []}

    async def get(self, url, headers=None, **kwargs):
        type(self).calls.append(("GET", url))
        return _FakeResp(self._match(url))

    async def post(self, url, headers=None, content=None, **kwargs):
        type(self).calls.append(("POST", url, content))
        return _FakeResp(self._match(url))


@pytest.fixture
def fake_http(monkeypatch):
    _FakeAsyncClient.routes = {}
    _FakeAsyncClient.calls = []
    monkeypatch.setattr(bg, "httpx_client", lambda **kwargs: _FakeAsyncClient())
    # Stub credentials so authenticated calls don't require secret storage.
    monkeypatch.setattr(bg.BitgetClient, "_get_creds", lambda self: ("k", "s", "p"))
    return _FakeAsyncClient


@pytest.mark.anyio
async def test_get_account(fake_http):
    fake_http.routes = {"/api/v2/spot/account/info": {
        "code": "00000",
        "data": {"userId": "123", "accountStatus": "normal"},
    }}
    client = bg.BitgetClient(mode="demo")
    acct = await client.get_account()
    assert acct.user_id == "123"
    assert acct.status == "normal"


@pytest.mark.anyio
async def test_get_positions(fake_http):
    fake_http.routes = {"/api/v2/mix/position/all-position": {
        "code": "00000",
        "data": [
            {"symbol": "BTCUSDT", "posId": "bitget-position-1",
             "holdSide": "long", "total": "0.5",
             "averageOpenPrice": "60000", "unrealizedPL": "12.5", "leverage": "2"},
        ],
    }}
    client = bg.BitgetClient(mode="demo")
    positions = await client.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "BTCUSDT"
    assert positions[0].side == "long"
    assert positions[0].quantity == 0.5
    assert positions[0].entry_price == 60000.0
    assert positions[0].position_id == "bitget-position-1"


@pytest.mark.anyio
async def test_get_positions_preserves_exchange_open_time(fake_http):
    fake_http.routes = {"/api/v2/mix/position/all-position": {
        "code": "00000",
        "data": [{
            "symbol": "BTCUSDT", "holdSide": "long", "total": "0.5",
            "averageOpenPrice": "60000", "openTime": "1784044800000",
        }],
    }}
    positions = await bg.BitgetClient(mode="demo").get_positions()
    assert positions[0].ctime_ms == 1784044800000


@pytest.mark.anyio
async def test_get_open_orders(fake_http):
    fake_http.routes = {"/api/v2/spot/trade/unfilled-orders": {
        "code": "00000",
        "data": [{"orderId": "888", "symbol": "ETHUSDT", "side": "buy",
                  "size": "1.0", "price": "3000", "status": "live"}],
    }}
    client = bg.BitgetClient(mode="demo")
    orders = await client.get_open_orders()
    assert len(orders) == 1
    assert orders[0]["orderId"] == "888"


@pytest.mark.anyio
async def test_cancel_order(fake_http):
    fake_http.routes = {"/api/v2/spot/trade/cancel-order": {
        "code": "00000", "data": {"orderId": "888"},
    }}
    client = bg.BitgetClient(mode="demo")
    result = await client.cancel_order("888", symbol="ETHUSDT")
    assert result["cancelled"] is True
    assert result["order_id"] == "888"
    # a POST was actually issued
    assert any(c[0] == "POST" for c in fake_http.calls)


@pytest.mark.anyio
async def test_cancel_order_error_surfaces(fake_http):
    fake_http.routes = {"/api/v2/spot/trade/cancel-order": {
        "code": "40109", "msg": "order not found",
    }}
    client = bg.BitgetClient(mode="demo")
    result = await client.cancel_order("nope", symbol="ETHUSDT")
    assert result["cancelled"] is False
    assert "order not found" in result["error"]


@pytest.mark.anyio
async def test_get_fills(fake_http):
    fake_http.routes = {"/api/v2/spot/trade/fills": {
        "code": "00000",
        "data": [{"orderId": "888", "fillPrice": "3000", "fillQuantity": "1.0",
                  "fee": "3.0", "tradeId": "t1"}],
    }}
    client = bg.BitgetClient(mode="demo")
    fills = await client.get_fills()
    assert len(fills) == 1
    assert fills[0]["fillPrice"] == "3000"


@pytest.mark.anyio
async def test_get_funding_ledger_uses_private_contract_bills(fake_http):
    fake_http.routes = {"/api/v2/mix/account/bill": {
        "code": "00000",
        "data": {
            "bills": [{
                "billId": "funding-1",
                "symbol": "BTCUSDT",
                "amount": "-1.5",
                "fee": "0",
                "businessType": "contract_settle_fee",
                "coin": "USDT",
                "cTime": "1784044800000",
            }],
            "endId": "funding-1",
        },
    }}
    client = bg.BitgetClient(mode="demo")
    ledger = await client.get_funding_ledger(
        broker_id="bitget_demo",
        symbol="BTC/USDT",
        start_time_ms=1783987200000,
        end_time_ms=1784073600000,
    )
    assert ledger["events"][0]["event_id"] == "funding-1"
    assert ledger["events"][0]["funding_cost"] == 1.5
    assert any(
        "/api/v2/mix/account/bill" in call[1]
        and "businessType=contract_settle_fee" in call[1]
        for call in fake_http.calls
    )

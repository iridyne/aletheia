"""Broker-contract regressions for fail-closed verified exit evidence."""

from __future__ import annotations

from dataclasses import replace
import time
from unittest.mock import AsyncMock, patch

import pytest


NOW_MS = int(time.time() * 1000)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHTTP:
    routes: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        for fragment, payload in self.routes.items():
            if fragment in url:
                return _FakeResponse(payload)
        return _FakeResponse({"code": "00000", "data": []})


@pytest.fixture
def fake_http(monkeypatch):
    from aletheia.exchanges.bitget import client as bitget

    _FakeHTTP.routes = {}
    monkeypatch.setattr(bitget, "httpx_client", lambda **kwargs: _FakeHTTP())
    monkeypatch.setattr(
        bitget.BitgetClient, "_get_creds", lambda self: ("key", "signer", "phrase")
    )
    return _FakeHTTP


def _request(broker_id: str, *, environment: str) -> object:
    from aletheia.execution.verified_exit import VerifiedExitRequest

    return VerifiedExitRequest(
        broker_id=broker_id,
        account_environment=environment,
        account_scope="logical-account-1",
        execution_mode=broker_id,
        position_id="position-1",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.5,
        history_start_ms=NOW_MS - 60_000,
        history_end_ms=NOW_MS,
    )


def test_protocol_classification_requires_absence_complete_history_and_full_quantity():
    from aletheia.execution.verified_exit import (
        VerifiedExitFill,
        classify_verified_exit,
    )

    request = _request("bitget_demo", environment="demo")
    fill = VerifiedExitFill(
        fill_id="fill-1",
        order_id="order-1",
        price=60_100.0,
        quantity=0.5,
        fee=0.25,
        fee_currency="USDT",
        realized_pnl=50.0,
        filled_at_ms=request.history_start_ms + 1_000,
    )

    verified = classify_verified_exit(
        request,
        position_absent=True,
        history_complete=True,
        close_fills=(fill,),
        ambiguous_activity=False,
        pages=1,
        source_endpoint="private-fills",
    )
    assert verified.state == "verified_fill"
    assert verified.fills == (fill,)
    assert verified.history_complete is True

    no_fill = classify_verified_exit(
        request,
        position_absent=True,
        history_complete=True,
        close_fills=(),
        ambiguous_activity=False,
        pages=1,
        source_endpoint="private-fills",
    )
    assert no_fill.state == "confirmed_absent_without_fill"

    for changes in (
        {"position_absent": False},
        {"history_complete": False},
        {"ambiguous_activity": True},
        {"close_fills": (replace(fill, quantity=0.1),)},
        {"close_fills": (fill, fill)},
    ):
        kwargs = {
            "position_absent": True,
            "history_complete": True,
            "close_fills": (fill,),
            "ambiguous_activity": False,
            "pages": 1,
            "source_endpoint": "private-fills",
            **changes,
        }
        assert classify_verified_exit(request, **kwargs).state == "inconclusive"


@pytest.mark.anyio
async def test_bitget_verified_exit_uses_direct_position_and_close_fill(fake_http):
    from aletheia.exchanges.bitget.client import BitgetClient

    fake_http.routes = {
        "/api/v2/mix/position/all-position": {"code": "00000", "data": []},
        "/api/v2/mix/order/fills": {
            "code": "00000",
            "data": {
                "fillList": [{
                    "tradeId": "bg-fill-1",
                    "orderId": "bg-order-1",
                    "symbol": "BTCUSDT",
                    "price": "60100",
                    "baseVolume": "0.5",
                    "profit": "50",
                    "side": "buy",
                    "tradeSide": "close",
                    "posMode": "hedge_mode",
                    "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.25"}],
                    "cTime": str(NOW_MS - 59_000),
                }],
                "endId": "bg-fill-1",
            },
        },
    }
    result = await BitgetClient(mode="demo").get_verified_position_exit(
        _request("bitget_demo", environment="demo")
    )
    assert result.state == "verified_fill"
    assert result.fills[0].fill_id == "bg-fill-1"
    assert result.fills[0].fee == pytest.approx(0.25)


@pytest.mark.anyio
async def test_bitget_missing_pagination_cursor_is_inconclusive(fake_http):
    from aletheia.exchanges.bitget.client import BitgetClient

    fill = {
        "tradeId": "same",
        "symbol": "BTCUSDT",
        "price": "60100",
        "baseVolume": "0.01",
        "side": "buy",
        "tradeSide": "close",
        "posMode": "hedge_mode",
        "cTime": str(NOW_MS - 59_000),
    }
    fake_http.routes = {
        "/api/v2/mix/position/all-position": {"code": "00000", "data": []},
        "/api/v2/mix/order/fills": {
            "code": "00000", "data": {"fillList": [fill] * 100},
        },
    }
    result = await BitgetClient(mode="demo").get_verified_position_exit(
        _request("bitget_demo", environment="demo")
    )
    assert result.state == "inconclusive"
    assert result.reason == "fill_history_incomplete"


@pytest.mark.anyio
async def test_bitget_complete_empty_history_confirms_absent_without_fill(fake_http):
    from aletheia.exchanges.bitget.client import BitgetClient

    fake_http.routes = {
        "/api/v2/mix/position/all-position": {"code": "00000", "data": []},
        "/api/v2/mix/order/fills": {
            "code": "00000", "data": {"fillList": [], "endId": ""},
        },
    }
    result = await BitgetClient(mode="demo").get_verified_position_exit(
        _request("bitget_demo", environment="demo")
    )
    assert result.state == "confirmed_absent_without_fill"


class _FakeOKX:
    async def fetch_positions(self, symbols=None):
        return []

    async def load_markets(self):
        return {}

    def market(self, symbol):
        return {"id": "BTC-USDT-SWAP"}

    async def private_get_trade_fills_history(self, params):
        return {"code": "0", "data": [{
            "billId": "okx-fill-1",
            "tradeId": "trade-1",
            "ordId": "okx-order-1",
            "instId": "BTC-USDT-SWAP",
            "side": "sell",
            "posSide": "long",
            "fillPx": "60100",
            "fillSz": "5",
            "fillPnl": "50",
            "fee": "-0.25",
            "feeCcy": "USDT",
            "ts": str(NOW_MS - 59_000),
        }]}


class _TruncatedOKX(_FakeOKX):
    async def private_get_trade_fills_history(self, params):
        return {"code": "0", "data": [{
            "billId": "unchanged-cursor",
            "instId": "BTC-USDT-SWAP",
            "side": "sell",
            "posSide": "long",
            "fillPx": "60100",
            "fillSz": "0.01",
            "ts": str(NOW_MS - 59_000),
        }] * 100}


@pytest.mark.anyio
async def test_okx_verified_exit_normalizes_contract_quantity():
    from aletheia.exchanges.okx.client import OKXClient

    client = object.__new__(OKXClient)
    client._mode = "demo"
    client._exchange = _FakeOKX()
    client._session = None
    with patch.object(client, "_contract_size", new=AsyncMock(return_value=0.1)):
        result = await client.get_verified_position_exit(
            _request("okx_demo", environment="demo")
        )
    assert result.state == "verified_fill"
    assert result.fills[0].quantity == pytest.approx(0.5)


@pytest.mark.anyio
async def test_okx_repeated_pagination_cursor_is_inconclusive():
    from aletheia.exchanges.okx.client import OKXClient

    client = object.__new__(OKXClient)
    client._mode = "demo"
    client._exchange = _TruncatedOKX()
    client._session = None
    with patch.object(client, "_contract_size", new=AsyncMock(return_value=0.1)):
        result = await client.get_verified_position_exit(
            _request("okx_demo", environment="demo")
        )
    assert result.state == "inconclusive"
    assert result.reason == "fill_history_incomplete"


class _FakeBinance:
    async def fetch_positions(self, symbols=None):
        return []

    async def fetch_my_trades(self, symbol, since=None, limit=None, params=None):
        return [{
            "id": "bn-fill-1",
            "order": "bn-order-1",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "price": 60_100.0,
            "amount": 0.5,
            "fee": {"cost": 0.25, "currency": "USDT"},
            "timestamp": NOW_MS - 59_000,
            "info": {"positionSide": "LONG", "realizedPnl": "50"},
        }]


class _TruncatedBinance(_FakeBinance):
    async def fetch_my_trades(self, symbol, since=None, limit=None, params=None):
        return [{
            "id": "not-a-numeric-cursor",
            "symbol": "BTC/USDT:USDT",
            "side": "sell",
            "price": 60_100.0,
            "amount": 0.001,
            "timestamp": NOW_MS - 59_000,
            "info": {"positionSide": "LONG"},
        }] * 1000


@pytest.mark.anyio
async def test_binance_verified_exit_and_network_error_fail_closed():
    from aletheia.exchanges.binance.client import BinanceClient

    client = object.__new__(BinanceClient)
    client._mode = "testnet"
    client._exchange = _FakeBinance()
    client._session = None
    verified = await client.get_verified_position_exit(
        _request("binance_testnet", environment="testnet")
    )
    assert verified.state == "verified_fill"

    client._exchange.fetch_my_trades = AsyncMock(side_effect=RuntimeError("denied"))
    failed = await client.get_verified_position_exit(
        _request("binance_testnet", environment="testnet")
    )
    assert failed.state == "inconclusive"
    assert failed.reason == "broker_query_failed"


@pytest.mark.anyio
async def test_binance_unpageable_full_response_is_inconclusive():
    from aletheia.exchanges.binance.client import BinanceClient

    client = object.__new__(BinanceClient)
    client._mode = "testnet"
    client._exchange = _TruncatedBinance()
    client._session = None
    result = await client.get_verified_position_exit(
        _request("binance_testnet", environment="testnet")
    )
    assert result.state == "inconclusive"
    assert result.reason == "fill_history_incomplete"


@pytest.mark.anyio
async def test_all_clients_reject_wrong_account_identity_without_querying():
    from aletheia.exchanges.binance.client import BinanceClient

    client = object.__new__(BinanceClient)
    client._mode = "testnet"
    client._exchange = AsyncMock()
    client._session = None
    result = await client.get_verified_position_exit(
        _request("okx_demo", environment="demo")
    )
    assert result.state == "inconclusive"
    assert result.reason == "account_identity_mismatch"
    client._exchange.fetch_positions.assert_not_awaited()


@pytest.mark.anyio
async def test_expired_private_history_window_is_inconclusive_without_querying():
    from aletheia.exchanges.binance.client import BinanceClient

    client = object.__new__(BinanceClient)
    client._mode = "testnet"
    client._exchange = AsyncMock()
    client._session = None
    request = replace(
        _request("binance_testnet", environment="testnet"),
        history_start_ms=1,
        history_end_ms=2,
    )
    result = await client.get_verified_position_exit(request)
    assert result.state == "inconclusive"
    assert result.reason == "history_window_unavailable"
    client._exchange.fetch_positions.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client_kind", "execution_mode"),
    [("okx", "okx_live_limited"), ("binance", "binance_live_limited")],
)
async def test_live_limited_execution_identity_is_the_scoped_live_account(
    client_kind, execution_mode,
):
    if client_kind == "okx":
        from aletheia.exchanges.okx.client import OKXClient

        client = object.__new__(OKXClient)
        client._mode = "live"
        client._exchange = _FakeOKX()
        client._session = None
        context = patch.object(
            client, "_contract_size", new=AsyncMock(return_value=0.1)
        )
    else:
        from aletheia.exchanges.binance.client import BinanceClient

        client = object.__new__(BinanceClient)
        client._mode = "live"
        client._exchange = _FakeBinance()
        client._session = None
        context = patch.object(client, "_ensure_exchange", return_value=client._exchange)

    with context:
        result = await client.get_verified_position_exit(
            _request(execution_mode, environment="live")
        )
    assert result.reason != "account_identity_mismatch"

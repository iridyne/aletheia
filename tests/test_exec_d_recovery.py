"""Tests for exec-d recovery path fixes.

#1779 — Bitget: recover order identity through current bounded UTA history
#1783 — Binance: resolve missing orderId via origClientOrderId lookup
#1785 — Binance: fix userTrades window violations (>7-day, fromId+endTime)
"""

from datetime import datetime, timezone, timedelta

import pytest


@pytest.mark.parametrize("kind", ["classic_v2", "uta_v3"])
@pytest.mark.parametrize("case", ["found", "missing", "partial"])
async def test_bitget_recovers_exact_client_order_identity(monkeypatch, kind, case):
    from aletheia.uta.models import OrderQuery, ReadCompleteness
    from aletheia.uta.compat import scope_from_local
    from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
    from tests.uta.test_ccxt_adapter import FakeExchange, _profile, _identity, _install

    profile = _profile(kind)
    exchange = FakeExchange()
    row = {
        "id": "RECOVERED-99", "clientOrderId": "client-1",
        "symbol": "BTC/USDT:USDT", "side": "buy", "type": "market",
        "amount": "1", "filled": "1", "status": "closed",
    }
    exchange.order_responses = [[] for _ in profile.open_order_requests]
    exchange.closed_order_responses = [
        TimeoutError("synthetic history failure") if case == "partial"
        else [row] if case == "found" else []
        for _ in profile.closed_order_requests
    ]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    try:
        report = await adapter.get_order(OrderQuery(
            scope=scope_from_local("BTC/USDT", "usdt_futures",
                start_at=datetime.now(timezone.utc) - timedelta(hours=1)),
            client_order_id="client-1",
        ))
        assert report.completeness is (
            ReadCompleteness.PARTIAL if case == "partial" else ReadCompleteness.COMPLETE
        )
        if case == "found":
            assert report.found.venue_order_id == "RECOVERED-99"
            assert report.found.client_order_id == "client-1"
        else:
            assert report.found is None
    finally:
        await adapter.close()


def _binance_adapter(monkeypatch):
    import ccxt.async_support as ccxt
    from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
    from tests.uta.test_ccxt_adapter import _profile, _identity, _install
    exchange = ccxt.binanceusdm()
    exchange.set_markets([{"id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "base": "BTC", "quote": "USDT", "settle": "USDT", "type": "swap", "spot": False, "swap": True, "future": False, "option": False, "contract": True, "linear": True, "inverse": False, "contractSize": 1}])
    async def load(*_args, **_kwargs):
        return exchange.markets
    exchange.load_markets = load
    _install(monkeypatch, exchange)
    profile = _profile("binance")
    return CCXTUTAAdapter(_identity(profile), profile), exchange


def _raw_trade(timestamp, *, trade_id=1, order_id=99):
    return {"id": trade_id, "orderId": order_id, "symbol": "BTCUSDT", "price": "50000", "qty": "0.01", "quoteQty": "500", "commission": "0.3", "commissionAsset": "USDT", "time": timestamp, "buyer": True, "maker": False, "side": "BUY", "positionSide": "LONG"}


@pytest.mark.asyncio
async def test_binance_recovers_order_id_from_client_order_id(monkeypatch):
    from aletheia.uta.models import FillQuery, ReadCompleteness
    from aletheia.uta.compat import scope_from_local
    adapter, exchange = _binance_adapter(monkeypatch)
    now = datetime.now(timezone.utc)
    order_requests = []
    async def order(params):
        order_requests.append(params)
        return {"orderId": 99, "clientOrderId": "client-abc-123", "symbol": "BTCUSDT", "status": "FILLED", "side": "BUY", "type": "MARKET", "origQty": "0.01", "executedQty": "0.01", "avgPrice": "50000", "price": "0", "time": int(now.timestamp()*1000)}
    async def trades(params):
        return [_raw_trade(int(now.timestamp()*1000))]
    exchange.fapiPrivateGetOrder = order
    exchange.fapiPrivateGetUserTrades = trades
    try:
        report = await adapter.get_fills(FillQuery(scope=scope_from_local("BTC/USDT", "usdt_futures", start_at=now-timedelta(hours=1)), client_order_id="client-abc-123"))
        assert report.completeness is ReadCompleteness.COMPLETE
        assert [(fill.venue_order_id, fill.client_order_id) for fill in report.fills] == [("99", None)]
        assert order_requests[0]["origClientOrderId"] == "client-abc-123"
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_binance_missing_client_order_identity_is_unavailable_not_empty_complete(monkeypatch):
    import ccxt
    from aletheia.uta.models import FillQuery, ReadCompleteness
    from aletheia.uta.compat import scope_from_local
    adapter, exchange = _binance_adapter(monkeypatch)
    async def order(params):
        raise ccxt.OrderNotFound("not found")
    exchange.fapiPrivateGetOrder = order
    try:
        report = await adapter.get_fills(FillQuery(scope=scope_from_local("BTC/USDT", "usdt_futures", start_at=datetime.now(timezone.utc)-timedelta(hours=1)), client_order_id="missing"))
        assert report.completeness is ReadCompleteness.PARTIAL
        assert report.reason_code == "private_fill_order_identity_unavailable"
    finally:
        await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("days", [1/24, 20])
async def test_binance_history_queries_every_bounded_window_including_empty_ones(monkeypatch, days):
    from aletheia.uta.models import FillQuery, ReadCompleteness
    from aletheia.uta.compat import scope_from_local
    adapter, exchange = _binance_adapter(monkeypatch)
    now = datetime.now(timezone.utc)
    start = now-timedelta(days=days)
    calls = []
    async def trades(params):
        calls.append(params)
        return [_raw_trade(int(start.timestamp()*1000)+1000)] if len(calls) == 1 else []
    exchange.fapiPrivateGetUserTrades = trades
    try:
        report = await adapter.get_fills(FillQuery(scope=scope_from_local("BTC/USDT", "usdt_futures", start_at=start), venue_order_id="99"))
        assert report.completeness is ReadCompleteness.COMPLETE
        assert [fill.venue_order_id for fill in report.fills] == ["99"]
        assert len(calls) == (1 if days < 7 else 3)
        assert all(0 <= call["endTime"]-call["startTime"] <= 7*86400000 for call in calls)
        assert all("fromId" not in call for call in calls)
        assert calls[-1]["endTime"] >= int(now.timestamp()*1000)
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_binance_full_page_uses_from_id_without_time_bounds(monkeypatch):
    from aletheia.uta.models import FillQuery, ReadCompleteness
    from aletheia.uta.compat import scope_from_local
    adapter, exchange = _binance_adapter(monkeypatch)
    now = datetime.now(timezone.utc)
    timestamp = int((now-timedelta(minutes=1)).timestamp()*1000)
    calls = []
    async def trades(params):
        calls.append(params)
        return [_raw_trade(timestamp, trade_id=i) for i in range(1, 1001)] if len(calls)==1 else [_raw_trade(timestamp, trade_id=1001)]
    exchange.fapiPrivateGetUserTrades = trades
    try:
        report = await adapter.get_fills(FillQuery(scope=scope_from_local("BTC/USDT", "usdt_futures", start_at=now-timedelta(hours=1)), venue_order_id="99"))
        assert report.completeness is ReadCompleteness.COMPLETE
        assert {fill.venue_fill_id for fill in report.fills} == {str(i) for i in range(1, 1002)}
        assert calls[1]["fromId"] == "1001"
        assert "startTime" not in calls[1] and "endTime" not in calls[1]
    finally:
        await adapter.close()

"""Current UTA open-order evidence preserves malformed-row incompleteness."""

import pytest

from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
from aletheia.uta.models import ReadCompleteness
from tests.uta.test_ccxt_adapter import FakeExchange, _identity, _install, _profile


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["okx", "binance"])
@pytest.mark.parametrize("malformed", [False, True])
async def test_open_orders_keep_identity_and_incomplete_evidence(monkeypatch, venue, malformed):
    profile = _profile(venue)
    exchange = FakeExchange()
    row = dict(id="order-1", symbol="BTC/USDT:USDT", status="open", amount="1", filled="0")
    rows = [row, {**row, "id": None}] if malformed else [row]
    exchange.order_responses = [rows for _ in profile.open_order_requests]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    try:
        report = await adapter.get_open_orders()
        assert [order.venue_order_id for order in report.orders] == ["order-1"]
        expected = ReadCompleteness.PARTIAL if malformed else ReadCompleteness.COMPLETE
        assert report.completeness is expected
    finally:
        await adapter.close()

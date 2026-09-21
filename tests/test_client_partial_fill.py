"""Current UTA exact-order reads preserve partial fills without inventing fills."""

from decimal import Decimal

import pytest

from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
from aletheia.uta.models import InstrumentIdentity, OrderQuery, QueryScope, ReadCompleteness
from tests.uta.test_ccxt_adapter import FakeExchange, _identity, _install, _profile


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["okx", "binance"])
@pytest.mark.parametrize("status,filled,expected", [
    ("open", "0.3", "partially_filled"),
    ("closed", "1", "filled"),
    ("open", "0", "open"),
])
async def test_exact_order_preserves_partial_full_and_unfilled(monkeypatch, venue, status, filled, expected):
    exchange = FakeExchange()
    exchange.exact_order = dict(
        id="order", symbol="BTC/USDT:USDT", type="market", side="buy",
        amount="1", filled=filled, status=status,
    )
    _install(monkeypatch, exchange)
    profile = _profile(venue)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    instrument = InstrumentIdentity(
        canonical_symbol="BTC/USDT", venue_symbol="BTC/USDT:USDT",
        market_type="usdt_futures", base_currency="BTC",
        quote_currency="USDT", settlement_currency="USDT",
    )
    try:
        report = await adapter.get_order(OrderQuery(
            scope=QueryScope(instrument=instrument), venue_order_id="order",
        ))
        assert report.completeness is ReadCompleteness.COMPLETE
        assert report.found.state.value == expected
        assert report.found.filled_quantity == Decimal(filled) * Decimal("0.01")
        assert report.found.price is None  # Missing price is not synthesized.
    finally:
        await adapter.close()

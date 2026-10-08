"""Bitget position evidence and close direction through the current UTA owner.

Account, open-order, cancel, fill and funding parity lives in
tests/uta/test_ccxt_adapter.py; the retired native trading client is not an owner.
These tests preserve exchange position provenance and the historic hedge-close
direction regression through the actual installed CCXT request builder.
"""

from datetime import datetime, timezone
from decimal import Decimal

import ccxt.async_support as ccxt
import pytest

from aletheia.uta.models import CommandEvidence, ReadCompleteness
from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
from tests.uta.test_ccxt_adapter import (
    FakeExchange,
    _bitget_command,
    _identity,
    _install,
    _profile,
)


async def test_get_positions_preserves_exchange_identity_and_open_time(monkeypatch):
    profile = _profile("uta_v3")
    exchange = FakeExchange()
    exchange.positions = [{
        "symbol": "BTC/USDT:USDT", "side": "long", "contracts": "50",
        "entryPrice": "60000", "unrealizedPnl": "12.5", "leverage": "2",
        "info": {"posId": "bitget-position-1", "openTime": "1784044800000"},
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    try:
        report = await adapter.get_positions()
        assert report.completeness is ReadCompleteness.COMPLETE
        assert len(report.positions) == 1
        position = report.positions[0]
        assert position.instrument.canonical_symbol == "BTC/USDT"
        assert position.side == "long"
        assert position.quantity == Decimal("0.5")
        assert position.entry_price == Decimal("60000")
        assert position.unrealized_pnl == Decimal("12.5")
        assert position.leverage == Decimal("2")
        assert position.venue_position_id == "bitget-position-1"
        assert position.opened_at == datetime.fromtimestamp(1784044800, timezone.utc)
        assert position.opened_at_provenance == "exchange_reported"
    finally:
        await adapter.close()


def _bitget_adapter(monkeypatch):
    """Actual CCXT codec, with local markets and no credential lookup."""
    exchange = ccxt.bitget()
    exchange.set_markets([{
        "id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "base": "BTC",
        "quote": "USDT", "settle": "USDT", "settleId": "USDT", "type": "swap",
        "spot": False, "swap": True, "future": False, "option": False,
        "contract": True, "linear": True, "inverse": False, "contractSize": 1,
        "precision": {"amount": 0.001, "price": 0.1},
    }])
    async def load(*args, **kwargs):
        return exchange.markets

    exchange.load_markets = load
    _install(monkeypatch, exchange)
    profile = _profile("uta_v3")
    return CCXTUTAAdapter(_identity(profile), profile), exchange


@pytest.mark.parametrize("position_side", ["long", "short"])
@pytest.mark.parametrize("purpose", ["entry", "exit"])
@pytest.mark.parametrize("post_only", [False, True])
async def test_bitget_order_wire_preserves_entry_and_hedge_close_direction(
    monkeypatch, position_side, purpose, post_only
):
    adapter, exchange = _bitget_adapter(monkeypatch)
    identity = adapter.identity
    requests = []

    async def uta(request):
        requests.append(request)
        return {"code": "00000", "data": {"orderId": "wire-order-1"}}

    exchange.privateUtaPostV3TradePlaceOrder = uta
    opening_side = "buy" if position_side == "long" else "sell"
    closing_side = "sell" if position_side == "long" else "buy"
    try:
        report = await adapter.submit_order(_bitget_command(
            identity, purpose=purpose, quantity="1", leverage=None,
            side=closing_side if purpose == "exit" else opening_side,
            reduce_only=purpose == "exit",
            order_type="limit" if post_only else "market",
            limit_price="60000" if post_only else None,
            post_only=post_only,
        ))
        assert report.evidence is CommandEvidence.ACKNOWLEDGED
        assert len(requests) == 1
        body = requests[0]
        assert body["symbol"] == "BTCUSDT"
        if post_only:
            assert body["timeInForce"] == "post_only"
        assert body["side"] == (closing_side if purpose == "exit" else opening_side)
        assert body["posSide"] == position_side
    finally:
        await adapter.close()

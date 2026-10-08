"""Malformed external order identity never becomes acknowledged mutation evidence."""

from unittest.mock import AsyncMock

import ccxt.async_support as ccxt
import pytest

from aletheia.uta.models import CancelOrderCommand, CommandEvidence, ModifyOrderCommand
from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
from tests.uta.test_ccxt_adapter import (
    FakeExchange, _bitget_command, _identity, _install, _profile,
)


@pytest.mark.parametrize("kind", ["uta_v3"])
@pytest.mark.parametrize("external_id", [[], {}, True])
async def test_bitget_sdk_coercion_does_not_acknowledge_invalid_native_id(monkeypatch, kind, external_id):
    exchange = ccxt.bitget()
    exchange.set_markets([{
        "id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "base": "BTC",
        "quote": "USDT", "settle": "USDT", "settleId": "USDT", "type": "swap",
        "spot": False, "swap": True, "future": False, "option": False,
        "contract": True, "linear": True, "inverse": False, "contractSize": 1,
        "precision": {"amount": 0.001, "price": 0.1},
    }])
    exchange.load_markets = AsyncMock(return_value=exchange.markets)
    response = AsyncMock(return_value={"code": "00000", "data": {"orderId": external_id}})
    exchange.privateMixPostV2MixOrderPlaceOrder = response
    exchange.privateUtaPostV3TradePlaceOrder = response
    _install(monkeypatch, exchange)
    profile = _profile(kind)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    try:
        report = await adapter.submit_order(_bitget_command(adapter.identity, leverage=None, quantity="1"))
        response.assert_awaited_once()
        assert report.evidence is CommandEvidence.AMBIGUOUS
        assert report.venue_order_id is None
        assert report.order is None
    finally:
        await adapter.close()


@pytest.mark.parametrize("operation", ["submit", "modify", "cancel"])
@pytest.mark.parametrize("kind", ["uta_v3", "okx", "binance"])
@pytest.mark.parametrize("external_id", [[], {}, True, 1.5, "", "valid-order"])
async def test_all_mutations_require_scalar_stable_normalized_identity(
    monkeypatch, kind, operation, external_id,
):
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.create_response = exchange.edit_response = exchange.cancel_response = {"id": external_id}
    exchange.exact_order = {
        "id": "target", "symbol": "BTC/USDT:USDT", "side": "buy",
        "type": "limit", "amount": "1", "filled": "0", "remaining": "1",
        "price": "50000", "status": "open",
    }
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    command = _bitget_command(adapter.identity, quantity="1", leverage=None)
    try:
        if operation == "submit":
            report = await adapter.submit_order(command)
        elif operation == "modify":
            report = await adapter.modify_order(ModifyOrderCommand(
                command_id="modify", expected_account=adapter.identity,
                instrument=command.instrument, purpose="entry",
                target_venue_order_id="target", quantity="1", limit_price="51000",
            ))
        else:
            report = await adapter.cancel_order(CancelOrderCommand(
                command_id="cancel", expected_account=adapter.identity,
                instrument=command.instrument, purpose="entry",
                target_venue_order_id="target", reason="test",
            ))
        assert any(event[0] == {"submit": "create_order", "modify": "edit_order", "cancel": "cancel_order"}[operation]
                   for event in exchange.events)
        valid = external_id == "valid-order"
        assert report.evidence is (CommandEvidence.ACKNOWLEDGED if valid else CommandEvidence.AMBIGUOUS)
        # Failed modify/cancel retains the known target, not a new acknowledgement.
        assert report.venue_order_id == (
            external_id if valid else None if operation == "submit" else "target"
        )
    finally:
        await adapter.close()

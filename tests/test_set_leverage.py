"""The current Bitget UTA owner must abort entry if leverage preparation fails."""

import ccxt
import pytest

from aletheia.uta.errors import UTAErrorCode
from aletheia.uta.models import CommandEvidence
from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
from tests.uta.test_ccxt_adapter import (
    FakeExchange,
    _bitget_command,
    _identity,
    _install,
    _profile,
)


@pytest.mark.parametrize("kind", ["uta_v3"])
@pytest.mark.parametrize("failure", ["rejected", "timeout"])
async def test_bitget_set_leverage_fail_closed(monkeypatch, kind, failure):
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.leverage_response = (
        ccxt.InvalidOrder("simulated leverage rejection")
        if failure == "rejected"
        else TimeoutError()
    )
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    try:
        report = await adapter.submit_order(_bitget_command(identity, leverage="3"))
        assert report.evidence is CommandEvidence.NOT_SENT
        if failure == "rejected":
            assert report.error_code == UTAErrorCode.INVALID_ORDER.value
        assert [event for event in exchange.events if event[0] == "set_leverage"] == [
            ("set_leverage", 3, "BTC/USDT:USDT", {"uta": kind == "uta_v3"})
        ]
        assert not any(event[0] == "create_order" for event in exchange.events)
    finally:
        await adapter.close()

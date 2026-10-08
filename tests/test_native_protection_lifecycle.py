"""The real native reader owns no transport beyond each HTTP request."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from extensions.exchanges.bitget import client as native_client
from extensions.exchanges.bitget.native_protection import BitgetNativeProtectionReader
from tests.uta.test_ccxt_adapter import _identity, _profile


@pytest.mark.parametrize("request_fails", [False, True])
async def test_real_native_reader_releases_request_before_idempotent_close(
    monkeypatch, request_fails,
):
    events = []
    get = AsyncMock(return_value={"code": "00000", "data": {"list": []}})
    if request_fails:
        get.side_effect = TimeoutError("synthetic request timeout")

    def make_exchange(_self):
        events.append("acquire")
        return SimpleNamespace(privateUtaGetV3TradeStrategySubOrders=get), "session"

    async def close_ccxt(_exchange, _session):
        events.append("release")

    monkeypatch.setattr(native_client.BitgetClient, "_make_exchange", make_exchange)
    monkeypatch.setattr(native_client, "close_ccxt", close_ccxt)
    reader = BitgetNativeProtectionReader(_identity(_profile("bitget")))
    if request_fails:
        with pytest.raises(TimeoutError, match="synthetic request timeout"):
            await reader.get_plan_order_history(order_id="plan")
    else:
        report = await reader.get_plan_order_history(order_id="plan")
        assert report.execute_order_ids == ()
    assert events == ["acquire", "release"]
    await reader.close()
    await reader.close()
    assert events == ["acquire", "release"]
    get.assert_awaited_once()

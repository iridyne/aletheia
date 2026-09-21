"""The real native reader owns no transport beyond each HTTP request."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from extensions.exchanges.bitget import client as native_client
from extensions.exchanges.bitget.native_protection import BitgetNativeProtectionReader
from tests.uta.test_ccxt_adapter import _identity, _profile


@pytest.mark.parametrize("profile", ["classic_v2", "uta_v3"])
@pytest.mark.parametrize("request_fails", [False, True])
async def test_real_native_reader_releases_request_before_idempotent_close(
    monkeypatch, profile, request_fails,
):
    events = []
    response = SimpleNamespace(json=lambda: {
        "code": "00000", "data": {"entrustedList": [], "list": []},
    })
    get = AsyncMock(return_value=response)
    if request_fails:
        get.side_effect = TimeoutError("synthetic request timeout")

    @asynccontextmanager
    async def transport(**_kwargs):
        events.append("acquire")
        try:
            yield SimpleNamespace(get=get)
        finally:
            events.append("release")

    monkeypatch.setattr(native_client, "httpx_client", transport)
    monkeypatch.setattr(native_client.BitgetClient, "_headers", lambda *_a, **_kw: {})
    reader = BitgetNativeProtectionReader(_identity(_profile("bitget")), profile=profile)
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

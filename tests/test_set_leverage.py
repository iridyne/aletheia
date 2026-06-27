"""set_leverage fail-closed behaviour (Round 2 futures enablement).

The exchange-side leverage call is what makes a perp open use the PI's chosen
leverage instead of the account default. It MUST fail closed: if the exchange
rejects the leverage change, the broker aborts rather than opening at the wrong
size. This verifies the BitgetClient native implementation's success/failure
outcome via a mocked HTTP transport (no network, no real order).
"""

import httpx
import pytest

from aletheia.exchanges.bitget.client import BitgetClient


class _Resp:
    def __init__(self, code: str):
        self._code = code

    def json(self):
        return {"code": self._code, "msg": "simulated", "data": {}}


@pytest.mark.real_network  # opt out of the fast-timeout httpx wrapper; we mock post directly
async def test_bitget_set_leverage_fail_closed(monkeypatch):
    monkeypatch.setattr(BitgetClient, "_get_creds", lambda self: ("key", "secret", "pass"))
    client = BitgetClient(mode="demo")

    # Success code 00000 → no raise (leverage accepted).
    async def _ok_post(self, url, **kw):
        return _Resp("00000")
    monkeypatch.setattr(httpx.AsyncClient, "post", _ok_post)
    await client.set_leverage("BTCUSDT", 3)  # must not raise

    # Any non-success code → RAISES (fail-closed: caller aborts the order).
    async def _fail_post(self, url, **kw):
        return _Resp("40774")
    monkeypatch.setattr(httpx.AsyncClient, "post", _fail_post)
    with pytest.raises(RuntimeError):
        await client.set_leverage("BTCUSDT", 3)

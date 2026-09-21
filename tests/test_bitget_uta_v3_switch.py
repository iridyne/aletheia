"""Native UTA-v3 protection-history reads and configured profile plumbing.

Trading, account, funding and verified-exit behavior belongs to UTA. Its tests
exercise current profile semantics rather than the retired native client API.
These offline checks cover only the remaining native read boundary.
"""

import asyncio

import pytest

from aletheia.exchanges.bitget import client as bg
from aletheia.exchanges.bitget.client import BitgetClient


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttp:
    """Capture native GET paths and return canned responses."""

    def __init__(self, get_payload=None):
        self.get_paths = []
        self._get_payload = get_payload

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None):
        self.get_paths.append(url)
        if self._get_payload is not None:
            return _Resp(self._get_payload)
        # default: order-detail follow-up reports no fill rows
        return _Resp({"code": "00000", "data": {"list": []}})


def _client(profile="uta_v3") -> BitgetClient:
    c = BitgetClient(mode="demo", profile=profile)
    c._headers = lambda *a, **k: {}
    return c


def test_get_plan_order_history_uta_v3_uses_strategy_children(monkeypatch):
    http = _FakeHttp(
        get_payload={
            "code": "00000",
            "data": {
                "list": [
                    {
                        "subOrderId": "SUB-7",
                        "status": "filled",
                        "symbol": "BTCUSDT",
                    }
                ],
                "cursor": "ignored-on-short-page",
            },
        }
    )
    monkeypatch.setattr(bg, "httpx_client", http)
    result = asyncio.run(
        _client("uta_v3").get_plan_order_history(
            symbol="BTCUSDT", order_id="PLAN-7"
        )
    )

    assert http.get_paths[-1].startswith(
        "https://api.bitget.com/api/v3/trade/strategy-sub-orders?"
    )
    assert "orderId=PLAN-7" in http.get_paths[-1]
    assert "limit=100" in http.get_paths[-1]
    assert result[0]["orderId"] == "PLAN-7"
    assert result[0]["executeOrderId"] == "SUB-7"
    assert result[0]["planStatus"] == "executed"


def test_get_plan_order_history_uta_v3_requires_parent_order_id():
    with pytest.raises(ValueError, match="requires order_id"):
        asyncio.run(_client("uta_v3").get_plan_order_history())


def test_get_plan_order_history_uta_v3_paginates_strategy_children(monkeypatch):
    class _PagedHttp(_FakeHttp):
        def __init__(self):
            super().__init__()
            self.page = 0

        async def get(self, url, headers=None):
            self.get_paths.append(url)
            self.page += 1
            if self.page == 1:
                children = [
                    {"subOrderId": f"SUB-{index}", "status": "cancelled"}
                    for index in range(100)
                ]
                return _Resp({
                    "code": "00000",
                    "data": {"list": children, "cursor": "SUB-99"},
                })
            return _Resp({
                "code": "00000",
                "data": {"list": [{"subOrderId": "SUB-100", "status": "filled"}]},
            })

    http = _PagedHttp()
    monkeypatch.setattr(bg, "httpx_client", http)
    result = asyncio.run(
        _client("uta_v3").get_plan_order_history(order_id="PLAN-PAGED")
    )

    assert len(result) == 101
    assert result[-1]["executeOrderId"] == "SUB-100"
    assert "cursor=SUB-99" in http.get_paths[-1]


def _use_profile(tmp_path, monkeypatch, profile):
    """Point config at tmp_path and set the requested bitget_profile."""
    import aletheia.config.loader as loader
    from aletheia.config.loader import load_config, save_config

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    cfg = load_config()
    cfg.exchange.bitget_profile = profile
    save_config(cfg)


def test_configured_profile_helper_reads_config(tmp_path, monkeypatch):
    from aletheia.exchanges.bitget.profile_config import configured_bitget_profile

    _use_profile(tmp_path, monkeypatch, "uta_v3")
    assert configured_bitget_profile() == "uta_v3"
    _use_profile(tmp_path, monkeypatch, "classic_v2")
    assert configured_bitget_profile() == "classic_v2"


def test_funding_recovery_has_no_venue_client_compatibility_layer():
    from aletheia.portfolio import broker_funding_settlement as settlement

    assert not hasattr(settlement, "_client_for_mode")
    assert not hasattr(settlement, "_UTAFundingProjection")

"""Native UTA-v3 protection-history reads and configured profile plumbing.

Trading, account, funding and verified-exit behavior belongs to UTA. Its tests
exercise current profile semantics rather than the retired native client API.
These offline checks cover only the remaining native read boundary.
"""

import asyncio

import pytest

from extensions.exchanges.bitget import client as bg
from extensions.exchanges.bitget.client import BitgetClient


class _FakeStrategyExchange:
    """CCXT transport double: records params, replays canned strategy-child pages."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls: list[dict] = []

    def set_sandbox_mode(self, enabled):
        return None

    async def privateUtaGetV3TradeStrategySubOrders(self, params):
        """Bitget /api/v3/trade/strategy-sub-orders, the UTA-v3 child endpoint."""
        self.calls.append(dict(params))
        return self._pages.pop(0)


class _FakeSession:
    async def close(self):
        return None


async def _close_ccxt(exchange, session):
    return True


def _install_ccxt(monkeypatch, exchange) -> None:
    """Point BitgetClient at a fake CCXT transport (the current read seam, #3697)."""
    monkeypatch.setattr(bg, "get_secret", lambda key: "synthetic")
    monkeypatch.setattr(bg, "make_ccxt", lambda *a, **k: (exchange, _FakeSession()))
    monkeypatch.setattr(bg, "close_ccxt", _close_ccxt)


def _client(profile="uta_v3") -> BitgetClient:
    return BitgetClient(mode="demo", profile=profile)


def test_get_plan_order_history_uta_v3_uses_strategy_children(monkeypatch):
    exchange = _FakeStrategyExchange([
        {
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
    ])
    _install_ccxt(monkeypatch, exchange)
    result = asyncio.run(
        _client("uta_v3").get_plan_order_history(
            symbol="BTCUSDT", order_id="PLAN-7"
        )
    )

    # the child endpoint is keyed by the parent plan id; a short page ends pagination
    assert exchange.calls == [{"orderId": "PLAN-7", "limit": "100"}]
    assert result[0]["orderId"] == "PLAN-7"
    assert result[0]["executeOrderId"] == "SUB-7"
    assert result[0]["planStatus"] == "executed"


def test_get_plan_order_history_uta_v3_requires_parent_order_id():
    with pytest.raises(ValueError, match="requires order_id"):
        asyncio.run(_client("uta_v3").get_plan_order_history())


def test_get_plan_order_history_uta_v3_paginates_strategy_children(monkeypatch):
    children = [
        {"subOrderId": f"SUB-{index}", "status": "cancelled"}
        for index in range(100)
    ]
    exchange = _FakeStrategyExchange([
        {"code": "00000", "data": {"list": children, "cursor": "SUB-99"}},
        {"code": "00000", "data": {"list": [{"subOrderId": "SUB-100", "status": "filled"}]}},
    ])
    _install_ccxt(monkeypatch, exchange)
    result = asyncio.run(
        _client("uta_v3").get_plan_order_history(order_id="PLAN-PAGED")
    )

    assert len(result) == 101
    assert result[-1]["executeOrderId"] == "SUB-100"
    assert exchange.calls[-1] == {"orderId": "PLAN-PAGED", "limit": "100", "cursor": "SUB-99"}


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
    from extensions.exchanges.bitget.profile_config import configured_bitget_profile

    _use_profile(tmp_path, monkeypatch, "uta_v3")
    assert configured_bitget_profile() == "uta_v3"
    _use_profile(tmp_path, monkeypatch, "classic_v2")
    assert configured_bitget_profile() == "classic_v2"


def test_funding_recovery_has_no_venue_client_compatibility_layer():
    from aletheia.portfolio import broker_funding_settlement as settlement

    assert not hasattr(settlement, "_client_for_mode")
    assert not hasattr(settlement, "_UTAFundingProjection")

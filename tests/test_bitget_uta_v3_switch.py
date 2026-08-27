"""uta_v3 switch tests — order/position routing and profile plumbing.

Companion to tests/test_bitget_profile.py. Offline tests (no network, no
credentials; HTTP mocked at ``httpx_client`` like test_demo_trading). They
prove the uta_v3 profile is end-to-end functional for demo qualification:

- place_order under uta_v3 posts /api/v3/trade/place-order with the v3 body
  contract (category/qty/timeInForce/posSide; close = opposite side +
  tradeSide:"close"; v3 preset-protection key names).
- hedge-mode close rejections carrying a profile close-absent code (45110)
  raise ClosePositionAbsentError on the v3 path.
- get_positions under uta_v3 reads /api/v3/position/current-position and maps
  exchange identity fields (posId/cTime) for reconciler fingerprints.
- get_mix_account stays classic_v2-only.
- _is_unified short-circuits on the configured profile (no doomed detection
  roundtrip, no cross-family fallback when the UTA wallet is momentarily empty).
- v2-only private-history endpoints fail loud under uta_v3 instead of silently
  querying the wrong account view:
  * get_funding_ledger raises ProfileContractError;
  * get_verified_position_exit degrades to an explicit inconclusive reason.
- shared construction sites inherit the configured profile.
"""

import asyncio
import json

import pytest

from aletheia.exchanges.bitget import client as bg
from aletheia.exchanges.bitget.client import (
    BitgetClient,
    ClosePositionAbsentError,
    OrderRequest,
    ProfileContractError,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHttp:
    """Capture POST bodies + GET paths; canned responses by prefix."""

    def __init__(self, post_payload=None, get_payload=None):
        self.post_bodies = []
        self.post_paths = []
        self.get_paths = []
        self._post_payload = post_payload or {"code": "00000", "data": {"orderId": "EX-1"}}
        self._get_payload = get_payload

    def __call__(self, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, content=None, headers=None):
        self.post_paths.append(url)
        self.post_bodies.append(json.loads(content or "{}"))
        return _Resp(self._post_payload)

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


# ─── place_order: open body contract ─────────────────────────────────────────


@pytest.mark.real_place_order
def test_place_order_uta_v3_open_body_contract(monkeypatch):
    http = _FakeHttp()
    monkeypatch.setattr(bg, "httpx_client", http)
    c = _client("uta_v3")
    resp = asyncio.run(
        c.place_order(
            OrderRequest(
                symbol="BTCUSDT",
                market_type="usdt_futures",
                side="buy",
                order_type="market",
                quantity=0.001,
                preset_stop_loss_price=59000.0,
                preset_take_profit_price=65000.0,
        client_oid="p1",
            )
        )
    )
    assert resp.order_id == "EX-1"
    assert http.post_paths[-1].endswith("/api/v3/trade/place-order")
    body = http.post_bodies[-1]
    assert body["category"] == "USDT-FUTURES"
    assert body["symbol"] == "BTCUSDT"
    assert body["side"] == "buy"
    assert body["orderType"] == "market"
    assert body["qty"] == "0.001"
    assert body["timeInForce"] == "gtc"
    assert body["posSide"] == "long"
    assert body["marginCoin"] == "USDT"
    assert body["marginMode"] == "crossed"
    # v3 preset-protection key names (v2 uses presetStopSurplusPrice)
    assert body["presetStopLossPrice"] == "59000.0"
    assert body["presetTakeProfitPrice"] == "65000.0"
    # v2-only keys must not leak into the v3 body
    assert "productType" not in body
    assert "size" not in body
    assert "tradeSide" not in body


@pytest.mark.real_place_order
def test_place_order_uta_v3_close_body_opposite_side(monkeypatch):
    http = _FakeHttp()
    monkeypatch.setattr(bg, "httpx_client", http)
    c = _client("uta_v3")
    asyncio.run(
        c.place_order(
            OrderRequest(
                symbol="BTCUSDT",
                market_type="usdt_futures",
                side="buy",
                order_type="market",
                quantity=0.001,
                hold_side="long",
            )
        )
    )
    body = http.post_bodies[-1]
    assert body["tradeSide"] == "close"
    assert body["posSide"] == "long"
    # UTA v3 close requires side OPPOSITE the position (close long = sell)
    assert body["side"] == "sell"


@pytest.mark.real_place_order
def test_place_order_uta_v3_close_absent_45110_raises_close_absent(monkeypatch):
    http = _FakeHttp(post_payload={"code": "45110", "msg": "no position"})
    monkeypatch.setattr(bg, "httpx_client", http)
    c = _client("uta_v3")
    with pytest.raises(ClosePositionAbsentError, match="45110"):
        asyncio.run(
            c.place_order(
                OrderRequest(
                    symbol="BTCUSDT",
                    market_type="usdt_futures",
                    side="sell",
                    order_type="market",
                    quantity=0.001,
                    hold_side="short",
                )
            )
        )


@pytest.mark.real_place_order
def test_place_order_classic_v2_still_posts_v2(monkeypatch):
    http = _FakeHttp()
    monkeypatch.setattr(bg, "httpx_client", http)
    c = _client("classic_v2")
    asyncio.run(
        c.place_order(
            OrderRequest(
                symbol="BTCUSDT",
                market_type="usdt_futures",
                side="buy",
                order_type="market",
                quantity=0.001,
            )
        )
    )
    assert http.post_paths[-1].endswith("/api/v2/mix/order/place-order")
    body = http.post_bodies[-1]
    assert body["productType"] == "USDT-FUTURES"
    assert body["size"] == "0.001"
    assert body["tradeSide"] == "open"


@pytest.mark.real_place_order
@pytest.mark.parametrize(
    ("profile", "field"),
    [("classic_v2", "force"), ("uta_v3", "timeInForce")],
)
def test_post_only_is_serialized_as_true_venue_semantics(monkeypatch, profile, field):
    http = _FakeHttp()
    monkeypatch.setattr(bg, "httpx_client", http)
    asyncio.run(
        _client(profile).place_order(
            OrderRequest(
                symbol="BTCUSDT",
                market_type="usdt_futures",
                side="buy",
                order_type="limit",
                quantity=0.001,
                price=60_000,
                time_in_force="post_only",
                post_only=True,
            )
        )
    )
    assert http.post_bodies[-1][field] == "post_only"


def test_uta_cancel_routes_to_v3_contract(monkeypatch):
    http = _FakeHttp()
    monkeypatch.setattr(bg, "httpx_client", http)
    result = asyncio.run(_client("uta_v3").cancel_order("EX-1", "BTCUSDT", "usdt_futures"))
    assert result["cancelled"] is True
    assert http.post_paths[-1].endswith("/api/v3/trade/cancel-order")


# ─── get_positions routing ────────────────────────────────────────────────────


def test_get_positions_uta_v3_routes_to_current_position(monkeypatch):
    payload = {
        "code": "00000",
        "data": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "posSide": "long",
                    "total": "0.001",
                    "avgPrice": "60000",
                    "unrealisedPnl": "1.5",
                    "leverage": "5",
                    "markPrice": "61000",
                    "posId": "POS-9",
                    "cTime": "1700000000000",
                }
            ]
        },
    }
    http = _FakeHttp(get_payload=payload)
    monkeypatch.setattr(bg, "httpx_client", http)
    c = _client("uta_v3")
    positions = asyncio.run(c.get_positions())
    assert http.get_paths[-1].startswith(f"{c._base}/api/v3/position/current-position")
    assert len(positions) == 1
    p = positions[0]
    assert p.symbol == "BTCUSDT"
    assert p.side == "long"
    assert p.quantity == pytest.approx(0.001)
    assert p.entry_price == pytest.approx(60000.0)
    assert p.unrealized_pnl == pytest.approx(1.5)
    assert p.leverage == 5
    assert p.mark_price == pytest.approx(61000.0)
    # identity fields feed reconciler fingerprints — must survive mapping
    assert p.position_id == "POS-9"
    assert p.ctime_ms == 1700000000000


def test_get_mix_account_still_blocked_on_uta_v3():
    with pytest.raises(ProfileContractError, match="classic_v2"):
        asyncio.run(_client("uta_v3").get_mix_account())


# ─── _is_unified short-circuit ────────────────────────────────────────────────


def test_is_unified_short_circuits_by_profile(monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("HTTP roundtrip forbidden: profile decides routing")

    monkeypatch.setattr(bg, "httpx_client", _boom)
    assert asyncio.run(BitgetClient(mode="demo", profile="uta_v3")._is_unified()) is True
    assert asyncio.run(BitgetClient(mode="demo", profile="classic_v2")._is_unified()) is False


# ─── v2-only private history fails loud under uta_v3 ─────────────────────────


def test_get_funding_ledger_fails_loud_on_uta_v3():
    c = BitgetClient(mode="demo", profile="uta_v3")
    with pytest.raises(ProfileContractError, match="classic_v2"):
        asyncio.run(
            c.get_funding_ledger(
                broker_id="bitget_demo",
                symbol="BTC/USDT",
                start_time_ms=1700000000000,
                end_time_ms=1700086400000,
            )
        )


def _verified_exit_request():
    from aletheia.execution.verified_exit import VerifiedExitRequest

    return VerifiedExitRequest(
        broker_id="bitget_demo",
        account_environment="demo",
        account_scope="bitget_demo",
        execution_mode="bitget_demo",
        position_id="pos-1",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        history_start_ms=1700000000000,
        history_end_ms=1700086400000,
    )


def test_verified_exit_inconclusive_on_uta_v3(monkeypatch):
    http = _FakeHttp()
    monkeypatch.setattr(bg, "httpx_client", http)
    c = _client("uta_v3")
    result = asyncio.run(c.get_verified_position_exit(_verified_exit_request()))
    assert result.state == "inconclusive"
    assert "uta_v3" in result.reason
    # must not have queried any v2 endpoint
    assert http.get_paths == [] and http.post_paths == []


# ─── construction sites inherit the configured profile ───────────────────────


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


def _stub_creds(monkeypatch):
    for cls in (bg.BitgetClient,):
        monkeypatch.setattr(cls, "_get_creds", lambda self: ("k", "s", "p"))


def test_exchange_sync_client_inherits_profile(tmp_path, monkeypatch):
    from aletheia.portfolio import exchange_sync

    _use_profile(tmp_path, monkeypatch, "uta_v3")
    _stub_creds(monkeypatch)
    client, _mode = exchange_sync._client_or_unconfigured()
    assert client is not None
    assert client.profile == "uta_v3"


def test_broker_funding_settlement_client_inherits_profile(tmp_path, monkeypatch):
    from aletheia.portfolio.broker_funding_settlement import _client_for_mode

    _use_profile(tmp_path, monkeypatch, "uta_v3")
    _stub_creds(monkeypatch)
    client = _client_for_mode("bitget_demo")
    assert client.profile == "uta_v3"


def test_demo_adapter_client_inherits_profile(tmp_path, monkeypatch):
    from aletheia.portfolio.uta.bitget_adapter import BitgetDemoAdapter

    _use_profile(tmp_path, monkeypatch, "uta_v3")
    _stub_creds(monkeypatch)
    adapter = BitgetDemoAdapter()
    assert adapter._client.profile == "uta_v3"

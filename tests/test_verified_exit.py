"""Broker-contract regressions for fail-closed verified exit evidence."""

from __future__ import annotations

from dataclasses import replace
import time
from unittest.mock import AsyncMock

import pytest


NOW_MS = int(time.time() * 1000)


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeHTTP:
    routes: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        for fragment, payload in self.routes.items():
            if fragment in url:
                return _FakeResponse(payload)
        return _FakeResponse({"code": "00000", "data": []})


@pytest.fixture
def fake_http(monkeypatch):
    from aletheia.exchanges.bitget import client as bitget

    _FakeHTTP.routes = {}
    monkeypatch.setattr(bitget, "httpx_client", lambda **kwargs: _FakeHTTP())
    monkeypatch.setattr(
        bitget.BitgetClient, "_get_creds", lambda self: ("key", "signer", "phrase")
    )
    return _FakeHTTP


def _request(
    broker_id: str,
    *,
    environment: str,
    known_entry_order_ids: tuple[str, ...] = (),
) -> object:
    from aletheia.execution.verified_exit import VerifiedExitRequest

    return VerifiedExitRequest(
        broker_id=broker_id,
        account_environment=environment,
        account_scope="logical-account-1",
        execution_mode=broker_id,
        position_id="position-1",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.5,
        history_start_ms=NOW_MS - 60_000,
        history_end_ms=NOW_MS,
        known_entry_order_ids=known_entry_order_ids,
    )


def test_protocol_classification_requires_absence_complete_history_and_full_quantity():
    from aletheia.execution.verified_exit import (
        VerifiedExitFill,
        classify_verified_exit,
    )

    request = _request("bitget_demo", environment="demo")
    fill = VerifiedExitFill(
        fill_id="fill-1",
        order_id="order-1",
        price=60_100.0,
        quantity=0.5,
        fee=0.25,
        fee_currency="USDT",
        realized_pnl=50.0,
        filled_at_ms=request.history_start_ms + 1_000,
    )

    verified = classify_verified_exit(
        request,
        position_absent=True,
        history_complete=True,
        close_fills=(fill,),
        ambiguous_activity=False,
        pages=1,
        source_endpoint="private-fills",
    )
    assert verified.state == "verified_fill"
    assert verified.fills == (fill,)
    assert verified.history_complete is True

    no_fill = classify_verified_exit(
        request,
        position_absent=True,
        history_complete=True,
        close_fills=(),
        ambiguous_activity=False,
        pages=1,
        source_endpoint="private-fills",
    )
    assert no_fill.state == "confirmed_absent_without_fill"

    for changes in (
        {"position_absent": False},
        {"history_complete": False},
        {"ambiguous_activity": True},
        {"close_fills": (replace(fill, quantity=0.1),)},
        {"close_fills": (fill, fill)},
    ):
        kwargs = {
            "position_absent": True,
            "history_complete": True,
            "close_fills": (fill,),
            "ambiguous_activity": False,
            "pages": 1,
            "source_endpoint": "private-fills",
            **changes,
        }
        assert classify_verified_exit(request, **kwargs).state == "inconclusive"


@pytest.mark.anyio
async def test_bitget_verified_exit_uses_direct_position_and_close_fill(fake_http):
    from aletheia.exchanges.bitget.client import BitgetClient

    fake_http.routes = {
        "/api/v2/mix/position/all-position": {"code": "00000", "data": []},
        "/api/v2/mix/order/fills": {
            "code": "00000",
            "data": {
                "fillList": [{
                    "tradeId": "bg-fill-1",
                    "orderId": "bg-order-1",
                    "symbol": "BTCUSDT",
                    "price": "60100",
                    "baseVolume": "0.5",
                    "profit": "50",
                    "side": "buy",
                    "tradeSide": "close",
                    "posMode": "hedge_mode",
                    "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.25"}],
                    "cTime": str(NOW_MS - 59_000),
                }],
                "endId": "bg-fill-1",
            },
        },
    }
    result = await BitgetClient(mode="demo").get_verified_position_exit(
        _request("bitget_demo", environment="demo")
    )
    assert result.state == "verified_fill"
    assert result.fills[0].fill_id == "bg-fill-1"
    assert result.fills[0].fee == pytest.approx(0.25)


@pytest.mark.anyio
async def test_bitget_ignores_only_known_entry_order_fills(fake_http):
    from aletheia.exchanges.bitget.client import BitgetClient

    entry = {
        "tradeId": "bg-entry-fill",
        "orderId": "bg-entry-order",
        "symbol": "BTCUSDT",
        "price": "60000",
        "baseVolume": "0.5",
        "side": "buy",
        "tradeSide": "open",
        "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.2"}],
        "cTime": str(NOW_MS - 59_500),
    }
    close = {
        "tradeId": "bg-close-fill",
        "orderId": "bg-close-order",
        "symbol": "BTCUSDT",
        "price": "60100",
        "baseVolume": "0.5",
        "side": "buy",
        "tradeSide": "close",
        "feeDetail": [{"feeCoin": "USDT", "totalFee": "-0.25"}],
        "cTime": str(NOW_MS - 59_000),
    }
    fake_http.routes = {
        "/api/v2/mix/position/all-position": {"code": "00000", "data": []},
        "/api/v2/mix/order/fills": {
            "code": "00000",
            "data": {"fillList": [entry, close], "endId": "bg-close-fill"},
        },
    }
    client = BitgetClient(mode="demo")
    verified = await client.get_verified_position_exit(
        _request(
            "bitget_demo",
            environment="demo",
            known_entry_order_ids=("bg-entry-order",),
        )
    )
    assert verified.state == "verified_fill"
    assert [fill.fill_id for fill in verified.fills] == ["bg-close-fill"]

    unknown_entry = await client.get_verified_position_exit(
        _request("bitget_demo", environment="demo")
    )
    assert unknown_entry.state == "inconclusive"
    assert unknown_entry.reason == "ambiguous_fill_activity"


@pytest.mark.anyio
async def test_bitget_missing_pagination_cursor_is_inconclusive(fake_http):
    from aletheia.exchanges.bitget.client import BitgetClient

    fill = {
        "tradeId": "same",
        "symbol": "BTCUSDT",
        "price": "60100",
        "baseVolume": "0.01",
        "side": "buy",
        "tradeSide": "close",
        "posMode": "hedge_mode",
        "cTime": str(NOW_MS - 59_000),
    }
    fake_http.routes = {
        "/api/v2/mix/position/all-position": {"code": "00000", "data": []},
        "/api/v2/mix/order/fills": {
            "code": "00000", "data": {"fillList": [fill] * 100},
        },
    }
    result = await BitgetClient(mode="demo").get_verified_position_exit(
        _request("bitget_demo", environment="demo")
    )
    assert result.state == "inconclusive"
    assert result.reason == "fill_history_incomplete"


@pytest.mark.anyio
async def test_bitget_complete_empty_history_confirms_absent_without_fill(fake_http):
    from aletheia.exchanges.bitget.client import BitgetClient

    fake_http.routes = {
        "/api/v2/mix/position/all-position": {"code": "00000", "data": []},
        "/api/v2/mix/order/fills": {
            "code": "00000", "data": {"fillList": [], "endId": ""},
        },
    }
    result = await BitgetClient(mode="demo").get_verified_position_exit(
        _request("bitget_demo", environment="demo")
    )
    assert result.state == "confirmed_absent_without_fill"


@pytest.mark.anyio
@pytest.mark.parametrize("venue", ["okx", "binance", "okx_live", "binance_live"])
@pytest.mark.parametrize("case", ["complete", "known_entry", "network_error", "full_unpageable", "expired", "wrong_account"])
async def test_uta_verified_exit_preserves_private_evidence_boundary(monkeypatch, venue, case):
    from datetime import datetime, timedelta, timezone
    from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
    from aletheia.uta.errors import UTAError, UTAErrorCode
    from aletheia.uta.models import InstrumentIdentity, VerifiedExitQuery, VerifiedExitState
    from tests.uta.test_ccxt_adapter import (
        FakeExchange, _profile, _identity, _install, BinanceLiveProfile, OKXLiveProfile,
    )

    if venue == "okx_live":
        profile = OKXLiveProfile(api_key="synthetic", secret="synthetic", passphrase="synthetic")
    elif venue == "binance_live":
        profile = BinanceLiveProfile(api_key="synthetic", secret="synthetic")
    else:
        profile = _profile(venue)
    now = datetime.now(timezone.utc)
    exchange = FakeExchange()
    row = dict(
        id="close-fill", order="close", symbol="BTC/USDT:USDT", side="sell",
        amount="1", price="60000", timestamp=int((now - timedelta(seconds=1)).timestamp() * 1000),
        info={"positionSide": "LONG", "posSide": "long", "tradeSide": "close"},
    )
    exchange.trades = [row] * (1000 if case == "full_unpageable" else 1)
    if case == "known_entry":
        exchange.trades.append({
            **row, "id": "entry-fill", "order": "entry", "side": "buy",
            "info": {"positionSide": "LONG", "posSide": "long", "tradeSide": "open"},
        })
    if case == "network_error":
        exchange.fetch_my_trades = AsyncMock(side_effect=TimeoutError("synthetic outage"))
    _session, calls = _install(monkeypatch, exchange)
    account = _identity(profile).model_copy(update={
        "execution_mode": profile.expected_execution_mode,
    })
    adapter = CCXTUTAAdapter(account, profile)
    identity = adapter.identity.model_copy(update={"account_id": "different"}) if case == "wrong_account" else adapter.identity
    instrument = InstrumentIdentity(
        canonical_symbol="BTC/USDT", venue_symbol="BTC/USDT:USDT",
        market_type="usdt_futures", base_currency="BTC", quote_currency="USDT",
        settlement_currency="USDT",
    )
    query = VerifiedExitQuery(
        identity=identity, local_position_id="position", instrument=instrument,
        direction="long", expected_quantity="0.01", local_order_ids=("entry",),
        history_start_at=now - timedelta(days=91 if case == "expired" else 1),
        history_end_at=now,
    )
    try:
        if case == "wrong_account":
            with pytest.raises(UTAError) as captured:
                await adapter.verify_position_exit(query)
            assert captured.value.code is UTAErrorCode.ACCOUNT_IDENTITY_MISMATCH
            assert calls == []
        else:
            report = await adapter.verify_position_exit(query)
            if case in {"complete", "known_entry"}:
                assert report.state is VerifiedExitState.VERIFIED_FILL_EXIT
                assert [fill.venue_fill_id for fill in report.exit_fills] == ["close-fill"]
                assert float(report.exit_fills[0].quantity) == pytest.approx(0.01)
            else:
                assert report.state is VerifiedExitState.INCONCLUSIVE
                assert report.exit_fills == ()
    finally:
        await adapter.close()

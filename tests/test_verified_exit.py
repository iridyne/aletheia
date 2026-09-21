"""Broker-contract regressions for fail-closed verified exit evidence."""

from __future__ import annotations

from dataclasses import replace
import time
from unittest.mock import AsyncMock

import pytest


NOW_MS = int(time.time() * 1000)


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
@pytest.mark.parametrize("venue", ["classic_v2", "uta_v3", "okx", "binance", "okx_live", "binance_live"])
@pytest.mark.parametrize("case", ["complete", "known_entry", "unknown_entry", "empty", "partial_quantity", "network_error", "full_unpageable", "expired", "wrong_account"])
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
        fee={"cost": "0.25", "currency": "USDT"},
        info={"positionSide": "LONG", "posSide": "long", "tradeSide": "close"},
    )
    exchange.trades = [row] * (1000 if case == "full_unpageable" else 1)
    if case == "empty":
        exchange.trades = []
    if case in {"known_entry", "unknown_entry"}:
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
        direction="long", expected_quantity="0.02" if case == "partial_quantity" else "0.01",
        local_order_ids=() if case == "unknown_entry" else ("entry",),
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
                assert float(report.exit_fills[0].fee) == pytest.approx(0.25)
                assert report.exit_fills[0].fee_currency == "USDT"
            elif case == "empty":
                assert report.state is VerifiedExitState.CONFIRMED_ABSENT_WITHOUT_FILL
                assert report.exit_fills == ()
            else:
                assert report.state is VerifiedExitState.INCONCLUSIVE
                assert report.exit_fills == ()
    finally:
        await adapter.close()

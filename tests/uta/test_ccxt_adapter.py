from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version
import json
from pathlib import Path
from time import monotonic
import time

import ccxt
import pytest

from extensions.exchanges.ccxt.adapter import CCXTUTAAdapter
from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import (
    CancelOrderCommand,
    CommandEvidence,
    FillQuery,
    FundingLedgerQuery,
    InstrumentIdentity,
    ModifyOrderCommand,
    OrderQuery,
    QueryScope,
    ReadCompleteness,
    SubmitOrderCommand,
    UTAAccountIdentity,
    VerifiedExitQuery,
    VerifiedExitState,
)
from extensions.exchanges.binance.profile import BinanceLiveProfile, BinanceTestnetProfile
from extensions.exchanges.okx.profile import OKXDemoProfile, OKXLiveProfile
from extensions.exchanges.bitget.profile import (
    BitgetClassicV2LiveProfile,
    BitgetClassicV2Profile,
    BitgetUTAV3LiveProfile,
    BitgetUTAV3Profile,
)


ACCOUNT_PARITY = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "uta"
        / "phase1_account_parity.json"
    ).read_text(encoding="utf-8")
)


class FakeSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeExchange:
    def __init__(self):
        self.events = []
        self.closed = False
        self.balance_responses = []
        self.positions = []
        self.order_responses = []
        self.closed_order_responses = []
        self.exact_order = None
        self.trades = []
        self.ledger = []
        self.ledger_responses = []
        self.balance_params = []
        self.position_calls = []
        self.order_params = []
        self.markets = {
            "BTC/USDT": {
                "id": "BTCUSDT-SPOT",
                "symbol": "BTC/USDT",
                "type": "spot",
                "base": "BTC",
                "quote": "USDT",
                "settle": None,
                "contractSize": None,
            },
            "BTC/USDT:USDT": {
                "id": "BTCUSDT-SWAP",
                "symbol": "BTC/USDT:USDT",
                "type": "swap",
                "base": "BTC",
                "quote": "USDT",
                "settle": "USDT",
                "contractSize": "0.01",
                "linear": True,
                "inverse": False,
            },
        }
        self.create_response = None
        self.edit_response = None
        self.cancel_response = None
        self.leverage_response = None

    def set_sandbox_mode(self, enabled):
        self.events.append(("sandbox", enabled))

    async def load_markets(self):
        self.events.append(("load_markets", asyncio.get_running_loop()))
        return self.markets

    async def fetch_balance(self, params):
        self.balance_params.append(params)
        response = self.balance_responses[len(self.balance_params) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    async def fetch_positions(self, symbols=None, params=None):
        self.position_calls.append(dict(params or {}))
        if isinstance(self.positions, Exception):
            raise self.positions
        return self.positions

    async def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        self.order_params.append(dict(params or {}))
        response = self.order_responses[len(self.order_params) - 1]
        if isinstance(response, Exception):
            raise response
        return response

    async def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None):
        response = self.closed_order_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def fetch_order(self, order_id, symbol=None, params=None):
        if isinstance(self.exact_order, Exception):
            raise self.exact_order
        return self.exact_order

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        self.events.append(("fetch_my_trades", dict(params or {})))
        return list(self.trades)

    async def fetch_ledger(self, code=None, since=None, limit=None, params=None):
        self.events.append(("fetch_ledger", dict(params or {})))
        if self.ledger_responses:
            response = self.ledger_responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return list(self.ledger)

    async def set_leverage(self, leverage, symbol, params=None):
        self.events.append(("set_leverage", leverage, symbol, params))
        if isinstance(self.leverage_response, Exception):
            raise self.leverage_response
        return self.leverage_response

    async def create_order(self, *args):
        self.events.append(("create_order", args))
        if isinstance(self.create_response, Exception):
            raise self.create_response
        return self.create_response

    async def edit_order(self, *args):
        self.events.append(("edit_order", args))
        if isinstance(self.edit_response, Exception):
            raise self.edit_response
        return self.edit_response

    async def cancel_order(self, *args):
        self.events.append(("cancel_order", args))
        if isinstance(self.cancel_response, Exception):
            raise self.cancel_response
        return self.cancel_response

    async def close(self):
        self.closed = True


def _profile(kind):
    credentials = {"api_key": "key", "secret": "secret"}
    if kind == "binance":
        return BinanceTestnetProfile(**credentials)
    credentials["passphrase"] = "passphrase"
    if kind == "okx":
        return OKXDemoProfile(**credentials)
    if kind == "classic_v2":
        return BitgetClassicV2Profile(**credentials)
    return BitgetUTAV3Profile(**credentials)


def _identity(profile):
    return UTAAccountIdentity(
        account_id=f"{profile.expected_venue}-{profile.expected_profile}-primary",
        execution_mode=f"{profile.expected_venue}_{profile.expected_environment}",
        venue=profile.expected_venue,
        environment=profile.expected_environment,
        profile=profile.expected_profile,
        account_fingerprint="fingerprint-1",
        credential_generation=4,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
        ccxt_version=version("ccxt"),
    )


def _install(monkeypatch, exchange):
    session = FakeSession()
    calls = []

    def make_ccxt(name, **options):
        calls.append((name, options, asyncio.get_running_loop()))
        return exchange, session

    monkeypatch.setattr("aletheia.net.make_ccxt", make_ccxt)
    return session, calls


@pytest.mark.parametrize("profile_cls,supported", [
    (BitgetClassicV2Profile, True), (BitgetUTAV3Profile, True),
    (BitgetClassicV2LiveProfile, True), (BitgetUTAV3LiveProfile, True),
    (BinanceTestnetProfile, False), (BinanceLiveProfile, False),
    (OKXDemoProfile, False), (OKXLiveProfile, False),
])
def test_match_cap_close_fallback_is_explicit_profile_capability(profile_cls, supported):
    from aletheia.uta.models import CapabilitySupport, UTACapabilities

    options = {"api_key": "test-key", "secret": "test-secret"}
    if profile_cls.expected_venue != "binance":
        options["passphrase"] = "test-passphrase"
    profile = profile_cls(**options)
    identity = _identity(profile).model_copy(update={
        "execution_mode": profile.expected_execution_mode,
    })
    adapter = CCXTUTAAdapter(identity, profile)
    expected = CapabilitySupport.SUPPORTED if supported else CapabilitySupport.UNSUPPORTED
    assert adapter.capabilities.match_cap_close_fallback is expected
    assert UTACapabilities(source="undeclared").match_cap_close_fallback is CapabilitySupport.UNKNOWN


@pytest.mark.parametrize("kind", ["binance", "okx", "classic_v2", "uta_v3"])
@pytest.mark.asyncio
async def test_profiles_construct_on_active_loop_and_enable_demo_before_first_request(
    monkeypatch, kind
):
    profile = _profile(kind)
    exchange = FakeExchange()
    session, calls = _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    await adapter.connect()

    assert calls[0][0] == profile.exchange_name
    assert calls[0][2] is asyncio.get_running_loop()
    assert exchange.events[0] == ("sandbox", True)
    assert exchange.events[1][0] == "load_markets"
    await adapter.close()
    await adapter.close()
    assert exchange.closed is True
    assert session.closed is True


@pytest.mark.asyncio
async def test_concurrent_connect_builds_exactly_one_loop_owned_client(monkeypatch):
    class SlowLoadExchange(FakeExchange):
        async def load_markets(self):
            await asyncio.sleep(0.01)
            return await super().load_markets()

    profile = _profile("binance")
    exchange = SlowLoadExchange()
    _session, calls = _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    await asyncio.gather(adapter.connect(), adapter.connect())

    assert len(calls) == 1
    assert [event[0] for event in exchange.events] == ["sandbox", "load_markets"]
    await adapter.close()


@pytest.mark.parametrize("kind", ACCOUNT_PARITY)
@pytest.mark.asyncio
async def test_account_profiles_preserve_decimal_equity_and_zero(
    monkeypatch, kind
):
    fixture = ACCOUNT_PARITY[kind]
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.balance_responses = fixture["balances"]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_account()

    assert str(report.equity) == fixture["expected_equity"]
    assert report.completeness.value == fixture["expected_completeness"]
    assert all(type(balance.total).__name__ in {"Decimal", "NoneType"} for balance in report.balances)
    await adapter.close()


@pytest.mark.asyncio
async def test_okx_position_contracts_map_to_canonical_coin_quantity(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    exchange.positions = [{
        "id": "position-1",
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "contracts": "2",
        "contractSize": "0.01",
        "entryPrice": "60000.1",
        "markPrice": "60100.2",
        "unrealizedPnl": "2.002",
        "leverage": "3",
        "marginMode": "cross",
        "info": {"posId": "position-1"},
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_positions()

    assert report.completeness is ReadCompleteness.COMPLETE
    assert report.positions[0].quantity.as_tuple().exponent == -2
    assert str(report.positions[0].quantity) == "0.02"
    assert str(report.positions[0].entry_price) == "60000.1"
    await adapter.close()


@pytest.mark.asyncio
async def test_open_orders_keep_spot_and_perp_identity_distinct(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    common = {
        "id": "order-1",
        "clientOrderId": "client-1",
        "side": "buy",
        "type": "limit",
        "amount": "2",
        "filled": "0",
        "remaining": "2",
        "price": "60000",
        "status": "open",
    }
    exchange.order_responses = [
        [{**common, "symbol": "BTC/USDT:USDT"}],
        [{**common, "symbol": "BTC/USDT"}],
    ]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_open_orders()

    assert report.completeness is ReadCompleteness.COMPLETE
    quantities = {
        item.instrument.market_type: item.original_quantity for item in report.orders
    }
    assert quantities == {"usdt_futures": Decimal("0.02"), "spot": Decimal("2")}
    await adapter.close()


@pytest.mark.asyncio
async def test_bitget_profiles_never_mix_v2_and_v3_request_families(monkeypatch):
    for kind, expected_uta in (("classic_v2", False), ("uta_v3", True)):
        profile = _profile(kind)
        exchange = FakeExchange()
        exchange.balance_responses = [
            {"total": {"USDT": "0"}, "free": {"USDT": "0"}, "used": {"USDT": "0"}, "info": []}
            for _ in profile.balance_requests
        ]
        exchange.positions = []
        exchange.order_responses = [[] for _ in profile.open_order_requests]
        _install(monkeypatch, exchange)
        adapter = CCXTUTAAdapter(_identity(profile), profile)

        await adapter.get_account()
        await adapter.get_positions()
        await adapter.get_open_orders()

        all_params = exchange.balance_params + exchange.position_calls + exchange.order_params
        assert all(bool(params.get("uta", False)) is expected_uta for params in all_params)
        assert profile.client_options()["options"]["uta"] is expected_uta
        await adapter.close()


@pytest.mark.asyncio
async def test_multi_surface_failure_is_partial_not_empty_complete(monkeypatch):
    profile = _profile("classic_v2")
    exchange = FakeExchange()
    exchange.balance_responses = [
        {"total": {"USDT": "0"}, "free": {"USDT": "0"}, "used": {"USDT": "0"}, "info": []},
        TimeoutError(),
    ]
    exchange.order_responses = [[], TimeoutError()]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    account = await adapter.get_account()
    orders = await adapter.get_open_orders()

    assert account.completeness is ReadCompleteness.PARTIAL
    assert account.reason_code == "account_surface_partial"
    assert orders.completeness is ReadCompleteness.PARTIAL
    assert orders.reason_code == "open_order_surface_partial"
    await adapter.close()


@pytest.mark.asyncio
async def test_classic_mix_only_partial_does_not_double_count_as_spot(monkeypatch):
    profile = _profile("classic_v2")
    exchange = FakeExchange()
    exchange.balance_responses = [
        TimeoutError(),
        {
            "total": {"USDT": "10"},
            "free": {"USDT": "8"},
            "used": {"USDT": "2"},
            "info": [{"usdtEquity": "10"}],
        },
    ]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_account()

    assert report.completeness is ReadCompleteness.PARTIAL
    assert report.equity == Decimal("10")
    await adapter.close()


@pytest.mark.asyncio
async def test_complete_empty_account_is_zero_not_unavailable(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.balance_responses = [{"total": {}, "free": {}, "used": {}}]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_account()

    assert report.completeness is ReadCompleteness.COMPLETE
    assert report.equity == Decimal(0)
    assert report.available == Decimal(0)
    assert report.balances == ()
    await adapter.close()


@pytest.mark.asyncio
async def test_future_venue_timestamp_makes_position_view_partial(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.positions = [{
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "contracts": "1",
        "entryPrice": "60000",
        "timestamp": int((time.time() + 60) * 1000),
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_positions()

    assert report.completeness is ReadCompleteness.PARTIAL
    assert report.positions == ()
    assert report.reason_code == "position_record_parse_failed"
    await adapter.close()


@pytest.mark.asyncio
async def test_private_request_timeout_is_cooperative_and_unavailable(monkeypatch):
    class SlowPositionExchange(FakeExchange):
        async def fetch_positions(self, symbols=None, params=None):
            await asyncio.sleep(1)

    profile = _profile("binance")
    exchange = SlowPositionExchange()
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile, request_timeout=0.001)

    started = monotonic()
    report = await adapter.get_positions()

    assert monotonic() - started < 0.1
    assert report.completeness is ReadCompleteness.UNAVAILABLE
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("okx", "classic_v2"))
async def test_funding_private_request_uses_adapter_deadline(monkeypatch, kind):
    profile = _profile(kind)
    exchange = FakeExchange()
    cancelled = False

    async def stalled_read(*args, **kwargs):
        nonlocal cancelled
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return []

    method = "fetch_funding_history" if kind == "classic_v2" else "fetch_ledger"
    monkeypatch.setattr(exchange, method, stalled_read, raising=False)
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile, request_timeout=0.001)
    try:
        report = await adapter.get_funding_ledger(FundingLedgerQuery(
            scope=_funding_scope(datetime.now(timezone.utc), age_days=2)
        ))
    finally:
        await adapter.close()
    assert cancelled
    assert report.completeness is ReadCompleteness.UNAVAILABLE
    assert report.reason_code == "private_funding_ledger_unavailable"


@pytest.mark.asyncio
async def test_total_read_failure_and_raw_object_are_never_complete_empty(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.balance_responses = [TimeoutError()]
    exchange.positions = [object()]
    exchange.order_responses = [[object()]]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    account = await adapter.get_account()
    positions = await adapter.get_positions()
    orders = await adapter.get_open_orders()

    assert account.completeness is ReadCompleteness.UNAVAILABLE
    assert positions.completeness is ReadCompleteness.PARTIAL
    assert orders.completeness is ReadCompleteness.PARTIAL
    await adapter.close()


def test_adapter_refuses_cross_loop_reuse(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    asyncio.run(adapter.connect())
    with pytest.raises(UTAError) as exc_info:
        asyncio.run(adapter.connect())
    assert exc_info.value.code is UTAErrorCode.CONFIG
    assert exc_info.value.reason == "cross_loop_adapter_reuse"

    with pytest.raises(UTAError) as read_exc:
        asyncio.run(adapter.get_account())
    assert read_exc.value.reason == "cross_loop_adapter_reuse"


@pytest.mark.asyncio
async def test_close_is_bounded_and_idempotent(monkeypatch):
    class SlowCloseExchange(FakeExchange):
        async def close(self):
            await asyncio.sleep(1)

    profile = _profile("binance")
    exchange = SlowCloseExchange()
    session, _calls = _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(
        _identity(profile), profile, close_timeout=0.001
    )
    await adapter.connect()

    started = monotonic()
    await adapter.close()
    await adapter.close()

    assert monotonic() - started < 0.1
    assert session.closed is True


@pytest.mark.asyncio
async def test_exact_order_preserves_stable_cancel_reason(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.exact_order = {
        "id": "order-1",
        "clientOrderId": "client-1",
        "symbol": "BTC/USDT:USDT",
        "type": "market",
        "side": "sell",
        "amount": "1",
        "filled": "0",
        "remaining": "1",
        "status": "canceled",
        "info": {"cancelReason": "trade_too_many_times_cancel"},
    }
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_order(OrderQuery(
        scope=QueryScope(instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        )),
        venue_order_id="order-1",
    ))

    assert report.completeness is ReadCompleteness.COMPLETE
    assert report.found is not None and report.found.state.value == "canceled"
    assert report.reason_code == "trade_too_many_times_cancel"
    await adapter.close()


@pytest.mark.asyncio
async def test_client_order_history_distinguishes_complete_not_found(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    from ccxt import OrderNotFound
    exchange.exact_order = OrderNotFound("not found")
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_order(OrderQuery(
        scope=QueryScope(
            start_at=datetime.now(timezone.utc) - timedelta(hours=1)
        ),
        client_order_id="missing-client-order",
    ))

    assert report.completeness is ReadCompleteness.COMPLETE
    assert report.found is None
    await adapter.close()


@pytest.mark.asyncio
async def test_fill_funding_and_verified_exit_reports_are_decimal_and_complete(
    monkeypatch,
):
    profile = _profile("binance")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    timestamp = int((now - timedelta(seconds=1)).timestamp() * 1000)
    exchange.trades = [{
        "id": "fill-1",
        "order": "close-order",
        "clientOrderId": "close-client",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "amount": "1",
        "price": "60000.25",
        "timestamp": timestamp,
        "info": {"positionSide": "BOTH"},
        "fee": {"cost": "0.1", "currency": "USDT"},
    }]
    exchange.ledger = [{"info": {
        "incomeType": "FUNDING_FEE",
        "tranId": "funding-1",
        "symbol": "BTCUSDT",
        "income": "-0.02",
        "asset": "USDT",
        "time": timestamp,
    }}]
    exchange.positions = []
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    instrument = InstrumentIdentity(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTC/USDT:USDT",
        market_type="usdt_futures",
        base_currency="BTC",
        quote_currency="USDT",
        settlement_currency="USDT",
    )
    scope = QueryScope(
        instrument=instrument,
        start_at=now - timedelta(hours=1),
        end_at=now,
    )

    fills = await adapter.get_fills(FillQuery(
        scope=scope, venue_order_id="close-order"
    ))
    funding = await adapter.get_funding_ledger(FundingLedgerQuery(scope=scope))
    verified = await adapter.verify_position_exit(VerifiedExitQuery(
        identity=adapter.identity,
        local_position_id="position-1",
        instrument=instrument,
        direction="long",
        expected_quantity="0.01",
        history_start_at=now - timedelta(hours=1),
        history_end_at=now,
        local_order_ids=("entry-order",),
    ))

    assert fills.completeness is ReadCompleteness.COMPLETE
    assert fills.fills[0].quantity == Decimal("0.01")
    assert funding.completeness is ReadCompleteness.COMPLETE
    assert funding.entries[0].amount == Decimal("-0.02")
    assert verified.state is VerifiedExitState.VERIFIED_FILL_EXIT
    await adapter.close()


@pytest.mark.asyncio
async def test_okx_verified_exit_normalizes_local_entry_client_id(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    timestamp = int((now - timedelta(seconds=1)).timestamp() * 1000)
    exchange.positions = []
    exchange.trades = [
        {
            "id": "entry-fill", "order": "entry-order-id",
            "clientOrderId": "entryorder", "symbol": "BTC/USDT:USDT",
            "side": "buy", "amount": "1", "price": "60000",
            "timestamp": timestamp, "info": {"posSide": "long", "tradeSide": "open"},
        },
        {
            "id": "close-fill", "order": "close-order-id",
            "clientOrderId": "closeposition", "symbol": "BTC/USDT:USDT",
            "side": "sell", "amount": "1", "price": "60100",
            "timestamp": timestamp, "info": {"posSide": "long", "tradeSide": "close"},
        },
    ]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    instrument = InstrumentIdentity(
        canonical_symbol="BTC/USDT", venue_symbol="BTC/USDT:USDT",
        market_type="usdt_futures", base_currency="BTC",
        quote_currency="USDT", settlement_currency="USDT",
    )

    report = await adapter.verify_position_exit(VerifiedExitQuery(
        identity=adapter.identity, local_position_id="position-1",
        instrument=instrument, direction="long", expected_quantity="0.01",
        history_start_at=now - timedelta(hours=1), history_end_at=now,
        local_order_ids=("entry-order",),
    ))

    assert report.state is VerifiedExitState.VERIFIED_FILL_EXIT
    assert [fill.venue_fill_id for fill in report.exit_fills] == ["close-fill"]


def _okx_funding_row(event_id: str, amount: str, timestamp: int) -> dict:
    return {"info": {
        "billId": event_id,
        "instId": "BTC-USDT-SWAP",
        "type": "8",
        "subType": "173",
        "pnl": amount,
        "ccy": "USDT",
        "ts": str(timestamp),
    }}


def _funding_scope(now: datetime, *, age_days: int) -> QueryScope:
    return QueryScope(
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        start_at=now - timedelta(days=age_days),
        end_at=now,
    )


@pytest.mark.asyncio
async def test_okx_recent_funding_window_uses_only_recent_surface(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    exchange.ledger = [_okx_funding_row(
        "recent", "0.02", int((now - timedelta(days=1)).timestamp() * 1000)
    )]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_funding_ledger(FundingLedgerQuery(
        scope=_funding_scope(now, age_days=2)
    ))

    assert report.completeness is ReadCompleteness.COMPLETE
    assert [event[1].get("method") for event in exchange.events
            if event[0] == "fetch_ledger"] == [None]


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", (1, 1000))
async def test_classic_funding_keeps_symbol_bound_read_and_completeness(monkeypatch, limit):
    profile = _profile("classic_v2")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    scope = _funding_scope(now, age_days=2).model_copy(update={"limit": limit})
    calls = []

    async def funding_history(symbol, since, requested_limit):
        calls.append((symbol, since, requested_limit))
        return [{"info": {
            "billId": "funding-1", "symbol": "BTCUSDT", "amount": "-0.02",
            "coin": "USDT", "businessType": "contract_settle_fee",
            "cTime": int((now - timedelta(days=1)).timestamp() * 1000),
        }}]

    monkeypatch.setattr(exchange, "fetch_funding_history", funding_history, raising=False)
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    try:
        report = await adapter.get_funding_ledger(FundingLedgerQuery(scope=scope))
    finally:
        await adapter.close()
    assert calls == [("BTC/USDT:USDT", int(scope.start_at.timestamp() * 1000), limit)]
    assert [(entry.venue_ledger_id, entry.amount) for entry in report.entries] == [
        ("funding-1", Decimal("-0.02")),
    ]
    assert report.completeness is (ReadCompleteness.PARTIAL if limit == 1 else ReadCompleteness.COMPLETE)
    assert report.reason_code == ("funding_history_bound_reached" if limit == 1 else None)



@pytest.mark.asyncio
async def test_funding_window_before_profile_retention_is_typed_partial(
    monkeypatch,
):
    profile = _profile("okx")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_funding_ledger(FundingLedgerQuery(
        scope=_funding_scope(now, age_days=91)
    ))

    assert report.completeness is ReadCompleteness.PARTIAL
    assert report.reason_code == "funding_history_retention_expired"
    assert report.entries == ()
    assert exchange.events == []

@pytest.mark.asyncio
async def test_okx_older_funding_window_merges_archive_without_duplicates(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    recent = _okx_funding_row(
        "recent", "0.02", int((now - timedelta(days=1)).timestamp() * 1000)
    )
    older = _okx_funding_row(
        "older", "-0.05", int((now - timedelta(days=12)).timestamp() * 1000)
    )
    exchange.ledger_responses = [[recent], [recent, older]]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_funding_ledger(FundingLedgerQuery(
        scope=_funding_scope(now, age_days=20)
    ))

    assert report.completeness is ReadCompleteness.COMPLETE
    assert [(entry.venue_ledger_id, entry.amount) for entry in report.entries] == [
        ("older", Decimal("-0.05")),
        ("recent", Decimal("0.02")),
    ]
    assert [event[1].get("method") for event in exchange.events
            if event[0] == "fetch_ledger"] == [
        None, "privateGetAccountBillsArchive"
    ]


@pytest.mark.asyncio
async def test_okx_archive_failure_keeps_recent_evidence_partial(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    exchange.ledger_responses = [[_okx_funding_row(
        "recent", "0.02", int((now - timedelta(days=1)).timestamp() * 1000)
    )], TimeoutError()]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_funding_ledger(FundingLedgerQuery(
        scope=_funding_scope(now, age_days=20)
    ))

    assert report.completeness is ReadCompleteness.PARTIAL
    assert report.reason_code == "funding_ledger_partial"
    assert [entry.venue_ledger_id for entry in report.entries] == ["recent"]


@pytest.mark.asyncio
async def test_verified_exit_matches_the_queried_position_leg(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.positions = [{
        "symbol": "BTC/USDT:USDT",
        "side": "short",
        "contracts": "1",
        "entryPrice": "60000",
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    now = datetime.now(timezone.utc)

    report = await adapter.verify_position_exit(VerifiedExitQuery(
        identity=adapter.identity,
        local_position_id="long-position",
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        direction="long",
        expected_quantity="0.01",
        history_start_at=now - timedelta(hours=1),
        history_end_at=now,
    ))

    assert report.state is VerifiedExitState.CONFIRMED_ABSENT_WITHOUT_FILL
    assert report.current_position is None
    await adapter.close()


@pytest.mark.asyncio
async def test_verified_exit_keeps_unrecognized_position_leg_inconclusive(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.positions = [{
        "symbol": "BTC/USDT:USDT",
        "side": "unknown",
        "contracts": "1",
        "entryPrice": "60000",
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)
    now = datetime.now(timezone.utc)

    report = await adapter.verify_position_exit(VerifiedExitQuery(
        identity=adapter.identity,
        local_position_id="long-position",
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        direction="long",
        expected_quantity="0.01",
        history_start_at=now - timedelta(hours=1),
        history_end_at=now,
    ))

    assert report.state is VerifiedExitState.INCONCLUSIVE
    assert report.reason_code == "position_leg_unresolved"
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "fill_side", "position_side", "trade_side", "expected"),
    (
        ("long", "sell", "SHORT", "open", VerifiedExitState.INCONCLUSIVE),
        ("long", "sell", "LONG", "close", VerifiedExitState.VERIFIED_FILL_EXIT),
        ("long", "sell", None, None, VerifiedExitState.INCONCLUSIVE),
        ("long", "sell", "BOTH", None, VerifiedExitState.VERIFIED_FILL_EXIT),
        ("short", "buy", "LONG", "open", VerifiedExitState.INCONCLUSIVE),
        ("short", "buy", "SHORT", "close", VerifiedExitState.VERIFIED_FILL_EXIT),
    ),
)
async def test_verified_exit_requires_compatible_fill_leg_or_one_way_evidence(
    monkeypatch, direction, fill_side, position_side, trade_side, expected,
):
    profile = _profile("binance")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    info = {}
    if position_side is not None:
        info["positionSide"] = position_side
    if trade_side is not None:
        info["tradeSide"] = trade_side
    exchange.trades = [{
        "id": "fill-hedge",
        "order": "venue-close",
        "symbol": "BTC/USDT:USDT",
        "side": fill_side,
        "amount": "1",
        "price": "60000",
        "timestamp": int((now - timedelta(seconds=1)).timestamp() * 1000),
        "info": info,
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.verify_position_exit(VerifiedExitQuery(
        identity=adapter.identity,
        local_position_id="long-position",
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        direction=direction,
        expected_quantity="0.01",
        history_start_at=now - timedelta(hours=1),
        history_end_at=now,
    ))

    assert report.state is expected
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "direction", "fill_side", "trade_side", "position_mode",
        "expected_state", "expected_leg",
    ),
    (
        ("long", "buy", "close", "hedge_mode", VerifiedExitState.VERIFIED_FILL_EXIT, "long"),
        ("short", "sell", "close", "hedge_mode", VerifiedExitState.VERIFIED_FILL_EXIT, "short"),
        ("long", "buy", "open", "hedge_mode", VerifiedExitState.INCONCLUSIVE, None),
        ("long", "buy", "close", None, VerifiedExitState.INCONCLUSIVE, None),
        ("long", "sell", "close", "hedge_mode", VerifiedExitState.INCONCLUSIVE, None),
    ),
)
async def test_bitget_hedge_close_normalizes_same_side_leg(
    monkeypatch, direction, fill_side, trade_side, position_mode,
    expected_state, expected_leg,
):
    profile = _profile("classic_v2")
    exchange = FakeExchange()
    now = datetime.now(timezone.utc)
    info = {"tradeSide": trade_side}
    if position_mode is not None:
        info["posMode"] = position_mode
    exchange.trades = [{
        "id": "bitget-hedge-fill",
        "order": "bitget-hedge-order",
        "symbol": "BTC/USDT:USDT",
        "side": fill_side,
        "amount": "1",
        "price": "60000",
        "timestamp": int((now - timedelta(seconds=1)).timestamp() * 1000),
        "info": info,
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.verify_position_exit(VerifiedExitQuery(
        identity=adapter.identity,
        local_position_id="position-1",
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        direction=direction,
        expected_quantity="0.01",
        history_start_at=now - timedelta(hours=1),
        history_end_at=now,
    ))

    assert report.state is expected_state
    if expected_leg:
        assert report.exit_fills[0].position_side == expected_leg
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_type", (BinanceTestnetProfile, BinanceLiveProfile))
@pytest.mark.parametrize("fill_count", (0, 1, 2))
async def test_binance_single_timestamp_fill_window_requires_real_read(monkeypatch, profile_type, fill_count):
    profile = profile_type(api_key="isolated", secret="isolated")
    exchange = FakeExchange()
    at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=1)
    timestamp = int(at.timestamp() * 1000)
    exchange.trades = [{
        "id": str(index + 1), "order": "closed-order", "symbol": "BTC/USDT:USDT",
        "side": "sell", "amount": "1", "price": "60000", "timestamp": timestamp,
        "fee": {"cost": "0.1", "currency": "USDT"},
    } for index in range(fill_count)]
    exchange.trades.append({
        "id": "999", "order": "later-order", "symbol": "BTC/USDT:USDT",
        "side": "sell", "amount": "1", "price": "60000", "timestamp": timestamp + 1,
        "fee": {"cost": "0.2", "currency": "USDT"},
    })
    read = exchange.fetch_my_trades

    async def read_once(*args, **kwargs):
        assert not any(event[0] == "fetch_my_trades" for event in exchange.events)
        return await read(*args, **kwargs)

    exchange.fetch_my_trades = read_once
    _install(monkeypatch, exchange)
    identity = _identity(profile).model_copy(update={"execution_mode": profile.expected_execution_mode})
    adapter = CCXTUTAAdapter(identity, profile)
    try:
        report = await adapter.get_fills(FillQuery(scope=QueryScope(start_at=at, end_at=at)))
    finally:
        await adapter.close()
    assert report.completeness is ReadCompleteness.COMPLETE
    assert len(report.fills) == fill_count
    assert [event for event in exchange.events if event[0] == "fetch_my_trades"] == [
        ("fetch_my_trades", {"startTime": timestamp, "endTime": timestamp + 1}),
    ]


@pytest.mark.asyncio
async def test_private_history_bound_is_partial(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    timestamp = int((datetime.now(timezone.utc) - timedelta(seconds=1)).timestamp() * 1000)
    exchange.trades = [{
        "id": "fill-1",
        "order": "order-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "amount": "1",
        "price": "1",
        "timestamp": timestamp,
        "fee": {"cost": "0", "currency": "USDT"},
    }] * 1000
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_fills(FillQuery(scope=QueryScope(
        start_at=datetime.now(timezone.utc) - timedelta(hours=1),
        limit=1000,
    )))

    assert report.completeness is ReadCompleteness.PARTIAL
    assert report.reason_code == "private_fill_history_bound_reached"
    await adapter.close()


@pytest.mark.asyncio
async def test_account_authority_probe_binds_authenticated_account(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.balance_responses = [{
        "total": {"USDT": "1"},
        "free": {"USDT": "1"},
        "used": {"USDT": "0"},
        "info": {"uid": "venue-account-1", "canTrade": True},
    }]
    _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.probe_account_authority()

    assert report.completeness is ReadCompleteness.COMPLETE
    assert report.authenticated_private_readable is True
    assert report.trading_permission.value == "supported"
    assert report.account_fingerprint != adapter.identity.account_fingerprint
    assert "venue-account-1" not in report.account_fingerprint
    await adapter.close()


@pytest.mark.asyncio
async def test_expired_private_history_window_fails_closed_without_network(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    _session, calls = _install(monkeypatch, exchange)
    adapter = CCXTUTAAdapter(_identity(profile), profile)

    report = await adapter.get_fills(FillQuery(scope=QueryScope(
        start_at=datetime.now(timezone.utc) - timedelta(days=91),
    )))

    assert report.completeness is ReadCompleteness.PARTIAL
    assert report.reason_code == "private_fill_history_retention_expired"
    assert calls == []


def _bitget_command(identity, **updates):
    values = dict(
        command_id="bitget-command-1",
        expected_account=identity,
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        purpose="entry",
        client_order_id="bitget-client-1",
        side="buy",
        quantity="0.02",
        order_type="market",
        leverage="2",
    )
    values.update(updates)
    return SubmitOrderCommand(**values)


@pytest.mark.parametrize("kind, expected_uta", [("classic_v2", False), ("uta_v3", True)])
@pytest.mark.asyncio
async def test_bitget_mutations_keep_contract_units_and_endpoint_family(
    monkeypatch, kind, expected_uta
):
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "bitget-order-1",
        "clientOrderId": "bitget-client-1",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "type": "market",
        "amount": "2",
        "filled": "0",
        "remaining": "2",
        "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(
        _bitget_command(
            identity,
            purpose="exit",
            side="sell",
            reduce_only=True,
            leverage=None,
        )
    )

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    create = next(event for event in exchange.events if event[0] == "create_order")
    assert create[1][:5] == (
        "BTC/USDT:USDT",
        "market",
        "sell",
        Decimal("2"),
        None,
    )
    assert create[1][5] == {
        "clientOrderId": "bitget-client-1",
        "hedged": True,
        "marginMode": "cross",
        "reduceOnly": True,
        "uta": expected_uta,
    }
    assert not any(event[0] == "set_leverage" for event in exchange.events)
    await adapter.close()


@pytest.mark.parametrize("kind, expected_uta", [("classic_v2", False), ("uta_v3", True)])
@pytest.mark.asyncio
async def test_bitget_entry_sets_profile_bound_leverage_before_send(
    monkeypatch, kind, expected_uta
):
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "bitget-entry-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "market",
        "amount": "2",
        "filled": "0",
        "remaining": "2",
        "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(
        _bitget_command(
            identity,
            leverage="3",
            stop_loss_price="49000",
            take_profit_price="62000",
        )
    )

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    assert [event[0] for event in exchange.events] == [
        "sandbox",
        "load_markets",
        "set_leverage",
        "create_order",
    ]
    assert exchange.events[2] == (
        "set_leverage",
        3,
        "BTC/USDT:USDT",
        {"uta": expected_uta},
    )
    assert exchange.events[3][1][5] == {
        "clientOrderId": "bitget-client-1",
        "hedged": True,
        "marginMode": "cross",
        "stopLoss": {"triggerPrice": Decimal("49000")},
        "takeProfit": {"triggerPrice": Decimal("62000")},
        "uta": expected_uta,
    }
    await adapter.close()


@pytest.mark.asyncio
async def test_bitget_rejects_non_integral_contract_quantity_before_send(monkeypatch):
    profile = _profile("uta_v3")
    exchange = FakeExchange()
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_bitget_command(identity, quantity="0.025"))

    assert report.evidence is CommandEvidence.NOT_SENT
    assert report.error_code == UTAErrorCode.INVALID_ORDER.value
    assert report.reason_code == "quantity_not_exact_contract_multiple"
    assert not any(event[0] in {"set_leverage", "create_order"} for event in exchange.events)
    await adapter.close()


@pytest.mark.parametrize("kind, expected_uta", [("classic_v2", False), ("uta_v3", True)])
@pytest.mark.asyncio
async def test_bitget_modify_cancel_and_protection_keep_endpoint_family(
    monkeypatch, kind, expected_uta
):
    profile = _profile(kind)
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "bitget-stop-1",
        "symbol": "BTC/USDT:USDT",
        "side": "sell",
        "type": "market",
        "amount": "2",
        "filled": "0",
        "remaining": "2",
        "status": "open",
    }
    exchange.exact_order = {
        "id": "bitget-order-1",
        "clientOrderId": "bitget-client-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "limit",
        "amount": "2",
        "filled": "0",
        "remaining": "2",
        "price": "50000",
        "status": "open",
    }
    exchange.edit_response = {"id": "bitget-order-1"}
    exchange.cancel_response = {"id": "bitget-order-1"}
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    protection = await adapter.submit_order(
        _bitget_command(
            identity,
            purpose="protective_stop",
            side="sell",
            trigger_price="49000",
            reduce_only=True,
            leverage=None,
        )
    )
    modified = await adapter.modify_order(
        ModifyOrderCommand(
            command_id="bitget-modify-1",
            expected_account=identity,
            instrument=_bitget_command(identity).instrument,
            purpose="entry",
            target_venue_order_id="bitget-order-1",
            quantity="0.03",
            limit_price="51000",
        )
    )
    cancelled = await adapter.cancel_order(
        CancelOrderCommand(
            command_id="bitget-cancel-1",
            expected_account=identity,
            instrument=_bitget_command(identity).instrument,
            purpose="protective_stop",
            target_venue_order_id="bitget-order-1",
            reason="replace_protection",
        )
    )

    assert protection.evidence is CommandEvidence.ACKNOWLEDGED
    assert modified.evidence is CommandEvidence.ACKNOWLEDGED
    assert cancelled.evidence is CommandEvidence.ACKNOWLEDGED
    create = next(event for event in exchange.events if event[0] == "create_order")
    assert create[1][5] == {
        "clientOrderId": "bitget-client-1",
        "hedged": True,
        "marginMode": "cross",
        "reduceOnly": True,
        "stopLossPrice": Decimal("49000"),
        "uta": expected_uta,
    }
    edit = next(event for event in exchange.events if event[0] == "edit_order")
    assert edit[1][4:7] == (Decimal("3"), Decimal("51000"), {"uta": expected_uta})
    cancel = next(event for event in exchange.events if event[0] == "cancel_order")
    assert cancel[1] == (
        "bitget-order-1",
        "BTC/USDT:USDT",
        {"trigger": True, "uta": expected_uta},
    )
    await adapter.close()


@pytest.mark.parametrize(
    "profile_type", [BitgetClassicV2LiveProfile, BitgetUTAV3LiveProfile]
)
@pytest.mark.asyncio
async def test_bitget_live_profiles_expose_phase_seven_mutations(profile_type, monkeypatch):
    profile = profile_type(api_key="key", secret="secret", passphrase="passphrase")
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "bitget-live-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "market",
        "amount": "2",
        "filled": "0",
        "remaining": "2",
        "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile).model_copy(
        update={"execution_mode": profile.expected_execution_mode}
    )
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_bitget_command(identity))

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    assert adapter.capabilities.market_order.value == "supported"
    await adapter.close()


def _okx_command(identity, *, market_type="usdt_futures", **updates):
    venue_symbol = "BTC/USDT" if market_type == "spot" else "BTC/USDT:USDT"
    values = dict(
        command_id="okx-command-1",
        expected_account=identity,
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol=venue_symbol,
            market_type=market_type,
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        purpose="entry",
        client_order_id="close-position-1",
        side="buy",
        quantity="0.02",
        order_type="market",
        leverage="2" if market_type != "spot" else None,
    )
    values.update(updates)
    return SubmitOrderCommand(**values)


@pytest.mark.asyncio
async def test_okx_perp_submit_converts_coins_to_contracts(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "okx-order-1", "clientOrderId": "closeposition1",
        "symbol": "BTC/USDT:USDT", "side": "buy", "type": "market",
        "amount": "2", "filled": "0", "remaining": "2", "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_okx_command(identity, reduce_only=True))

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    create = next(event for event in exchange.events if event[0] == "create_order")
    symbol, order_type, side, amount, price, params = create[1]
    assert (symbol, order_type, side, amount, price) == (
        "BTC/USDT:USDT", "market", "buy", Decimal("2"), None,
    )
    assert params == {
        "clOrdId": "closeposition1", "tdMode": "cross", "reduceOnly": True,
    }


@pytest.mark.asyncio
async def test_okx_spot_market_buy_keeps_base_quantity(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "okx-spot-1", "symbol": "BTC/USDT", "side": "buy",
        "type": "market", "amount": "0.02", "filled": "0",
        "remaining": "0.02", "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_okx_command(identity, market_type="spot"))

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    create = next(event for event in exchange.events if event[0] == "create_order")
    assert create[1][3] == Decimal("0.02")
    assert create[1][5]["tgtCcy"] == "base_ccy"
    assert not any(event[0] == "set_leverage" for event in exchange.events)


@pytest.mark.asyncio
async def test_okx_entry_preserves_native_attached_protection(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "okx-protected-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "market",
        "amount": "2",
        "filled": "0",
        "remaining": "2",
        "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(
        _okx_command(
            identity,
            stop_loss_price="49000",
            take_profit_price="62000",
        )
    )

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    create = next(event for event in exchange.events if event[0] == "create_order")
    assert create[1][5]["stopLoss"] == {"triggerPrice": Decimal("49000")}
    assert create[1][5]["takeProfit"] == {"triggerPrice": Decimal("62000")}


@pytest.mark.asyncio
async def test_okx_trigger_and_live_profile_boundaries(monkeypatch):
    profile = _profile("okx")
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "okx-stop-1", "symbol": "BTC/USDT:USDT", "side": "sell",
        "type": "market", "amount": "2", "filled": "0",
        "remaining": "2", "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    command = _okx_command(
        identity, purpose="protective_stop", side="sell", trigger_price="50000",
        reduce_only=True, leverage=None,
    )

    report = await adapter.submit_order(command)

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    create = next(event for event in exchange.events if event[0] == "create_order")
    assert create[1][5]["stopLossPrice"] == Decimal("50000")
    assert adapter.capabilities.native_tpsl.value == "supported"

    live = OKXLiveProfile(api_key="key", secret="secret", passphrase="passphrase")
    live_identity = _identity(live).model_copy(
        update={"execution_mode": live.expected_execution_mode}
    )
    live_adapter = CCXTUTAAdapter(live_identity, live)
    assert live_adapter.capabilities.market_order.value == "supported"
    assert live_adapter.capabilities.native_tpsl.value == "supported"


def _binance_command(identity, **updates):
    values = dict(
        command_id="command-1",
        expected_account=identity,
        instrument=InstrumentIdentity(
            canonical_symbol="BTC/USDT",
            venue_symbol="BTC/USDT:USDT",
            market_type="usdt_futures",
            base_currency="BTC",
            quote_currency="USDT",
            settlement_currency="USDT",
        ),
        purpose="entry",
        client_order_id="client-1",
        side="buy",
        quantity="0.01",
        order_type="market",
        leverage="2",
    )
    values.update(updates)
    return SubmitOrderCommand(**values)


@pytest.mark.asyncio
async def test_binance_live_profile_exposes_phase_seven_mutations(monkeypatch):
    profile = BinanceLiveProfile(api_key="key", secret="secret")
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "venue-live-1",
        "clientOrderId": "client-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "market",
        "amount": "0.01",
        "filled": "0",
        "remaining": "0.01",
        "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile).model_copy(
        update={"execution_mode": profile.expected_execution_mode}
    )
    adapter = CCXTUTAAdapter(identity, profile)
    submitted = await adapter.submit_order(_binance_command(identity))

    mutation_capabilities = (
        adapter.capabilities.market_order,
        adapter.capabilities.limit_order,
        adapter.capabilities.modify_order,
        adapter.capabilities.cancel_order,
        adapter.capabilities.reduce_only,
        adapter.capabilities.leverage,
        adapter.capabilities.post_only,
        adapter.capabilities.client_order_id,
    )
    assert all(value.value == "supported" for value in mutation_capabilities)
    assert submitted.evidence is CommandEvidence.ACKNOWLEDGED
    assert adapter.capabilities.account_read.value == "supported"
    assert adapter.capabilities.position_read.value == "supported"
    await adapter.close()


@pytest.mark.asyncio
async def test_binance_submit_acknowledges_without_fabricating_fill(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.create_response = {
        "id": "venue-1",
        "clientOrderId": "client-1",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "type": "market",
        "amount": "1",
        "filled": "0",
        "remaining": "1",
        "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_binance_command(identity))

    assert report.evidence is CommandEvidence.ACKNOWLEDGED
    assert report.venue_order_id == "venue-1"
    assert report.order is not None and report.order.filled_quantity == 0
    assert [event[0] for event in exchange.events] == [
        "sandbox", "load_markets", "set_leverage", "create_order"
    ]


@pytest.mark.parametrize(
    "response,evidence",
    [
        (TimeoutError(), CommandEvidence.AMBIGUOUS),
        ({"status": "open"}, CommandEvidence.AMBIGUOUS),
        (ccxt.InvalidOrder("rejected"), CommandEvidence.VENUE_REJECTED),
    ],
)
@pytest.mark.asyncio
async def test_binance_submit_classifies_post_send_outcome(
    monkeypatch, response, evidence
):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.create_response = response
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_binance_command(identity))

    assert report.evidence is evidence


@pytest.mark.asyncio
async def test_binance_leverage_failure_is_not_sent(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.leverage_response = TimeoutError()
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_binance_command(identity))

    assert report.evidence is CommandEvidence.NOT_SENT
    assert not any(event[0] == "create_order" for event in exchange.events)


@pytest.mark.asyncio
async def test_binance_leverage_rejection_keeps_structured_not_sent_code(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.leverage_response = ccxt.InvalidOrder("invalid leverage")
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(_binance_command(identity))

    assert report.evidence is CommandEvidence.NOT_SENT
    assert report.error_code == UTAErrorCode.INVALID_ORDER.value
    assert not any(event[0] == "create_order" for event in exchange.events)


@pytest.mark.asyncio
async def test_binance_fractional_leverage_is_rejected_before_connect(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    _session, calls = _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)

    report = await adapter.submit_order(
        _binance_command(identity, leverage="1.5")
    )

    assert report.evidence is CommandEvidence.NOT_SENT
    assert report.error_code == UTAErrorCode.INVALID_ORDER.value
    assert report.reason_code == "integer_leverage_required"
    assert calls == []


@pytest.mark.asyncio
async def test_binance_modify_and_cancel_return_normalized_evidence(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.exact_order = {
        "id": "venue-1", "clientOrderId": "client-1",
        "symbol": "BTC/USDT:USDT", "side": "buy", "type": "limit",
        "amount": "1", "filled": "0", "remaining": "1", "price": "100",
        "status": "open",
    }
    exchange.edit_response = {"id": "venue-1"}
    exchange.cancel_response = {"id": "venue-1"}
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    instrument = _binance_command(identity).instrument

    modified = await adapter.modify_order(ModifyOrderCommand(
        command_id="modify-1", expected_account=identity, instrument=instrument,
        purpose="entry", target_venue_order_id="venue-1", limit_price="101",
    ))
    cancelled = await adapter.cancel_order(CancelOrderCommand(
        command_id="cancel-1", expected_account=identity, instrument=instrument,
        purpose="entry", target_venue_order_id="venue-1", reason="operator_cancel",
    ))

    assert modified.evidence is CommandEvidence.ACKNOWLEDGED
    assert cancelled.evidence is CommandEvidence.ACKNOWLEDGED
    assert [event[0] for event in exchange.events].count("edit_order") == 1
    assert [event[0] for event in exchange.events].count("cancel_order") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ("trigger_price", "time_in_force"))
async def test_binance_modify_rejects_unsupported_fields_before_edit(monkeypatch, field):
    profile = _profile("binance")
    exchange = FakeExchange()
    exchange.exact_order = {
        "id": "venue-1", "clientOrderId": "client-1",
        "symbol": "BTC/USDT:USDT", "side": "buy", "type": "limit",
        "amount": "1", "filled": "0", "remaining": "1", "price": "100",
        "status": "open",
    }
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    value = "99" if field == "trigger_price" else "IOC"

    report = await adapter.modify_order(ModifyOrderCommand(
        command_id="modify-unsupported",
        expected_account=identity,
        instrument=_binance_command(identity).instrument,
        purpose="entry",
        target_venue_order_id="venue-1",
        **{field: value},
    ))

    assert report.evidence is CommandEvidence.NOT_SENT
    assert report.error_code == UTAErrorCode.NOT_SUPPORTED.value
    assert report.reason_code == "modify_field_unsupported"
    assert not any(event[0] == "edit_order" for event in exchange.events)


def test_profile_rejects_missing_credentials_without_exposing_values():
    with pytest.raises(ValueError, match="credentials are incomplete"):
        BinanceTestnetProfile(api_key="", secret="secret")


def test_profile_rejects_execution_mode_identity_mismatch():
    profile = _profile("binance")
    wrong = _identity(profile).model_copy(update={"execution_mode": "okx_demo"})
    with pytest.raises(ValueError, match="does not match"):
        CCXTUTAAdapter(wrong, profile)

@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["funded", "topup", "missing_balance", "transfer_rejected", "no_reference", "uta_v3"])
async def test_classic_margin_preparation_uses_only_proven_account_local_funds(monkeypatch, case):
    import ccxt.async_support as ccxt
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import SubmitOrderCommand
    exchange = ccxt.bitget()
    exchange.set_markets([{"id":"BTCUSDT", "symbol":"BTC/USDT:USDT", "base":"BTC", "quote":"USDT", "settle":"USDT", "type":"swap", "spot":False, "swap":True, "future":False, "option":False, "contract":True, "linear":True, "inverse":False, "contractSize":1}])
    events = []
    async def load(*args, **kwargs):
        return exchange.markets
    async def balance(params):
        events.append(("balance", params))
        return {"code":"00000", "data": [] if case == "missing_balance" else [{"marginCoin":"USDT", "available":"20" if case == "funded" else "2", "accountEquity":"20", "locked":"0"}]}
    async def transfer(params):
        events.append(("transfer", params))
        if case == "transfer_rejected":
            raise ccxt.InsufficientFunds("isolated transfer denied")
        return {"code":"00000", "data":{"transferId":"same-account-transfer"}}
    async def leverage(params):
        events.append(("leverage", params))
        return {"code":"00000", "data":{}}
    async def create(symbol, order_type, side, quantity, price, params):
        events.append(("order", {"type":order_type, "price":price}))
        return {"id":"entry-1", "symbol":symbol, "status":"open", "type":order_type, "side":side, "amount":quantity, "filled":0}
    exchange.load_markets = load
    exchange.privateMixGetV2MixAccountAccounts = balance
    exchange.privateSpotPostV2SpotWalletTransfer = transfer
    exchange.privateMixPostV2MixAccountSetLeverage = leverage
    exchange.privateUtaPostV3AccountSetLeverage = leverage
    exchange.create_order = create
    _install(monkeypatch, exchange)
    profile = _profile("uta_v3" if case == "uta_v3" else "classic_v2")
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    try:
        report = await adapter.submit_order(SubmitOrderCommand(command_id="margin-entry", expected_account=identity, instrument=instrument_from_local("BTC/USDT", "usdt_futures"), purpose="entry", side="buy", quantity="1", order_type="market", reference_price=None if case == "no_reference" else "100", leverage="10"))
        names = [name for name, _ in events]
        if case in {"missing_balance", "transfer_rejected"}:
            assert report.evidence is CommandEvidence.NOT_SENT
            assert "order" not in names
            if case == "missing_balance":
                assert report.reason_code == "classic_margin_balance_unavailable"
                assert "transfer" not in names
        else:
            assert report.evidence is CommandEvidence.ACKNOWLEDGED
            assert events[-1] == ("order", {"type":"market", "price":None})
        if case in {"topup", "transfer_rejected"}:
            request = next(params for name, params in events if name == "transfer")
            assert request == {"fromType":"spot", "toType":"usdt_futures", "amount":"8.00", "coin":"USDT"}
        else:
            assert "transfer" not in names
        if case in {"no_reference", "uta_v3"}:
            assert "balance" not in names
    finally:
        await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,code,purpose,absent", [("classic_v2","22002","exit",True), ("classic_v2","45110","exit",False), ("uta_v3","45110","exit",True), ("uta_v3","22002","entry",False)])
async def test_bitget_exact_close_absence_codes_preserve_profile_and_purpose(monkeypatch, kind, code, purpose, absent):
    import ccxt
    import json
    profile = _profile(kind)
    exchange = FakeExchange()
    response = {"code":code,"msg":"venue rejection"}
    try:
        ccxt.bitget().handle_errors(400,"Bad Request","url","POST",{},json.dumps(response),response,{},None)
    except ccxt.ExchangeError as error:
        exchange.create_response = error
    _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    try:
        report = await adapter.submit_order(_bitget_command(identity, purpose=purpose, reduce_only=purpose == "exit"))
        assert report.evidence is CommandEvidence.VENUE_REJECTED
        assert report.reason_code == ("close_position_absence_unverified" if absent else "venue_rejected_command")
        assert report.venue_order_id is None
        assert report.order is None
    finally:
        await adapter.close()

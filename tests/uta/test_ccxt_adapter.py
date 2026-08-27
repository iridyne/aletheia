from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version
import json
from pathlib import Path
from time import monotonic
import time

import pytest

from aletheia.uta.ccxt_adapter import CCXTUTAAdapter
from aletheia.uta.errors import UTAError, UTAErrorCode
from aletheia.uta.models import (
    CommandEvidence,
    FillQuery,
    FundingLedgerQuery,
    InstrumentIdentity,
    OrderQuery,
    QueryScope,
    ReadCompleteness,
    SubmitOrderCommand,
    UTAAccountIdentity,
    VerifiedExitQuery,
    VerifiedExitState,
)
from aletheia.uta.profiles import (
    BinanceTestnetProfile,
    BitgetClassicV2Profile,
    BitgetUTAV3Profile,
    OKXDemoProfile,
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

    async def fetch_order(self, order_id, symbol=None):
        if isinstance(self.exact_order, Exception):
            raise self.exact_order
        return self.exact_order

    async def fetch_my_trades(self, symbol=None, since=None, limit=None, params=None):
        self.events.append(("fetch_my_trades", dict(params or {})))
        return list(self.trades)

    async def fetch_ledger(self, code=None, since=None, limit=None, params=None):
        self.events.append(("fetch_ledger", dict(params or {})))
        return list(self.ledger)

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
    exchange.order_responses = [[]]
    exchange.closed_order_responses = [[]]
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
    assert all(
        event[1].get("paginate") is True
        and event[1].get("paginationCalls") == 10
        for event in exchange.events
        if event[0] in {"fetch_my_trades", "fetch_ledger"}
    )
    await adapter.close()


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


@pytest.mark.asyncio
async def test_shadow_adapter_refuses_mutation_before_send(monkeypatch):
    profile = _profile("binance")
    exchange = FakeExchange()
    _session, calls = _install(monkeypatch, exchange)
    identity = _identity(profile)
    adapter = CCXTUTAAdapter(identity, profile)
    assert adapter.capabilities.order_read.value == "supported"
    instrument = InstrumentIdentity(
        canonical_symbol="BTC/USDT",
        venue_symbol="BTC/USDT:USDT",
        market_type="usdt_futures",
        base_currency="BTC",
        quote_currency="USDT",
        settlement_currency="USDT",
    )
    command = SubmitOrderCommand(
        command_id="command-1",
        expected_account=identity,
        instrument=instrument,
        purpose="entry",
        side="buy",
        quantity="0.001",
        order_type="market",
    )

    report = await adapter.submit_order(command)

    assert report.evidence is CommandEvidence.NOT_SENT
    assert report.error_code == UTAErrorCode.NOT_SUPPORTED.value
    assert calls == []


def test_profile_rejects_missing_credentials_without_exposing_values():
    with pytest.raises(ValueError, match="credentials are incomplete"):
        BinanceTestnetProfile(api_key="", secret="secret")


def test_profile_rejects_execution_mode_identity_mismatch():
    profile = _profile("binance")
    wrong = _identity(profile).model_copy(update={"execution_mode": "okx_demo"})
    with pytest.raises(ValueError, match="does not match"):
        CCXTUTAAdapter(wrong, profile)

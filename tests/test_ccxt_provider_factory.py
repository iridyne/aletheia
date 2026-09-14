"""Tests for get_market_data_provider() — the single market-data entry point.

The factory consolidates the "read config → construct CCXTProvider" template
that was previously duplicated at 3 call sites. It must:
  - pull primary exchange + fallback from market_data config
  - let callers override the primary (HTTP exchange= param / xStocks special case)
    while the fallback always comes from config
  - NOT cache (each call returns a fresh provider — CCXT binds its aiohttp
    session to the creating event loop, and the scheduler uses a fresh loop
    per job, so a cached provider would raise "Event loop is closed")

No network: CCXTProvider does no I/O until the first fetch, so constructing one
is cheap and offline.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aletheia.config import loader
from extensions.exchanges.ccxt.market_data import CCXTProvider
from aletheia.data.ccxt_provider import get_market_data_provider
from aletheia.data.sources.chain import FallbackChainProvider
from extensions.exchanges.ccxt.market_capabilities import load_public_market_capabilities
from extensions.exchanges.ccxt.market_data import install_market_id_guard


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "request", "cancel", "identity"])
async def test_primary_ohlcv_is_normalized_without_fallback_or_side_effects(monkeypatch, failure):
    import asyncio
    from aletheia.data.symbols import InstrumentId
    from extensions.market_data import build_native_provider

    provider = build_native_provider("binance", "okx")
    calls = []

    class Exchange:
        async def fetch_ohlcv(self, symbol, *, timeframe, limit):
            calls.append((symbol, timeframe, limit))
            if failure == "request":
                raise OSError("unavailable")
            if failure == "cancel":
                raise asyncio.CancelledError
            return [[1, 100, 101, 99, 100, 1], [2, 100, 90, 99, 100, 1]]

    provider._primary_perp = Exchange()

    def forbidden(*args, **kwargs):
        pytest.fail("primary read must not construct, fallback, persist or change health")

    monkeypatch.setattr(provider, "_client", forbidden)
    monkeypatch.setattr(provider, "_ensure_fallback", forbidden)
    monkeypatch.setattr(provider, "close", forbidden)
    monkeypatch.setattr("extensions.exchanges.ccxt.market_data.market_history.persist_closed_candles", forbidden)
    monkeypatch.setattr("extensions.exchanges.ccxt.market_data.record_venue_success", forbidden)
    monkeypatch.setattr("extensions.exchanges.ccxt.market_data.record_venue_failure", forbidden)
    monkeypatch.setattr("extensions.exchanges.ccxt.market_data.is_venue_available", forbidden)
    instrument = InstrumentId.from_parts("okx" if failure == "identity" else "binance", "BTC/USDT", "perp")
    call = provider.get_primary_ohlcv(instrument, timeframe="1h", limit=2)
    if failure:
        error = {"request": OSError, "cancel": asyncio.CancelledError, "identity": ValueError}[failure]
        with pytest.raises(error):
            await call
    else:
        result = await call
        assert result.accepted == result.rejected == 1
        assert result[0].exchange == "binance"
        assert result[0].close == 100
    assert calls == ([] if failure == "identity" else [("BTC/USDT:USDT", "1h", 2)])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "request", "cancel", "identity"])
async def test_primary_oi_keeps_logical_identity_and_owns_no_lifecycle(monkeypatch, failure):
    import asyncio
    from datetime import datetime, timezone
    from aletheia.data.symbols import InstrumentId
    from extensions.market_data import build_native_provider

    provider = build_native_provider("binance", "okx")
    exchange = SimpleNamespace(id="binanceusdm", markets={"BTC/USDT:USDT": {
        "id": "BTCUSDT", "symbol": "BTC/USDT:USDT", "swap": True,
        "base": "BTC", "quote": "USDT", "settle": "USDT", "linear": True,
    }}, fetch_open_interest=AsyncMock(return_value={
        "symbol": "BTC/USDT:USDT", "openInterestValue": 123,
        "info": {"sumOpenInterestValue": "123"},
        "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
    }))
    provider._primary_perp = exchange
    if failure in {"request", "cancel"}:
        exchange.fetch_open_interest.side_effect = OSError() if failure == "request" else asyncio.CancelledError()

    def forbidden(*args, **kwargs):
        pytest.fail("OI read cannot construct/fallback/write/close")

    monkeypatch.setattr(provider, "_client", forbidden)
    monkeypatch.setattr(provider, "_ensure_fallback", forbidden)
    monkeypatch.setattr(provider, "close", forbidden)
    monkeypatch.setattr("aletheia.storage.repositories.insert_open_interest_snapshot", forbidden)
    instrument = InstrumentId.from_parts("okx" if failure == "identity" else "binance", "BTC/USDT", "perp")
    if failure:
        with pytest.raises({"request": OSError, "cancel": asyncio.CancelledError, "identity": ValueError}[failure]):
            await provider.get_primary_open_interest(instrument)
    else:
        fact = await provider.get_primary_open_interest(instrument)
        assert fact["availability"] == "available" and fact["value"] == 123
        assert fact["venue"] == "binance" and fact["unit"] == "USD"
        assert "info" not in fact and "openInterestValue" not in fact
    assert exchange.fetch_open_interest.await_count == (0 if failure == "identity" else 1)


def test_missing_bitget_funding_adapter_does_not_fall_back_to_generic_requests(monkeypatch):
    import builtins
    from extensions.market_data import build_native_provider

    original_import = builtins.__import__

    def import_without_bitget(name, *args, **kwargs):
        if name == "extensions.exchanges.bitget.market_funding":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_bitget)
    with pytest.raises(ModuleNotFoundError):
        build_native_provider("bitget", "okx")
    provider = build_native_provider("okx", "binance")
    assert provider._primary_name == "okx" and provider._sessions == []


@pytest.mark.asyncio
@pytest.mark.parametrize("venue,value,expected", [
    ("binance", "2.5", 2.5), ("binance", "NaN", None),
    ("binance", "Infinity", None), ("binance", True, None),
    ("okx", "5", 2.5), ("okx", "0", 0),
    ("okx", "Infinity", None), ("okx", "-1", None),
    ("bitget", "5", None),
])
async def test_primary_taker_uses_venue_reader_and_rejects_invalid_values(monkeypatch, venue, value, expected):
    from aletheia.data.symbols import InstrumentId
    from extensions.market_data import build_native_provider

    provider = build_native_provider(venue, "other")
    binance = AsyncMock(return_value=[{"buySellRatio": value}])
    okx = AsyncMock(return_value={"data": [[1, "2", value]]})
    exchange = SimpleNamespace(id="binanceusdm" if venue == "binance" else venue,
                               fapipublic_get_takerlongshortratio=binance,
                               public_get_rubik_stat_taker_volume=okx)
    provider._primary = provider._primary_perp = exchange

    def forbidden(*args, **kwargs):
        pytest.fail("taker read must not create/fallback/close")

    monkeypatch.setattr(provider, "_client", forbidden)
    monkeypatch.setattr(provider, "_ensure_fallback", forbidden)
    monkeypatch.setattr(provider, "close", forbidden)
    assert await provider.get_primary_taker_ratio(InstrumentId.from_parts(venue, "BTC/USDT", "perp")) == expected
    if venue == "binance":
        binance.assert_awaited_once_with({"symbol": "BTCUSDT", "period": "1h", "limit": 1})
    else:
        binance.assert_not_awaited()
    if venue == "okx":
        okx.assert_awaited_once_with({"ccy": "BTC", "instType": "CONTRACTS", "period": "1H"})
    else:
        okx.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("market", ["spot", "perp", "futures"])
@pytest.mark.parametrize("failure", [None, "load", "cancel"])
async def test_primary_market_preparation_owns_wire_mode_and_success_clock(monkeypatch, market, failure):
    import asyncio
    from datetime import datetime, timezone

    provider = CCXTProvider("binance", "other")
    acquired = datetime(2026, 9, 14, tzinfo=timezone.utc)
    finished = False

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert finished
            return acquired

    exchange = SimpleNamespace(options={}, _candidate_markets_acquired_at="old")

    async def load():
        nonlocal finished
        assert exchange.options["defaultType"] == ("spot" if market == "spot" else "swap")
        if market != "spot":
            assert exchange._candidate_markets_acquired_at is None
        if failure == "load":
            raise OSError("load unavailable")
        if failure == "cancel":
            raise asyncio.CancelledError
        finished = True

    exchange.load_markets = AsyncMock(side_effect=load)
    provider._primary = provider._primary_perp = exchange
    monkeypatch.setattr("extensions.exchanges.ccxt.market_data.datetime", Clock)

    def forbidden(*args, **kwargs):
        pytest.fail("preparation cannot fallback/create/close a loaded client")

    monkeypatch.setattr(provider, "_client", forbidden)
    monkeypatch.setattr(provider, "_ensure_fallback", forbidden)
    monkeypatch.setattr(provider, "close", forbidden)
    if failure:
        with pytest.raises(OSError if failure == "load" else asyncio.CancelledError):
            await provider.prepare_primary_market(market)
    else:
        assert await provider.prepare_primary_market(market) is None
    exchange.load_markets.assert_awaited_once()
    if market != "spot":
        assert exchange._candidate_markets_acquired_at == (None if failure else acquired.isoformat())


def _fake_config(exchange="binance", fallback="bybit", crypto_fallback=True):
    md = SimpleNamespace(
        ccxt_exchange=exchange,
        ccxt_fallback=fallback,
        enable_crypto_fallback=crypto_fallback,
    )
    return SimpleNamespace(market_data=md)


def test_factory_uses_config_exchanges(monkeypatch):
    monkeypatch.setattr(loader, "load_config",
                        lambda: _fake_config("binance", "bybit"))
    p = get_market_data_provider()
    assert isinstance(p, FallbackChainProvider)
    assert not hasattr(p, "_ensure_primary")
    assert not hasattr(p, "_primary_name")
    ccxt = p._providers[0]
    assert isinstance(ccxt, CCXTProvider)
    assert ccxt._primary_name == "binance"
    assert ccxt._fallback_name == "bybit"


def test_factory_exchange_override_keeps_config_fallback(monkeypatch):
    monkeypatch.setattr(loader, "load_config",
                        lambda: _fake_config("binance", "bybit"))
    # Override the primary (e.g. ?exchange=kraken on the OHLCV route); the
    # fallback still comes from config, never from the override.
    p = get_market_data_provider("kraken")
    ccxt = p._providers[0]
    assert ccxt._primary_name == "kraken"
    assert ccxt._fallback_name == "bybit"


def test_factory_returns_fresh_instance_each_call(monkeypatch):
    monkeypatch.setattr(loader, "load_config",
                        lambda: _fake_config("binance", "bybit"))
    a = get_market_data_provider()
    b = get_market_data_provider()
    # Never cached — distinct objects so cross-event-loop reuse is impossible.
    assert a is not b


def test_missing_native_adapter_preserves_bound_public_fallback(monkeypatch):
    from aletheia.data import ccxt_provider

    fallback = SimpleNamespace()
    monkeypatch.setattr(loader, "load_config", _fake_config)
    monkeypatch.setattr(ccxt_provider, "_native_provider_factory", None)
    monkeypatch.setattr(ccxt_provider, "_spot_fallback_factory", lambda: [fallback])
    assert get_market_data_provider()._providers == [fallback]
    with pytest.raises(RuntimeError, match="ccxt_market_data_extension_unavailable"):
        get_market_data_provider(native_ccxt=True)
    monkeypatch.setattr(ccxt_provider, "_spot_fallback_factory", None)
    with pytest.raises(RuntimeError, match="ccxt_market_data_extension_unavailable"):
        get_market_data_provider()
    monkeypatch.setattr(loader, "load_config", lambda: _fake_config(crypto_fallback=False))
    with pytest.raises(RuntimeError, match="ccxt_market_data_extension_unavailable"):
        get_market_data_provider()


async def test_client_session_trusts_env(monkeypatch):
    """ClientSession must use trust_env=True so HTTP_PROXY env vars are honored.

    Without trust_env=True, aiohttp ignores HTTP_PROXY/HTTPS_PROXY and all
    requests bypass the system proxy — causing total failure in environments
    where exchanges are only reachable through a proxy (e.g. behind GFW).
    """
    p = CCXTProvider(exchange="binance", fallback_exchange="bybit")
    client = p._client("binance")
    assert len(p._sessions) == 1
    session = p._sessions[0]
    assert session.trust_env is True, (
        "ClientSession missing trust_env=True — proxy env vars will be ignored"
    )
    await session.close()
    await client.close()


async def test_autouse_fixture_permits_ccxt_provider_close():
    """#238: the _no_network_market_data autouse fixture must NOT noop close().

    CCXTProvider.close() is idempotent and session-safe when no real CCXT client
    was constructed (the fake get_ticker/get_ohlcv patches create no sessions).
    Patching close() to a no-op prevented legitimate cleanup of any provider that
    DID open a session through an unguarded path.

    This test injects fake sessions so close() has real work to do and verifies
    that close() runs to completion under the normal pytest fixture stack.
    """
    from unittest.mock import AsyncMock

    provider = CCXTProvider(exchange="binance", fallback_exchange="bybit")

    # Inject two fake owned sessions so close() has real resources to clear.
    fake_session_a = AsyncMock()
    fake_session_b = AsyncMock()
    provider._sessions = [fake_session_a, fake_session_b]

    await provider.close()

    # Both sessions must have been closed
    fake_session_a.close.assert_awaited_once()
    fake_session_b.close.assert_awaited_once()
    # And the sessions list must be cleared (idempotent)
    assert provider._sessions == []


@pytest.mark.parametrize("side", ["primary", "fallback"])
@pytest.mark.parametrize("venue", ["binance", "fixture"])
async def test_physical_client_selection_reuse_and_cleanup_are_symmetric(monkeypatch, side, venue):
    """Both routes must survive the same transport extraction, not only primary."""
    from extensions.market_data import build_native_provider
    if venue == "binance":
        provider = build_native_provider(venue, venue)
        perp_name = "binanceusdm"
    else:
        perp_name = "fixture_perp"
        provider = CCXTProvider(venue, venue, primary_perp_exchange=perp_name,
                                fallback_perp_exchange=perp_name)
    created = []

    def client(name, **options):
        instance = SimpleNamespace(close=AsyncMock())
        created.append((name, options, instance))
        return instance

    monkeypatch.setattr(provider, "_client", client)
    select = getattr(provider, "_ensure_" + side)
    spot = select("spot")
    perp = select("perp")
    assert select("spot") is spot
    assert select("futures") is perp
    assert spot is not perp
    assert [(name, options) for name, options, _ in created] == [
        (venue, {}),
        (perp_name, {"options": {"defaultType": "swap"}}),
    ]
    await provider.close()
    await provider.close()
    for _, _, instance in created:
        instance.close.assert_awaited_once()


def test_generic_client_selection_contains_no_concrete_venue_names():
    import ast
    import inspect
    import textwrap

    for method in (CCXTProvider._ensure_primary, CCXTProvider._ensure_fallback):
        tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
        assert not {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        } & {"binance", "binanceusdm", "bitget", "okx"}


@pytest.mark.asyncio
async def test_live_batch_delegates_wire_parameters_to_ccxt(monkeypatch):
    import ccxt.async_support as ccxt

    exchange = ccxt.okx()
    monkeypatch.setattr(exchange, "load_markets", AsyncMock(return_value={}))
    funding = AsyncMock(return_value={"data": []})
    interest = AsyncMock(return_value={"data": []})
    monkeypatch.setattr(exchange, "publicGetPublicFundingRate", funding)
    monkeypatch.setattr(exchange, "publicGetPublicOpenInterest", interest)
    for name in ("fetch_funding_rates", "fetch_open_interests"):
        monkeypatch.setattr(exchange, name, AsyncMock(wraps=getattr(exchange, name)))
    provider = CCXTProvider("okx", "okx")
    provider._primary = exchange
    try:
        result = await provider.get_live_derivatives(
            [], batched=True, lsr_map={}, top_position_map={},
        )
        assert result == {"funding": [], "open_interest": [], "errors": []}
        exchange.fetch_funding_rates.assert_awaited_once_with()
        exchange.fetch_open_interests.assert_awaited_once_with()
        funding.assert_awaited_once_with({"instId": "ANY"})
        interest.assert_awaited_once_with({"instType": "SWAP"})
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("clock", ["provided", "missing", "invalid", "future"])
async def test_primary_funding_preserves_parsed_clock_status(clock):
    from datetime import datetime, timedelta, timezone
    from aletheia.data.symbols import InstrumentId

    now = datetime.now(timezone.utc)
    timestamp = {"provided": int(now.timestamp() * 1000), "missing": None,
                 "invalid": True, "future": int((now + timedelta(days=1)).timestamp() * 1000)}[clock]

    async def read(exchange, symbol, ticker):
        return {"fundingRate": -.001, "timestamp": timestamp}, "8h", "funding_response.interval", None

    provider = CCXTProvider("fixture", "fixture", funding_reader=read)
    provider._primary = SimpleNamespace(markets={"BTC/USDT:USDT": {
        "id": "BTCUSDT", "swap": True, "base": "BTC", "quote": "USDT",
        "settle": "USDT", "linear": True,
    }}, close=AsyncMock())
    try:
        result = await provider.get_primary_funding(InstrumentId.from_parts("fixture", "BTC/USDT", "perp"), {})
    finally:
        await provider.close()
    fact = result["evidence"]
    assert fact["interval_seconds"] == 28800
    assert result["source_observed_at"] == fact["source_observed_at"]
    if clock in {"provided", "missing"}:
        assert fact["availability"] == "available" and fact["value"] == -.001
    else:
        assert fact["availability"] == "invalid" and fact["value"] is None
    if clock == "missing":
        assert fact["source_observed_at"] is None


@pytest.mark.parametrize("side", ["primary", "fallback"])
def test_missing_public_client_profile_is_not_replaced_by_generic_transport(monkeypatch, side):
    import builtins
    from extensions.market_data import build_native_provider

    original_import = builtins.__import__

    def without_profile(name, *args, **kwargs):
        if name == "extensions.exchanges.binance.public_market_data":
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_profile)
    with pytest.raises(ModuleNotFoundError):
        build_native_provider(*(("binance", "okx") if side == "primary" else ("okx", "binance")))
    provider = build_native_provider("okx", "okx")
    assert provider._sessions == []


async def test_batch_tickers_normalize_units_reuse_client_and_keep_partial_chunks(monkeypatch):
    symbols = [f"COIN{i}/USDT:USDT" for i in range(201)]
    calls = []

    async def fetch(symbols):
        calls.append(list(symbols))
        if len(calls) == 2:
            raise OSError("fixture chunk unavailable")
        return {symbol: (
            {"quoteVolume": 0} if symbol == "COIN0/USDT:USDT" else
            {"last": 5} if symbol == "COIN1/USDT:USDT" else
            [] if symbol == "COIN2/USDT:USDT" else
            {"baseVolume": 10, "last": 5}
        ) for symbol in symbols}

    exchange = SimpleNamespace(
        markets={symbol: {"contract": True, "contractSize": 2} for symbol in symbols},
        fetch_tickers=fetch, close=AsyncMock(),
    )
    provider = CCXTProvider("fixture", "fallback")
    provider._primary = exchange

    def forbidden(*args, **kwargs):
        raise AssertionError("batch must reuse its loaded client")

    monkeypatch.setattr(provider, "_client", forbidden)
    canonical = [symbol.split(":")[0] for symbol in symbols]
    result = await provider.get_tickers(canonical + ["UNKNOWN/USDT", "龙虾/USDT"], market_type="perp")
    assert [len(chunk) for chunk in calls] == [100, 100, 1]
    assert len(result) == 100
    assert ("perp", canonical[2]) not in result
    assert result["perp", canonical[0]]["quote_volume_24h"] == 0
    assert result["perp", canonical[1]]["quote_volume_24h"] is None
    assert result["perp", canonical[-1]]["quote_volume_24h"] == 100
    exchange.close.assert_not_awaited()
    await provider.close()
    exchange.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_canonical_ticker_keys_keep_same_symbol_markets_distinct():
    from aletheia.universe.features import _evaluate_ticker_prefilters

    requests = []

    async def fetch(symbols):
        requests.append(symbols)
        return {**{symbol: {"quoteVolume": 20 if ":" in symbol else 10} for symbol in symbols},
                "ETH/USDT": {"quoteVolume": 999}}

    provider = CCXTProvider("okx", "other")
    provider._primary = SimpleNamespace(
        markets={"BTC/USDT": {}, "BTC/USDT:USDT": {}}, fetch_tickers=fetch,
    )
    tickers = await provider.get_tickers(["BTC/USDT"], market_type="spot")
    tickers.update(await provider.get_tickers(["BTC/USDT"], market_type="perp"))
    assert set(tickers) == {("spot", "BTC/USDT"), ("perp", "BTC/USDT")}
    assert requests == [["BTC/USDT"], ["BTC/USDT:USDT"]]
    assets = [{"id": market, "symbol": "BTC/USDT", "venue": "okx", "market_type": market}
              for market in ("spot", "perp")]
    candidates, facts, volumes = _evaluate_ticker_prefilters(assets, tickers, min_volume=15)
    assert [asset["id"] for asset, _ticker in candidates] == ["perp"]
    assert [row["decision"] for row in volumes] == ["rejected", "passed"]
    assert [row["inputs"] for row in facts] == [
        {"ticker_symbol": "BTC/USDT", "market_type": market} for market in ("spot", "perp")
    ]


@pytest.mark.parametrize("malformed_info", [False, True])
def test_batch_ticker_projection_does_not_leak_wire_payload(malformed_info):
    from extensions.exchanges.ccxt.tickers import _normalize_ticker

    result = _normalize_ticker(SimpleNamespace(markets={}), "BTC/USDT", {
        "quoteVolume": "0", "bid": "100", "ask": True,
        "timestamp": "1700000000000", "unused": "vendor detail",
        "info": ["invalid"] if malformed_info else {"fundingRate": "-.001", "markPrice": "101", "unused": 123},
    })
    assert result == {
        "quote_volume_24h": 0.0, "bid_price": 100.0, "ask_price": None,
        "source_timestamp_ms": 1700000000000.0,
        "funding_rate": None if malformed_info else -.001,
        "mark_price": None if malformed_info else 101.0,
    }


def test_market_id_guard_drops_malformed_ccxt_rows():
    class _FakeExchange:
        def __init__(self):
            self.seen_markets = None

        def set_markets(self, markets, currencies=None):
            self.seen_markets = markets
            return {"markets": markets, "currencies": currencies}

    exchange = install_market_id_guard(_FakeExchange(), "okx")

    result = exchange.set_markets(
        [
            {"id": "BTC-USDT", "symbol": "BTC/USDT"},
            {"id": None, "symbol": "BROKEN/USDT"},
            {"symbol": "MISSING-ID/USDT"},
        ],
        {"USDT": {}},
    )

    assert result["markets"] == [{"id": "BTC-USDT", "symbol": "BTC/USDT"}]
    assert exchange.seen_markets == [{"id": "BTC-USDT", "symbol": "BTC/USDT"}]


async def test_public_market_capabilities_use_generic_factory_and_close(monkeypatch):
    events: list[object] = []

    class FakeExchange:
        markets = {
            "BTC/USDT:USDT": {"active": True, "type": "swap", "quote": "USDT", "base": "BTC"},
            "WLD/USDT": {"active": True, "type": "spot", "quote": "USDT", "base": "WLD"},
            "ETH/USDC:USDC": {"active": True, "type": "swap", "quote": "USDC", "base": "ETH"},
            "OLD/USDT": {"active": False, "type": "spot", "quote": "USDT", "base": "OLD"},
            "BTC/USD": {"active": True, "type": "future", "quote": "USD", "base": "BTC"},
        }

        def set_sandbox_mode(self, enabled):
            events.append(("sandbox", enabled))

        async def load_markets(self):
            events.append("load_markets")

    monkeypatch.setattr(
        "aletheia.net.make_ccxt",
        lambda name, **options: (events.append(("factory", name, options)) or (FakeExchange(), object())),
    )

    async def close_ccxt(exchange, session):
        events.append(("close", exchange.__class__.__name__, session.__class__.__name__))
        return True

    monkeypatch.setattr("aletheia.net.close_ccxt", close_ccxt)

    result = await load_public_market_capabilities("okx", sandbox=True)

    assert result == {
        "swap": frozenset({"BTC/USDT"}),
        "spot": frozenset({"WLD/USDT"}),
    }
    assert events == [
        ("factory", "okx", {"options": {"defaultType": "swap"}}),
        ("sandbox", True),
        "load_markets",
        ("close", "FakeExchange", "object"),
    ]

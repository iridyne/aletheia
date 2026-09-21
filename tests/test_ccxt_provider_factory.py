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

import pytest

from aletheia.config import loader
from aletheia.data.ccxt_provider import (
    _install_market_id_guard,
    get_market_data_provider,
    CCXTProvider,
)
from aletheia.data.sources.chain import FallbackChainProvider


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


async def test_client_session_trusts_env():
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


def test_market_id_guard_drops_malformed_ccxt_rows():
    class _FakeExchange:
        def __init__(self):
            self.seen_markets = None

        def set_markets(self, markets, currencies=None):
            self.seen_markets = markets
            return {"markets": markets, "currencies": currencies}

    exchange = _install_market_id_guard(_FakeExchange(), "okx")

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

    provider = CCXTProvider(exchange="okx", fallback_exchange="bybit")
    result = await provider.get_public_market_capabilities(sandbox=True)

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


@pytest.mark.parametrize("failure", ["read", "cancel", "cleanup"])
async def test_public_market_capability_failure_closes_local_client(monkeypatch, failure):
    import asyncio
    from unittest.mock import AsyncMock

    error = asyncio.CancelledError() if failure == "cancel" else RuntimeError("read failed")
    exchange = SimpleNamespace(
        markets={}, load_markets=AsyncMock(side_effect=None if failure == "cleanup" else error),
    )
    session = object()
    close = AsyncMock(return_value=failure != "cleanup")
    monkeypatch.setattr("aletheia.net.make_ccxt", lambda *args, **kwargs: (exchange, session))
    monkeypatch.setattr("aletheia.net.close_ccxt", close)
    provider = CCXTProvider(exchange="okx", fallback_exchange="bybit")
    expected = asyncio.CancelledError if failure == "cancel" else RuntimeError
    with pytest.raises(expected, match=None if failure == "cancel" else (
        "ccxt_cleanup_failed" if failure == "cleanup" else "read failed"
    )):
        await provider.get_public_market_capabilities()
    close.assert_awaited_once_with(exchange, session)
    assert provider._primary is None and provider._fallback is None

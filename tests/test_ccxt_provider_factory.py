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

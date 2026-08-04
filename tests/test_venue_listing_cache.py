"""Tests for aletheia.data.venue_listing_cache.

Covers:
- TTL expiry
- invalidate_venue_listing / invalidate_all
- get_cached_listing (sync read-only)
- get_venue_listing (async, shared load lock)
- filter_by_listing (fail-open on None, drops unlisted, keeps other-venue)
- market_data xStock pre-filter integration
- ccxt_provider.get_ticker perp defaultType
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aletheia.data.symbols import InstrumentId, MARKET_PERP, MARKET_SPOT
from aletheia.data.venue_listing_cache import (
    _CACHE,
    _CACHE_LOCK,
    _LISTING_TTL_SEC,
    filter_by_listing,
    get_cached_listing,
    invalidate_all,
    invalidate_venue_listing,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _populate_cache(venue: str, symbols: frozenset[str], *, offset_sec: float = 0.0) -> None:
    """Directly write a cache entry, optionally offset TTL for expiry tests."""
    expires_at = time.monotonic() + _LISTING_TTL_SEC + offset_sec
    with _CACHE_LOCK:
        _CACHE[venue] = (symbols, expires_at)


@pytest.fixture(autouse=True)
def clear_listing_cache():
    """Reset module-level cache before and after each test."""
    invalidate_all()
    yield
    invalidate_all()


# ---------------------------------------------------------------------------
# Sync cache read: get_cached_listing
# ---------------------------------------------------------------------------

class TestGetCachedListing:
    def test_returns_none_when_cold(self):
        assert get_cached_listing("binance") is None

    def test_returns_listing_when_warm(self):
        syms = frozenset(["BTC/USDT", "ETH/USDT"])
        _populate_cache("binance", syms)
        assert get_cached_listing("binance") == syms

    def test_case_insensitive_key(self):
        syms = frozenset(["BTC/USDT"])
        _populate_cache("bitget", syms)
        assert get_cached_listing("BITGET") == syms
        assert get_cached_listing("Bitget") == syms

    def test_returns_none_when_expired(self):
        syms = frozenset(["BTC/USDT"])
        # Write an already-expired entry (offset far in the past)
        _populate_cache("bybit", syms, offset_sec=-_LISTING_TTL_SEC - 1)
        assert get_cached_listing("bybit") is None

    def test_cleans_up_expired_entry(self):
        _populate_cache("okx", frozenset(["BTC/USDT"]), offset_sec=-_LISTING_TTL_SEC - 1)
        get_cached_listing("okx")  # triggers cleanup
        with _CACHE_LOCK:
            assert "okx" not in _CACHE


# ---------------------------------------------------------------------------
# invalidate_venue_listing / invalidate_all
# ---------------------------------------------------------------------------

class TestInvalidate:
    def test_invalidate_single_venue(self):
        _populate_cache("binance", frozenset(["BTC/USDT"]))
        _populate_cache("bitget", frozenset(["AAPLON/USDT"]))
        invalidate_venue_listing("binance")
        assert get_cached_listing("binance") is None
        assert get_cached_listing("bitget") is not None

    def test_invalidate_all(self):
        _populate_cache("binance", frozenset(["BTC/USDT"]))
        _populate_cache("bitget", frozenset(["AAPLON/USDT"]))
        invalidate_all()
        assert get_cached_listing("binance") is None
        assert get_cached_listing("bitget") is None

    def test_invalidate_nonexistent_is_noop(self):
        invalidate_venue_listing("nonexistent")  # must not raise


# ---------------------------------------------------------------------------
# filter_by_listing
# ---------------------------------------------------------------------------

class TestFilterByListing:
    def _make(self, symbol: str, exchange: str, market_type: str = MARKET_SPOT) -> InstrumentId:
        return InstrumentId(exchange=exchange, symbol=symbol, market_type=market_type)

    def test_fail_open_on_none_listing(self):
        instruments = [
            self._make("BTC/USDT", "bitget"),
            self._make("AAPLON/USDT", "bitget"),
        ]
        result = filter_by_listing(instruments, listing=None, venue="bitget")
        assert result == instruments

    def test_keeps_listed_symbols(self):
        listing = frozenset(["BTC/USDT", "ETH/USDT"])
        instruments = [
            self._make("BTC/USDT", "bitget"),
            self._make("ETH/USDT", "bitget"),
        ]
        result = filter_by_listing(instruments, listing=listing, venue="bitget")
        assert result == instruments

    def test_drops_unlisted_symbols(self):
        listing = frozenset(["BTC/USDT"])
        instruments = [
            self._make("BTC/USDT", "bitget"),
            self._make("AAPLON/USDT", "bitget"),  # delisted
            self._make("TSLAON/USDT", "bitget"),  # delisted
        ]
        result = filter_by_listing(instruments, listing=listing, venue="bitget")
        assert len(result) == 1
        assert result[0].symbol == "BTC/USDT"

    def test_keeps_instruments_from_other_venue(self):
        """Instruments on a different exchange are never filtered."""
        listing = frozenset(["BTC/USDT"])
        instruments = [
            self._make("BTC/USDT", "binance"),   # different venue — keep regardless
            self._make("AAPLON/USDT", "bitget"),  # target venue, not in listing — drop
        ]
        result = filter_by_listing(instruments, listing=listing, venue="bitget")
        assert len(result) == 1
        assert result[0].exchange == "binance"

    def test_empty_listing_drops_all_target_venue(self):
        listing: frozenset[str] = frozenset()
        instruments = [
            self._make("BTC/USDT", "bitget"),
            self._make("ETH/USDT", "bitget"),
        ]
        result = filter_by_listing(instruments, listing=listing, venue="bitget")
        assert result == []

    def test_empty_instruments(self):
        result = filter_by_listing([], listing=frozenset(["BTC/USDT"]), venue="bitget")
        assert result == []


# ---------------------------------------------------------------------------
# async get_venue_listing — uses mocked CCXTProvider
# ---------------------------------------------------------------------------

class TestGetVenueListing:
    @pytest.fixture(autouse=True)
    def reset_async_locks(self):
        """Clear per-venue async locks between tests."""
        from aletheia.data import venue_listing_cache
        venue_listing_cache._LOAD_LOCKS.clear()
        yield
        venue_listing_cache._LOAD_LOCKS.clear()

    def _mock_provider(self, active_markets: dict) -> MagicMock:
        exchange = MagicMock()
        exchange.load_markets = AsyncMock(return_value=active_markets)
        exchange.markets = active_markets
        provider = MagicMock()
        provider._ensure_primary = MagicMock(return_value=exchange)
        provider.close = AsyncMock()
        return provider

    @pytest.mark.asyncio
    async def test_loads_and_caches(self):
        from aletheia.data.venue_listing_cache import get_venue_listing

        markets = {
            "BTC/USDT": {"active": True},
            "ETH/USDT": {"active": True},
            "AAPLON/USDT": {"active": True},
        }
        provider = self._mock_provider(markets)
        # Patch in the ccxt_provider module because that's where the lazy import resolves.
        with patch("aletheia.data.ccxt_provider.get_market_data_provider",
                   return_value=provider):
            result = await get_venue_listing("bitget")

        assert result is not None
        assert "BTC/USDT" in result
        assert "AAPLON/USDT" in result
        # Second call should hit cache (no network call needed).
        result2 = await get_venue_listing("bitget")
        assert result2 == result

    @pytest.mark.asyncio
    async def test_excludes_inactive_markets(self):
        from aletheia.data.venue_listing_cache import get_venue_listing

        markets = {
            "BTC/USDT": {"active": True},
            "AAPLON/USDT": {"active": False},   # delisted
            "ETH/USDT": {"active": None},        # unknown — keep (fail-open)
        }
        provider = self._mock_provider(markets)
        with patch("aletheia.data.ccxt_provider.get_market_data_provider",
                   return_value=provider):
            result = await get_venue_listing("bitget")

        assert result is not None
        assert "BTC/USDT" in result
        assert "ETH/USDT" in result        # None active → kept
        assert "AAPLON/USDT" not in result  # False → excluded

    @pytest.mark.asyncio
    async def test_returns_none_on_network_failure(self):
        from aletheia.data.venue_listing_cache import get_venue_listing

        provider = MagicMock()
        provider._ensure_primary = MagicMock(return_value=MagicMock(
            load_markets=AsyncMock(side_effect=RuntimeError("network down"))
        ))
        provider.close = AsyncMock()

        with patch("aletheia.data.ccxt_provider.get_market_data_provider",
                   return_value=provider):
            result = await get_venue_listing("bitget")

        assert result is None

    @pytest.mark.asyncio
    async def test_fail_open_does_not_cache(self):
        """A None result (network failure) must not be cached so next call retries."""
        from aletheia.data.venue_listing_cache import get_venue_listing

        provider = MagicMock()
        provider._ensure_primary = MagicMock(return_value=MagicMock(
            load_markets=AsyncMock(side_effect=RuntimeError("network down"))
        ))
        provider.close = AsyncMock()

        with patch("aletheia.data.ccxt_provider.get_market_data_provider",
                   return_value=provider):
            await get_venue_listing("bybit")

        assert get_cached_listing("bybit") is None


# ---------------------------------------------------------------------------
# Integration: market_data xStock pre-filter
# ---------------------------------------------------------------------------

class TestMarketDataXStockFilter:
    def test_delisted_xstock_filtered_by_cache(self):
        """filter_by_listing removes delisted xStock from the instrument list.

        This tests the filter logic that collect_market_snapshots uses directly,
        without needing to run the full async function.
        """
        aaplon = InstrumentId(exchange="bitget", symbol="AAPLON/USDT", market_type=MARKET_SPOT)
        tsla = InstrumentId(exchange="bitget", symbol="TSLAON/USDT", market_type=MARKET_SPOT)
        btc = InstrumentId(exchange="bitget", symbol="BTC/USDT", market_type=MARKET_SPOT)

        # Only BTC/USDT listed; AAPLON and TSLAON are delisted.
        _populate_cache("bitget", frozenset(["BTC/USDT", "ETH/USDT"]))
        listing = get_cached_listing("bitget")

        from aletheia.data.venue_listing_cache import filter_by_listing
        result = filter_by_listing([aaplon, tsla, btc], listing=listing, venue="bitget")

        assert len(result) == 1
        assert result[0].symbol == "BTC/USDT"

    def test_fail_open_when_cache_cold(self):
        """When cache is cold (None), all instruments are kept."""
        aaplon = InstrumentId(exchange="bitget", symbol="AAPLON/USDT", market_type=MARKET_SPOT)
        btc = InstrumentId(exchange="bitget", symbol="BTC/USDT", market_type=MARKET_SPOT)

        # No cache entry for bitget
        listing = get_cached_listing("bitget")  # returns None
        assert listing is None

        from aletheia.data.venue_listing_cache import filter_by_listing
        result = filter_by_listing([aaplon, btc], listing=listing, venue="bitget")

        # Fail-open: all instruments kept
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Integration: ccxt_provider perp defaultType
# ---------------------------------------------------------------------------

class TestCcxtProviderPerpDefaultType:
    # Mark tests in this class to bypass the global _no_network_market_data guard
    # (conftest.py autouse fixture that replaces CCXTProvider.get_ticker with a stub).
    # We need to run the real get_ticker code to verify defaultType handling.
    pytestmark = pytest.mark.real_network
    @pytest.mark.asyncio
    async def test_perp_sets_defaulttype_swap(self):
        """get_ticker for a perp InstrumentId must set defaultType=swap before fetch."""
        from aletheia.data.ccxt_provider import CCXTProvider

        perp_inst = InstrumentId(exchange="bitget", symbol="BTC/USDT", market_type=MARKET_PERP)
        observed_default_types: list[str | None] = []

        exchange_mock = MagicMock()
        exchange_mock.options = {}

        async def fake_fetch_ticker(ccxt_symbol):
            observed_default_types.append(exchange_mock.options.get("defaultType"))
            return {
                "last": 65000.0, "close": None, "quoteVolume": 1e9,
                "baseVolume": None, "percentage": 1.5, "high": 66000.0, "low": 64000.0,
            }

        exchange_mock.fetch_ticker = fake_fetch_ticker

        provider = CCXTProvider(exchange="bitget", fallback_exchange="bitget")
        # Point primary to our mock; make fallback unreachable by cooling it down.
        provider._primary = exchange_mock
        provider._fallback = exchange_mock  # same mock — primary succeeds, loop exits

        with patch("aletheia.data.venue_health.is_venue_available", return_value=True):
            ticker = await provider.get_ticker(perp_inst)

        assert ticker.last_price == 65000.0
        # defaultType was "swap" during the fetch call
        assert "swap" in observed_default_types
        # defaultType restored to absent after the call
        assert exchange_mock.options.get("defaultType") is None

    @pytest.mark.asyncio
    async def test_spot_does_not_set_defaulttype(self):
        """get_ticker for a spot InstrumentId must NOT change defaultType."""
        from aletheia.data.ccxt_provider import CCXTProvider

        spot_inst = InstrumentId(exchange="binance", symbol="BTC/USDT", market_type=MARKET_SPOT)
        observed_default_types: list[str | None] = []

        exchange_mock = MagicMock()
        exchange_mock.options = {}

        async def fake_fetch_ticker(ccxt_symbol):
            observed_default_types.append(exchange_mock.options.get("defaultType"))
            return {
                "last": 65000.0, "close": None, "quoteVolume": 1e9,
                "baseVolume": None, "percentage": 0.5, "high": 66000.0, "low": 64000.0,
            }

        exchange_mock.fetch_ticker = fake_fetch_ticker

        provider = CCXTProvider(exchange="binance", fallback_exchange="binance")
        provider._primary = exchange_mock
        provider._fallback = exchange_mock

        with patch("aletheia.data.venue_health.is_venue_available", return_value=True):
            ticker = await provider.get_ticker(spot_inst)

        assert ticker.last_price == 65000.0
        # For spot, defaultType must never be set (all observed values should be None)
        assert all(v is None for v in observed_default_types)

    @pytest.mark.asyncio
    async def test_perp_restores_defaulttype_after_successful_fetch(self):
        """defaultType is restored to its original value after a successful perp fetch."""
        from aletheia.data.ccxt_provider import CCXTProvider

        perp_inst = InstrumentId(exchange="bitget", symbol="BTC/USDT", market_type=MARKET_PERP)

        exchange_mock = MagicMock()
        exchange_mock.options = {"defaultType": "spot"}  # pre-existing value

        async def fake_fetch_ticker(ccxt_symbol):
            return {
                "last": 65000.0, "close": None, "quoteVolume": 1e9,
                "baseVolume": None, "percentage": 1.5, "high": 66000.0, "low": 64000.0,
            }

        exchange_mock.fetch_ticker = fake_fetch_ticker

        provider = CCXTProvider(exchange="bitget", fallback_exchange="bitget")
        provider._primary = exchange_mock
        provider._fallback = exchange_mock

        with patch("aletheia.data.venue_health.is_venue_available", return_value=True):
            await provider.get_ticker(perp_inst)

        # Pre-existing "spot" value must be restored
        assert exchange_mock.options.get("defaultType") == "spot"

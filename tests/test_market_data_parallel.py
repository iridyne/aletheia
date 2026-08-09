"""Tests for parallel market data fetching.

The serial per-symbol fetch in refresh_watched_prices and collect_market_snapshots
should be replaced with concurrent asyncio.gather + semaphore to avoid exceeding
the scheduler's 90s timeout with many symbols.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import aletheia.data.price_feed as pf


@pytest.fixture(autouse=True)
def reset_caches():
    """Ensure price caches are clean before and after each test."""
    with pf._cache_lock:
        pf._price_cache.clear()
        pf._instrument_price_cache.clear()
    yield
    with pf._cache_lock:
        pf._price_cache.clear()
        pf._instrument_price_cache.clear()


def _make_instrument(symbol, exchange="bitget", market_type="perp"):
    return MagicMock(symbol=symbol, exchange=exchange, market_type=market_type)


async def test_parallel_fetch_concurrent():
    """Multiple symbols must be fetched concurrently, not serially."""
    instruments = [_make_instrument(f"SYM{i}") for i in range(10)]

    call_times = []

    async def mock_get_ticker(instrument):
        call_times.append((instrument.symbol, asyncio.get_running_loop().time()))
        await asyncio.sleep(0.01)  # simulate network latency
        ticker = MagicMock()
        ticker.last_price = 100.0
        ticker.volume_24h = 1000.0
        ticker.provider = "test"
        return ticker

    with patch.object(pf, "get_watched_instruments", return_value=instruments), \
         patch("aletheia.config.loader.load_config") as mock_cfg, \
         patch("aletheia.data.ccxt_provider.require_exact_ticker_venue", side_effect=lambda t, v: t), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider") as mock_provider, \
         patch("aletheia.data.ccxt_provider.CCXTProvider") as mock_ccxt:
        mock_cfg.return_value.event_triggers.enabled = False
        mock_cfg.return_value.market_data.ccxt_exchange = "bitget"
        provider = MagicMock()
        provider.get_ticker = mock_get_ticker
        provider.close = AsyncMock()
        mock_provider.return_value = provider
        mock_ccxt.return_value = provider

        result = await pf.refresh_watched_prices()

        # All 10 symbols should be refreshed
        assert result["refreshed"] == 10

        # If truly concurrent, total time should be ~0.01s, not 0.1s
        # We verify by checking that calls overlap (start times are close)
        if len(call_times) >= 2:
            start_times = [t[1] for t in call_times]
            spread = max(start_times) - min(start_times)
            # Concurrent: spread should be small (< 0.05s for 10 calls)
            # Serial: spread would be ~0.1s (10 * 0.01)
            assert spread < 0.05, f"Calls appear serial: spread={spread:.3f}s"


async def test_sem_limit_respected():
    """Concurrent fetches must not exceed the semaphore limit."""
    instruments = [_make_instrument(f"SYM{i}") for i in range(20)]

    concurrent_count = {"n": 0, "max": 0}
    lock = asyncio.Lock()

    async def mock_get_ticker(instrument):
        async with lock:
            concurrent_count["n"] += 1
            concurrent_count["max"] = max(concurrent_count["max"], concurrent_count["n"])
        await asyncio.sleep(0.02)
        async with lock:
            concurrent_count["n"] -= 1
        ticker = MagicMock()
        ticker.last_price = 100.0
        ticker.volume_24h = 1000.0
        ticker.provider = "test"
        return ticker

    with patch.object(pf, "get_watched_instruments", return_value=instruments), \
         patch("aletheia.config.loader.load_config") as mock_cfg, \
         patch("aletheia.data.ccxt_provider.require_exact_ticker_venue", side_effect=lambda t, v: t), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider") as mock_provider:
        mock_cfg.return_value.event_triggers.enabled = False
        mock_cfg.return_value.market_data.ccxt_exchange = "bitget"
        provider = MagicMock()
        provider.get_ticker = mock_get_ticker
        provider.close = AsyncMock()
        mock_provider.return_value = provider

        await pf.refresh_watched_prices()

        # Max concurrent should not exceed the semaphore limit (8)
        assert concurrent_count["max"] <= 8, (
            f"Concurrency exceeded limit: max={concurrent_count['max']}"
        )


async def test_one_failure_does_not_block_others():
    """One symbol failing must not prevent others from being fetched."""
    instruments = [_make_instrument(f"SYM{i}") for i in range(5)]

    async def mock_get_ticker(instrument):
        if instrument.symbol == "SYM2":
            raise ValueError("exchange timeout")
        ticker = MagicMock()
        ticker.last_price = 100.0
        ticker.volume_24h = 1000.0
        ticker.provider = "test"
        return ticker

    with patch.object(pf, "get_watched_instruments", return_value=instruments), \
         patch("aletheia.config.loader.load_config") as mock_cfg, \
         patch("aletheia.data.ccxt_provider.require_exact_ticker_venue", side_effect=lambda t, v: t), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider") as mock_provider:
        mock_cfg.return_value.event_triggers.enabled = False
        mock_cfg.return_value.market_data.ccxt_exchange = "bitget"
        provider = MagicMock()
        provider.get_ticker = mock_get_ticker
        provider.close = AsyncMock()
        mock_provider.return_value = provider

        result = await pf.refresh_watched_prices()

        # 4 out of 5 should succeed (SYM2 fails)
        assert result["refreshed"] == 4
        assert len(result["errors"]) == 1
        assert "SYM2" in result["errors"][0]


def test_max_concurrency_constant_exists():
    """The max concurrency constant must be defined and reasonable."""
    assert hasattr(pf, "_MARKET_DATA_MAX_CONCURRENCY")
    assert 3 <= pf._MARKET_DATA_MAX_CONCURRENCY <= 16

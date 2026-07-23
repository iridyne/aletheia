"""Tests for aletheia.data.price_feed — in-memory price cache."""

import asyncio
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from aletheia.data.symbols import InstrumentId

from aletheia.data.price_feed import (
    get_cached_price,
    get_watched_instruments,
    get_watched_symbols,
    refresh_watched_prices,
    cache_stats,
    _price_cache,
    _instrument_price_cache,
    _cache_lock,
    _STALE_THRESHOLD_SEC,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Reset module-level cache before each test."""
    with _cache_lock:
        _price_cache.clear()
        _instrument_price_cache.clear()
    yield
    with _cache_lock:
        _price_cache.clear()
        _instrument_price_cache.clear()


class TestGetCachedPrice:
    def test_returns_none_when_empty(self):
        assert get_cached_price("BTC/USDT") is None

    def test_returns_price_when_fresh(self):
        now = datetime.now(timezone.utc)
        with _cache_lock:
            _price_cache["BTC/USDT"] = {"price": 65000.0, "ts": now}
        assert get_cached_price("BTC/USDT") == 65000.0

    def test_returns_none_when_stale(self):
        stale_ts = datetime.now(timezone.utc) - timedelta(seconds=_STALE_THRESHOLD_SEC + 1)
        with _cache_lock:
            _price_cache["ETH/USDT"] = {"price": 3500.0, "ts": stale_ts}
        assert get_cached_price("ETH/USDT") is None

    def test_canonical_conversion(self):
        now = datetime.now(timezone.utc)
        with _cache_lock:
            _price_cache["BTC/USDT"] = {"price": 65000.0, "ts": now}
        assert get_cached_price("BTCUSDT") == 65000.0

    def test_thread_safety(self):
        """Concurrent reads don't crash."""
        now = datetime.now(timezone.utc)
        with _cache_lock:
            _price_cache["SOL/USDT"] = {"price": 150.0, "ts": now}

        results = []

        def reader():
            for _ in range(100):
                results.append(get_cached_price("SOL/USDT"))

        threads = [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(r == 150.0 for r in results)


class TestProtectiveClosePrice:
    def test_exact_cache_keeps_spot_and_perp_marks_separate(self):
        from aletheia.data import price_feed

        now = datetime.now(timezone.utc)
        with _cache_lock:
            price_feed._instrument_price_cache[("binance", "BTC/USDT", "spot")] = {
                "price": 65_000.0, "ts": now, "market_type": "spot",
            }
            price_feed._instrument_price_cache[("binance", "BTC/USDT", "perp")] = {
                "price": 64_500.0, "ts": now, "market_type": "perp",
            }

        assert price_feed.get_fresh_price_for_protective_close(
            "BTC/USDT", "spot", venue="binance",
        ) == 65_000.0
        assert price_feed.get_fresh_price_for_protective_close(
            "BTC/USDT", "usdt_futures", venue="binance",
        ) == 64_500.0

    def test_exact_cache_keeps_execution_venues_separate(self):
        from aletheia.data import price_feed

        now = datetime.now(timezone.utc)
        with _cache_lock:
            price_feed._instrument_price_cache[("okx", "BTC/USDT", "perp")] = {
                "price": 65_000.0, "ts": now, "market_type": "perp",
                "exchange": "okx",
            }
            price_feed._instrument_price_cache[("binance", "BTC/USDT", "perp")] = {
                "price": 64_000.0, "ts": now, "market_type": "perp",
                "exchange": "binance",
            }

        assert price_feed.get_fresh_price_for_protective_close(
            "BTC/USDT", "usdt_futures", venue="okx",
        ) == 65_000.0
        assert price_feed.get_fresh_price_for_protective_close(
            "BTC/USDT", "usdt_futures", venue="binance",
        ) == 64_000.0


class TestGetWatchedSymbols:
    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.storage.repositories.list_all_open_positions")
    def test_returns_unique_symbols(self, mock_list, mock_cfg):
        mock_cfg.return_value.event_triggers.enabled = False
        mock_cfg.return_value.market_data.ccxt_exchange = "binance"
        mock_list.return_value = [
            {"symbol": "BTC/USDT", "execution_mode": "paper_internal"},
            {"symbol": "ETH/USDT", "execution_mode": "paper_internal"},
            {
                "symbol": "BTC/USDT", "execution_mode": "paper_internal",
            },  # duplicate
        ]
        result = get_watched_symbols()
        assert result == ["BTC/USDT", "ETH/USDT"]

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.storage.repositories.list_all_open_positions")
    def test_returns_empty_on_no_positions(self, mock_list, mock_cfg):
        mock_cfg.return_value.event_triggers.enabled = False
        mock_cfg.return_value.market_data.ccxt_exchange = "binance"
        mock_list.return_value = []
        assert get_watched_symbols() == []

    @patch("aletheia.config.loader.load_config")
    @patch(
        "aletheia.storage.repositories.list_all_open_positions",
        side_effect=Exception("db error"),
    )
    def test_returns_empty_on_error(self, mock_list, mock_cfg):
        mock_cfg.return_value.event_triggers.enabled = False
        mock_cfg.return_value.market_data.ccxt_exchange = "binance"
        assert get_watched_symbols() == []

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.storage.repositories.get_universe_asset")
    @patch("aletheia.storage.repositories.get_active_universe_members")
    @patch("aletheia.storage.repositories.list_all_open_positions")
    def test_instruments_preserve_position_and_member_market(
        self, mock_positions, mock_members, mock_asset, mock_cfg
    ):
        mock_positions.return_value = [
            {
                "symbol": "BTC/USDT",
                "market_type": "usdt_futures",
                "execution_mode": "paper_internal",
            }
        ]
        mock_members.return_value = [
            {
                "asset_id": "asset-eth",
                "symbol": "ETH/USDT",
                "market_type": "perp",
                "execution_eligible": 1,
            }
        ]
        mock_asset.return_value = {"venue": "okx", "market_type": "perp"}
        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "okx"
        cfg.event_triggers.enabled = True
        cfg.event_triggers.watch_universe_members = True
        mock_cfg.return_value = cfg

        with patch("aletheia.data.price_feed._UNIVERSE_TICK_EVERY", 1):
            result = get_watched_instruments()

        assert [str(item) for item in result] == [
            "okx:BTC/USDT:perp",
            "okx:ETH/USDT:perp",
        ]

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.storage.repositories.list_all_open_positions")
    def test_same_symbol_spot_and_perp_are_both_watched(self, mock_positions, mock_cfg):
        mock_positions.return_value = [
            {
                "symbol": "BTC/USDT", "market_type": "spot",
                "execution_mode": "paper_internal",
            },
            {
                "symbol": "BTC/USDT", "market_type": "usdt_futures",
                "execution_mode": "paper_internal",
            },
        ]
        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "binance"
        cfg.event_triggers.enabled = False
        mock_cfg.return_value = cfg

        assert [str(item) for item in get_watched_instruments()] == [
            "binance:BTC/USDT:spot",
            "binance:BTC/USDT:perp",
        ]

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.storage.repositories.list_all_open_positions")
    def test_position_execution_mode_selects_watched_venue(
        self, mock_positions, mock_cfg
    ):
        mock_positions.return_value = [
            {
                "symbol": "BTC/USDT", "market_type": "usdt_futures",
                "execution_mode": "okx_demo",
            },
            {
                "symbol": "BTC/USDT", "market_type": "usdt_futures",
                "execution_mode": "binance_testnet",
            },
            {
                "symbol": "ETH/USDT", "market_type": "spot",
                "execution_mode": "paper_internal",
            },
        ]
        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "bitget"
        cfg.event_triggers.enabled = False
        mock_cfg.return_value = cfg

        assert [str(item) for item in get_watched_instruments()] == [
            "okx:BTC/USDT:perp",
            "binance:BTC/USDT:perp",
            "bitget:ETH/USDT:spot",
        ]


class TestRefreshWatchedPrices:
    @patch("aletheia.data.price_feed.get_watched_instruments", return_value=[])
    def test_no_symbols_early_return(self, mock_symbols):
        result = asyncio.run(refresh_watched_prices())
        assert result == {"refreshed": 0, "symbols": [], "errors": []}

    @patch(
        "aletheia.data.price_feed.get_watched_instruments",
        return_value=[
            InstrumentId.from_parts("binance", "BTC/USDT", "spot"),
            InstrumentId.from_parts("binance", "ETH/USDT", "spot"),
        ],
    )
    @patch("aletheia.data.ccxt_provider.get_market_data_provider")
    @patch("aletheia.config.loader.load_config")
    def test_refreshes_cache_on_success(self, mock_cfg, mock_provider_fn, mock_symbols):
        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "binance"
        mock_cfg.return_value = cfg

        mock_ticker_btc = MagicMock()
        mock_ticker_btc.last_price = 65000.0
        mock_ticker_btc.provider = "binance"
        mock_ticker_eth = MagicMock()
        mock_ticker_eth.last_price = 3500.0
        mock_ticker_eth.provider = "binance"

        provider = AsyncMock()
        provider.get_ticker = AsyncMock(side_effect=[mock_ticker_btc, mock_ticker_eth])
        provider.close = AsyncMock()
        mock_provider_fn.return_value = provider

        result = asyncio.run(refresh_watched_prices())
        assert result["refreshed"] == 2
        assert "BTC/USDT" in result["symbols"]
        assert "ETH/USDT" in result["symbols"]
        assert get_cached_price("BTC/USDT") == 65000.0
        assert get_cached_price("ETH/USDT") == 3500.0
        from aletheia.data.price_feed import get_fresh_price_for_protective_close
        assert get_fresh_price_for_protective_close(
            "BTC/USDT", "spot", venue="binance",
        ) == 65000.0

    @patch(
        "aletheia.data.price_feed.get_watched_instruments",
        return_value=[InstrumentId.from_parts("binance", "ON/USDT", "perp")],
    )
    @patch("aletheia.data.ccxt_provider.get_market_data_provider")
    @patch("aletheia.config.loader.load_config")
    def test_cross_venue_fallback_never_enters_exact_cache(
        self, mock_cfg, mock_provider_fn, mock_symbols
    ):
        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "binance"
        cfg.event_triggers.enabled = False
        mock_cfg.return_value = cfg

        ticker = MagicMock(
            last_price=90.57,
            volume_24h=3_012_868.0,
            provider="bybit",
        )
        provider = AsyncMock()
        provider.get_ticker = AsyncMock(return_value=ticker)
        provider.close = AsyncMock()
        mock_provider_fn.return_value = provider

        result = asyncio.run(refresh_watched_prices())

        mock_provider_fn.assert_called_once_with(
            exchange="binance",
            native_ccxt=True,
            fallback_exchange="binance",
        )
        assert result["refreshed"] == 0
        assert result["errors"] == [
            "binance:ON/USDT:perp: ticker provider bybit does not match venue binance"
        ]
        from aletheia.data.price_feed import get_fresh_price_for_protective_close
        assert get_fresh_price_for_protective_close(
            "ON/USDT", "usdt_futures", venue="binance",
        ) is None
        assert get_cached_price("ON/USDT") is None

    @patch(
        "aletheia.data.price_feed.get_watched_instruments",
        return_value=[InstrumentId.from_parts("binance", "BTC/USDT", "spot")],
    )
    @patch("aletheia.data.ccxt_provider.get_market_data_provider")
    @patch("aletheia.config.loader.load_config")
    def test_handles_ticker_failure_gracefully(self, mock_cfg, mock_provider_fn, mock_symbols):
        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "binance"
        mock_cfg.return_value = cfg

        provider = AsyncMock()
        provider.get_ticker = AsyncMock(side_effect=Exception("network error"))
        provider.close = AsyncMock()
        mock_provider_fn.return_value = provider

        result = asyncio.run(refresh_watched_prices())
        assert result["refreshed"] == 0
        assert len(result["errors"]) == 1
        assert get_cached_price("BTC/USDT") is None


class TestCacheStats:
    def test_empty_cache(self):
        stats = cache_stats()
        assert stats == {"entries": 0, "oldest_sec": None, "newest_sec": None}

    def test_with_entries(self):
        now = datetime.now(timezone.utc)
        with _cache_lock:
            _price_cache["BTC/USDT"] = {"price": 65000.0, "ts": now}
            _price_cache["ETH/USDT"] = {"price": 3500.0, "ts": now - timedelta(seconds=30)}
        stats = cache_stats()
        assert stats["entries"] == 2
        assert stats["newest_sec"] < 2.0
        assert stats["oldest_sec"] >= 29.0

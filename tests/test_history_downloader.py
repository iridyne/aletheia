"""Tests for aletheia.data.history_downloader."""

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from aletheia.data.history_downloader import download_symbol_history, check_coverage


def _mock_config():
    cfg = MagicMock()
    cfg.historical_data.max_bars_per_pair = 50_000
    cfg.historical_data.min_bars_for_backtest = 200
    cfg.market_data.ccxt_exchange = "binance"
    return cfg


@dataclass
class FakeCandle:
    timestamp: int
    open: float = 100.0
    high: float = 101.0
    low: float = 99.0
    close: float = 100.5
    volume: float = 1000.0


def _make_candles(start_ts: int, count: int, tf_ms: int = 3_600_000):
    return [FakeCandle(timestamp=start_ts + i * tf_ms) for i in range(count)]


@pytest.mark.asyncio
async def test_download_basic():
    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=[
        _make_candles(1_000_000_000, 500),
        _make_candles(1_000_000_000 + 500 * 3_600_000, 500),
        _make_candles(1_000_000_000 + 1000 * 3_600_000, 100),
        [],
    ])
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=None), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("BTC/USDT", "1h", months=1)

    assert result.bars_fetched == 1100
    assert result.error is None


@pytest.mark.asyncio
async def test_download_incremental():
    """When newest_ts exists, should start from there (not from months ago)."""
    existing_ts = 1_700_000_000_000

    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=[
        _make_candles(existing_ts + 3_600_000, 200),
        [],
    ])
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=existing_ts), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("BTC/USDT", "1h", months=6)

    assert result.bars_fetched == 200


@pytest.mark.asyncio
async def test_download_error_handling():
    """Provider failure should be captured in result.error, not raise."""
    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=RuntimeError("network error"))
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=None), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("ETH/USDT", "1h", months=1)

    assert result.bars_fetched == 0
    assert result.error is not None


def test_check_coverage_enough():
    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
                return_value={"bar_count": 500, "oldest_ts": 1, "newest_ts": 2}):
        assert check_coverage("BTC/USDT", "1h", min_bars=200) is True


def test_check_coverage_not_enough():
    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
                return_value={"bar_count": 50, "oldest_ts": 1, "newest_ts": 2}):
        assert check_coverage("BTC/USDT", "1h", min_bars=200) is False

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
    now_ms = 1_000_000_000 + 1101 * 3_600_000

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.data.history_downloader.time.time", return_value=now_ms / 1000), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=None), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 0, "oldest_ts": None, "newest_ts": None}), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("BTC/USDT", "1h", months=3)

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
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 200, "oldest_ts": existing_ts, "newest_ts": existing_ts}), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("BTC/USDT", "1h", months=6)

    assert result.bars_fetched == 200


@pytest.mark.asyncio
async def test_download_left_gap_starts_near_oldest_existing():
    """New listings can return empty when queried from long before listing."""
    tf_ms = 3_600_000
    oldest_ts = 1_700_000_000_000
    newest_ts = oldest_ts + 100 * tf_ms
    expected_since = oldest_ts - 500 * tf_ms

    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=[
        _make_candles(oldest_ts - 10 * tf_ms, 10),
        [],
    ])
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.data.history_downloader.time.time", return_value=(newest_ts + tf_ms) / 1000), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=newest_ts), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 100, "oldest_ts": oldest_ts, "newest_ts": newest_ts}), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("NES/USDT", "1h", months=6)

    assert result.bars_fetched == 10
    assert provider.get_ohlcv.await_args_list[0].kwargs["since"] == expected_since


@pytest.mark.asyncio
async def test_download_left_gap_retries_closer_window_when_listing_start_is_empty():
    tf_ms = 3_600_000
    oldest_ts = 1_700_000_000_000
    newest_ts = oldest_ts + 100 * tf_ms

    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=[
        [],
        _make_candles(oldest_ts - 10 * tf_ms, 10),
        [],
    ])
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.data.history_downloader.time.time", return_value=(newest_ts + tf_ms) / 1000), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=newest_ts), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 100, "oldest_ts": oldest_ts, "newest_ts": newest_ts}), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("NES/USDT", "1h", months=6)

    assert result.bars_fetched == 10
    assert provider.get_ohlcv.await_args_list[0].kwargs["since"] == oldest_ts - 500 * tf_ms
    assert provider.get_ohlcv.await_args_list[1].kwargs["since"] == oldest_ts - 100 * tf_ms


@pytest.mark.asyncio
async def test_download_right_gap_runs_after_empty_left_gap():
    """An empty left-gap page must not prevent latest-bar catch-up."""
    tf_ms = 3_600_000
    oldest_ts = 1_700_000_000_000
    newest_ts = oldest_ts + 100 * tf_ms

    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=[
        [],
        [],
        [],
        [],
        _make_candles(newest_ts + tf_ms, 5),
    ])
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.data.history_downloader.time.time", return_value=(newest_ts + 10 * tf_ms) / 1000), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=newest_ts), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 100, "oldest_ts": oldest_ts, "newest_ts": newest_ts}), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("RE/USDT", "1h", months=6)

    assert result.bars_fetched == 5
    assert provider.get_ohlcv.await_args_list[4].kwargs["since"] == newest_ts + tf_ms


@pytest.mark.asyncio
async def test_download_error_handling():
    """Provider failure should be captured in result.error, not raise."""
    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=RuntimeError("network error"))
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=None), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 0, "oldest_ts": None, "newest_ts": None}), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider):
        result = await download_symbol_history("ETH/USDT", "1h", months=1)

    assert result.bars_fetched == 0
    assert result.error is not None


@pytest.mark.asyncio
async def test_download_scopes_fetch_coverage_and_write_to_instrument():
    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(return_value=[])
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=None) as newest, \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 0, "oldest_ts": None, "newest_ts": None}) as coverage, \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles") as upsert, \
         patch("aletheia.data.ccxt_provider.get_market_data_provider", return_value=provider) as factory:
        await download_symbol_history(
            "BTC/USDT", "1h", months=1, exchange="okx", market_type="perp"
        )

    instrument = provider.get_ohlcv.await_args.args[0]
    assert str(instrument) == "okx:BTC/USDT:perp"
    newest.assert_called_once_with(
        "BTC/USDT", "1h", exchange="okx", market_type="perp"
    )
    coverage.assert_called_once_with(
        "BTC/USDT", "1h", exchange="okx", market_type="perp"
    )
    factory.assert_called_once_with("okx", native_ccxt=True, fallback_exchange="okx")
    upsert.assert_not_called()


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

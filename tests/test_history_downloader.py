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
    is_closed: bool = True


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
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider):
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
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider):
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
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider):
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
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider):
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
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider):
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
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider):
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
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider) as factory:
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
    factory.assert_called_once_with("okx", exact_venue=True)
    upsert.assert_not_called()


@pytest.mark.asyncio
async def test_empty_new_listing_probes_near_now_then_backfills_left():
    """A venue may return empty when `since` predates a new listing."""
    tf_ms = 3_600_000
    now_ms = 1_800_000_000_000
    recent_start = now_ms - 100 * tf_ms
    older_start = recent_start - 50 * tf_ms

    provider = AsyncMock()
    provider.get_ohlcv = AsyncMock(side_effect=[
        [],  # requested month start is before listing
        [],  # 500h probe is still before listing
        _make_candles(recent_start, 100),  # 100h probe discovers the listing
        [],  # 500h left probe is before listing
        _make_candles(older_start, 50),  # closer left probe finds earlier bars
        [],  # right edge is already current
    ])
    provider.close = AsyncMock()

    with patch("aletheia.config.loader.load_config", return_value=_mock_config()), \
         patch("aletheia.data.history_downloader.time.time", return_value=now_ms / 1000), \
         patch("aletheia.storage.repositories.get_ohlcv_newest_ts", return_value=None), \
         patch("aletheia.storage.repositories.get_ohlcv_coverage",
               return_value={"bar_count": 0, "oldest_ts": None, "newest_ts": None}), \
         patch("aletheia.storage.repositories.upsert_ohlcv_candles"), \
         patch("aletheia.data.providers.get_market_data_provider", return_value=provider):
        result = await download_symbol_history(
            "CAP/USDT", "1h", months=1, exchange="okx", market_type="perp"
        )

    assert result.bars_fetched == 150
    assert result.oldest_ts == older_start
    assert result.newest_ts == recent_start + 99 * tf_ms


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


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["forward", "bootstrap", "left_gap", "all_forming"])
async def test_history_persists_only_closed_bars_and_recovers_final_tail(monkeypatch, path):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.config import loader
    from aletheia.data import history_downloader as downloader, providers
    from aletheia.storage import repositories as repo

    cfg = AletheiaConfig()
    monkeypatch.setattr(loader, "load_config", lambda: cfg)
    now = 1_800_000_000_000
    tf = 3_600_000
    monkeypatch.setattr(downloader.time, "time", lambda: now / 1000)
    monkeypatch.setattr(downloader, "_PAGE_SIZE", 2)
    monkeypatch.setattr(downloader, "_RATE_LIMIT_SEC", 0)
    closed = FakeCandle(now - 2 * tf)
    forming = FakeCandle(now - tf, close=100.1, is_closed=False)
    page = [forming] if path == "all_forming" else [closed, forming]
    if path == "left_gap":
        repo.upsert_ohlcv_candles("BTC/USDT", "1h", [FakeCandle(now)], exchange="okx", market_type="perp")
    pages = [[], page] if path == "bootstrap" else [page]
    calls = []
    async def fetch(instrument, timeframe, **kwargs):
        assert str(instrument) == "okx:BTC/USDT:perp"
        calls.append(kwargs["since"])
        return pages.pop(0) if pages else []
    provider = AsyncMock()
    provider.get_ohlcv.side_effect = fetch
    monkeypatch.setattr(providers, "get_market_data_provider", lambda *a, **kw: provider)
    progress = []
    result = await download_symbol_history("BTC/USDT", "1h", months=1, exchange="okx",
                                           market_type="perp", progress_callback=progress.append)
    rows = repo.get_ohlcv_candles("BTC/USDT", "1h", exchange="okx", market_type="perp")
    assert forming.timestamp not in [row["ts"] for row in rows]
    assert result.error is None
    assert result.bars_fetched == (0 if path == "all_forming" else 1)
    assert result.oldest_ts == result.newest_ts == (None if path == "all_forming" else closed.timestamp)
    assert progress == ([] if path == "all_forming" else [1])
    assert len(calls) <= 7
    provider.close.assert_awaited_once()
    if path == "forward":
        assert len(calls) == 1  # Raw page cursor reached now; do not refetch its forming tail.
    if path in {"forward", "all_forming"}:
        now += tf
        final = FakeCandle(forming.timestamp, close=100.9, is_closed=True)
        async def final_fetch(instrument, timeframe, **kwargs):
            return [final] if kwargs["since"] <= final.timestamp else []
        provider.get_ohlcv.side_effect = final_fetch
        second = await download_symbol_history("BTC/USDT", "1h", months=1, exchange="okx", market_type="perp")
        assert second.bars_fetched == 1
        rows = repo.get_ohlcv_candles("BTC/USDT", "1h", exchange="okx", market_type="perp")
        assert rows[-1]["ts"] == final.timestamp
        assert rows[-1]["close"] == 100.9
        assert all(row["exchange"] == "okx" and row["market_type"] == "perp" for row in rows)
        third = await download_symbol_history("BTC/USDT", "1h", months=1, exchange="okx", market_type="perp")
        assert third.bars_fetched == 0

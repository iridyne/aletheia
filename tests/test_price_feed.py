"""Tests for aletheia.data.price_feed — in-memory price cache."""

import asyncio
import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from aletheia.data.symbols import InstrumentId
from aletheia.data.providers import Ticker

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
    from aletheia.data import price_feed

    with _cache_lock:
        _price_cache.clear()
        _instrument_price_cache.clear()
    with price_feed._tick_lock:
        price_feed._tick_counter = 0
        price_feed._universe_round = 0
    yield
    with _cache_lock:
        _price_cache.clear()
        _instrument_price_cache.clear()
    with price_feed._tick_lock:
        price_feed._tick_counter = 0
        price_feed._universe_round = 0


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
    def test_exact_cache_returns_bounded_observation_provenance(self):
        from aletheia.data import price_feed

        observed_at = datetime.now(timezone.utc) - timedelta(seconds=3)
        with _cache_lock:
            price_feed._instrument_price_cache[("okx", "BTC/USDT", "perp")] = {
                "price": 64_500.0,
                "ts": observed_at,
                "provider": "must-not-leak",
            }

        mark = price_feed.get_fresh_mark_for_protective_close(
            "BTC/USDT", "usdt_futures", venue="okx",
        )

        assert mark is not None
        assert mark["price"] == 64_500.0
        assert mark["observed_at"] == observed_at.isoformat()
        assert 2.0 <= mark["age_seconds"] <= 5.0
        assert mark["source"] == "instrument_price_cache"
        assert mark["venue"] == "okx"
        assert mark["symbol"] == "BTC/USDT"
        assert mark["market_type"] == "perp"
        assert "provider" not in mark

    def test_sqlite_fallback_returns_snapshot_observation_provenance(self, monkeypatch):
        from aletheia.data import price_feed

        observed_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        monkeypatch.setattr(
            "aletheia.storage.repositories.list_market_snapshots",
            lambda **kwargs: [{
                "price": 65_100.0,
                "snapshot_at": observed_at.isoformat(),
                "provider": "must-not-leak",
            }]
            if kwargs == {
                "symbol": "BTC/USDT",
                "exchange": "binance",
                "market_type": "spot",
                "limit": 1,
            }
            else [],
        )

        mark = price_feed.get_fresh_mark_for_protective_close(
            "BTC/USDT", "spot", venue="binance",
        )

        assert mark is not None
        assert mark["price"] == 65_100.0
        assert mark["observed_at"] == observed_at.isoformat()
        assert 9.0 <= mark["age_seconds"] <= 12.0
        assert mark["source"] == "market_snapshot"
        assert mark["venue"] == "binance"
        assert mark["market_type"] == "spot"
        assert "provider" not in mark

    def test_exact_cache_rejects_mark_before_causal_lower_bound(self):
        from aletheia.data import price_feed

        observed_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        with _cache_lock:
            price_feed._instrument_price_cache[("binance", "BTC/USDT", "spot")] = {
                "price": 65_000.0,
                "ts": observed_at,
            }

        mark = price_feed.get_fresh_mark_for_protective_close(
            "BTC/USDT",
            "spot",
            venue="binance",
            not_before=observed_at + timedelta(seconds=1),
        )

        assert mark is None

    def test_sqlite_fallback_rejects_mark_before_causal_lower_bound(
        self, monkeypatch,
    ):
        from aletheia.data import price_feed

        observed_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        monkeypatch.setattr(
            "aletheia.storage.repositories.list_market_snapshots",
            lambda **_kwargs: [{
                "price": 65_100.0,
                "snapshot_at": observed_at.isoformat(),
            }],
        )

        mark = price_feed.get_fresh_mark_for_protective_close(
            "BTC/USDT",
            "spot",
            venue="binance",
            not_before=observed_at + timedelta(seconds=1),
        )

        assert mark is None

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
            {"symbol": "BTC/USDT", "execution_mode": "binance_testnet"},
            {"symbol": "ETH/USDT", "execution_mode": "binance_testnet"},
            {
                "symbol": "BTC/USDT", "execution_mode": "binance_testnet",
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
                "execution_mode": "okx_demo",
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

        always_on: set[tuple[str, str, str]] = set()
        universe_rounds: dict[tuple[str, str, str], int] = {}
        with patch("aletheia.data.price_feed._UNIVERSE_TICK_EVERY", 1):
            result = get_watched_instruments(
                _always_on_identities=always_on,
                _universe_round_by_identity=universe_rounds,
            )

        assert [str(item) for item in result] == [
            "okx:BTC/USDT:perp",
            "okx:ETH/USDT:perp",
        ]
        assert always_on == {("okx", "BTC/USDT", "perp")}
        assert universe_rounds == {("okx", "ETH/USDT", "perp"): 1}

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.storage.repositories.list_all_open_positions")
    def test_same_symbol_spot_and_perp_are_both_watched(self, mock_positions, mock_cfg):
        mock_positions.return_value = [
            {
                "symbol": "BTC/USDT", "market_type": "spot",
                "execution_mode": "binance_testnet",
            },
            {
                "symbol": "BTC/USDT", "market_type": "usdt_futures",
                "execution_mode": "binance_testnet",
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

    def test_archived_position_is_not_watched_even_with_protection_state(self, monkeypatch):
        import aletheia.config.loader as loader
        import aletheia.storage.repositories as repo

        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "bitget"
        cfg.event_triggers.enabled = False
        monkeypatch.setattr(loader, "load_config", lambda: cfg)
        monkeypatch.setattr(
            repo,
            "list_all_open_positions",
            lambda: [
                {
                    "id": "protected-position",
                    "symbol": "BTC/USDT",
                    "market_type": "usdt_futures",
                    "execution_mode": "paper_internal",
                }
            ],
        )
        monkeypatch.setattr(
            repo,
            "get_position_protection_state",
            lambda _position_id: {
                "venue": "binance",
                "symbol": "BTC/USDT",
                "market_type": "perp",
            },
        )

        assert get_watched_instruments() == []

    def test_planning_spot_and_perp_bypass_universe_tick_gate(self, monkeypatch):
        import aletheia.config.loader as loader
        import aletheia.storage.repositories as repo

        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "binance"
        cfg.event_triggers.enabled = False
        monkeypatch.setattr(loader, "load_config", lambda: cfg)
        monkeypatch.setattr(repo, "list_all_open_positions", lambda: [])
        monkeypatch.setattr(
            repo,
            "list_candidate_planning_refresh_assets",
            lambda: [
                {"venue": "okx", "symbol": "BTC/USDT", "market_type": "spot"},
                {"venue": "binance", "symbol": "BTC/USDT", "market_type": "perp"},
            ],
        )

        always_on: set[tuple[str, str, str]] = set()
        assert [
            str(item)
            for item in get_watched_instruments(_always_on_identities=always_on)
        ] == [
            "okx:BTC/USDT:spot",
            "binance:BTC/USDT:perp",
        ]
        assert always_on == {
            ("okx", "BTC/USDT", "spot"),
            ("binance", "BTC/USDT", "perp"),
        }

    @patch("aletheia.config.loader.load_config")
    @patch("aletheia.storage.repositories.get_universe_asset")
    @patch("aletheia.storage.repositories.get_active_universe_members")
    @patch(
        "aletheia.storage.repositories.list_all_open_positions",
        side_effect=Exception("db error"),
    )
    def test_always_on_source_failure_uses_strict_cadence(
        self, mock_positions, mock_members, mock_asset, mock_cfg
    ):
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

        always_on: set[tuple[str, str, str]] = set()
        with patch("aletheia.data.price_feed._UNIVERSE_TICK_EVERY", 1):
            result = get_watched_instruments(_always_on_identities=always_on)

        assert [str(item) for item in result] == ["okx:ETH/USDT:perp"]
        assert always_on == {("okx", "ETH/USDT", "perp")}

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
                "execution_mode": "bitget_demo",
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

        mock_ticker_btc = Ticker(
            symbol="BTC/USDT", last_price=65000.0, volume_24h=0.0,
            change_pct_24h=0.0, provider="binance",
        )
        mock_ticker_eth = Ticker(
            symbol="ETH/USDT", last_price=3500.0, volume_24h=0.0,
            change_pct_24h=0.0, provider="binance",
        )

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
        return_value=[
            InstrumentId.from_parts("binance", "BTC/USDT", "spot"),
            InstrumentId.from_parts("binance", "BTC/USDT", "perp"),
        ],
    )
    @patch("aletheia.data.ccxt_provider.get_market_data_provider")
    @patch("aletheia.config.loader.load_config")
    def test_same_symbol_spot_perp_basis_does_not_trigger_price_event(
        self, mock_cfg, mock_provider_fn, mock_symbols
    ):
        cfg = MagicMock()
        cfg.event_triggers.enabled = True
        cfg.event_triggers.price_move_pct_threshold = 5.0
        cfg.event_triggers.volume_surge_multiplier = 2.0
        mock_cfg.return_value = cfg

        spot = Ticker(
            symbol="BTC/USDT", last_price=100.0, volume_24h=100.0,
            change_pct_24h=0.0, provider="binance",
        )
        perp = Ticker(
            symbol="BTC/USDT", last_price=120.0, volume_24h=300.0,
            change_pct_24h=0.0, provider="binance",
        )
        provider = AsyncMock()
        provider.get_ticker = AsyncMock(side_effect=[spot, perp, spot, perp])
        provider.close = AsyncMock()
        mock_provider_fn.return_value = provider

        with patch("aletheia.data.price_feed._check_event_triggers") as mock_events:
            asyncio.run(refresh_watched_prices())
            asyncio.run(refresh_watched_prices())

        assert [call.args[1] for call in mock_events.call_args_list] == [0.0, 0.0]

    @patch(
        "aletheia.data.price_feed.get_watched_instruments",
        return_value=[InstrumentId.from_parts("binance", "BTC/USDT", "spot")],
    )
    @patch("aletheia.data.ccxt_provider.get_market_data_provider")
    @patch("aletheia.config.loader.load_config")
    @pytest.mark.parametrize(
        ("previous_age_sec", "always_on", "expects_event"),
        [
            (360, False, True),
            (361, False, False),
            (90, True, True),
            (91, True, False),
            (-1, False, False),
            ("malformed", False, False),
            ("naive", False, False),
        ],
    )
    def test_event_continuity_respects_watcher_cadence_and_invalid_timestamps(
        self,
        mock_cfg,
        mock_provider_fn,
        mock_symbols,
        previous_age_sec,
        always_on,
        expects_event,
    ):
        from aletheia.data import price_feed

        instrument = InstrumentId.from_parts("binance", "BTC/USDT", "spot")
        instrument_key = ("binance", "BTC/USDT", "spot")
        universe_round = 2

        def watched(*, _always_on_identities, _universe_round_by_identity):
            nonlocal universe_round
            if always_on:
                _always_on_identities.add(instrument_key)
            else:
                _universe_round_by_identity[instrument_key] = universe_round
                universe_round += 1
            return [instrument]

        mock_symbols.side_effect = watched
        cfg = MagicMock()
        cfg.event_triggers.enabled = True
        cfg.event_triggers.price_move_pct_threshold = 5.0
        cfg.event_triggers.volume_surge_multiplier = 2.0
        mock_cfg.return_value = cfg

        observed_at = datetime(2026, 8, 29, tzinfo=timezone.utc)
        if previous_age_sec == "malformed":
            previous_ts = "not-a-timestamp"
        elif previous_age_sec == "naive":
            previous_ts = observed_at.replace(tzinfo=None)
        else:
            previous_ts = observed_at - timedelta(seconds=previous_age_sec)
        with _cache_lock:
            price_feed._instrument_price_cache[instrument_key] = {
                "price": 100.0,
                "ts": previous_ts,
                "volume_24h": 100.0,
                "event_universe_round": 1,
            }

        provider = AsyncMock()
        provider.get_ticker = AsyncMock(
            return_value=Ticker(
                symbol="BTC/USDT",
                last_price=110.0,
                volume_24h=300.0,
                change_pct_24h=0.0,
                provider="binance",
            )
        )
        provider.close = AsyncMock()
        mock_provider_fn.return_value = provider

        with (
            patch("aletheia.data.price_feed.datetime") as clock,
            patch("aletheia.data.price_feed._check_event_triggers") as mock_events,
        ):
            clock.now.return_value = observed_at
            asyncio.run(refresh_watched_prices())

            if expects_event:
                mock_events.assert_called_once()
            else:
                mock_events.assert_not_called()
            assert price_feed._instrument_price_cache[instrument_key]["prev_price"] == (
                100.0 if expects_event else None
            )

            if previous_age_sec == 361:
                clock.now.return_value = observed_at + timedelta(seconds=1)
                asyncio.run(refresh_watched_prices())
                mock_events.assert_called_once()

    def test_universe_events_survive_slow_scheduler_tick_gate(self, monkeypatch):
        from aletheia.data import ccxt_provider, price_feed
        import aletheia.config.loader as loader
        import aletheia.storage.repositories as repo

        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "binance"
        cfg.event_triggers.enabled = True
        cfg.event_triggers.watch_universe_members = True
        cfg.event_triggers.price_move_pct_threshold = 5.0
        cfg.event_triggers.volume_surge_multiplier = 2.0
        monkeypatch.setattr(loader, "load_config", lambda: cfg)
        monkeypatch.setattr(repo, "list_all_open_positions", lambda: [])
        monkeypatch.setattr(
            repo, "list_candidate_planning_refresh_assets", lambda: [],
        )
        monkeypatch.setattr(
            repo,
            "get_active_universe_members",
            lambda **_kwargs: [{
                "asset_id": "asset-btc",
                "symbol": "BTC/USDT",
                "market_type": "spot",
                "execution_eligible": 1,
            }],
        )
        monkeypatch.setattr(
            repo, "get_universe_asset", lambda _asset_id: {"venue": "binance"},
        )

        provider = MagicMock()
        provider.get_ticker = AsyncMock(
            side_effect=[
                Ticker(
                    symbol="BTC/USDT", last_price=100.0, volume_24h=100.0,
                    change_pct_24h=0.0, provider="binance",
                ),
                Ticker(
                    symbol="BTC/USDT", last_price=110.0, volume_24h=300.0,
                    change_pct_24h=0.0, provider="binance",
                ),
            ]
        )
        provider.close = AsyncMock()
        monkeypatch.setattr(
            ccxt_provider, "get_market_data_provider", lambda **_kwargs: provider,
        )

        first_observed = datetime(2026, 8, 29, tzinfo=timezone.utc)
        # Three actual calls can be 90s apart when a late 45s invocation crosses
        # two 30s fire points; a late serial quote adds another 45s.
        second_observed = first_observed + timedelta(seconds=(3 * 90) + 45)
        with (
            patch("aletheia.data.price_feed.datetime") as clock,
            patch("aletheia.data.price_feed._check_event_triggers") as mock_events,
        ):
            clock.now.side_effect = [first_observed, second_observed]
            results = [asyncio.run(refresh_watched_prices()) for _ in range(6)]

        assert [item["refreshed"] for item in results] == [0, 0, 1, 0, 0, 1]
        mock_events.assert_called_once()
        assert provider.get_ticker.await_count == 2

    @pytest.mark.parametrize(
        "discontinuity",
        [
            "left_watch",
            "quote_outage",
            "expansion_failure",
            "always_on_transition",
        ],
    )
    def test_universe_recovery_requires_a_new_sampling_segment(
        self, monkeypatch, discontinuity
    ):
        from aletheia.data import ccxt_provider
        import aletheia.config.loader as loader
        import aletheia.storage.repositories as repo

        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "binance"
        cfg.event_triggers.enabled = True
        cfg.event_triggers.watch_universe_members = True
        cfg.event_triggers.price_move_pct_threshold = 5.0
        cfg.event_triggers.volume_surge_multiplier = 2.0
        monkeypatch.setattr(loader, "load_config", lambda: cfg)
        position_call = 0

        def open_positions():
            nonlocal position_call
            position_call += 1
            if discontinuity == "always_on_transition" and position_call == 1:
                return [{
                    "id": "position-btc",
                    "symbol": "BTC/USDT",
                    "market_type": "spot",
                    "execution_mode": "binance_testnet",
                }]
            return []

        monkeypatch.setattr(repo, "list_all_open_positions", open_positions)
        monkeypatch.setattr(
            repo, "list_candidate_planning_refresh_assets", lambda: [],
        )

        member = {
            "asset_id": "asset-btc",
            "symbol": "BTC/USDT",
            "market_type": "spot",
            "execution_eligible": 1,
        }
        round_index = 0

        def active_members(**_kwargs):
            nonlocal round_index
            round_index += 1
            if discontinuity == "left_watch" and round_index == 2:
                return []
            if discontinuity == "expansion_failure" and round_index == 2:
                raise RuntimeError("universe expansion failed")
            return [member]

        monkeypatch.setattr(repo, "get_active_universe_members", active_members)
        monkeypatch.setattr(
            repo, "get_universe_asset", lambda _asset_id: {"venue": "binance"},
        )

        quotes = [
            Ticker(
                symbol="BTC/USDT", last_price=100.0, volume_24h=100.0,
                change_pct_24h=0.0, provider="binance",
            ),
            Ticker(
                symbol="BTC/USDT", last_price=110.0, volume_24h=200.0,
                change_pct_24h=0.0, provider="binance",
            ),
            Ticker(
                symbol="BTC/USDT", last_price=120.0, volume_24h=300.0,
                change_pct_24h=0.0, provider="binance",
            ),
        ]
        if discontinuity == "quote_outage":
            quotes.insert(1, RuntimeError("quote timeout"))
        provider = MagicMock()
        provider.get_ticker = AsyncMock(side_effect=quotes)
        provider.close = AsyncMock()
        monkeypatch.setattr(
            ccxt_provider, "get_market_data_provider", lambda **_kwargs: provider,
        )

        first_observed = datetime(2026, 8, 29, tzinfo=timezone.utc)
        with (
            patch("aletheia.data.price_feed._UNIVERSE_TICK_EVERY", 1),
            patch("aletheia.data.price_feed.datetime") as clock,
            patch("aletheia.data.price_feed._check_event_triggers") as mock_events,
        ):
            clock.now.side_effect = [
                first_observed,
                first_observed + timedelta(seconds=180),
                first_observed + timedelta(seconds=270),
            ]
            refresh_count = 3 if discontinuity == "always_on_transition" else 4
            for _ in range(refresh_count):
                asyncio.run(refresh_watched_prices())

        # The first sample after a missed round or watcher-authority transition
        # is a new baseline. Only the following consecutive round may emit.
        mock_events.assert_called_once()

    @patch(
        "aletheia.data.price_feed.get_watched_instruments",
        return_value=[
            InstrumentId.from_parts("binance", "BTC/USDT", "spot"),
            InstrumentId.from_parts("binance", "ETH/USDT", "spot"),
        ],
    )
    @patch("aletheia.data.ccxt_provider.get_market_data_provider")
    @patch("aletheia.config.loader.load_config")
    def test_each_ticker_records_its_actual_observation_time(
        self, mock_cfg, mock_provider_fn, mock_symbols
    ):
        cfg = MagicMock()
        cfg.event_triggers.enabled = False
        mock_cfg.return_value = cfg

        provider = AsyncMock()
        provider.get_ticker = AsyncMock(
            side_effect=[
                Ticker(
                    symbol="BTC/USDT", last_price=65000.0, volume_24h=0.0,
                    change_pct_24h=0.0, provider="binance",
                ),
                Ticker(
                    symbol="ETH/USDT", last_price=3500.0, volume_24h=0.0,
                    change_pct_24h=0.0, provider="binance",
                ),
            ]
        )
        provider.close = AsyncMock()
        mock_provider_fn.return_value = provider
        first_observed = datetime(2026, 8, 8, 1, 0, tzinfo=timezone.utc)
        second_observed = first_observed + timedelta(seconds=20)

        with patch("aletheia.data.price_feed.datetime") as clock:
            clock.now.side_effect = [first_observed, second_observed]
            result = asyncio.run(refresh_watched_prices())

        assert result["refreshed"] == 2
        assert _instrument_price_cache[("binance", "BTC/USDT", "spot")]["ts"] == first_observed
        assert _instrument_price_cache[("binance", "ETH/USDT", "spot")]["ts"] == second_observed

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

        ticker = Ticker(
            symbol="ON/USDT",
            last_price=90.57,
            volume_24h=3_012_868.0,
            change_pct_24h=0.0,
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


def test_stock_token_refresh_uses_pinned_factory(monkeypatch):
    from aletheia.data import ccxt_provider, price_feed

    instrument = InstrumentId.from_parts("bitget", "AAPLON/USDT", "spot")
    monkeypatch.setattr(price_feed, "get_watched_instruments", lambda **kwargs: [instrument])
    provider = AsyncMock()
    provider.get_ticker.return_value = Ticker(
        symbol=instrument.symbol, last_price=200.0, volume_24h=1000.0,
        change_pct_24h=0.0, provider="bitget",
    )
    factory = MagicMock(return_value=provider)
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", factory)
    monkeypatch.setattr(
        ccxt_provider, "_native_provider_factory",
        MagicMock(side_effect=AssertionError("direct provider construction")),
    )

    result = asyncio.run(refresh_watched_prices())

    factory.assert_called_once_with(
        exchange="bitget", native_ccxt=True, fallback_exchange="bitget",
    )
    assert result["refreshed"] == 1
    assert result["errors"] == []
    provider.close.assert_awaited_once()
    assert _instrument_price_cache[("bitget", instrument.symbol, "spot")]["price"] == 200.0


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

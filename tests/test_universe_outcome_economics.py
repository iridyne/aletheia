"""Focused tests for market-aware, cost-aware forward outcomes."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


def test_short_perp_funding_credit_and_directional_path():
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    result = estimate_outcome_economics(
        entry_price=100.0,
        exit_price=96.0,
        direction="short",
        market_type="perp",
        hold_seconds=8 * 3600,
        entry_spread_bps=8.0,
        fallback_spread_bps=25.0,
        funding_rate_at_entry=0.0008,
        execution_fee_pct=0.06,
        slippage_bps=5.0,
        range_high=102.0,
        range_low=94.0,
        price_quality_ok=True,
    )

    assert result["gross_return_pct"] == pytest.approx(-4.0)
    assert result["directional_return_pct"] == pytest.approx(4.0)
    assert result["estimated_fee_pct"] == pytest.approx(0.12)
    assert result["estimated_spread_slippage_pct"] == pytest.approx(0.18)
    assert result["estimated_funding_pct"] == pytest.approx(-0.08)
    assert result["net_return_pct"] == pytest.approx(3.78)
    assert result["mfe_pct"] == pytest.approx(6.0)
    assert result["mae_pct"] == pytest.approx(-2.0)


def test_late_price_and_unknown_direction_do_not_create_net_result():
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    late = estimate_outcome_economics(
        entry_price=100.0, exit_price=105.0, direction="long", market_type="spot",
        hold_seconds=3600, entry_spread_bps=5.0, fallback_spread_bps=25.0,
        funding_rate_at_entry=None, execution_fee_pct=0.06, slippage_bps=5.0,
        price_quality_ok=False,
    )
    unknown_side = estimate_outcome_economics(
        entry_price=100.0, exit_price=105.0, direction=None, market_type="spot",
        hold_seconds=3600, entry_spread_bps=5.0, fallback_spread_bps=25.0,
        funding_rate_at_entry=None, execution_fee_pct=0.06, slippage_bps=5.0,
        price_quality_ok=True,
    )

    assert late["gross_return_pct"] == pytest.approx(5.0)
    assert late["net_return_pct"] is None
    assert unknown_side["directional_return_pct"] is None
    assert unknown_side["net_return_pct"] is None


def test_register_pending_carries_point_in_time_cost_inputs(monkeypatch):
    from aletheia.universe.outcome_recorder import register_pending

    captured = []
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.insert_forward_outcomes_batch",
        lambda rows: captured.extend(rows),
    )

    count = register_pending([{
        "id": "member-1",
        "snapshot_id": "snapshot-1",
        "symbol": "BTC/USDT",
        "venue": "okx",
        "market_type": "perp",
        "scanner_family": "momentum",
        "direction": "short",
        "entry_price_usd": 100.0,
        "spread_bps": 7.0,
        "funding_rate": 0.0005,
    }], entry_ts="2026-07-10T00:00:00+00:00")

    assert count == 4
    assert {row.market_type for row in captured} == {"perp"}
    assert {row.venue for row in captured} == {"okx"}
    assert {row.entry_spread_bps for row in captured} == {7.0}
    assert {row.funding_rate_at_entry for row in captured} == {0.0005}


def test_perp_provider_fallback_never_downgrades_to_spot(monkeypatch):
    from aletheia.data.providers import Ticker
    from aletheia.data.sources.chain import FallbackChainProvider
    from aletheia.data.symbols import InstrumentId

    class Primary:
        async def get_ticker(self, symbol):
            raise RuntimeError("perp unavailable")

        async def close(self):
            pass

    class SpotFallback:
        def __init__(self):
            self.called = False

        async def get_ticker(self, symbol):
            self.called = True
            return Ticker("BTC/USDT", 99.0, 1.0, 0.0)

        async def close(self):
            pass

    fallback = SpotFallback()
    provider = FallbackChainProvider([Primary(), fallback])
    instrument = InstrumentId.from_parts("okx", "BTC/USDT", "perp")

    with pytest.raises(RuntimeError, match="perp unavailable"):
        asyncio.run(provider.get_ticker(instrument))
    assert fallback.called is False


def test_settle_due_uses_exact_instrument_and_horizon_candles(monkeypatch):
    from aletheia.data.providers import Candle
    from aletheia.universe.outcome_recorder import settle_due

    now = datetime.now(timezone.utc)
    entry = now - timedelta(minutes=65)
    due = entry + timedelta(hours=1)
    seen = []
    settled = []

    class Provider:
        async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
            seen.append((instrument, timeframe, limit, since))
            return [
                Candle(int(entry.timestamp() * 1000), 100, 101, 99, 100.5, 10),
                Candle(int((due - timedelta(minutes=1)).timestamp() * 1000), 100.5, 103, 100, 102, 12),
                # This bar opens at due_at and closes afterwards. Using it would
                # leak future information into the fixed-horizon result.
                Candle(int(due.timestamp() * 1000), 102, 200, 1, 190, 99),
            ]

        async def get_ticker(self, instrument):
            raise AssertionError("horizon OHLCV should avoid current-ticker settlement")

        async def close(self):
            pass

    row = {
        "id": "outcome-exact-perp",
        "member_id": "member-exact-perp",
        "snapshot_id": "snapshot-exact-perp",
        "symbol": "BTC/USDT",
        "venue": "okx",
        "market_type": "perp",
        "scanner_family": "momentum",
        "direction": "long",
        "horizon": "1h",
        "entry_price_usd": 100.0,
        "entry_spread_bps": 8.0,
        "funding_rate_at_entry": 0.0008,
        "entry_ts": entry.isoformat(),
        "due_at": due.isoformat(),
        "status": "pending",
    }
    monkeypatch.setattr("aletheia.data.ccxt_provider.get_market_data_provider", lambda: Provider())
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.list_pending_forward_outcomes",
        lambda **_: [row],
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.settle_forward_outcome",
        lambda **kwargs: settled.append(kwargs),
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.calibration_stats",
        lambda: [],
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._maybe_generate_guard_candidates",
        lambda rows: None,
    )

    assert asyncio.run(settle_due(max_rows=1)) == 1
    instrument, timeframe, _, _ = seen[0]
    assert instrument.exchange == "okx"
    assert instrument.symbol == "BTC/USDT"
    assert instrument.market_type == "perp"
    assert timeframe == "1m"
    assert settled[0]["settlement_price_source"] == "horizon_ohlcv"
    assert settled[0]["economics"]["gross_return_pct"] == pytest.approx(2.0)
    assert settled[0]["economics"]["net_return_pct"] == pytest.approx(1.69)
    assert settled[0]["economics"]["mfe_pct"] == pytest.approx(3.0)
    assert settled[0]["economics"]["mae_pct"] == pytest.approx(-1.0)


def test_migration_backfills_identity_without_inventing_historical_net():
    from aletheia.storage.migrations import _backfill_forward_outcome_instrument_identity
    from aletheia.storage.sqlite import get_db

    db = get_db()
    entry = "2026-07-10T00:00:00+00:00"
    db.execute(
        "INSERT INTO universe_registry (id, venue, market_type, symbol) "
        "VALUES ('asset-backfill', 'okx', 'perp', 'BTC/USDT')"
    )
    db.execute(
        "INSERT INTO universe_snapshots (id, market_type, expires_at) "
        "VALUES ('snapshot-backfill', 'crypto', '2026-07-11T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO universe_members "
        "(id, snapshot_id, asset_id, symbol, market_type, rank, total_score, spread_bps, expires_at) "
        "VALUES ('member-backfill', 'snapshot-backfill', 'asset-backfill', 'BTC/USDT', "
        "'perp', 1, 80, 6, '2026-07-11T00:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO universe_feature_snapshots (id, asset_id, ts, funding_rate) "
        "VALUES ('feature-backfill', 'asset-backfill', '2026-07-09T23:59:00+00:00', 0.0004)"
    )
    db.execute(
        "INSERT INTO universe_forward_outcomes "
        "(id, member_id, snapshot_id, symbol, market_type, direction, horizon, entry_price_usd, "
        "entry_ts, due_at, settled_at, exit_price_usd, forward_return_pct, status) "
        "VALUES ('outcome-backfill', 'member-backfill', 'snapshot-backfill', 'BTC/USDT', "
        "'spot', 'long', '1h', 100, ?, '2026-07-10T01:00:00+00:00', "
        "'2026-07-10T01:00:00+00:00', 101, 1, 'settled')",
        (entry,),
    )

    _backfill_forward_outcome_instrument_identity(db)
    row = db.execute(
        "SELECT * FROM universe_forward_outcomes WHERE id='outcome-backfill'"
    ).fetchone()

    assert row["venue"] == "okx"
    assert row["market_type"] == "perp"
    assert row["entry_spread_bps"] == 6.0
    assert row["funding_rate_at_entry"] == pytest.approx(0.0004)
    assert row["net_return_pct"] is None


def test_derivative_ohlcv_persists_with_market_identity(monkeypatch):
    from aletheia.data.ccxt_provider import CCXTProvider
    from aletheia.data.providers import Candle

    calls = []
    monkeypatch.setattr(
        "aletheia.storage.repositories.upsert_ohlcv_candles",
        lambda **kwargs: calls.append(kwargs),
    )
    candles = [Candle(1, 100, 101, 99, 100.5, 10)]

    CCXTProvider._persist("BTC/USDT", "1h", candles, "okx", market_type="perp")
    assert len(calls) == 1
    assert calls[0]["market_type"] == "perp"
    CCXTProvider._persist("BTC/USDT", "1h", candles, "okx", market_type="spot")
    assert len(calls) == 2
    assert calls[1]["market_type"] == "spot"


def test_forming_ohlcv_is_not_visible_to_db_consumers_and_closed_bar_is_persisted():
    from aletheia.data.ccxt_provider import CCXTProvider
    from aletheia.data.providers import Candle
    from aletheia.storage.repositories import get_ohlcv_candles

    closed = Candle(1_000, 100, 101, 99, 100.5, 10)
    forming = Candle(2_000, 100.5, 102, 100, 101.5, 8, is_closed=False)

    CCXTProvider._persist(
        "BTC/USDT", "1h", [closed, forming], "okx", market_type="perp"
    )

    assert get_ohlcv_candles(
        "BTC/USDT", "1h", exchange="okx", market_type="perp"
    ) == [
        {
            "ts": 1_000,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
            "provider": "ccxt",
            "exchange": "okx",
            "market_type": "perp",
        }
    ]
    assert get_ohlcv_candles(
        "BTC/USDT", "1h", exchange="okx", market_type="spot"
    ) == []

    forming.is_closed = True
    CCXTProvider._persist(
        "BTC/USDT", "1h", [forming], "okx", market_type="perp"
    )

    assert [row["ts"] for row in get_ohlcv_candles(
        "BTC/USDT", "1h", exchange="okx", market_type="perp"
    )] == [1_000, 2_000]


# --- local-store-first horizon observation -----------------------------------
#
# The settler is the only writer of the 1m/5m/15m timeframes it reads back, so a
# local hit must reproduce the network answer exactly. The integration tests below
# assert the SAME economics as
# test_settle_due_uses_exact_instrument_and_horizon_candles above; that numeric
# equality is what proves the two paths cannot drift apart.


def test_expected_bar_count_handles_unaligned_entry():
    from aletheia.universe.outcome_recorder import _expected_bar_count

    # Aligned entry: every bar from entry up to due-1bar is fully inside.
    assert _expected_bar_count(entry_ms=0, due_ms=3_600_000, bar_ms=60_000) == 60
    # Unaligned entry loses the partial leading bar -> 59, not 60.
    assert _expected_bar_count(entry_ms=153_000, due_ms=3_753_000, bar_ms=60_000) == 59
    assert _expected_bar_count(entry_ms=1_000, due_ms=1_000, bar_ms=60_000) == 0
    assert _expected_bar_count(entry_ms=0, due_ms=3_600_000, bar_ms=0) == 0


def test_window_observation_excludes_the_bar_opening_at_due_at():
    from aletheia.universe.outcome_recorder import _window_observation

    window = _window_observation(
        [
            (0, 101, 99, 100.5),
            (60_000, 103, 100, 102),
            # Opens exactly at due_at: using it would leak future information.
            (120_000, 200, 1, 190),
        ],
        entry_ms=0,
        due_ms=120_000,
        bar_ms=60_000,
    )
    assert window == {
        "exit_price": 102.0,
        "range_high": 103.0,
        "range_low": 99.0,
        "bar_count": 2,
    }


def test_window_observation_rejects_a_stale_final_bar():
    from aletheia.universe.outcome_recorder import _window_observation

    # The last eligible bar closes ten bars before due_at. A halted or delisted
    # symbol must not settle off it.
    assert (
        _window_observation(
            [(0, 101, 99, 100.5)], entry_ms=0, due_ms=660_000, bar_ms=60_000
        )
        is None
    )


def test_window_observation_rejects_an_unsupported_timeframe():
    from aletheia.universe.outcome_recorder import _window_observation

    # timeframe_ms() returns 0 for an unknown timeframe; degenerating the
    # predicate would admit the bar opening at due_at.
    assert (
        _window_observation(
            [(0, 101, 99, 100.5), (120_000, 200, 1, 190)],
            entry_ms=0,
            due_ms=120_000,
            bar_ms=0,
        )
        is None
    )


def _aligned_1h_window():
    """An aligned, already-past one-hour window. Exactly 60 expected 1m bars."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    due_ms = (now_ms // 60_000) * 60_000 - 60_000
    entry_ms = due_ms - 3_600_000
    return (
        entry_ms,
        due_ms,
        datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc),
        datetime.fromtimestamp(due_ms / 1000, tz=timezone.utc),
    )


def _forward_row(entry, due, *, venue="okx"):
    return {
        "id": "outcome-local-perp",
        "member_id": "member-local-perp",
        "snapshot_id": "snapshot-local-perp",
        "symbol": "BTC/USDT",
        "venue": venue,
        "market_type": "perp",
        "scanner_family": "momentum",
        "direction": "long",
        "horizon": "1h",
        "entry_price_usd": 100.0,
        "entry_spread_bps": 8.0,
        "funding_rate_at_entry": 0.0008,
        "entry_ts": entry.isoformat(),
        "due_at": due.isoformat(),
        "status": "pending",
    }


def _install_settle_stubs(monkeypatch, row, provider):
    settled = []
    monkeypatch.setattr(
        "aletheia.data.ccxt_provider.get_market_data_provider", lambda: provider
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.list_pending_forward_outcomes",
        lambda **_: [row],
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.settle_forward_outcome",
        lambda **kwargs: settled.append(kwargs),
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.calibration_stats", lambda: []
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._maybe_generate_guard_candidates",
        lambda rows: None,
    )
    return settled


class _RefusingProvider:
    """Any call means the local window was not consulted, or not trusted."""

    async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
        raise AssertionError("a complete local window must not hit the network")

    async def get_ticker(self, instrument):
        raise AssertionError("a complete local window must not hit the ticker")

    async def close(self):
        pass


class _RecordingProvider:
    """The pre-existing network answer, with a wider range than any local seed."""

    def __init__(self, entry_ms, due_ms):
        from aletheia.data.providers import Candle

        self.calls = []
        self._candles = [
            Candle(entry_ms, 100, 101, 99, 100.5, 10),
            Candle(due_ms - 60_000, 100.5, 103, 100, 102, 12),
            Candle(due_ms, 102, 200, 1, 190, 99),
        ]

    async def get_ohlcv(self, instrument, timeframe="1h", limit=100, since=None):
        self.calls.append((instrument, timeframe, limit, since))
        return list(self._candles)

    async def get_ticker(self, instrument):
        raise AssertionError("horizon OHLCV should avoid current-ticker settlement")

    async def close(self):
        pass


def _seed_local_1m(entry_ms, count, *, skip=(), exchange="okx", extra=()):
    from aletheia.data.providers import Candle
    from aletheia.storage.repositories import upsert_ohlcv_candles

    skipped = set(skip)
    candles = [
        Candle(entry_ms + i * 60_000, 100, 101, 99, 100.5, 10)
        for i in range(count)
        if i not in skipped
    ]
    if count - 1 not in skipped:
        # Final bar carries the window's extremes and the exit close.
        candles[-1] = Candle(entry_ms + (count - 1) * 60_000, 100.5, 103, 100, 102, 12)
    candles.extend(extra)
    upsert_ohlcv_candles(
        "BTC/USDT", "1m", candles, exchange=exchange, market_type="perp"
    )


def test_complete_local_window_settles_without_any_network_call(monkeypatch):
    from aletheia.universe.outcome_recorder import settle_due

    entry_ms, _due_ms, entry, due = _aligned_1h_window()
    _seed_local_1m(entry_ms, 60)
    settled = _install_settle_stubs(
        monkeypatch, _forward_row(entry, due), _RefusingProvider()
    )

    assert asyncio.run(settle_due(max_rows=1)) == 1
    assert settled[0]["settlement_price_source"] == "horizon_ohlcv_local"
    assert settled[0]["economics"]["gross_return_pct"] == pytest.approx(2.0)
    assert settled[0]["economics"]["net_return_pct"] == pytest.approx(1.69)
    assert settled[0]["economics"]["mfe_pct"] == pytest.approx(3.0)
    assert settled[0]["economics"]["mae_pct"] == pytest.approx(-1.0)


def test_local_window_ignores_the_bar_opening_at_due_at(monkeypatch):
    from aletheia.data.providers import Candle
    from aletheia.universe.outcome_recorder import settle_due

    entry_ms, due_ms, entry, due = _aligned_1h_window()
    _seed_local_1m(entry_ms, 60, extra=[Candle(due_ms, 102, 200, 1, 190, 99)])
    settled = _install_settle_stubs(
        monkeypatch, _forward_row(entry, due), _RefusingProvider()
    )

    assert asyncio.run(settle_due(max_rows=1)) == 1
    assert settled[0]["settlement_price_source"] == "horizon_ohlcv_local"
    assert settled[0]["economics"]["mfe_pct"] == pytest.approx(3.0)
    assert settled[0]["economics"]["mae_pct"] == pytest.approx(-1.0)


def test_local_window_with_a_mid_window_hole_falls_back_to_the_exchange(monkeypatch):
    from aletheia.universe.outcome_recorder import settle_due

    entry_ms, due_ms, entry, due = _aligned_1h_window()
    # One missing bar. Settling off the remaining 59 would mint a real
    # net_return_pct beside a silently narrowed mfe/mae.
    _seed_local_1m(entry_ms, 60, skip={30})
    provider = _RecordingProvider(entry_ms, due_ms)
    settled = _install_settle_stubs(monkeypatch, _forward_row(entry, due), provider)

    assert asyncio.run(settle_due(max_rows=1)) == 1
    assert provider.calls, "an incomplete local window must fall through"
    assert settled[0]["settlement_price_source"] == "horizon_ohlcv"


def test_a_second_concurrent_drain_yields_instead_of_duplicating_requests(monkeypatch):
    """Two scheduler jobs call settle_due(); only one may drain at a time.

    ``outcome_settler`` and ``universe_refresh`` fire on the same 10-minute
    interval 30s apart, and ``max_instances=1`` bounds each job against itself but
    not against the other. Both selected the same oldest-due rows, so they
    duplicated one another's exchange requests: double the external load for half
    the useful work.
    """
    from aletheia.universe import outcome_recorder as orec

    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def slow_drain(*, max_rows, time_budget_sec):
        calls.append(max_rows)
        started.set()
        await release.wait()
        return 7

    monkeypatch.setattr(orec, "_settle_due_once", slow_drain)

    async def scenario():
        first = asyncio.create_task(orec.settle_due(max_rows=8))
        await started.wait()
        second = await orec.settle_due(max_rows=8)
        release.set()
        return await first, second

    first, second = asyncio.run(scenario())
    assert first == 7
    assert second == 0, "the second caller must yield, not queue a duplicate drain"
    assert calls == [8], "the drain body must have run exactly once"
    assert orec._settle_in_flight is False, "the flag must clear even on the happy path"


def test_the_in_flight_flag_clears_when_a_drain_raises(monkeypatch):
    """A flag that leaks on failure would silence settlement permanently.

    The process is long-lived, so a single raise inside the drain must not leave
    every later cycle yielding to a caller that is no longer running.
    """
    from aletheia.universe import outcome_recorder as orec

    async def boom(*, max_rows, time_budget_sec):
        raise RuntimeError("drain failed")

    monkeypatch.setattr(orec, "_settle_due_once", boom)

    with pytest.raises(RuntimeError):
        asyncio.run(orec.settle_due(max_rows=8))
    assert orec._settle_in_flight is False


def test_local_read_is_scoped_to_the_exact_instrument(monkeypatch):
    from aletheia.universe.outcome_recorder import settle_due

    entry_ms, due_ms, entry, due = _aligned_1h_window()
    # A complete window, but on another venue. Settling an okx row from binance
    # bars is the cross-venue contamination the 10x ratio guard exists to catch.
    _seed_local_1m(entry_ms, 60, exchange="binance")
    provider = _RecordingProvider(entry_ms, due_ms)
    settled = _install_settle_stubs(
        monkeypatch, _forward_row(entry, due, venue="okx"), provider
    )

    assert asyncio.run(settle_due(max_rows=1)) == 1
    assert provider.calls and provider.calls[0][0].exchange == "okx"
    assert settled[0]["settlement_price_source"] == "horizon_ohlcv"

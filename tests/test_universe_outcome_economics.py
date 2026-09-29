"""Focused tests for market-aware, cost-aware forward outcomes."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.usefixtures("installed_learning_consumers")


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


def test_decision_grade_settlement_writers_require_price_provenance():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    common = {
        "symbol": "BTC/USDT",
        "venue": "okx",
        "market_type": "spot",
        "direction": "long",
        "horizon": "1h",
        "entry_price_usd": 100.0,
        "entry_ts": "2026-07-10T00:00:00+00:00",
        "due_at": "2026-07-10T01:00:00+00:00",
    }
    filtered = FilterCandidateOutcome(outcome_candidate_key="writer-filter", **common)
    gross_only = FilterCandidateOutcome(outcome_candidate_key="writer-gross-only", **common)
    repo.insert_filter_candidate_outcomes_batch([filtered, gross_only])
    economics = estimate_outcome_economics(
        entry_price=100.0, exit_price=101.0, direction="long", market_type="spot",
        hold_seconds=3600, entry_spread_bps=0.0, fallback_spread_bps=0.0,
        funding_rate_at_entry=None, execution_fee_pct=0.0, slippage_bps=0.0,
        price_quality_ok=True,
    )

    with pytest.raises(ValueError, match="decision-grade price provenance"):
        repo.settle_filter_candidate_outcome(
            filtered.id, exit_price=101.0, settled_at="2026-07-10T01:00:00+00:00",
            economics=economics, settlement_price_source="late_ticker", settlement_lag_sec=3600.0,
        )
    assert repo._select_one("filter_candidate_outcomes", filtered.id)["status"] == "pending"

    late_economics = {**economics, "net_return_pct": None}
    repo.settle_filter_candidate_outcome(
        gross_only.id, exit_price=101.0, settled_at="2026-07-10T02:00:00+00:00",
        economics=late_economics, settlement_price_source="late_ticker", settlement_lag_sec=3600.0,
    )
    stored = repo._select_one("filter_candidate_outcomes", gross_only.id)
    assert stored["status"] == "settled"
    assert stored["gross_return_pct"] == pytest.approx(1.0)
    assert stored["net_return_pct"] is None


def test_terminal_filter_settlement_is_first_writer_wins():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome
    from aletheia.universe.outcome_economics import estimate_outcome_economics

    common = {
        "outcome_candidate_key": "opportunity:cas-filter",
        "venue": "okx",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "horizon": "24h",
        "entry_price_usd": 100.0,
        "entry_ts": "2026-07-10T00:00:00+00:00",
        "due_at": "2026-07-11T00:00:00+00:00",
    }
    outcome = FilterCandidateOutcome(**common)
    repo.insert_filter_candidate_outcomes_batch([outcome])
    economics = estimate_outcome_economics(
        entry_price=100.0,
        exit_price=101.0,
        direction="long",
        market_type="spot",
        hold_seconds=86400,
        entry_spread_bps=0.0,
        fallback_spread_bps=0.0,
        funding_rate_at_entry=None,
        execution_fee_pct=0.0,
        slippage_bps=0.0,
        price_quality_ok=True,
    )

    assert repo.settle_filter_candidate_outcome(
        outcome.id,
        exit_price=101.0,
        settled_at="2026-07-11T00:00:00+00:00",
        economics=economics,
        settlement_price_source="horizon_ohlcv",
        settlement_lag_sec=0.0,
    ) is True
    first = repo._select_one("filter_candidate_outcomes", outcome.id)

    alternate = estimate_outcome_economics(
        entry_price=100.0,
        exit_price=99.0,
        direction="long",
        market_type="spot",
        hold_seconds=86400,
        entry_spread_bps=0.0,
        fallback_spread_bps=0.0,
        funding_rate_at_entry=None,
        execution_fee_pct=0.0,
        slippage_bps=0.0,
        price_quality_ok=True,
    )
    assert repo.settle_filter_candidate_outcome(
        outcome.id,
        exit_price=99.0,
        settled_at="2026-07-11T00:01:00+00:00",
        economics=alternate,
        settlement_price_source="horizon_ohlcv",
        settlement_lag_sec=0.0,
    ) is False
    second = repo._select_one("filter_candidate_outcomes", outcome.id)
    assert second["exit_price_usd"] == first["exit_price_usd"]
    assert second["settled_at"] == first["settled_at"]


def test_pending_filter_outcome_writer_and_settler_require_exact_venue_atomically():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome
    from aletheia.storage.sqlite import get_db

    common = {
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "direction": "long",
        "horizon": "1h",
        "entry_price_usd": 100.0,
        "entry_ts": "2026-07-10T00:00:00+00:00",
        "due_at": "2026-07-10T01:00:00+00:00",
    }
    valid = FilterCandidateOutcome(
        outcome_candidate_key="filter-valid",
        venue=" okx ",
        **common,
    )
    invalid = FilterCandidateOutcome(
        outcome_candidate_key="filter-invalid",
        venue=" \t",
        **common,
    )

    with pytest.raises(ValueError, match="pending filter outcome requires a nonblank venue"):
        repo.insert_filter_candidate_outcomes_batch([valid, invalid])
    assert get_db().execute(
        "SELECT COUNT(*) FROM filter_candidate_outcomes WHERE id IN (?, ?)",
        (valid.id, invalid.id),
    ).fetchone()[0] == 0
    assert repo.insert_filter_candidate_outcomes_batch([valid]) == 1
    assert repo._select_one("filter_candidate_outcomes", valid.id)["venue"] == "okx"

    legacy = invalid.model_copy(update={"status": "settled"})
    repo.insert_filter_candidate_outcomes_batch([legacy])
    get_db().execute(
        "UPDATE filter_candidate_outcomes SET status='pending' WHERE id=?",
        (legacy.id,),
    )
    with pytest.raises(ValueError, match="filter outcome requires a nonblank venue"):
        repo.settle_filter_candidate_outcome(
            legacy.id,
            exit_price=101.0,
            settled_at="2026-07-10T01:00:00+00:00",
            economics={},
            settlement_price_source="test",
            settlement_lag_sec=0.0,
        )
    assert repo.count_pending_filter_candidate_outcomes_missing_venue(
        "2026-07-11T00:00:00+00:00"
    ) == 1
    assert legacy.id not in {
        row["id"]
        for row in repo.list_pending_filter_candidate_outcomes(
            "2026-07-11T00:00:00+00:00"
        )
    }
    priority_rows, priority_truncated = (
        repo.list_pending_filter_candidate_outcomes_for_keys(
            "2026-07-11T00:00:00+00:00",
            {"filter-invalid:1h"},
        )
    )
    assert priority_rows == []
    assert priority_truncated is False
    assert repo._select_one("filter_candidate_outcomes", legacy.id)["status"] == "pending"


def test_perp_provider_fallback_never_downgrades_to_spot(monkeypatch):
    from aletheia.data.providers import Ticker
    from extensions.data.sources.chain import FallbackChainProvider
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


def test_observation_provider_is_pinned_per_persisted_venue(monkeypatch):
    from aletheia.data.providers import Ticker
    from aletheia.universe.outcome_recorder import _batch_fetch_market_observations

    now = datetime.now(timezone.utc)
    entry = now - timedelta(hours=1)
    factories = []
    calls = []

    class Provider:
        def __init__(self, venue):
            self.venue = venue

        async def get_ohlcv(self, instrument, **kwargs):
            calls.append((self.venue, "ohlcv", instrument.exchange))
            assert instrument.exchange == self.venue
            return []

        async def get_ticker(self, instrument):
            calls.append((self.venue, "ticker", instrument.exchange))
            assert instrument.exchange == self.venue
            return Ticker(
                instrument.symbol,
                101.0 if self.venue == "bitget" else 99.0,
                1.0,
                0.0,
                provider=self.venue,
            )

        async def close(self):
            pass

    def provider_factory(exchange, *, native_ccxt, fallback_exchange):
        factories.append((exchange, native_ccxt, fallback_exchange))
        return Provider(exchange)

    rows = [
        {
            "id": f"same-symbol-{venue}",
            "symbol": "BTC/USDT",
            "venue": venue,
            "market_type": "perp",
            "horizon": "1h",
            "entry_ts": entry.isoformat(),
            "due_at": now.isoformat(),
        }
        for venue in ("bitget", "binance")
    ]
    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider", provider_factory
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._local_observation",
        lambda plan: (None, "missing"),
    )

    observations = asyncio.run(
        _batch_fetch_market_observations(rows, now=now)
    )

    assert set(factories) == {
        ("bitget", True, "bitget"),
        ("binance", True, "binance"),
    }
    assert set(calls) == {
        ("bitget", "ohlcv", "bitget"),
        ("bitget", "ticker", "bitget"),
        ("binance", "ohlcv", "binance"),
        ("binance", "ticker", "binance"),
    }
    assert observations["same-symbol-bitget"]["exit_price"] == 101.0
    assert observations["same-symbol-binance"]["exit_price"] == 99.0


def test_partial_exact_provider_construction_closes_created_provider(monkeypatch):
    from aletheia.universe.outcome_recorder import _batch_fetch_market_observations

    now = datetime.now(timezone.utc)
    closed = []

    class Provider:
        async def close(self):
            closed.append("bitget")

    def provider_factory(exchange, **_kwargs):
        if exchange == "okx":
            raise RuntimeError("provider unavailable")
        return Provider()

    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider", provider_factory
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(_batch_fetch_market_observations([
            {
                "id": f"provider-{venue}",
                "symbol": "BTC/USDT",
                "venue": venue,
                "market_type": "perp",
                "horizon": "1h",
                "entry_ts": (now - timedelta(hours=1)).isoformat(),
                "due_at": now.isoformat(),
            }
            for venue in ("bitget", "okx")
        ], now=now))

    assert closed == ["bitget"]


def test_ticker_provider_mismatch_is_not_observation_evidence(monkeypatch):
    from aletheia.data.providers import Ticker
    from aletheia.universe.outcome_recorder import (
        _batch_fetch_market_observations,
        is_deferred,
    )

    now = datetime.now(timezone.utc)

    class Provider:
        async def get_ohlcv(self, instrument, **_kwargs):
            return []

        async def get_ticker(self, instrument):
            return Ticker(instrument.symbol, 101.0, 1.0, 0.0, provider="okx")

        async def close(self):
            pass

    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: Provider(),
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._local_observation",
        lambda plan: (None, "missing"),
    )
    row = {
        "id": "ticker-provider-mismatch",
        "symbol": "BTC/USDT",
        "venue": "bitget",
        "market_type": "perp",
        "horizon": "1h",
        "entry_ts": (now - timedelta(hours=1)).isoformat(),
        "due_at": now.isoformat(),
    }

    observation = asyncio.run(
        _batch_fetch_market_observations([row], now=now)
    )[row["id"]]

    assert is_deferred(observation)
    assert observation["defer_reason"] == "ticker:ValueError"


def test_provider_close_failure_does_not_skip_other_cleanup(monkeypatch):
    from aletheia.universe.outcome_recorder import _batch_fetch_market_observations

    now = datetime.now(timezone.utc)
    closed = []

    class Provider:
        def __init__(self, venue):
            self.venue = venue

        async def close(self):
            closed.append(self.venue)
            if self.venue == "bitget":
                raise RuntimeError("close failed")

    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda exchange, **_kwargs: Provider(exchange),
    )
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder._local_observation",
        lambda plan: ({
            "exit_price": 101.0,
            "range_high": 101.0,
            "range_low": 100.0,
            "price_source": "horizon_ohlcv_local",
            "price_quality_ok": True,
            "settlement_lag_sec": 0.0,
        }, "complete"),
    )
    rows = [
        {
            "id": f"close-{venue}",
            "symbol": "BTC/USDT",
            "venue": venue,
            "market_type": "perp",
            "horizon": "1h",
            "entry_ts": (now - timedelta(hours=1)).isoformat(),
            "due_at": now.isoformat(),
        }
        for venue in ("bitget", "okx")
    ]

    observations = asyncio.run(_batch_fetch_market_observations(rows, now=now))

    assert set(observations) == {"close-bitget", "close-okx"}
    assert closed == ["bitget", "okx"]


def test_derivative_ohlcv_persists_with_market_identity(monkeypatch):
    from extensions.data.ccxt_provider import CCXTProvider
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
    from extensions.data.ccxt_provider import CCXTProvider
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

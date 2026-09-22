"""Focused tests for market-aware, cost-aware forward outcomes."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


@pytest.mark.parametrize("missing", [
    "extensions", "extensions.knowledge", "extensions.knowledge.manager", "broken_knowledge_dependency",
])
def test_optional_guard_import_does_not_interrupt_settlement(monkeypatch, caplog, missing):
    import builtins
    from unittest.mock import AsyncMock

    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome
    from aletheia.storage.sqlite import get_db
    from aletheia.universe import outcome_recorder

    now = datetime.now(timezone.utc)
    row = UniverseForwardOutcome(
        member_id="optional-guard-member", snapshot_id="optional-guard-snapshot",
        symbol="BTC/USDT", venue="okx", market_type="spot", direction="long",
        scanner_family="momentum", horizon="4h", entry_price_usd=100.0,
        entry_ts=(now - timedelta(hours=5)).isoformat(),
        due_at=(now - timedelta(hours=1)).isoformat(),
    )
    repo.insert_forward_outcome(row)
    monkeypatch.setattr(outcome_recorder, "_batch_fetch_market_observations", AsyncMock(
        return_value={row.id: {
            "exit_price": 95.0, "range_high": 101.0, "range_low": 94.0,
            "price_source": "horizon_ohlcv", "price_quality_ok": True,
            "settlement_lag_sec": 0,
        }},
    ))
    shadow = AsyncMock(return_value=2)
    monkeypatch.setattr(outcome_recorder, "_structure_shadow_settler", shadow)
    real_import = builtins.__import__

    def absent(name, *args, **kwargs):
        if name == "extensions.knowledge.manager":
            raise ModuleNotFoundError(name=missing)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", absent)
    if missing == "broken_knowledge_dependency":
        with pytest.raises(ModuleNotFoundError) as exc:
            asyncio.run(outcome_recorder.settle_due(max_rows=1))
        assert exc.value.name == missing
        shadow.assert_not_awaited()
    else:
        assert asyncio.run(outcome_recorder.settle_due(max_rows=1)) == 3
        shadow.assert_awaited_once_with(limit=100)
        assert "guard candidates unavailable" in caplog.text

    persisted = get_db().execute(
        "SELECT status, net_return_pct FROM universe_forward_outcomes WHERE id = ?", (row.id,),
    ).fetchone()
    assert persisted["status"] == "settled"
    assert persisted["net_return_pct"] < -3.0  # qualifying 4h loss, not a filtered-out row
    assert outcome_recorder._settle_in_flight is False


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
    from aletheia.storage.models import (
        FilterCandidateOutcome,
        ShadowTrade,
        UniverseForwardOutcome,
    )
    from aletheia.storage.sqlite import get_db
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
    forward = UniverseForwardOutcome(
        member_id="writer-forward",
        snapshot_id="writer-snapshot",
        **common,
    )
    filtered = FilterCandidateOutcome(
        outcome_candidate_key="writer-filter",
        **common,
    )
    gross_only = FilterCandidateOutcome(
        outcome_candidate_key="writer-gross-only",
        **common,
    )
    repo.insert_forward_outcome(forward)
    repo.insert_filter_candidate_outcomes_batch([filtered, gross_only])
    economics = estimate_outcome_economics(
        entry_price=100.0,
        exit_price=101.0,
        direction="long",
        market_type="spot",
        hold_seconds=3600,
        entry_spread_bps=0.0,
        fallback_spread_bps=0.0,
        funding_rate_at_entry=None,
        execution_fee_pct=0.0,
        slippage_bps=0.0,
        price_quality_ok=True,
    )
    get_db().execute(
        "UPDATE universe_forward_outcomes SET settlement_price_source='horizon_ohlcv',"
        "settlement_lag_sec=0 WHERE id=?",
        (forward.id,),
    )

    with pytest.raises(ValueError, match="decision-grade price provenance"):
        repo.settle_forward_outcome(
            forward.id,
            exit_price=101.0,
            forward_return=1.0,
            settled_at="2026-07-10T01:00:00+00:00",
            economics=economics,
        )
    assert repo._select_one("universe_forward_outcomes", forward.id)["status"] == "pending"

    with pytest.raises(ValueError, match="decision-grade price provenance"):
        repo.settle_filter_candidate_outcome(
            filtered.id,
            exit_price=101.0,
            settled_at="2026-07-10T01:00:00+00:00",
            economics=economics,
            settlement_price_source="late_ticker",
            settlement_lag_sec=3600.0,
        )
    assert repo._select_one("filter_candidate_outcomes", filtered.id)["status"] == "pending"

    repo.settle_forward_outcome(
        forward.id,
        exit_price=101.0,
        forward_return=1.0,
        settled_at="2026-07-10T01:00:00+00:00",
        economics=economics,
        settlement_price_source="near_due_ticker",
        settlement_lag_sec=360.0,
    )
    assert repo._select_one("universe_forward_outcomes", forward.id)["status"] == "settled"

    late_economics = {**economics, "net_return_pct": None}
    repo.settle_filter_candidate_outcome(
        gross_only.id,
        exit_price=101.0,
        settled_at="2026-07-10T02:00:00+00:00",
        economics=late_economics,
        settlement_price_source="late_ticker",
        settlement_lag_sec=3600.0,
    )
    stored = repo._select_one("filter_candidate_outcomes", gross_only.id)
    assert stored["status"] == "settled"
    assert stored["gross_return_pct"] == pytest.approx(1.0)
    assert stored["net_return_pct"] is None

    shadow = ShadowTrade(
        opportunity_id="writer-filter",
        symbol="BTC/USDT",
        venue="okx",
        market_type="spot",
        direction="long",
        entry_price=100.0,
        created_at=common["entry_ts"],
        outcome_candidate_key="opportunity:writer-filter",
    )
    repo.insert_shadow_trade(shadow)
    assert repo.settle_linked_shadow_outcomes("opportunity:writer-filter") == 0
    assert repo._select_one("shadow_trades", shadow.id)["resolved_at"] is None


def test_terminal_filter_settlement_is_first_writer_wins_and_projects_shadow_atomically():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import FilterCandidateOutcome, ShadowTrade
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
    shadow = ShadowTrade(
        opportunity_id="cas-filter",
        symbol="BTC/USDT",
        venue="okx",
        market_type="spot",
        direction="long",
        entry_price=100.0,
        created_at=common["entry_ts"],
        outcome_candidate_key=common["outcome_candidate_key"],
    )
    repo.insert_filter_candidate_outcomes_batch([outcome])
    repo.insert_shadow_trade(shadow)
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
    assert repo._select_one("shadow_trades", shadow.id)["resolved_at"] == first["settled_at"]

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


def test_register_pending_skips_members_without_exact_venue(monkeypatch):
    from aletheia.universe.outcome_recorder import register_pending

    captured = []
    monkeypatch.setattr(
        "aletheia.universe.outcome_recorder.repo.insert_forward_outcomes_batch",
        lambda rows: captured.extend(rows),
    )
    common = {
        "snapshot_id": "snapshot-identity",
        "symbol": "BTC/USDT",
        "entry_price_usd": 100.0,
    }

    count = register_pending(
        [
            {**common, "id": "missing", "venue": None},
            {**common, "id": "blank", "venue": " \t"},
            {**common, "id": "exact", "venue": " okx "},
        ],
        entry_ts="2026-07-10T00:00:00+00:00",
    )

    assert count == 4
    assert {row.member_id for row in captured} == {"exact"}
    assert {row.venue for row in captured} == {"okx"}


def test_pending_forward_outcome_writer_rejects_missing_venue_atomically():
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import UniverseForwardOutcome
    from aletheia.storage.sqlite import get_db

    common = {
        "snapshot_id": "snapshot-writer-identity",
        "symbol": "BTC/USDT",
        "horizon": "1h",
        "entry_price_usd": 100.0,
        "due_at": "2026-07-10T01:00:00+00:00",
    }
    valid = UniverseForwardOutcome(member_id="valid", venue="okx", **common)
    invalid = UniverseForwardOutcome(member_id="invalid", venue=None, **common)

    with pytest.raises(ValueError, match="nonblank venue"):
        repo.insert_forward_outcomes_batch([valid, invalid])

    stored = get_db().execute(
        "SELECT id FROM universe_forward_outcomes WHERE id IN (?, ?)",
        (valid.id, invalid.id),
    ).fetchall()
    assert stored == []

    legacy = UniverseForwardOutcome(
        member_id="legacy", venue=None, status="settled", **common
    )
    repo.insert_forward_outcome(legacy)
    assert repo._select_one("universe_forward_outcomes", legacy.id)["venue"] is None
    get_db().execute(
        "UPDATE universe_forward_outcomes SET status='pending' WHERE id=?",
        (legacy.id,),
    )
    with pytest.raises(ValueError, match="forward outcome requires a nonblank venue"):
        repo.settle_forward_outcome(
            legacy.id,
            exit_price=101.0,
            forward_return=1.0,
            settled_at="2026-07-10T01:00:00+00:00",
        )
    assert repo._select_one("universe_forward_outcomes", legacy.id)["status"] == "pending"
    assert repo.count_pending_forward_outcomes_missing_venue(
        "2026-07-11T00:00:00+00:00"
    ) == 1
    assert legacy.id not in {
        row["id"]
        for row in repo.list_pending_forward_outcomes(
            "2026-07-11T00:00:00+00:00"
        )
    }


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


def test_settle_due_uses_exact_instrument_and_horizon_candles(monkeypatch):
    from aletheia.data.providers import Candle
    from aletheia.universe.outcome_recorder import settle_due

    now = datetime.now(timezone.utc)
    entry = now - timedelta(minutes=65)
    due = entry + timedelta(hours=1)
    seen = []
    settled = []
    factory_calls = []

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
    def provider_factory(exchange, *, native_ccxt, fallback_exchange):
        factory_calls.append((exchange, native_ccxt, fallback_exchange))
        return Provider()

    monkeypatch.setattr(
        "extensions.data.ccxt_provider.get_market_data_provider", provider_factory
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
    assert factory_calls == [("okx", True, "okx")]
    assert timeframe == "1m"
    assert settled[0]["settlement_price_source"] == "horizon_ohlcv"
    assert settled[0]["economics"]["gross_return_pct"] == pytest.approx(2.0)
    assert settled[0]["economics"]["net_return_pct"] == pytest.approx(1.69)
    assert settled[0]["economics"]["mfe_pct"] == pytest.approx(3.0)
    assert settled[0]["economics"]["mae_pct"] == pytest.approx(-1.0)


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
        "extensions.data.ccxt_provider.get_market_data_provider",
        lambda *args, **kwargs: provider,
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

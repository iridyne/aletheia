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

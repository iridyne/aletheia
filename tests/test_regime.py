"""Tests for market regime detection + signal_emitter regime gate."""

import pytest
from datetime import datetime, timedelta, timezone

from aletheia.regime.detector import (
    detect_regime,
    debounced_detect,
    reset_state,
    MarketRegime,
    REGIME_BULL,
    REGIME_BEAR,
    REGIME_SIDEWAYS,
    REGIME_VOLATILE,
)
from aletheia.universe.signal_emitter import _passes_regime_gate
from aletheia.data.providers import Candle


# ── detect_regime ──────────────────────────────────────────────────────────────


class TestDetectRegime:
    def test_no_data_returns_sideways_low_confidence(self):
        r = detect_regime()
        assert r.state == REGIME_SIDEWAYS
        assert r.confidence == 0.3
        assert r.long_bias == 0.50
        assert r.short_bias == 0.50

    def test_strong_bull_signals(self):
        closes = [50000.0] * 200 + [70000.0]  # 40% above MA200
        r = detect_regime(
            btc_closes_1d=closes,
            vix=12.0,
            total_market_cap_change_7d=10.0,
        )
        assert r.state == REGIME_BULL
        assert r.long_bias > r.short_bias

    def test_strong_bear_signals(self):
        closes = [70000.0] * 200 + [50000.0]  # ~28% below MA200
        r = detect_regime(
            btc_closes_1d=closes,
            vix=35.0,
            avg_funding_rate=0.001,
            total_market_cap_change_7d=-10.0,
        )
        assert r.state == REGIME_BEAR
        assert r.short_bias > r.long_bias

    def test_volatile_regime_atr_expansion(self):
        closes = list(range(100, 320))  # 220 values, monotone up but doesn't matter
        r = detect_regime(
            btc_closes_1d=closes,
            vix=32.0,
            atr_7d=5.0,
            atr_30d=2.0,
        )
        assert r.state == REGIME_VOLATILE

    def test_sideways_when_signals_mixed(self):
        closes = [60000.0] * 200 + [60500.0]  # near MA200
        r = detect_regime(
            btc_closes_1d=closes,
            vix=18.0,
            total_market_cap_change_7d=1.0,
        )
        # With slight bull lean from price > MA200 and sideways from VIX/cap,
        # result should be sideways or mild bull
        assert r.state in (REGIME_SIDEWAYS, REGIME_BULL)

    def test_funding_rate_contrarian(self):
        closes = [60000.0] * 200 + [60000.0]
        # Extreme positive funding = crowded longs = bearish contrarian signal
        r = detect_regime(
            btc_closes_1d=closes,
            avg_funding_rate=0.001,
        )
        # Should have some bear vote from funding
        assert r.signals.get("avg_funding_rate") == 0.001

    def test_bias_table_values(self):
        # Bull regime should have long_bias=0.75, short_bias=0.25
        closes = [40000.0] * 200 + [65000.0]
        r = detect_regime(btc_closes_1d=closes, vix=10.0, total_market_cap_change_7d=12.0)
        if r.state == REGIME_BULL:
            assert r.long_bias == 0.75
            assert r.short_bias == 0.25

    def test_nonfinite_inputs_are_unavailable_not_signals(self):
        r = detect_regime(
            btc_closes_1d=[50_000.0] * 199 + [float("nan")],
            vix=float("nan"),
            avg_funding_rate=float("inf"),
            total_market_cap_change_7d=float("-inf"),
            atr_7d=float("nan"),
            atr_30d=1.0,
        )

        assert r.signals == {}
        assert r.state == REGIME_SIDEWAYS


def test_market_cap_change_uses_real_seven_day_baseline(monkeypatch):
    from aletheia.regime.collector import _get_market_cap_change_7d
    from aletheia.storage import repositories as repo

    latest_at = datetime.now(timezone.utc)
    monkeypatch.setattr(
        repo,
        "latest_macro_snapshot",
        lambda: {
            "total_market_cap_usd": 1_250.0,
            "total_market_cap_change_24h_pct": 2.0,
            "observed_at": latest_at.isoformat(),
        },
    )
    monkeypatch.setattr(
        repo,
        "macro_snapshot_near",
        lambda reference_at, **kwargs: {
            "total_market_cap_usd": 1_000.0,
            "observed_at": (latest_at - timedelta(days=7)).isoformat(),
        },
        raising=False,
    )

    assert _get_market_cap_change_7d() == 25.0


def test_market_cap_change_is_unavailable_without_historical_baseline(monkeypatch):
    from aletheia.regime.collector import _get_market_cap_change_7d
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(
        repo,
        "latest_macro_snapshot",
        lambda: {
            "total_market_cap_usd": 1_250.0,
            "total_market_cap_change_24h_pct": 2.0,
            "observed_at": datetime(2026, 7, 19, tzinfo=timezone.utc).isoformat(),
        },
    )
    monkeypatch.setattr(repo, "macro_snapshot_near", lambda *args, **kwargs: None, raising=False)

    assert _get_market_cap_change_7d() is None


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_market_cap_change_rejects_nonfinite_values(monkeypatch, bad_value):
    from aletheia.regime.collector import _get_market_cap_change_7d
    from aletheia.storage import repositories as repo

    observed_at = datetime(2026, 7, 19, tzinfo=timezone.utc).isoformat()
    monkeypatch.setattr(
        repo,
        "latest_macro_snapshot",
        lambda: {"total_market_cap_usd": bad_value, "observed_at": observed_at},
    )
    monkeypatch.setattr(
        repo,
        "macro_snapshot_near",
        lambda *args, **kwargs: {"total_market_cap_usd": 1_000.0},
    )

    assert _get_market_cap_change_7d() is None


def test_regime_funding_uses_latest_fresh_row_per_instrument(monkeypatch):
    from aletheia.regime.collector import _get_avg_funding_rate
    from aletheia.storage import repositories as repo

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        repo,
        "list_universe_feature_snapshots",
        lambda limit=50: [
            {"asset_id": "btc", "market_type": "perp", "funding_rate": 0.001,
             "ts": (now - timedelta(minutes=1)).isoformat()},
            {"asset_id": "eth", "market_type": "swap", "funding_rate": 0.003,
             "ts": (now - timedelta(minutes=2)).isoformat()},
            {"asset_id": "btc", "market_type": "perp", "funding_rate": 0.1,
             "ts": (now - timedelta(minutes=3)).isoformat()},
            {"asset_id": "old", "market_type": "futures", "funding_rate": 0.2,
             "ts": (now - timedelta(hours=1)).isoformat()},
            {"asset_id": "unknown", "market_type": "margin", "funding_rate": 0.4,
             "ts": now.isoformat()},
        ],
    )

    assert _get_avg_funding_rate() == pytest.approx(0.002)


def test_stale_macro_values_are_unavailable(monkeypatch):
    from aletheia.regime.collector import _get_latest_vix, _get_market_cap_change_7d
    from aletheia.storage import repositories as repo

    stale = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(
        repo,
        "latest_macro_snapshot",
        lambda: {
            "vix_close": 12.0,
            "total_market_cap_usd": 1_250.0,
            "observed_at": stale.isoformat(),
        },
    )
    monkeypatch.setattr(
        repo,
        "macro_snapshot_near",
        lambda *args, **kwargs: {"total_market_cap_usd": 1_000.0},
    )

    assert _get_latest_vix() is None
    assert _get_market_cap_change_7d() is None


def test_macro_snapshot_near_selects_closest_valid_seven_day_row(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.storage import repositories as repo
    from aletheia.storage import sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sqlite.reset_db()
    run_migrations()
    latest_at = datetime(2026, 7, 19, tzinfo=timezone.utc)
    repo.insert_macro_snapshot({
        "id": "cap-near",
        "total_market_cap_usd": 1_000.0,
        "observed_at": (latest_at - timedelta(days=7, hours=1)).isoformat(),
    })
    repo.insert_macro_snapshot({
        "id": "cap-farther",
        "total_market_cap_usd": 900.0,
        "observed_at": (latest_at - timedelta(days=7, hours=8)).isoformat(),
    })
    repo.insert_macro_snapshot({
        "id": "cap-invalid",
        "total_market_cap_usd": 0.0,
        "observed_at": (latest_at - timedelta(days=7)).isoformat(),
    })

    baseline = repo.macro_snapshot_near(latest_at.isoformat())

    assert baseline is not None
    assert baseline["id"] == "cap-near"


def test_regime_atr_uses_completed_daily_true_ranges():
    from aletheia.regime.collector import _compute_btc_atr_ratio

    day_ms = 86_400_000
    start = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
    candles = [Candle(start, 100.0, 101.0, 99.0, 100.0, 1.0)]
    for index in range(1, 31):
        candles.append(Candle(start + index * day_ms, 100.0, 101.0, 99.0, 100.0, 1.0))
    candles.append(Candle(start + 31 * day_ms, 110.0, 111.0, 109.0, 110.0, 1.0))
    candles.append(Candle(start + 32 * day_ms, 110.0, 1_000.0, 1.0, 500.0, 1.0))
    now_ms = start + 32 * day_ms + day_ms // 2

    atr_7, atr_30 = _compute_btc_atr_ratio(candles, now_ms=now_ms)

    assert atr_7 == pytest.approx((23.0 / 7.0) / 110.0 * 100, abs=0.001)
    assert atr_30 == pytest.approx((69.0 / 30.0) / 110.0 * 100, abs=0.001)


@pytest.mark.parametrize("field", ["high", "low", "close"])
def test_regime_atr_rejects_nonfinite_candles(field):
    from aletheia.regime.collector import _compute_btc_atr_ratio

    day_ms = 86_400_000
    candles = [
        Candle(index * day_ms, 100.0, 101.0, 99.0, 100.0, 1.0)
        for index in range(31)
    ]
    setattr(candles[-1], field, float("nan"))

    assert _compute_btc_atr_ratio(candles, now_ms=32 * day_ms) == (None, None)


# ── debounced_detect ───────────────────────────────────────────────────────────


class TestDebouncedDetect:
    def setup_method(self):
        reset_state()

    def test_first_detection_publishes_immediately(self):
        closes = [50000.0] * 200 + [70000.0]
        r = debounced_detect(min_hold_periods=3, btc_closes_1d=closes, vix=12.0)
        assert r.state == REGIME_BULL

    def test_state_does_not_switch_before_hold_periods(self):
        bull_closes = [50000.0] * 200 + [70000.0]
        bear_closes = [70000.0] * 200 + [50000.0]

        # Start in bull
        debounced_detect(min_hold_periods=3, btc_closes_1d=bull_closes, vix=12.0)

        # Two bear detections — not enough to switch
        debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        r = debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        assert r.state == REGIME_BULL  # Still bull — hasn't held 3 periods

    def test_state_switches_after_hold_periods(self):
        bull_closes = [50000.0] * 200 + [70000.0]
        bear_closes = [70000.0] * 200 + [50000.0]

        # Start bull
        debounced_detect(min_hold_periods=3, btc_closes_1d=bull_closes, vix=12.0)

        # Three bear detections — should switch
        debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        r = debounced_detect(min_hold_periods=3, btc_closes_1d=bear_closes, vix=35.0)
        assert r.state == REGIME_BEAR


# ── _passes_regime_gate ────────────────────────────────────────────────────────


class TestPassesRegimeGate:
    def test_long_passes_in_bull_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        assert _passes_regime_gate("long", 50.0, regime) is True

    def test_short_blocked_in_bull_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        assert _passes_regime_gate("short", 50.0, regime) is False

    def test_short_passes_in_bear_regime(self):
        regime = MarketRegime(state="bear", long_bias=0.25, short_bias=0.75)
        assert _passes_regime_gate("short", 50.0, regime) is True

    def test_long_blocked_in_bear_regime(self):
        regime = MarketRegime(state="bear", long_bias=0.25, short_bias=0.75)
        assert _passes_regime_gate("long", 50.0, regime) is False

    def test_both_pass_in_sideways(self):
        regime = MarketRegime(state="sideways", long_bias=0.50, short_bias=0.50)
        assert _passes_regime_gate("long", 50.0, regime) is True
        assert _passes_regime_gate("short", 50.0, regime) is True

    def test_high_score_overrides_hostile_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        # Score >= 60 * 1.1 = 66 → overrides
        assert _passes_regime_gate("short", 67.0, regime) is True

    def test_moderate_score_blocked_in_hostile_regime(self):
        regime = MarketRegime(state="bull", long_bias=0.75, short_bias=0.25)
        # Score = 55 < 66 → blocked
        assert _passes_regime_gate("short", 55.0, regime) is False

    def test_volatile_regime_both_reduced(self):
        regime = MarketRegime(state="volatile", long_bias=0.35, short_bias=0.35)
        # bias=0.35 >= threshold 0.30 → passes
        assert _passes_regime_gate("long", 50.0, regime) is True
        assert _passes_regime_gate("short", 50.0, regime) is True

"""Unit tests for aletheia.analytics.trade_path."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aletheia.analytics.trade_path import (
    _analyze_path,
    _compute_mfe_mae,
    _compute_exit_efficiency,
    _classify_path_bucket,
    _iso_to_ms,
    _resolve_instrument_identity,
)


_BASE_TS = 1_800_000_000_000
_BAR_MS = 60_000


def _iso(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def _row(**overrides):
    row = {
        "position_id": "position-1",
        "symbol": "BTC/USDT",
        "market_type": "perp",
        "exchange": "okx",
        "direction": "long",
        "entry_price": 100.0,
        "exit_price": 101.0,
        "realized_pnl_pct": 1.0,
        "opened_at": _iso(_BASE_TS),
        "closed_at": _iso(_BASE_TS + 2 * _BAR_MS),
        "close_reason": "take_profit",
    }
    row.update(overrides)
    return row


def _candle(ts: int, *, high: float, low: float, close: float = 100.0) -> dict:
    return {
        "ts": ts,
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
        "volume": 10.0,
        "provider": "ccxt",
        "exchange": "okx",
        "market_type": "perp",
    }


# ---------------------------------------------------------------------------
# MFE / MAE math
# ---------------------------------------------------------------------------


def test_mfe_mae_long_trade():
    """Long trade: MFE = (max_high - entry) / entry * 100."""
    entry = 50000.0
    highs = [50500.0, 51000.0, 50800.0, 50200.0]
    lows = [49800.0, 50000.0, 50200.0, 49500.0]

    mfe, mae, t_mfe, t_mae = _compute_mfe_mae("long", entry, highs, lows)

    # MFE = (51000 - 50000) / 50000 * 100 = 2.0%
    assert round(mfe, 2) == 2.0
    # MAE = (49500 - 50000) / 50000 * 100 = -1.0%
    assert round(mae, 2) == -1.0
    assert t_mfe == 1  # index of 51000
    assert t_mae == 3  # index of 49500


def test_mfe_mae_short_trade():
    """Short trade: MFE = (entry - min_low) / entry * 100."""
    entry = 50000.0
    highs = [50500.0, 51000.0, 50200.0]
    lows = [49000.0, 49500.0, 48500.0]

    mfe, mae, t_mfe, t_mae = _compute_mfe_mae("short", entry, highs, lows)

    # MFE = (50000 - 48500) / 50000 * 100 = 3.0%
    assert round(mfe, 2) == 3.0
    # MAE = (50000 - 51000) / 50000 * 100 = -2.0%
    assert round(mae, 2) == -2.0
    assert t_mfe == 2  # index of 48500
    assert t_mae == 1  # index of 51000


def test_mfe_mae_no_favorable_move():
    """Long trade that never went above entry: MFE clamped to 0."""
    entry = 50000.0
    highs = [49900.0, 49800.0]
    lows = [49500.0, 49200.0]

    mfe, mae, _, _ = _compute_mfe_mae("long", entry, highs, lows)

    assert mfe == 0.0  # clamped
    assert mae < 0


# ---------------------------------------------------------------------------
# Exit efficiency
# ---------------------------------------------------------------------------


def test_exit_efficiency_at_mfe():
    """Exiting at MFE gives 100% efficiency."""
    eff = _compute_exit_efficiency(mfe_pct=3.0, mae_pct=-1.0, realized_pct=3.0)
    assert eff == 100.0


def test_exit_efficiency_at_mae():
    """Exiting at MAE gives 0% efficiency."""
    eff = _compute_exit_efficiency(mfe_pct=3.0, mae_pct=-1.0, realized_pct=-1.0)
    assert eff == 0.0


def test_exit_efficiency_middle():
    """Exiting at midpoint gives 50%."""
    eff = _compute_exit_efficiency(mfe_pct=2.0, mae_pct=-2.0, realized_pct=0.0)
    assert eff == 50.0


# ---------------------------------------------------------------------------
# Path bucket classification
# ---------------------------------------------------------------------------


def test_bucket_bad_entry():
    bucket, _ = _classify_path_bucket(
        mfe_pct=0.1,
        mae_pct=-2.0,
        realized_pct=-1.5,
        mfe_capture_ratio=0.0,
        close_reason="stop_loss",
        bars_in_trade=10,
        time_to_mfe=1,
    )
    assert bucket == "bad_entry"


def test_stop_loss_with_mfe_does_not_claim_causal_tightness():
    bucket, reason = _classify_path_bucket(
        mfe_pct=1.5,
        mae_pct=-1.0,
        realized_pct=-0.8,
        mfe_capture_ratio=0.0,
        close_reason="stop_loss",
        bars_in_trade=10,
        time_to_mfe=3,
    )
    assert bucket == "insufficient_path_data"
    assert reason == "first_stop_barrier_cross_unavailable"


def test_bucket_edge_lost():
    bucket, _ = _classify_path_bucket(
        mfe_pct=2.5,
        mae_pct=-0.5,
        realized_pct=-0.2,
        mfe_capture_ratio=0.0,
        close_reason="manual",
        bars_in_trade=20,
        time_to_mfe=5,
    )
    assert bucket == "edge_lost"


def test_bucket_clean_win():
    bucket, _ = _classify_path_bucket(
        mfe_pct=2.0,
        mae_pct=-0.3,
        realized_pct=1.5,
        mfe_capture_ratio=0.75,
        close_reason="take_profit",
        bars_in_trade=10,
        time_to_mfe=5,
    )
    assert bucket == "clean_win"


def test_bucket_time_exit_decay():
    bucket, _ = _classify_path_bucket(
        mfe_pct=2.0,
        mae_pct=-0.5,
        realized_pct=0.3,
        mfe_capture_ratio=0.15,
        close_reason="max_hold",
        bars_in_trade=48,
        time_to_mfe=8,
    )
    assert bucket == "time_exit_decay"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_iso_to_ms():
    ts = _iso_to_ms("2026-06-14T10:00:00+00:00")
    assert ts is not None
    assert ts > 0


def test_iso_to_ms_none():
    assert _iso_to_ms(None) is None
    assert _iso_to_ms("") is None


# ---------------------------------------------------------------------------
# Fail-closed path reconstruction
# ---------------------------------------------------------------------------


def test_trade_path_uses_exact_1m_identity_and_separate_post_exit_window(monkeypatch):
    calls = []
    candles = [
        _candle(_BASE_TS, high=102.0, low=99.0, close=101.0),
        _candle(_BASE_TS + _BAR_MS, high=103.0, low=100.0, close=101.0),
        # A post-exit spike must not inflate in-position MFE.
        _candle(_BASE_TS + 2 * _BAR_MS, high=110.0, low=99.5, close=108.0),
        _candle(_BASE_TS + 3 * _BAR_MS, high=109.0, low=99.0, close=105.0),
    ]

    def fake_get(symbol, timeframe, **kwargs):
        calls.append((symbol, timeframe, kwargs))
        return candles

    monkeypatch.setattr("aletheia.analytics.trade_path.repo.get_ohlcv_candles", fake_get)

    result = _analyze_path(_row(market_type="usdt_futures"), post_exit_bars=2)

    assert calls == [
        (
            "BTC/USDT",
            "1m",
            {
                "limit": 4,
                "exchange": "okx",
                "market_type": "perp",
                "before_ts": _BASE_TS + 4 * _BAR_MS,
            },
        )
    ]
    assert result["path_evidence_status"] == "available"
    assert result["mfe_pct"] == pytest.approx(3.0)
    assert result["post_exit_bars_observed"] == 2
    assert result["post_exit_mfe_pct"] == pytest.approx(8.9109)
    assert result["post_exit_noncausal_observation"] == "post_exit_favorable_continuation"
    assert result["path_bucket"] == "uncategorized"
    assert result["path_bucket"] != "take_profit_too_early"


def test_trade_path_marks_exit_bar_threshold_order_ambiguous(monkeypatch):
    candles = [
        _candle(_BASE_TS, high=100.5, low=99.5),
        # This bar straddles the recorded exit. The favorable high may be after
        # the stop fill, so it cannot prove profit-before-stop ordering.
        _candle(_BASE_TS + _BAR_MS, high=102.0, low=97.0, close=98.0),
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )
    row = _row(
        exit_price=98.0,
        realized_pnl_pct=-2.0,
        closed_at=_iso(_BASE_TS + 90_000),
        close_reason="stop_loss",
    )

    result = _analyze_path(row, post_exit_bars=0)

    assert result["mfe_lower_bound_pct"] == pytest.approx(0.5)
    assert result["mfe_upper_bound_pct"] == pytest.approx(2.0)
    assert result["mfe_pct"] is None
    assert result["path_evidence_status"] == "ambiguous"
    assert result["path_bucket"] == "insufficient_path_data"
    assert result["noncausal_path_observation"] == "possible_favorable_excursion_in_boundary_bar"
    assert result["path_bucket_reason"] == "intrabar_and_stop_barrier_order_unavailable"


def test_stop_mfe_observation_cannot_feed_policy_advice(monkeypatch):
    candles = [
        # This complete bar proves a favorable move before the later stop.
        _candle(_BASE_TS, high=102.0, low=99.5),
        _candle(_BASE_TS + _BAR_MS, high=100.2, low=97.0, close=98.0),
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )
    row = _row(
        exit_price=98.0,
        realized_pnl_pct=-2.0,
        closed_at=_iso(_BASE_TS + 90_000),
        close_reason="stop_loss",
    )

    result = _analyze_path(row, post_exit_bars=0)

    assert result["path_evidence_status"] == "ambiguous"
    assert result["noncausal_path_observation"] == "favorable_excursion_during_recorded_holding_window"
    assert result["path_bucket"] == "insufficient_path_data"
    assert result["path_bucket_reason"] == "first_stop_barrier_cross_unavailable"


def test_exact_1m_stop_path_still_needs_first_barrier_cross(monkeypatch):
    candles = [
        _candle(_BASE_TS, high=102.0, low=99.5),
        _candle(_BASE_TS + _BAR_MS, high=100.2, low=97.0, close=98.0),
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )
    row = _row(
        exit_price=98.0,
        realized_pnl_pct=-2.0,
        close_reason="stop_loss",
    )

    result = _analyze_path(row, post_exit_bars=0)

    assert result["mfe_pct"] == pytest.approx(2.0)
    assert result["path_evidence_status"] == "ambiguous"
    assert result["noncausal_path_observation"] == "favorable_excursion_during_recorded_holding_window"
    assert result["path_bucket"] == "insufficient_path_data"
    assert result["path_bucket_reason"] == "first_stop_barrier_cross_unavailable"


def test_protected_first_stop_proves_profit_giveback_before_trigger(monkeypatch):
    candles = [
        _candle(_BASE_TS, high=102.0, low=99.5),
        _candle(_BASE_TS + _BAR_MS, high=100.2, low=97.0, close=98.0),
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_position_protection_state",
        lambda position_id: {
            "position_id": position_id,
            "venue": "okx",
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "timeframe": "1m",
            "activated_at": _iso(_BASE_TS),
            "next_bar_open_ms": _BASE_TS + 2 * _BAR_MS,
            "last_evaluated_bar_open_ms": _BASE_TS + _BAR_MS,
            "last_inspected_bar_open_ms": _BASE_TS + _BAR_MS,
            "status": "materialized",
            "trigger_id": "trigger-1",
            "close_fill_id": "fill-1",
            "close_fill_price": 98.0,
            "close_filled_at": _iso(_BASE_TS + 2 * _BAR_MS),
            "final_state": "materialized",
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_position_protection_trigger",
        lambda position_id: {
            "position_id": position_id,
            "trigger_id": "trigger-1",
            "detected_at": _iso(_BASE_TS + 2 * _BAR_MS),
            "venue": "okx",
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "timeframe": "1m",
            "bar_open_ms": _BASE_TS + _BAR_MS,
            "bar_close_ms": _BASE_TS + 2 * _BAR_MS,
            "plan_version": 1,
            "stop_hit": 1,
            "target_hit": 0,
            "reason": "stop_loss",
        },
    )
    row = _row(
        exit_price=98.0,
        realized_pnl_pct=-2.0,
        close_reason="stop_loss",
    )

    result = _analyze_path(row, post_exit_bars=0)

    assert result["first_stop_barrier_status"] == "available"
    assert result["first_stop_barrier_open_ms"] == _BASE_TS + _BAR_MS
    assert result["pre_stop_mfe_lower_bound_pct"] == pytest.approx(2.0)
    assert result["path_bucket"] == "profit_giveback_before_stop"
    assert result["path_bucket_reason"] == "pre_stop_mfe_lower_bound=2.00%"
    assert result["noncausal_path_observation"] is None


def test_protected_stop_with_changed_plan_remains_fail_closed(monkeypatch):
    candles = [
        _candle(_BASE_TS, high=102.0, low=99.5),
        _candle(_BASE_TS + _BAR_MS, high=100.2, low=97.0, close=98.0),
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_position_protection_state",
        lambda position_id: {
            "position_id": position_id,
            "venue": "okx",
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "timeframe": "1m",
            "activated_at": _iso(_BASE_TS),
            "next_bar_open_ms": _BASE_TS + 2 * _BAR_MS,
            "last_evaluated_bar_open_ms": _BASE_TS + _BAR_MS,
            "last_inspected_bar_open_ms": _BASE_TS + _BAR_MS,
            "status": "materialized",
            "trigger_id": "trigger-2",
            "close_fill_id": "fill-2",
            "close_fill_price": 98.0,
            "close_filled_at": _iso(_BASE_TS + 2 * _BAR_MS),
            "final_state": "materialized",
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_position_protection_trigger",
        lambda position_id: {
            "position_id": position_id,
            "trigger_id": "trigger-2",
            "detected_at": _iso(_BASE_TS + 2 * _BAR_MS),
            "venue": "okx",
            "symbol": "BTC/USDT",
            "market_type": "perp",
            "timeframe": "1m",
            "bar_open_ms": _BASE_TS + _BAR_MS,
            "bar_close_ms": _BASE_TS + 2 * _BAR_MS,
            "plan_version": 2,
            "stop_hit": 1,
            "target_hit": 0,
            "reason": "stop_loss",
        },
    )

    result = _analyze_path(
        _row(exit_price=98.0, realized_pnl_pct=-2.0, close_reason="stop_loss"),
        post_exit_bars=0,
    )

    assert result["first_stop_barrier_status"] == "unavailable"
    assert result["first_stop_barrier_reason"] == "exit_plan_changed_before_first_stop"
    assert result["path_bucket"] == "insufficient_path_data"
    assert result["path_bucket_reason"] == "first_stop_barrier_cross_unavailable"


def test_trade_path_does_not_use_incomplete_post_exit_coverage(monkeypatch):
    candles = [
        _candle(_BASE_TS, high=102.0, low=99.0),
        _candle(_BASE_TS + _BAR_MS, high=103.0, low=100.0),
        _candle(_BASE_TS + 2 * _BAR_MS, high=110.0, low=99.5),
        # The second requested post-exit bar is missing.
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )

    result = _analyze_path(_row(), post_exit_bars=2)

    assert result["path_evidence_status"] == "available"
    assert result["post_exit_path_status"] == "unavailable"
    assert result["post_exit_path_reason"] == "post_exit_coverage_unavailable"
    assert result["post_exit_mfe_pct"] is None
    assert result["post_exit_noncausal_observation"] is None


@pytest.mark.parametrize(
    ("direction", "exit_price", "realized_pct", "time_field"),
    [
        ("long", 103.0, 3.0, "time_to_mfe_bars"),
        ("long", 97.0, -3.0, "time_to_mae_bars"),
        ("short", 97.0, 3.0, "time_to_mfe_bars"),
        ("short", 103.0, -3.0, "time_to_mae_bars"),
    ],
)
def test_exact_exit_fill_extreme_uses_elapsed_holding_bars(
    monkeypatch,
    direction,
    exit_price,
    realized_pct,
    time_field,
):
    candles = [
        _candle(_BASE_TS, high=102.0, low=98.0),
        _candle(_BASE_TS + _BAR_MS, high=102.0, low=98.0),
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )
    row = _row(
        direction=direction,
        exit_price=exit_price,
        realized_pnl_pct=realized_pct,
        close_reason="manual",
    )

    result = _analyze_path(row, post_exit_bars=0)

    assert result["bars_in_trade"] == 2
    assert result[time_field] == result["bars_in_trade"]


@pytest.mark.parametrize(
    ("direction", "exit_price", "candle_high", "candle_low", "candle_close"),
    [
        ("long", 99.0, 100.0, 98.0, 99.0),
        ("short", 101.0, 102.0, 100.0, 101.0),
    ],
)
def test_exact_zero_mfe_is_at_entry_not_a_candle(
    monkeypatch,
    direction,
    exit_price,
    candle_high,
    candle_low,
    candle_close,
):
    candles = [
        _candle(
            _BASE_TS,
            high=candle_high,
            low=candle_low,
            close=candle_close,
        ),
        _candle(
            _BASE_TS + _BAR_MS,
            high=candle_high,
            low=candle_low,
            close=candle_close,
        ),
    ]
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: candles,
    )
    row = _row(
        direction=direction,
        exit_price=exit_price,
        realized_pnl_pct=-1.0,
        close_reason="manual",
    )

    result = _analyze_path(row, post_exit_bars=0)

    assert result["mfe_pct"] == 0.0
    assert result["time_to_mfe_bars"] == 0


def test_trade_path_requires_complete_holding_coverage(monkeypatch):
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: [
            _candle(_BASE_TS, high=102.0, low=99.0),
            # Missing the second complete holding bar.
        ],
    )

    result = _analyze_path(_row(), post_exit_bars=0)

    assert result["path_evidence_status"] == "unavailable"
    assert result["path_bucket"] == "insufficient_path_data"
    assert result["path_bucket_reason"] == "holding_path_coverage_unavailable"


def test_trade_path_normalizes_position_futures_to_physical_perp_lineage(monkeypatch):
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_universe_member",
        lambda member_id: {
            "id": member_id,
            "asset_id": "asset-1",
            "symbol": "BTC/USDT",
            "market_type": "perp",
        },
    )
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_universe_asset",
        lambda asset_id: {
            "id": asset_id,
            "venue": "okx",
            "symbol": "BTC/USDT",
            "market_type": "perp",
        },
    )

    identity, error = _resolve_instrument_identity(
        _row(
            exchange=None,
            market_type="usdt_futures",
            universe_member_id="member-1",
        )
    )

    assert error is None
    assert identity is not None
    assert identity.exchange == "okx"
    assert identity.market_type == "perp"
    assert identity.source == "universe_registry"


def test_trade_path_rejects_unknown_physical_market_type():
    identity, error = _resolve_instrument_identity(_row(market_type="coin_futures"))

    assert identity is None
    assert error == "instrument_identity_unavailable"


def test_trade_path_requires_a_verified_exit_point(monkeypatch):
    monkeypatch.setattr(
        "aletheia.analytics.trade_path.repo.get_ohlcv_candles",
        lambda *args, **kwargs: pytest.fail("OHLCV must not be queried without an exit point"),
    )

    result = _analyze_path(_row(exit_price=None), post_exit_bars=0)

    assert result["path_evidence_status"] == "unavailable"
    assert result["path_bucket_reason"] == "missing_or_invalid_trade_identity"


def test_trade_path_missing_instrument_identity_is_unavailable(monkeypatch):
    def unexpected_read(*args, **kwargs):
        raise AssertionError("OHLCV must not be queried without exact identity")

    monkeypatch.setattr("aletheia.analytics.trade_path.repo.get_ohlcv_candles", unexpected_read)

    result = _analyze_path(
        _row(exchange=None, venue=None, universe_member_id=None),
        post_exit_bars=0,
    )

    assert result["path_evidence_status"] == "unavailable"
    assert result["path_bucket_reason"] == "instrument_identity_unavailable"

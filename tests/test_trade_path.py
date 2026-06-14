"""Unit tests for aletheia.analytics.trade_path."""
from __future__ import annotations

from aletheia.analytics.trade_path import (
    _compute_mfe_mae,
    _compute_exit_efficiency,
    _classify_path_bucket,
    _iso_to_ms,
)


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
        mfe_pct=0.1, mae_pct=-2.0, realized_pct=-1.5,
        mfe_capture_ratio=0.0, close_reason="stop_loss",
        bars_in_trade=10, time_to_mfe=1,
    )
    assert bucket == "bad_entry"


def test_bucket_stop_too_tight():
    bucket, _ = _classify_path_bucket(
        mfe_pct=1.5, mae_pct=-1.0, realized_pct=-0.8,
        mfe_capture_ratio=0.0, close_reason="stop_loss",
        bars_in_trade=10, time_to_mfe=3,
    )
    assert bucket == "stop_too_tight"


def test_bucket_edge_lost():
    bucket, _ = _classify_path_bucket(
        mfe_pct=2.5, mae_pct=-0.5, realized_pct=-0.2,
        mfe_capture_ratio=0.0, close_reason="manual",
        bars_in_trade=20, time_to_mfe=5,
    )
    assert bucket == "edge_lost"


def test_bucket_clean_win():
    bucket, _ = _classify_path_bucket(
        mfe_pct=2.0, mae_pct=-0.3, realized_pct=1.5,
        mfe_capture_ratio=0.75, close_reason="take_profit",
        bars_in_trade=10, time_to_mfe=5,
    )
    assert bucket == "clean_win"


def test_bucket_time_exit_decay():
    bucket, _ = _classify_path_bucket(
        mfe_pct=2.0, mae_pct=-0.5, realized_pct=0.3,
        mfe_capture_ratio=0.15, close_reason="max_hold",
        bars_in_trade=48, time_to_mfe=8,
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

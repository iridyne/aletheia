"""Unit tests for aletheia.analytics.exit_policy_advisor."""
from __future__ import annotations

from collections import Counter

from aletheia.analytics.exit_policy_advisor import _classify_exit_problem


def _make_bucket_counts(dominant: str, count: int, total: int) -> Counter:
    c = Counter()
    c[dominant] = count
    return c


def test_diagnosis_entry_problem():
    diagnosis, suggested, confidence = _classify_exit_problem(
        rows=[],
        avg_mfe=0.1, avg_mae=-2.0, avg_realized=-1.5, avg_capture=0.0,
        common_close_reason="stop_loss",
        bucket_counts=Counter({"bad_entry": 5, "uncategorized": 2}),
        trade_count=7,
    )
    assert diagnosis == "entry_problem"
    assert suggested == "no_change"


def test_diagnosis_stop_too_tight_high_mfe():
    diagnosis, suggested, confidence = _classify_exit_problem(
        rows=[],
        avg_mfe=1.5, avg_mae=-0.8, avg_realized=-0.5, avg_capture=0.2,
        common_close_reason="stop_loss",
        bucket_counts=Counter({"stop_too_tight": 4, "clean_win": 1}),
        trade_count=5,
    )
    assert diagnosis == "stop_too_tight"
    assert suggested == "switch_to_atr_stop"
    assert confidence == "high"


def test_diagnosis_stop_too_tight_low_mfe():
    diagnosis, suggested, confidence = _classify_exit_problem(
        rows=[],
        avg_mfe=0.5, avg_mae=-0.8, avg_realized=-0.5, avg_capture=0.2,
        common_close_reason="stop_loss",
        bucket_counts=Counter({"stop_too_tight": 3, "clean_win": 2}),
        trade_count=5,
    )
    assert diagnosis == "stop_too_tight"
    assert suggested == "increase_stop_loss_pct"


def test_diagnosis_edge_lost():
    diagnosis, suggested, confidence = _classify_exit_problem(
        rows=[],
        avg_mfe=2.5, avg_mae=-0.5, avg_realized=-0.2, avg_capture=0.1,
        common_close_reason="manual",
        bucket_counts=Counter({"edge_lost": 4, "clean_win": 1}),
        trade_count=5,
    )
    assert diagnosis == "edge_lost"
    assert suggested == "add_trailing_stop_candidate"


def test_diagnosis_time_decay_early_mfe():
    rows = [
        {"time_to_mfe_bars": 3, "bars_in_trade": 20} for _ in range(4)
    ] + [{"time_to_mfe_bars": 15, "bars_in_trade": 20}]

    diagnosis, suggested, confidence = _classify_exit_problem(
        rows=rows,
        avg_mfe=2.0, avg_mae=-0.5, avg_realized=0.3, avg_capture=0.15,
        common_close_reason="max_hold",
        bucket_counts=Counter({"time_exit_decay": 4, "clean_win": 1}),
        trade_count=5,
    )
    assert diagnosis == "max_hold_too_long"
    assert suggested == "shorten_max_hold"


def test_diagnosis_exit_ok_signal_bad():
    diagnosis, suggested, confidence = _classify_exit_problem(
        rows=[],
        avg_mfe=1.0, avg_mae=-1.5, avg_realized=-0.5, avg_capture=0.5,
        common_close_reason="stop_loss",
        bucket_counts=Counter({"uncategorized": 5}),
        trade_count=5,
    )
    assert diagnosis == "exit_ok_signal_bad"
    assert suggested == "pause_factor_until_more_data"


def test_diagnosis_no_clear_issue():
    diagnosis, suggested, confidence = _classify_exit_problem(
        rows=[],
        avg_mfe=1.5, avg_mae=-0.5, avg_realized=0.8, avg_capture=0.6,
        common_close_reason="take_profit",
        bucket_counts=Counter({"clean_win": 4, "uncategorized": 1}),
        trade_count=5,
    )
    assert diagnosis == "no_clear_issue"
    assert suggested == "no_change"

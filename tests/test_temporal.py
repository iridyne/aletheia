#!/usr/bin/env python3
"""Tests for aletheia.analytics.temporal (time-of-day / weekday PnL)."""
from __future__ import annotations

from aletheia.analytics import temporal as T


def _o(symbol, pnl, when):
    return {
        "symbol": symbol,
        "strategy_id": "s1",
        "close_reason": "tp",
        "realized_pnl": pnl,
        "closed_at": when,
    }


def test_parse_closed_at_z_suffix():
    dt = T._parse_closed_at("2026-07-20T10:30:00Z")
    assert dt is not None
    assert dt.hour == 10
    assert dt.minute == 30


def test_parse_closed_at_naive_and_none():
    dt = T._parse_closed_at("2026-07-20T22:00:00")
    assert dt is not None and dt.hour == 22
    assert T._parse_closed_at(None) is None
    assert T._parse_closed_at("") is None
    assert T._parse_closed_at("not-a-date") is None


def test_bucket_by_hour_and_weekday():
    outcomes = [
        _o("BTC", 1.0, "2026-07-20T09:00:00"),  # Mon 09
        _o("ETH", 2.0, "2026-07-20T09:30:00"),  # Mon 09
        _o("BTC", -1.0, "2026-07-21T15:00:00"),  # Tue 15
    ]
    by_hour = T.bucket_by_hour(outcomes)
    assert sorted(by_hour.keys()) == [9, 15]
    assert by_hour[9] == [1.0, 2.0]
    by_wd = T.bucket_by_weekday(outcomes)
    # 2026-07-20 is a Monday (weekday 0); 07-21 is Tuesday (weekday 1)
    assert sorted(by_wd.keys()) == [0, 1]


def test_bucket_skips_missing_closed_at():
    outcomes = [
        _o("BTC", 1.0, "2026-07-20T09:00:00"),
        {"symbol": "ETH", "realized_pnl": -1.0},  # no closed_at
    ]
    by_hour = T.bucket_by_hour(outcomes)
    assert by_hour == {9: [1.0]}


def test_temporal_summary_shape_and_best_worst():
    outcomes = [
        _o("BTC", 5.0, "2026-07-20T09:00:00"),   # Mon 09, +5
        _o("ETH", -3.0, "2026-07-20T09:30:00"),  # Mon 09, -3  -> hour 9 net +2
        _o("BTC", -4.0, "2026-07-21T15:00:00"),  # Tue 15, -4
    ]
    s = T.temporal_summary(outcomes)
    assert s["outcome_count"] == 3
    assert s["bucketed_count"] == 3
    assert 9 in s["by_hour"] and 15 in s["by_hour"]
    # best hour is 9 (net +2), worst is 15 (net -4)
    assert s["best_hour"]["hour"] == 9
    assert s["best_hour"]["total_pnl"] == 2.0
    assert s["worst_hour"]["hour"] == 15
    assert s["worst_hour"]["total_pnl"] == -4.0
    # best weekday Monday (0), worst Tuesday (1)
    assert s["best_weekday"]["weekday"] == 0
    assert s["worst_weekday"]["weekday"] == 1


def test_temporal_summary_empty():
    s = T.temporal_summary([])
    assert s["outcome_count"] == 0
    assert s["bucketed_count"] == 0
    assert s["best_hour"]["hour"] is None
    assert s["best_weekday"]["weekday"] is None


def test_report_wires_temporal_section(monkeypatch):
    fake = [
        _o("BTC", 5.0, "2026-07-20T09:00:00"),
        _o("ETH", -3.0, "2026-07-20T09:30:00"),
        _o("BTC", -4.0, "2026-07-21T15:00:00"),
    ]
    import performance_report as pr

    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda: fake,
    )
    rep = pr._collect()
    assert "temporal" in rep
    assert rep["temporal"]["best_hour"]["hour"] == 9
    human = pr._render_human(rep)
    assert "[时间序列 temporal]" in human
    assert "最佳时段: 09:00" in human


def test_unified_init_exports():
    import aletheia.analytics as A

    assert hasattr(A, "summarize")
    assert hasattr(A, "attribute")
    assert hasattr(A, "summarize_streaks")
    assert hasattr(A, "temporal_summary")

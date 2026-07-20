#!/usr/bin/env python3
"""Tests for aletheia.analytics.streaks (pure sequence primitives)."""
from __future__ import annotations

from aletheia.analytics import streaks as S


def test_classify_buckets():
    assert S.classify(1.0) == S.WIN
    assert S.classify(0.0) == S.FLAT
    assert S.classify(-0.5) == S.LOSS


def test_runs_identifies_sign_length_bounds():
    r = S.runs([1.0, 2.0, -1.0, -1.0, -1.0])
    # win(2) -> loss(3)
    assert r == [
        {"sign": S.WIN, "length": 2, "start": 0, "end": 1},
        {"sign": S.LOSS, "length": 3, "start": 2, "end": 4},
    ]


def test_runs_with_flat_breaks_runs():
    r = S.runs([1.0, 0.0, 1.0])
    assert [x["sign"] for x in r] == [S.WIN, S.FLAT, S.WIN]
    assert [x["length"] for x in r] == [1, 1, 1]


def test_runs_empty():
    assert S.runs([]) == []


def test_max_consecutive_wins_and_losses():
    pnls = [1.0, 1.0, -1.0, 1.0, 1.0, 1.0, -1.0, -1.0]
    assert S.max_consecutive_wins(pnls) == 3
    assert S.max_consecutive_losses(pnls) == 2


def test_max_consecutive_empty():
    assert S.max_consecutive_wins([]) == 0
    assert S.max_consecutive_losses([]) == 0


def test_current_streak_trailing_win():
    assert S.current_streak([1.0, -1.0, 1.0, 1.0]) == {"sign": S.WIN, "length": 2}


def test_current_streak_trailing_loss():
    assert S.current_streak([1.0, -1.0, -1.0, -1.0]) == {"sign": S.LOSS, "length": 3}


def test_current_streak_trailing_flat():
    assert S.current_streak([1.0, 0.0, 0.0]) == {"sign": S.FLAT, "length": 2}


def test_current_streak_empty():
    assert S.current_streak([]) == {"sign": None, "length": 0}


def test_summarize_streaks_shape_and_values():
    pnls = [1.0, 1.0, -1.0, -1.0, -1.0, 1.0]
    s = S.summarize_streaks(pnls)
    assert s["trade_count"] == 6
    assert s["max_consecutive_wins"] == 2
    assert s["max_consecutive_losses"] == 3
    assert s["current_streak_sign"] == S.WIN
    assert s["current_streak_length"] == 1


def test_summarize_streaks_empty():
    s = S.summarize_streaks([])
    assert s["trade_count"] == 0
    assert s["current_streak_sign"] is None
    assert s["current_streak_length"] == 0


def test_report_wires_streaks_section(monkeypatch):
    """The report's equity block should emit a streaks section from outcomes."""
    fake = [
        {"symbol": "BTC", "strategy_id": "s1", "close_reason": "tp",
         "realized_pnl": 1.0, "closed_at": "2026-07-20T10:00:00"},
        {"symbol": "BTC", "strategy_id": "s1", "close_reason": "tp",
         "realized_pnl": 1.0, "closed_at": "2026-07-20T11:00:00"},
        {"symbol": "ETH", "strategy_id": "s1", "close_reason": "sl",
         "realized_pnl": -1.0, "closed_at": "2026-07-20T12:00:00"},
    ]
    import performance_report as pr

    monkeypatch.setattr(
        "aletheia.analytics.outcome_integrity.eligible_strategy_outcomes",
        lambda: fake,
    )
    rep = pr._collect()
    assert "streaks" in rep
    assert rep["streaks"]["max_consecutive_wins"] == 2
    assert rep["streaks"]["max_consecutive_losses"] == 1
    assert rep["streaks"]["current_streak_sign"] == "loss"

    human = pr._render_human(rep)
    assert "[连胜连败 streaks]" in human
    assert "最长连胜: 2" in human

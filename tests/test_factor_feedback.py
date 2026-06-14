"""Unit tests for aletheia.analytics.factor_feedback."""
from __future__ import annotations

from unittest.mock import patch

from aletheia.analytics.factor_feedback import (
    build_factor_feedback,
    lookup_factor_signal,
    _classify_factor,
    _compute_group_stats,
    _max_consecutive_losses,
)


# ---------------------------------------------------------------------------
# _classify_factor
# ---------------------------------------------------------------------------

def test_classify_watch_insufficient_data():
    status, reason = _classify_factor(
        trade_count=2, min_trades=3, expectancy=0.5, win_rate=0.6, profit_factor=2.0
    )
    assert status == "watch"
    assert "insufficient" in reason


def test_classify_blocked_candidate():
    status, reason = _classify_factor(
        trade_count=5, min_trades=3, expectancy=-0.5, win_rate=0.30, profit_factor=0.5
    )
    assert status == "blocked_candidate"
    assert "negative_expectancy" in reason


def test_classify_preferred():
    status, reason = _classify_factor(
        trade_count=10, min_trades=3, expectancy=2.0, win_rate=0.65, profit_factor=2.5
    )
    assert status == "preferred"
    assert "positive_expectancy" in reason


def test_classify_active_fallback():
    status, reason = _classify_factor(
        trade_count=5, min_trades=3, expectancy=0.1, win_rate=0.50, profit_factor=1.0
    )
    assert status == "active"


# ---------------------------------------------------------------------------
# _max_consecutive_losses
# ---------------------------------------------------------------------------

def test_max_consecutive_losses():
    rows = [
        {"realized_pnl": 1.0, "closed_at": "2026-06-14T01:00:00"},
        {"realized_pnl": -1.0, "closed_at": "2026-06-14T02:00:00"},
        {"realized_pnl": -2.0, "closed_at": "2026-06-14T03:00:00"},
        {"realized_pnl": -0.5, "closed_at": "2026-06-14T04:00:00"},
        {"realized_pnl": 3.0, "closed_at": "2026-06-14T05:00:00"},
    ]
    assert _max_consecutive_losses(rows) == 3


def test_max_consecutive_losses_none():
    rows = [
        {"realized_pnl": 1.0, "closed_at": "2026-06-14T01:00:00"},
        {"realized_pnl": 2.0, "closed_at": "2026-06-14T02:00:00"},
    ]
    assert _max_consecutive_losses(rows) == 0


# ---------------------------------------------------------------------------
# _compute_group_stats
# ---------------------------------------------------------------------------

def test_compute_group_stats_basic():
    rows = [
        {"realized_pnl": 10.0, "holding_hours": 4.0, "close_reason": "take_profit", "closed_at": "2026-06-14T01:00:00"},
        {"realized_pnl": -5.0, "holding_hours": 2.0, "close_reason": "stop_loss", "closed_at": "2026-06-14T02:00:00"},
        {"realized_pnl": 8.0, "holding_hours": 6.0, "close_reason": "take_profit", "closed_at": "2026-06-14T03:00:00"},
        {"realized_pnl": -3.0, "holding_hours": 1.0, "close_reason": "stop_loss", "closed_at": "2026-06-14T04:00:00"},
    ]
    stats = _compute_group_stats("test_key", "exact", rows, min_trades=3)
    assert stats["trade_count"] == 4
    assert stats["win_count"] == 2
    assert stats["loss_count"] == 2
    assert stats["win_rate"] == 0.5
    assert stats["total_pnl"] == 10.0
    assert stats["most_common_close_reason"] in ("take_profit", "stop_loss")


# ---------------------------------------------------------------------------
# lookup_factor_signal
# ---------------------------------------------------------------------------

def test_lookup_factor_signal_no_history():
    """Missing factor history returns watch status."""
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=[]):
        signal = lookup_factor_signal({
            "scanner_family": "momentum",
            "symbol": "BTC/USDT",
            "direction": "long",
            "confidence_score": 75,
            "confidence": "high",
            "late_entry_penalty": None,
        })
    assert signal["status"] == "watch"
    assert signal["trade_count"] == 0
    assert signal["reason"] == "no_history"


def test_lookup_factor_signal_with_matching_history():
    """Matching history returns computed status."""
    fake_rows = [
        {
            "scanner_family": "momentum", "symbol": "BTC/USDT", "direction": "long",
            "confidence_score": 75, "confidence": "high", "late_entry_penalty": None,
            "realized_pnl": 5.0, "holding_hours": 4.0, "close_reason": "take_profit",
            "closed_at": "2026-06-14T01:00:00",
        },
        {
            "scanner_family": "momentum", "symbol": "BTC/USDT", "direction": "long",
            "confidence_score": 75, "confidence": "high", "late_entry_penalty": None,
            "realized_pnl": 3.0, "holding_hours": 2.0, "close_reason": "take_profit",
            "closed_at": "2026-06-14T02:00:00",
        },
        {
            "scanner_family": "momentum", "symbol": "BTC/USDT", "direction": "long",
            "confidence_score": 75, "confidence": "high", "late_entry_penalty": None,
            "realized_pnl": -1.0, "holding_hours": 1.0, "close_reason": "stop_loss",
            "closed_at": "2026-06-14T03:00:00",
        },
    ]
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=fake_rows):
        signal = lookup_factor_signal({
            "scanner_family": "momentum",
            "symbol": "BTC/USDT",
            "direction": "long",
            "confidence_score": 75,
            "confidence": "high",
            "late_entry_penalty": None,
        })
    assert signal["trade_count"] == 3
    assert signal["status"] == "preferred"  # all winners except one small loss


# ---------------------------------------------------------------------------
# build_factor_feedback
# ---------------------------------------------------------------------------

def test_build_factor_feedback_empty():
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=[]):
        result = build_factor_feedback(limit=10)
    assert result == []


def test_build_factor_feedback_groups():
    fake_rows = [
        {
            "scanner_family": "momentum", "symbol": "BTC/USDT", "direction": "long",
            "confidence_score": 75, "confidence": "high", "late_entry_penalty": 5.0,
            "realized_pnl": 10.0, "holding_hours": 4.0, "close_reason": "take_profit",
            "closed_at": "2026-06-14T01:00:00",
        },
        {
            "scanner_family": "momentum", "symbol": "ETH/USDT", "direction": "short",
            "confidence_score": 60, "confidence": "medium", "late_entry_penalty": 0.0,
            "realized_pnl": -2.0, "holding_hours": 1.0, "close_reason": "stop_loss",
            "closed_at": "2026-06-14T02:00:00",
        },
    ]
    with patch("aletheia.analytics.factor_feedback.build_trade_attribution_rows", return_value=fake_rows):
        result = build_factor_feedback(limit=10, min_trades=1)

    group_types = {r["group_type"] for r in result}
    assert "exact" in group_types
    assert "scanner" in group_types
    assert "scanner_dir" in group_types
    assert "symbol" in group_types
    assert "late_entry" in group_types

"""Regression tests for M1 analytics fixes: #1255 #1257 #1258 #1260."""
from __future__ import annotations

from aletheia.analytics.trade_insights import _profit_factor
from aletheia.analytics.streaks import classify, WIN, LOSS, FLAT
import statistics


def test_flat_outcomes_not_counted_as_losses():
    """#1255: three-way classification — flat is distinct from win/loss."""
    assert classify(0.0) == FLAT
    assert classify(1.0) == WIN
    assert classify(-1.0) == LOSS


def test_profit_factor_state_unbounded_no_losses():
    """#1257: all-win cohort → profit_factor_state=unbounded_no_losses."""
    rows = [
        {"realized_pnl": 1.0},
        {"realized_pnl": 2.0},
        {"realized_pnl": 3.0},
    ]
    result = _profit_factor(rows)
    assert result["profit_factor_state"] == "unbounded_no_losses"
    assert result["profit_factor"] is None
    assert result["gross_profit"] > 0
    assert result["gross_loss"] == 0


def test_profit_factor_state_unavailable():
    """#1257: no outcomes → profit_factor_state=unavailable_no_outcomes."""
    result = _profit_factor([])
    assert result["profit_factor_state"] == "unavailable_no_outcomes"
    assert result["profit_factor"] is None


def test_profit_factor_state_finite():
    """#1257: normal case → profit_factor_state=finite."""
    rows = [
        {"realized_pnl": 100.0},
        {"realized_pnl": -50.0},
    ]
    result = _profit_factor(rows)
    assert result["profit_factor_state"] == "finite"
    assert result["profit_factor"] == 2.0  # 100 / 50


def test_median_even_cohort():
    """#1260: statistics.median for even-sized list averages the two middle values."""
    durations = [1, 3]
    assert statistics.median(durations) == 2.0  # not 3
    durations = [1, 2, 3, 4]
    assert statistics.median(durations) == 2.5  # not 3

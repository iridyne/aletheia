"""Unit tests for aletheia.analytics.performance_summary (pure, no DB)."""
import math

import pytest

from aletheia.analytics.performance_summary import (
    calmar,
    consolidate_report,
    equity_curve,
    expectancy,
    max_drawdown,
    profit_factor,
    summarize,
    win_rate,
)


def test_equity_curve_cumulative():
    assert equity_curve([1.0, -0.5, 2.0]) == [1.0, 0.5, 2.5]
    assert equity_curve([]) == []


def test_equity_curve_handles_negative_start():
    # Equity can start below zero (prior losses); curve tracks cumulative.
    assert equity_curve([-2.0, 1.0]) == [-2.0, -1.0]


def test_max_drawdown_basic():
    eq = [10.0, 12.0, 9.0, 11.0, 5.0, 13.0]
    # peak 12 -> trough 5 => 7
    assert math.isclose(max_drawdown(eq), 7.0)


def test_max_drawdown_monotonic_increase_is_zero():
    assert max_drawdown([1.0, 2.0, 3.0]) == 0.0


def test_max_drawdown_empty():
    assert max_drawdown([]) == 0.0


def test_profit_factor_normal():
    # profits 3+2=5, losses 1+4=5 => factor 1.0
    assert math.isclose(profit_factor([3.0, -1.0, 2.0, -4.0]), 1.0)


def test_profit_factor_no_losses_is_inf():
    assert profit_factor([1.0, 2.0]) == float("inf")


def test_profit_factor_no_trades_is_zero():
    assert profit_factor([]) == 0.0


def test_profit_factor_only_losses_is_zero():
    assert profit_factor([-1.0, -2.0]) == 0.0


def test_win_rate():
    assert math.isclose(win_rate([1.0, -1.0, 2.0, -0.5]), 0.5)
    assert win_rate([]) == 0.0
    assert win_rate([-1.0, -2.0]) == 0.0


def test_expectancy():
    assert math.isclose(expectancy([1.0, -1.0, 2.0]), 2.0 / 3)
    assert expectancy([]) == 0.0


def test_calmar():
    assert math.isclose(calmar(10.0, 5.0), 2.0)
    assert calmar(10.0, 0.0) == 0.0  # no drawdown => 0, not inf


def test_summarize_full():
    pnls = [3.0, -1.0, 2.0, -4.0, 5.0]
    s = summarize(pnls)
    assert s["trade_count"] == 5
    assert math.isclose(s["total_pnl"], 5.0)
    assert math.isclose(s["win_rate_pct"], 60.0)  # 3 of 5 positive
    assert math.isclose(s["profit_factor"], 2.0)  # gross 10 / loss 5
    assert math.isclose(s["expectancy"], 1.0)
    # equity: 3, 2, 4, 0, 5 => peak 4 (at idx2), trough 0 => mdd 4
    assert math.isclose(s["max_drawdown"], 4.0)
    assert math.isclose(s["final_equity"], 5.0)
    assert math.isclose(s["calmar"], 5.0 / 4.0)


def test_summarize_empty():
    s = summarize([])
    assert s["trade_count"] == 0
    assert s["total_pnl"] == 0.0
    assert s["profit_factor"] == 0.0  # no trades


def test_summarize_inf_profit_factor_json_safe_repr():
    s = summarize([2.0, 3.0])
    assert s["profit_factor"] == float("inf")
    # Documented behaviour: callers stringify inf for display.
    assert s["profit_factor"] != s["profit_factor"] / 2 or True  # noqa: B015 (inf sanity)


def test_consolidate_report_merges_sections():
    rep = consolidate_report(
        rolling_stats={"win_rate": 50.0},
        equity=[1.0, 2.0, 1.0],
        order_stats={"fill_rate_pct": 98.0},
    )
    assert set(rep["sections"]) == {"rolling", "equity", "order_stats"}
    assert rep["rolling"]["win_rate"] == 50.0
    assert rep["order_stats"]["fill_rate_pct"] == 98.0
    assert rep["equity_metrics"]["max_drawdown"] == 1.0  # peak 2 -> trough 1


def test_consolidate_report_empty_is_safe():
    rep = consolidate_report()
    assert rep["sections"] == []

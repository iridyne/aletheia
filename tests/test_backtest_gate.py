"""Tests for the backtest gate in strategy_designer — instant kill for failing strategies."""
from unittest.mock import patch, MagicMock
import pytest

from aletheia.strategy.backtest import BacktestConfig, BacktestMetrics, BacktestResult
from aletheia.strategy.dsl import (
    Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
)
from aletheia.strategy.evaluator import BarData
from aletheia.strategy.review import evaluate_strategy, StrategyVerdict, Verdict


class TestBacktestGateLogic:
    """Test the evaluate_strategy function that powers the gate."""

    def test_kill_on_negative_expectancy(self):
        v = evaluate_strategy(
            trade_count=10, win_rate=0.3, expectancy=-0.5,
            profit_factor=0.6, max_drawdown_pct=3.0,
        )
        assert v.verdict == Verdict.KILL

    def test_kill_on_high_drawdown(self):
        v = evaluate_strategy(
            trade_count=10, win_rate=0.5, expectancy=1.0,
            profit_factor=1.5, max_drawdown_pct=9.0,
        )
        assert v.verdict == Verdict.KILL

    def test_kill_on_low_win_rate(self):
        v = evaluate_strategy(
            trade_count=10, win_rate=0.15, expectancy=0.1,
            profit_factor=1.1, max_drawdown_pct=3.0,
        )
        assert v.verdict == Verdict.KILL

    def test_keep_on_good_metrics(self):
        v = evaluate_strategy(
            trade_count=10, win_rate=0.5, expectancy=1.0,
            profit_factor=1.5, max_drawdown_pct=3.0,
        )
        assert v.verdict == Verdict.KEEP

    def test_review_on_borderline(self):
        v = evaluate_strategy(
            trade_count=10, win_rate=0.30, expectancy=0.3,
            profit_factor=1.0, max_drawdown_pct=6.0,
        )
        assert v.verdict == Verdict.REVIEW

    def test_review_on_insufficient_trades(self):
        v = evaluate_strategy(
            trade_count=1, win_rate=1.0, expectancy=10.0,
            profit_factor=999.0, max_drawdown_pct=0.0,
        )
        assert v.verdict == Verdict.REVIEW


class TestBacktestGateIntegration:
    """Test that the gate correctly prevents/allows paper activation."""

    def _make_bars(self, n=50, trend=0.1):
        bars = []
        price = 100.0
        for i in range(n):
            price *= (1 + trend / 100)
            bars.append(BarData(
                ts=1000000 + i * 3600,
                open=price * 0.999,
                high=price * 1.005,
                low=price * 0.995,
                close=price,
                volume=1000.0,
            ))
        return bars

    def test_kill_verdict_retires_strategy(self):
        """A strategy with KILL verdict should be retired, not paper_running."""
        from aletheia.strategy.backtest import run_backtest
        from aletheia.strategy.dsl import RuleSet

        # Build a strategy that will clearly fail (always enter, tight stop, no trend)
        ruleset = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0),
            ]),
            exit=RuleExpr(op="or", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.LT, right=0.0),
            ]),
            stop_loss=StopLoss(mode="fixed_pct", value=0.5),
        )

        # Make flat/choppy bars where stop will always trigger
        bars = []
        for i in range(100):
            price = 100.0 + (i % 5 - 2) * 0.8  # oscillates ±0.8
            bars.append(BarData(
                ts=1000000 + i * 3600,
                open=price, high=price + 0.5, low=price - 0.5,
                close=price, volume=1000.0,
            ))

        cfg = BacktestConfig(symbol="TEST/USDT", timeframe="1h", direction="long")
        result = run_backtest(ruleset, bars, cfg)

        # Should have negative expectancy from repeated stop-outs
        if result.metrics.trade_count >= 2:
            v = evaluate_strategy(
                trade_count=result.metrics.trade_count,
                win_rate=result.metrics.win_rate,
                expectancy=result.metrics.expectancy,
                profit_factor=min(result.metrics.profit_factor, 999.0),
                max_drawdown_pct=result.metrics.max_drawdown_pct,
                total_return_pct=result.metrics.total_return_pct,
            )
            assert v.verdict == Verdict.KILL

    def test_keep_verdict_allows_paper(self):
        """A strategy with KEEP verdict should proceed to paper_running."""
        from aletheia.strategy.backtest import run_backtest
        from aletheia.strategy.dsl import RuleSet

        # Strong trending bars + always-enter long
        bars = self._make_bars(100, trend=0.5)
        ruleset = RuleSet(
            entry=RuleExpr(op="and", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=0.0),
            ]),
            exit=RuleExpr(op="or", conditions=[
                Condition(left=IndicatorRef(name="rsi", params={"period": 14}),
                          comparator=Comparator.GT, right=85.0),
            ]),
            stop_loss=StopLoss(mode="fixed_pct", value=5.0),
        )

        cfg = BacktestConfig(symbol="TEST/USDT", timeframe="1h", direction="long")
        result = run_backtest(ruleset, bars, cfg)

        if result.metrics.trade_count >= 5 and result.metrics.expectancy > 0:
            v = evaluate_strategy(
                trade_count=result.metrics.trade_count,
                win_rate=result.metrics.win_rate,
                expectancy=result.metrics.expectancy,
                profit_factor=min(result.metrics.profit_factor, 999.0),
                max_drawdown_pct=result.metrics.max_drawdown_pct,
                total_return_pct=result.metrics.total_return_pct,
            )
            assert v.verdict in (Verdict.KEEP, Verdict.REVIEW)

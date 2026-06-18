"""Tests for walk-forward OOS validation."""
import pytest

from aletheia.strategy.backtest import BacktestConfig
from aletheia.strategy.dsl import (
    Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
)
from aletheia.strategy.evaluator import BarData
from aletheia.strategy.walk_forward import (
    WalkForwardConfig, WalkForwardResult, run_walk_forward,
)


def _make_trending_bars(n: int, start_price: float = 100.0, trend: float = 0.1) -> list[BarData]:
    """Generate synthetic bars with a steady uptrend."""
    bars = []
    price = start_price
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


def _make_mixed_bars(n: int) -> list[BarData]:
    """Generate bars: first half trends up, second half trends down."""
    up = _make_trending_bars(n // 2, start_price=100.0, trend=0.2)
    down = _make_trending_bars(n - n // 2, start_price=up[-1].close, trend=-0.2)
    return up + down


def _simple_long_ruleset() -> RuleSet:
    """RSI < 40 entry, RSI > 60 exit — works in uptrends."""
    return RuleSet(
        entry=RuleExpr(op="and", conditions=[
            Condition(
                left=IndicatorRef(name="rsi", params={"period": 14}),
                comparator=Comparator.LT,
                right=40.0,
            ),
        ]),
        exit=RuleExpr(op="or", conditions=[
            Condition(
                left=IndicatorRef(name="rsi", params={"period": 14}),
                comparator=Comparator.GT,
                right=60.0,
            ),
        ]),
        stop_loss=StopLoss(mode="fixed_pct", value=3.0),
    )


def _always_enter_ruleset() -> RuleSet:
    """Close > 0 (always true) — a degenerate strategy for testing."""
    return RuleSet(
        entry=RuleExpr(op="and", conditions=[
            Condition(
                left=IndicatorRef(name="close"),
                comparator=Comparator.GT,
                right=0.0,
            ),
        ]),
        exit=RuleExpr(op="or", conditions=[
            Condition(
                left=IndicatorRef(name="rsi", params={"period": 14}),
                comparator=Comparator.GT,
                right=80.0,
            ),
        ]),
        stop_loss=StopLoss(mode="fixed_pct", value=2.0),
    )


class TestWalkForwardSplitting:
    def test_insufficient_bars_passes_by_default(self):
        bars = _make_trending_bars(30)
        ruleset = _simple_long_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        result = run_walk_forward(ruleset, bars, cfg)
        assert result.passed is True
        assert result.folds == []

    def test_three_folds_created(self):
        bars = _make_trending_bars(300)
        ruleset = _simple_long_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        result = run_walk_forward(ruleset, bars, cfg)
        assert len(result.folds) == 3

    def test_fold_ranges_non_overlapping(self):
        bars = _make_trending_bars(300)
        ruleset = _simple_long_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        result = run_walk_forward(ruleset, bars, cfg)
        ranges = [(f.is_bar_range[0], f.oos_bar_range[1]) for f in result.folds]
        for i in range(len(ranges) - 1):
            assert ranges[i][1] <= ranges[i + 1][0]

    def test_is_before_oos_in_each_fold(self):
        bars = _make_trending_bars(300)
        ruleset = _simple_long_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        result = run_walk_forward(ruleset, bars, cfg)
        for fold in result.folds:
            assert fold.is_bar_range[1] <= fold.oos_bar_range[0]


class TestWalkForwardVerdict:
    def test_trending_strategy_passes_on_trending_data(self):
        bars = _make_trending_bars(300, trend=0.5)
        ruleset = _always_enter_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h", direction="long")
        wf_cfg = WalkForwardConfig(min_bars_per_fold=30)
        result = run_walk_forward(ruleset, bars, cfg, wf_cfg)
        # With strong enough trend, OOS should still be positive
        positive_folds = sum(1 for f in result.folds if f.oos_metrics.expectancy > 0)
        assert positive_folds >= 1

    def test_overfitted_strategy_fails_on_mixed_data(self):
        bars = _make_mixed_bars(300)
        ruleset = _always_enter_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h", direction="long")
        result = run_walk_forward(ruleset, bars, cfg)
        negative_folds = sum(1 for f in result.folds if f.oos_metrics.expectancy <= 0)
        assert negative_folds >= 1

    def test_degradation_ratio_computed(self):
        bars = _make_trending_bars(300)
        ruleset = _always_enter_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h", direction="long")
        result = run_walk_forward(ruleset, bars, cfg)
        assert isinstance(result.degradation_ratio, float)


class TestWalkForwardConfig:
    def test_custom_fold_count(self):
        bars = _make_trending_bars(500)
        ruleset = _always_enter_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h", direction="long")
        wf_cfg = WalkForwardConfig(n_folds=5, min_bars_per_fold=30)
        result = run_walk_forward(ruleset, bars, cfg, wf_cfg)
        assert len(result.folds) == 5

    def test_require_all_oos_positive(self):
        bars = _make_mixed_bars(300)
        ruleset = _always_enter_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h", direction="long")
        wf_cfg = WalkForwardConfig(require_all_oos_positive=True, require_majority_oos_positive=False)
        result = run_walk_forward(ruleset, bars, cfg, wf_cfg)
        if any(f.oos_metrics.expectancy <= 0 for f in result.folds):
            assert result.passed is False

    def test_custom_is_ratio(self):
        bars = _make_trending_bars(300)
        ruleset = _always_enter_ruleset()
        cfg = BacktestConfig(symbol="BTC/USDT", timeframe="1h", direction="long")
        wf_cfg = WalkForwardConfig(is_ratio=0.5)
        result = run_walk_forward(ruleset, bars, cfg, wf_cfg)
        for fold in result.folds:
            is_len = fold.is_bar_range[1] - fold.is_bar_range[0]
            oos_len = fold.oos_bar_range[1] - fold.oos_bar_range[0]
            assert abs(is_len - oos_len) <= 2

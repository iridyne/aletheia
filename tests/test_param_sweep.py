"""Tests for parameter sweep generator."""
import pytest

from aletheia.strategy.dsl import (
    Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
)
from aletheia.strategy.evaluator import BarData
from aletheia.strategy.param_sweep import (
    SweepConfig, SweepResult, run_parameter_sweep,
    _generate_variants, _flat_conditions,
)


def _trending_bars(n=200, trend=0.3):
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


def _base_ruleset() -> RuleSet:
    return RuleSet(
        entry=RuleExpr(op="and", conditions=[
            Condition(
                left=IndicatorRef(name="rsi", params={"period": 14}),
                comparator=Comparator.LT,
                right=35.0,
            ),
        ]),
        exit=RuleExpr(op="or", conditions=[
            Condition(
                left=IndicatorRef(name="rsi", params={"period": 14}),
                comparator=Comparator.GT,
                right=65.0,
            ),
        ]),
        stop_loss=StopLoss(mode="fixed_pct", value=2.5),
    )


class TestVariantGeneration:
    def test_generates_period_variants(self):
        cfg = SweepConfig(
            base_ruleset=_base_ruleset(),
            symbol="BTC/USDT", timeframe="1h",
            period_range=(10, 40, 10),
        )
        variants = _generate_variants(cfg)
        # Should have variants for period 10, 20, 30 (not 14 which is original)
        assert len(variants) >= 3

    def test_generates_threshold_variants(self):
        cfg = SweepConfig(
            base_ruleset=_base_ruleset(),
            symbol="BTC/USDT", timeframe="1h",
            period_range=(14, 15, 10),  # no period variation
            threshold_pct_variations=[-0.1, 0.1],
        )
        variants = _generate_variants(cfg)
        # RSI threshold 35 → 31.5 and 38.5
        assert len(variants) >= 2

    def test_generates_stop_loss_variants(self):
        cfg = SweepConfig(
            base_ruleset=_base_ruleset(),
            symbol="BTC/USDT", timeframe="1h",
            period_range=(14, 15, 10),
            threshold_pct_variations=[],
            stop_loss_range=(1.0, 4.0, 1.0),
        )
        variants = _generate_variants(cfg)
        # SL values: 1.0, 2.0, 3.0, 4.0 (skip 2.5 original)
        assert len(variants) >= 3

    def test_max_variants_cap(self):
        cfg = SweepConfig(
            base_ruleset=_base_ruleset(),
            symbol="BTC/USDT", timeframe="1h",
            period_range=(5, 100, 5),
            max_variants=10,
        )
        variants = _generate_variants(cfg)
        assert len(variants) <= 10

    def test_no_variants_for_empty_ruleset(self):
        rs = RuleSet(
            entry=RuleExpr(op="and", conditions=[]),
            exit=RuleExpr(op="or", conditions=[]),
        )
        cfg = SweepConfig(base_ruleset=rs, symbol="BTC/USDT", timeframe="1h")
        variants = _generate_variants(cfg)
        assert len(variants) == 0


class TestSweepExecution:
    def test_sweep_on_trending_data(self):
        bars = _trending_bars(200, trend=0.4)
        cfg = SweepConfig(
            base_ruleset=_base_ruleset(),
            symbol="BTC/USDT", timeframe="1h",
            max_variants=20,
        )
        result = run_parameter_sweep(cfg, bars)
        assert isinstance(result, SweepResult)
        assert result.variants_tested <= 20
        assert result.variants_tested > 0

    def test_sweep_returns_survivors(self):
        bars = _trending_bars(200, trend=0.5)
        cfg = SweepConfig(
            base_ruleset=_base_ruleset(),
            symbol="BTC/USDT", timeframe="1h", direction="long",
            max_variants=30,
        )
        result = run_parameter_sweep(cfg, bars)
        assert result.variants_survived >= 0
        if result.variants_survived > 0:
            assert result.best_variant is not None
            assert result.best_metrics is not None
            assert result.best_metrics.expectancy > 0

    def test_sweep_insufficient_bars(self):
        bars = _trending_bars(10)
        cfg = SweepConfig(
            base_ruleset=_base_ruleset(),
            symbol="BTC/USDT", timeframe="1h",
        )
        result = run_parameter_sweep(cfg, bars)
        assert result.variants_tested == 0

    def test_flat_conditions_helper(self):
        expr = RuleExpr(op="and", conditions=[
            Condition(left=IndicatorRef(name="rsi"), comparator=Comparator.GT, right=50.0),
            RuleExpr(op="or", conditions=[
                Condition(left=IndicatorRef(name="close"), comparator=Comparator.GT, right=100.0),
            ]),
        ])
        flat = _flat_conditions(expr)
        assert len(flat) == 2

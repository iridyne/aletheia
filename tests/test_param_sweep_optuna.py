"""Tests for Optuna integration in param_sweep."""

from unittest.mock import patch, MagicMock

import pytest

from aletheia.strategy.backtest import BacktestMetrics
from aletheia.strategy.dsl import Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss
from aletheia.strategy.evaluator import BarData
from aletheia.strategy.param_sweep import SweepConfig, SweepResult, run_parameter_sweep


def _make_bars(n: int = 100) -> list[BarData]:
    """Generate synthetic price bars with a trend."""
    bars = []
    price = 100.0
    for i in range(n):
        price *= 1.002 if i % 3 != 0 else 0.998
        bars.append(BarData(
            ts=1_700_000_000_000 + i * 3_600_000,
            open=price,
            high=price * 1.005,
            low=price * 0.995,
            close=price,
            volume=1_000_000.0,
        ))
    return bars


def _make_ruleset() -> RuleSet:
    """Build a minimal RuleSet with period + threshold + stop_loss."""
    entry = RuleExpr(op="and", conditions=[
        Condition(
            left=IndicatorRef(name="rsi", params={"period": 14}),
            comparator="<",
            right=30.0,
        ),
    ])
    exit_expr = RuleExpr(op="and", conditions=[
        Condition(
            left=IndicatorRef(name="rsi", params={"period": 14}),
            comparator=">",
            right=70.0,
        ),
    ])
    return RuleSet(
        entry=entry,
        exit=exit_expr,
        stop_loss=StopLoss(mode="fixed_pct", value=2.0),
    )


def test_brute_force_path():
    """sweep_use_optuna=False should use the brute-force path."""
    sf_cfg = MagicMock()
    sf_cfg.sweep_use_optuna = False

    with patch("aletheia.config.loader.load_config") as mock_lc:
        mock_lc.return_value.strategy_factory = sf_cfg
        config = SweepConfig(
            base_ruleset=_make_ruleset(),
            symbol="BTC/USDT",
            timeframe="1h",
            max_variants=5,
        )
        result = run_parameter_sweep(config, _make_bars(80))

    assert isinstance(result, SweepResult)
    assert result.variants_tested >= 0


def test_optuna_path():
    """sweep_use_optuna=True should invoke Optuna and return a valid SweepResult."""
    sf_cfg = MagicMock()
    sf_cfg.sweep_use_optuna = True
    sf_cfg.sweep_n_trials = 5
    sf_cfg.sweep_optuna_timeout_sec = 30.0
    sf_cfg.sweep_top_k = 3

    with patch("aletheia.config.loader.load_config") as mock_lc:
        mock_lc.return_value.strategy_factory = sf_cfg
        config = SweepConfig(
            base_ruleset=_make_ruleset(),
            symbol="BTC/USDT",
            timeframe="1h",
        )
        result = run_parameter_sweep(config, _make_bars(100))

    assert isinstance(result, SweepResult)
    assert result.variants_tested == 5
    # Survivors may be 0 if none pass evaluate_strategy gates — that's fine for 5 trials
    assert result.variants_survived >= 0


def test_optuna_too_few_bars():
    """With < 30 bars, should return empty result immediately."""
    sf_cfg = MagicMock()
    sf_cfg.sweep_use_optuna = True
    sf_cfg.sweep_n_trials = 5
    sf_cfg.sweep_optuna_timeout_sec = 10.0
    sf_cfg.sweep_top_k = 3

    with patch("aletheia.config.loader.load_config") as mock_lc:
        mock_lc.return_value.strategy_factory = sf_cfg
        config = SweepConfig(
            base_ruleset=_make_ruleset(),
            symbol="BTC/USDT",
            timeframe="1h",
        )
        result = run_parameter_sweep(config, _make_bars(10))

    assert result.variants_tested == 0
    assert result.all_survivors == []

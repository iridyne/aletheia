"""Walk-Forward validation — multi-fold out-of-sample robustness check.

Splits a bar series into N equal folds. Within each fold, the first
`is_ratio` fraction is in-sample (IS) and the rest is out-of-sample (OOS).
Both are backtested independently; the strategy passes only if a majority
of OOS segments show positive expectancy.

This catches overfitted strategies that look good in-sample but degrade
on unseen data — the single most common failure mode of generated RuleSets.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from aletheia.strategy.backtest import BacktestConfig, BacktestMetrics, run_backtest
from aletheia.strategy.dsl import RuleSet
from aletheia.strategy.evaluator import BarData


@dataclass
class WalkForwardConfig:
    n_folds: int = 3
    is_ratio: float = 0.7
    min_bars_per_fold: int = 50
    require_all_oos_positive: bool = False
    require_majority_oos_positive: bool = True


@dataclass
class FoldResult:
    fold_index: int
    is_metrics: BacktestMetrics
    oos_metrics: BacktestMetrics
    is_bar_range: tuple[int, int]
    oos_bar_range: tuple[int, int]


@dataclass
class WalkForwardResult:
    passed: bool
    folds: list[FoldResult] = field(default_factory=list)
    oos_avg_expectancy: float = 0.0
    oos_avg_return_pct: float = 0.0
    degradation_ratio: float = 0.0


def run_walk_forward(
    ruleset: RuleSet,
    bars: list[BarData],
    bt_config: BacktestConfig,
    wf_config: WalkForwardConfig | None = None,
) -> WalkForwardResult:
    """Run walk-forward validation across N folds.

    Returns WalkForwardResult with per-fold metrics and overall pass/fail.
    """
    if wf_config is None:
        wf_config = WalkForwardConfig()

    total_bars = len(bars)
    min_total = wf_config.n_folds * wf_config.min_bars_per_fold
    if total_bars < min_total:
        return WalkForwardResult(passed=True, oos_avg_expectancy=0.0)

    fold_size = total_bars // wf_config.n_folds
    folds: list[FoldResult] = []
    oos_expectancies: list[float] = []
    oos_returns: list[float] = []
    is_expectancies: list[float] = []

    for i in range(wf_config.n_folds):
        start = i * fold_size
        end = start + fold_size if i < wf_config.n_folds - 1 else total_bars
        fold_bars = bars[start:end]

        is_end = int(len(fold_bars) * wf_config.is_ratio)
        if is_end < 20 or (len(fold_bars) - is_end) < 10:
            continue

        is_bars = fold_bars[:is_end]
        oos_bars = fold_bars[is_end:]

        is_result = run_backtest(ruleset, is_bars, bt_config)
        oos_result = run_backtest(ruleset, oos_bars, bt_config)

        folds.append(FoldResult(
            fold_index=i,
            is_metrics=is_result.metrics,
            oos_metrics=oos_result.metrics,
            is_bar_range=(start, start + is_end),
            oos_bar_range=(start + is_end, end),
        ))

        oos_expectancies.append(oos_result.metrics.expectancy)
        oos_returns.append(oos_result.metrics.total_return_pct)
        is_expectancies.append(is_result.metrics.expectancy)

    if not folds:
        return WalkForwardResult(passed=True)

    oos_avg_exp = mean(oos_expectancies)
    oos_avg_ret = mean(oos_returns)
    is_avg_exp = mean(is_expectancies) if is_expectancies else 0.0

    if is_avg_exp > 0:
        degradation = oos_avg_exp / is_avg_exp
    elif is_avg_exp == 0:
        degradation = 1.0 if oos_avg_exp >= 0 else 0.0
    else:
        degradation = 1.0

    positive_oos = sum(1 for e in oos_expectancies if e > 0)
    majority = len(folds) / 2

    if wf_config.require_all_oos_positive:
        passed = positive_oos == len(folds)
    elif wf_config.require_majority_oos_positive:
        passed = positive_oos > majority
    else:
        passed = oos_avg_exp > 0

    return WalkForwardResult(
        passed=passed,
        folds=folds,
        oos_avg_expectancy=oos_avg_exp,
        oos_avg_return_pct=oos_avg_ret,
        degradation_ratio=degradation,
    )

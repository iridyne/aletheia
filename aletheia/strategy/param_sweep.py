"""Parameter sweep — generate RuleSet variants via grid mutation and batch-backtest.

Given a base RuleSet that passed the backtest gate, this module generates
parameter variants (indicator periods, thresholds, stop-loss values) and
backtests each one. Only survivors (KEEP verdict) are returned for batch
strategy creation.

This is the "brute-force alpha amplifier" — one good LLM idea becomes 10-50
tested variants, each with slightly different parameters that may fit
different market conditions.
"""
from __future__ import annotations

import itertools
import logging
import random
from copy import deepcopy
from dataclasses import dataclass, field

from aletheia.strategy.backtest import BacktestConfig, BacktestMetrics, run_backtest
from aletheia.strategy.cost_model import CostModel
from aletheia.strategy.dsl import Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss
from aletheia.strategy.evaluator import BarData
from aletheia.strategy.review import Verdict, evaluate_strategy

logger = logging.getLogger("aletheia.strategy.param_sweep")


@dataclass
class SweepConfig:
    base_ruleset: RuleSet
    symbol: str
    timeframe: str
    direction: str = "long"
    period_range: tuple[int, int, int] = (10, 50, 10)
    threshold_pct_variations: list[float] = field(default_factory=lambda: [-0.2, -0.1, 0.1, 0.2])
    stop_loss_range: tuple[float, float, float] = (1.0, 4.0, 1.0)
    max_variants: int = 100
    cost_model: CostModel = field(default_factory=CostModel)


@dataclass
class SweepResult:
    variants_tested: int
    variants_survived: int
    best_variant: RuleSet | None
    best_metrics: BacktestMetrics | None
    all_survivors: list[tuple[RuleSet, BacktestMetrics]]


def run_parameter_sweep(config: SweepConfig, bars: list[BarData]) -> SweepResult:
    """Generate parameter variants of a base RuleSet, backtest each, return survivors."""
    if len(bars) < 30:
        return SweepResult(0, 0, None, None, [])

    variants = _generate_variants(config)
    if not variants:
        return SweepResult(0, 0, None, None, [])

    bt_cfg = BacktestConfig(
        symbol=config.symbol,
        timeframe=config.timeframe,
        direction=config.direction,
        cost_model=config.cost_model,
    )

    survivors: list[tuple[RuleSet, BacktestMetrics]] = []
    best: tuple[RuleSet | None, BacktestMetrics | None] = (None, None)
    best_expectancy = float("-inf")

    for variant in variants:
        try:
            result = run_backtest(variant, bars, bt_cfg)
            m = result.metrics
            if m.trade_count < 2:
                continue

            pf = min(m.profit_factor, 999.0)
            verdict = evaluate_strategy(
                trade_count=m.trade_count, win_rate=m.win_rate,
                expectancy=m.expectancy, profit_factor=pf,
                max_drawdown_pct=m.max_drawdown_pct,
                total_return_pct=m.total_return_pct,
            )

            if verdict.verdict == Verdict.KEEP:
                survivors.append((variant, m))
                if m.expectancy > best_expectancy:
                    best_expectancy = m.expectancy
                    best = (variant, m)
        except Exception:
            continue

    logger.info(
        "param_sweep: %d variants tested, %d survived (symbol=%s, tf=%s)",
        len(variants), len(survivors), config.symbol, config.timeframe,
    )

    return SweepResult(
        variants_tested=len(variants),
        variants_survived=len(survivors),
        best_variant=best[0],
        best_metrics=best[1],
        all_survivors=survivors,
    )


def _generate_variants(config: SweepConfig) -> list[RuleSet]:
    """Generate parameter grid variants from the base RuleSet."""
    base = config.base_ruleset
    period_values = list(range(*config.period_range))
    threshold_pcts = config.threshold_pct_variations
    stop_values = _frange(*config.stop_loss_range)

    variants: list[RuleSet] = []

    # Strategy 1: Vary indicator periods in entry conditions
    for cond_idx, cond in enumerate(_flat_conditions(base.entry)):
        if not isinstance(cond, Condition):
            continue
        if "period" in cond.left.params:
            for period in period_values:
                if period == cond.left.params.get("period"):
                    continue
                variant = _clone_with_period(base, "entry", cond_idx, period)
                if variant:
                    variants.append(variant)

    # Strategy 2: Vary thresholds in entry conditions
    for cond_idx, cond in enumerate(_flat_conditions(base.entry)):
        if not isinstance(cond, Condition):
            continue
        if isinstance(cond.right, (int, float)) and cond.right != 0:
            for pct in threshold_pcts:
                new_val = cond.right * (1 + pct)
                variant = _clone_with_threshold(base, "entry", cond_idx, new_val)
                if variant:
                    variants.append(variant)

    # Strategy 3: Vary stop-loss
    if base.stop_loss and base.stop_loss.mode == "fixed_pct":
        for sl_val in stop_values:
            if abs(sl_val - base.stop_loss.value) < 0.01:
                continue
            variant = deepcopy(base)
            variant.stop_loss = StopLoss(mode="fixed_pct", value=sl_val)
            variants.append(variant)

    # Cap at max_variants (random sample if exceeded)
    if len(variants) > config.max_variants:
        variants = random.sample(variants, config.max_variants)

    return variants


def _flat_conditions(expr: RuleExpr) -> list[Condition]:
    """Flatten a RuleExpr tree into a list of leaf Conditions."""
    result = []
    for item in expr.conditions:
        if isinstance(item, Condition):
            result.append(item)
        elif isinstance(item, RuleExpr):
            result.extend(_flat_conditions(item))
    return result


def _clone_with_period(base: RuleSet, side: str, cond_idx: int, new_period: int) -> RuleSet | None:
    """Clone RuleSet with one condition's indicator period changed."""
    variant = deepcopy(base)
    expr = variant.entry if side == "entry" else variant.exit
    conditions = _flat_conditions(expr)
    if cond_idx >= len(conditions):
        return None
    conditions[cond_idx].left.params["period"] = new_period
    return variant


def _clone_with_threshold(base: RuleSet, side: str, cond_idx: int, new_val: float) -> RuleSet | None:
    """Clone RuleSet with one condition's threshold changed."""
    variant = deepcopy(base)
    expr = variant.entry if side == "entry" else variant.exit
    conditions = _flat_conditions(expr)
    if cond_idx >= len(conditions):
        return None
    conditions[cond_idx].right = new_val
    return variant


def _frange(start: float, stop: float, step: float) -> list[float]:
    """Float range helper."""
    result = []
    val = start
    while val <= stop + 1e-9:
        result.append(round(val, 2))
        val += step
    return result

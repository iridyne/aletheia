"""Structured kill/keep strategy evaluation based on backtest metrics.

Replaces narrative LLM review with deterministic rules for the first pass.
The LLM review can still add qualitative nuance on top; this module
provides the hard quantitative verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Verdict(str, Enum):
    KILL = "kill"
    KEEP = "keep"
    REVIEW = "review"  # borderline — needs human judgment


@dataclass
class ReviewThreshold:
    """Configurable thresholds for the kill/keep decision."""
    # KILL conditions (any one triggers kill)
    max_drawdown_kill: float = 8.0       # % — strategy lost too much peak-to-trough
    min_win_rate_kill: float = 0.20      # below 20% wins is noise
    min_expectancy_kill: float = 0.0     # negative expectancy = losing money
    min_profit_factor_kill: float = 0.8  # below 0.8 is clearly net-negative

    # KEEP conditions (ALL must hold for a keep)
    max_drawdown_keep: float = 5.0       # % — acceptable drawdown
    min_win_rate_keep: float = 0.35      # 35%+ wins
    min_expectancy_keep: float = 0.5     # positive per-trade average (USDT)
    min_profit_factor_keep: float = 1.2  # gross_profit > 1.2x gross_loss
    min_trades_keep: int = 5             # at least 5 trades to be statistically meaningful


DEFAULT_THRESHOLDS = ReviewThreshold()


@dataclass
class StrategyVerdict:
    """The output of evaluate_strategy."""
    verdict: Verdict
    reasons: list[str]       # why this verdict was reached
    metrics_summary: dict    # raw metrics for audit trail


def evaluate_strategy(
    trade_count: int,
    win_rate: float,
    expectancy: float,
    profit_factor: float,
    max_drawdown_pct: float,
    total_return_pct: float = 0.0,
    thresholds: ReviewThreshold = DEFAULT_THRESHOLDS,
) -> StrategyVerdict:
    """Deterministic kill/keep/review based on backtest metrics.

    Returns StrategyVerdict with verdict and reasons.
    Evaluation order: KILL checks first, then KEEP checks, else REVIEW.
    """
    reasons: list[str] = []
    metrics_summary = {
        "trade_count": trade_count,
        "win_rate": win_rate,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "total_return_pct": total_return_pct,
    }

    # ── KILL checks (any one triggers) ──
    if trade_count < 2:
        return StrategyVerdict(
            verdict=Verdict.REVIEW,
            reasons=["insufficient_trades: need at least 2 for any judgment"],
            metrics_summary=metrics_summary,
        )

    kill_reasons: list[str] = []
    if max_drawdown_pct > thresholds.max_drawdown_kill:
        kill_reasons.append(f"drawdown {max_drawdown_pct:.1f}% > {thresholds.max_drawdown_kill}% limit")
    if win_rate < thresholds.min_win_rate_kill:
        kill_reasons.append(f"win_rate {win_rate:.1%} < {thresholds.min_win_rate_kill:.0%} minimum")
    if expectancy < thresholds.min_expectancy_kill:
        kill_reasons.append(f"expectancy {expectancy:+.2f} < 0 (net-losing)")
    if profit_factor < thresholds.min_profit_factor_kill:
        kill_reasons.append(f"profit_factor {profit_factor:.2f} < {thresholds.min_profit_factor_kill} minimum")

    if kill_reasons:
        return StrategyVerdict(verdict=Verdict.KILL, reasons=kill_reasons, metrics_summary=metrics_summary)

    # ── KEEP checks (ALL must hold) ──
    keep_checks = [
        (max_drawdown_pct <= thresholds.max_drawdown_keep,
         f"drawdown {max_drawdown_pct:.1f}% <= {thresholds.max_drawdown_keep}%"),
        (win_rate >= thresholds.min_win_rate_keep,
         f"win_rate {win_rate:.1%} >= {thresholds.min_win_rate_keep:.0%}"),
        (expectancy >= thresholds.min_expectancy_keep,
         f"expectancy {expectancy:+.2f} >= {thresholds.min_expectancy_keep}"),
        (profit_factor >= thresholds.min_profit_factor_keep,
         f"profit_factor {profit_factor:.2f} >= {thresholds.min_profit_factor_keep}"),
        (trade_count >= thresholds.min_trades_keep,
         f"trade_count {trade_count} >= {thresholds.min_trades_keep}"),
    ]
    all_pass = all(check for check, _ in keep_checks)
    reasons = [desc for check, desc in keep_checks]

    if all_pass:
        return StrategyVerdict(verdict=Verdict.KEEP, reasons=reasons, metrics_summary=metrics_summary)

    # ── REVIEW (borderline) ──
    failed = [desc for check, desc in keep_checks if not check]
    return StrategyVerdict(
        verdict=Verdict.REVIEW,
        reasons=[f"borderline — failed KEEP on: {'; '.join(failed)}"],
        metrics_summary=metrics_summary,
    )

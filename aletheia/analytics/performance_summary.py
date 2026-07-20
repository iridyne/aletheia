"""Consolidated, risk-adjusted performance summary for closed trades.

The analytics package already produces per-dimension aggregations
(``trade_stats``, ``trade_insights`` equity curve, ``go_live_scorecard``
drawdown guard, ``factor_feedback`` profit factor). This module collects the
*standard operator-facing risk-adjusted metrics* into one small, **pure**
library so they can be unit-tested without a database and reused by reports:

* equity curve (cumulative PnL)
* max drawdown (peak-to-trough)
* profit factor (gross profit / gross loss)
* per-trade expectancy
* win rate
* Calmar ratio (total return / max drawdown)

``consolidate_report`` then merges the existing analytics outputs (rolling
stats, full trade stats, equity curve, order stats) into a single
operator-facing dict.
"""
from __future__ import annotations

from typing import Iterable, Sequence


def equity_curve(pnls: Sequence[float]) -> list[float]:
    """Cumulative equity curve from an ordered per-trade PnL series."""
    out: list[float] = []
    running = 0.0
    for p in pnls:
        running += float(p)
        out.append(running)
    return out


def max_drawdown(equity: Sequence[float]) -> float:
    """Peak-to-trough decline of the equity curve (always >= 0)."""
    if not equity:
        return 0.0
    peak = float(equity[0])
    mdd = 0.0
    for v in equity:
        v = float(v)
        if v > peak:
            peak = v
        dd = peak - v
        if dd > mdd:
            mdd = dd
    return mdd


def profit_factor(pnls: Sequence[float]) -> float:
    """Gross profit divided by gross loss.

    Returns ``inf`` when there are profits but no losses, ``0.0`` when there
    are no trades or only losses.
    """
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def win_rate(pnls: Sequence[float]) -> float:
    """Fraction of trades with positive PnL in [0, 1]."""
    if not pnls:
        return 0.0
    return sum(1 for p in pnls if p > 0) / len(pnls)


def expectancy(pnls: Sequence[float]) -> float:
    """Mean per-trade PnL."""
    if not pnls:
        return 0.0
    return sum(pnls) / len(pnls)


def calmar(total_return: float, max_dd: float) -> float:
    """Total return divided by max drawdown (0.0 when there is no drawdown)."""
    if max_dd <= 0:
        return 0.0
    return float(total_return) / float(max_dd)


def summarize(pnls: Sequence[float]) -> dict:
    """Full risk-adjusted summary from an ordered per-trade PnL series.

    ``profit_factor`` may be ``inf`` (profits, no losses); ``max_drawdown`` and
    ``final_equity`` are floats. All values are JSON-serialisable except an
    ``inf`` profit factor, which callers may stringify for display.
    """
    pnls = [float(p) for p in pnls]
    eq = equity_curve(pnls)
    total = sum(pnls)
    mdd = max_drawdown(eq)
    return {
        "trade_count": len(pnls),
        "total_pnl": round(total, 6),
        "win_rate_pct": round(win_rate(pnls) * 100, 2),
        "profit_factor": profit_factor(pnls),
        "expectancy": round(expectancy(pnls), 6),
        "max_drawdown": round(mdd, 6),
        "final_equity": round(eq[-1], 6) if eq else 0.0,
        "calmar": round(calmar(total, mdd), 4),
    }


def consolidate_report(
    rolling_stats: dict | None = None,
    trade_stats: dict | None = None,
    equity: Sequence[float] | None = None,
    order_stats: dict | None = None,
) -> dict:
    """Merge existing analytics outputs into one operator-facing report.

    Each argument is optional; missing sections are omitted so callers can
    build a partial report when a data source (e.g. DB) is unavailable.
    """
    report: dict = {"sections": []}

    if rolling_stats is not None:
        report["rolling"] = rolling_stats
        report["sections"].append("rolling")

    if trade_stats is not None:
        report["trade_stats"] = trade_stats
        report["sections"].append("trade_stats")

    if order_stats is not None:
        report["order_stats"] = order_stats
        report["sections"].append("order_stats")

    if equity is not None:
        eq = list(equity)
        report["equity_curve_points"] = len(eq)
        report["equity_metrics"] = {
            "final_equity": round(eq[-1], 6) if eq else 0.0,
            "max_drawdown": round(max_drawdown(eq), 6),
        }
        report["sections"].append("equity")

    return report

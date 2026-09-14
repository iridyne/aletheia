"""Trade attribution: segment closed-trade PnL by symbol / strategy / close_reason.

The rest of the analytics package reports *totals* (``trade_stats``, the
risk-adjusted summary in ``performance_summary``). This module answers the
operator question *"where does the PnL come from?"* by slicing the same
eligible closed outcomes along three dimensions and computing per-segment
risk metrics.

Pure functions only -> unit-testable with no database. The outcome dicts
are the same shape returned by
``aletheia.analytics.outcome_integrity.eligible_strategy_outcomes``
(one dict per closed ``positions`` row, carrying ``symbol``, ``strategy_id``,
``close_reason`` and ``realized_pnl``).
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from aletheia.analytics.performance_summary import (
    expectancy,
    max_drawdown,
    profit_factor,
    win_rate,
)


def _coerce_pnl(outcome: Mapping[str, Any]) -> float:
    """``realized_pnl`` -> float, tolerating NULL / non-numeric values."""
    raw = outcome.get("realized_pnl")
    try:
        return 0.0 if raw is None else float(raw)
    except (TypeError, ValueError):
        return 0.0


def group_pnls(
    outcomes: Iterable[Mapping[str, Any]], key: str
) -> dict[str, list[float]]:
    """Group the ordered PnL series by the value of ``outcome[key]``.

    ``None`` / empty segments collapse to a single ``"(unknown)"`` bucket so a
    missing dimension never silently drops trades.
    """
    grouped: dict[str, list[float]] = {}
    for o in outcomes:
        seg = o.get(key)
        if seg is None or seg == "":
            seg = "(unknown)"
        grouped.setdefault(str(seg), []).append(_coerce_pnl(o))
    return grouped


def segment_stats(pnls: Sequence[float]) -> dict:
    """Risk-adjusted summary for a single segment's PnL series."""
    if not pnls:
        return {
            "trade_count": 0,
            "win_rate_pct": 0.0,
            "total_pnl": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "max_drawdown": 0.0,
        }
    from aletheia.analytics.performance_summary import equity_curve

    eq = equity_curve(pnls)
    return {
        "trade_count": len(pnls),
        "win_rate_pct": round(win_rate(pnls) * 100, 2),
        "total_pnl": round(sum(pnls), 6),
        "profit_factor": profit_factor(pnls),
        "expectancy": round(expectancy(pnls), 6),
        "max_drawdown": round(max_drawdown(eq), 6),
    }


def top_contributors(
    by_segment: Mapping[str, dict], metric: str = "total_pnl", n: int = 3
) -> dict:
    """Best and worst segments by ``metric`` (defaults to total PnL).

    Both lists are ranked from the *full* segment set, so the genuine
    losers are never masked by the top-n cutoff.
    """
    items = [
        (seg, float(stats.get(metric, 0.0))) for seg, stats in by_segment.items()
    ]
    items.sort(key=lambda kv: kv[1], reverse=True)
    best = items[: max(n, 1)]
    worst = sorted(items, key=lambda kv: kv[1])[: max(n, 1)]  # ascending: most-negative first
    return {"best": best, "worst": worst}


def attribute(outcomes: Iterable[Mapping[str, Any]]) -> dict:
    """Full attribution across symbol / strategy / close_reason dimensions."""
    outcomes = list(outcomes)
    by_symbol = {
        s: segment_stats(p) for s, p in group_pnls(outcomes, "symbol").items()
    }
    by_strategy = {
        s: segment_stats(p)
        for s, p in group_pnls(outcomes, "strategy_id").items()
    }
    by_reason = {
        s: segment_stats(p)
        for s, p in group_pnls(outcomes, "close_reason").items()
    }
    return {
        "outcome_count": len(outcomes),
        "by_symbol": by_symbol,
        "by_strategy": by_strategy,
        "by_close_reason": by_reason,
        "top_contributors": top_contributors(by_symbol, "total_pnl", 3),
    }

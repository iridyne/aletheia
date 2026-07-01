"""Factor feedback — group attribution rows by factor, compute statistics.

Classifies each factor as: watch / blocked_candidate / active / preferred.
Used by PaperTrader v2 to avoid losing factors and prioritize winning ones.

Read-only. No DB mutations. No LLM calls.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from aletheia.analytics.close_reasons import is_synthetic_close
from aletheia.analytics.trade_attribution import (
    build_trade_attribution_rows,
    confidence_bucket,
    factor_key,
    late_entry_bucket,
)

logger = logging.getLogger("aletheia.analytics.factor_feedback")


def _genuine_rows(rows: list[dict]) -> list[dict]:
    """Drop synthetic/administrative closes (reconciler orphan sweeps, cleanups).

    These are not trade outcomes — feeding them to the signal gate mis-teaches
    win-rate / expectancy (they mostly close at ≈entry, near-random). See
    close_reasons.py + docs/research/ENTRY_EXIT_ASYMMETRY_ANALYSIS_2026-07-01.md.
    """
    return [r for r in rows if not is_synthetic_close(r.get("close_reason"))]


def build_factor_feedback(limit: int = 500, min_trades: int = 3) -> list[dict]:
    """Build factor feedback from closed trade attribution data.

    Groups by multiple dimensions:
    - exact factor_key
    - scanner_family
    - scanner_family + direction
    - symbol
    - late_entry_bucket

    Returns a list of factor feedback dicts with status classification.
    """
    rows = _genuine_rows(build_trade_attribution_rows(limit=limit))
    if not rows:
        return []

    groups: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        fk = factor_key(row)
        groups[f"exact:{fk}"].append(row)

        scanner = row.get("scanner_family") or "unknown"
        groups[f"scanner:{scanner}"].append(row)

        direction = row.get("direction") or "unknown"
        groups[f"scanner_dir:{scanner}:{direction}"].append(row)

        symbol = row.get("symbol") or "unknown"
        groups[f"symbol:{symbol}"].append(row)

        late = late_entry_bucket(row.get("late_entry_penalty"))
        groups[f"late_entry:{late}"].append(row)

    results: list[dict] = []
    for group_key, group_rows in groups.items():
        group_type, _, key_value = group_key.partition(":")
        fb = _compute_group_stats(key_value, group_type, group_rows, min_trades)
        results.append(fb)

    return results


def lookup_factor_signal(candidate: dict) -> dict:
    """Look up factor feedback for a specific candidate opportunity.

    Returns the signal for the most specific matching factor (exact key).
    Falls back to scanner-level. Returns 'watch' if no history.
    """
    rows = _genuine_rows(build_trade_attribution_rows(limit=500))
    if not rows:
        return _watch_signal(factor_key(candidate))

    # Build exact-key group
    target_key = factor_key(candidate)
    exact_rows = [r for r in rows if factor_key(r) == target_key]
    if exact_rows:
        stats = _compute_group_stats(target_key, "exact", exact_rows, min_trades=3)
        return {
            "factor_key": target_key,
            "status": stats["status"],
            "expectancy": stats["expectancy"],
            "win_rate": stats["win_rate"],
            "trade_count": stats["trade_count"],
            "reason": stats["reason"],
        }

    # Fallback: scanner + direction
    scanner = candidate.get("scanner_family") or "unknown"
    direction = candidate.get("direction") or "unknown"
    scanner_dir_rows = [
        r for r in rows
        if (r.get("scanner_family") or "unknown") == scanner
        and (r.get("direction") or "unknown") == direction
    ]
    if scanner_dir_rows:
        stats = _compute_group_stats(
            f"{scanner}:{direction}", "scanner_dir", scanner_dir_rows, min_trades=3
        )
        return {
            "factor_key": target_key,
            "status": stats["status"],
            "expectancy": stats["expectancy"],
            "win_rate": stats["win_rate"],
            "trade_count": stats["trade_count"],
            "reason": stats["reason"],
        }

    return _watch_signal(target_key)


def _watch_signal(fk: str) -> dict:
    return {
        "factor_key": fk,
        "status": "watch",
        "expectancy": None,
        "win_rate": None,
        "trade_count": 0,
        "reason": "no_history",
    }


def _compute_group_stats(
    key_value: str, group_type: str, rows: list[dict], min_trades: int
) -> dict:
    """Compute statistics for a group of attribution rows."""
    trade_count = len(rows)
    wins = [r for r in rows if (r.get("realized_pnl") or 0) > 0]
    losses = [r for r in rows if (r.get("realized_pnl") or 0) < 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = win_count / trade_count if trade_count else 0.0

    avg_win = (
        sum(r.get("realized_pnl", 0) for r in wins) / win_count
        if win_count else 0.0
    )
    avg_loss = (
        sum(abs(r.get("realized_pnl", 0)) for r in losses) / loss_count
        if loss_count else 0.0
    )
    expectancy = avg_win * win_rate - avg_loss * (1 - win_rate)
    total_pnl = sum(r.get("realized_pnl", 0) for r in rows)
    gross_profit = sum(r.get("realized_pnl", 0) for r in wins)
    gross_loss = sum(abs(r.get("realized_pnl", 0)) for r in losses)
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

    max_consecutive_losses = _max_consecutive_losses(rows)
    avg_holding_hours = _avg_holding_hours(rows)
    most_common_close_reason = _most_common_close_reason(rows)

    status, reason = _classify_factor(
        trade_count, min_trades, expectancy, win_rate, profit_factor
    )

    return {
        "factor_key": key_value,
        "group_type": group_type,
        "trade_count": trade_count,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "expectancy": round(expectancy, 4),
        "total_pnl": round(total_pnl, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999.0,
        "max_consecutive_losses": max_consecutive_losses,
        "avg_holding_hours": avg_holding_hours,
        "most_common_close_reason": most_common_close_reason,
        "status": status,
        "reason": reason,
    }


_MIN_TRADES_FOR_PREFERRED = 8     # P0-4: raised from 3; 3-trade win_rate CI spans 0-1
_MIN_TRADES_FOR_HARD_BLOCKED = 10  # P1-4: need sufficient evidence before hard block


def _classify_factor(
    trade_count: int,
    min_trades: int,
    expectancy: float,
    win_rate: float,
    profit_factor: float,
) -> tuple[str, str]:
    """Classify a factor into watch/blocked_candidate/hard_blocked/active/preferred."""
    if trade_count < min_trades:
        return "watch", f"insufficient_data({trade_count}/{min_trades})"

    # P1-4: hard_blocked — strong negative signal with sufficient evidence
    if (trade_count >= _MIN_TRADES_FOR_HARD_BLOCKED
            and win_rate < 0.30 and expectancy < -0.5):
        return "hard_blocked", f"hard_blocked:wr={win_rate:.2%}_n={trade_count}"

    if expectancy < 0 and win_rate < 0.40:
        return "blocked_candidate", (
            f"negative_expectancy({expectancy:.4f})_low_winrate({win_rate:.2%})"
        )

    # P0-4: outlier detection — high PF with small sample may be single-trade outlier
    if profit_factor > 5.0 and trade_count < 15:
        return "active", f"possible_outlier_driven(pf={profit_factor:.1f},n={trade_count})"

    # P0-4: require n >= 8 and win_rate >= 0.45 for preferred promotion
    if (expectancy > 0 and profit_factor >= 1.2
            and win_rate >= 0.45
            and trade_count >= _MIN_TRADES_FOR_PREFERRED):
        return "preferred", (
            f"positive_expectancy({expectancy:.4f})_pf({profit_factor:.2f})"
            f"_wr={win_rate:.2%}_n={trade_count}"
        )

    return "active", "meets_minimum_criteria"


def _max_consecutive_losses(rows: list[dict]) -> int:
    """Count max consecutive losing trades (sorted by closed_at)."""
    sorted_rows = sorted(rows, key=lambda r: r.get("closed_at") or "")
    max_streak = 0
    current = 0
    for r in sorted_rows:
        if (r.get("realized_pnl") or 0) < 0:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def _avg_holding_hours(rows: list[dict]) -> float | None:
    hours = [r["holding_hours"] for r in rows if r.get("holding_hours") is not None]
    if not hours:
        return None
    return round(sum(hours) / len(hours), 2)


def _most_common_close_reason(rows: list[dict]) -> str | None:
    reasons = [r.get("close_reason") for r in rows if r.get("close_reason")]
    if not reasons:
        return None
    return Counter(reasons).most_common(1)[0][0]

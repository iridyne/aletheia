"""Exit policy advisor — diagnose whether losses stem from stop/TP/hold design.

Proposes exit-policy changes but NEVER applies them. Output is data only.

Read-only. No DB mutations. No LLM calls. No config writes.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from aletheia.analytics.trade_attribution import factor_key
from aletheia.analytics.trade_path import build_trade_path_rows

logger = logging.getLogger("aletheia.analytics.exit_policy_advisor")


def build_exit_policy_advice(limit: int = 500, min_trades: int = 3) -> list[dict]:
    """Build exit-policy advice from MFE/MAE evidence.

    Groups by strategy_id, scanner_family, factor_key, symbol.
    Each group gets a diagnosis and suggested change.
    """
    path_rows = build_trade_path_rows(limit=limit)
    if not path_rows:
        return []

    # Filter to rows with path data
    rows_with_paths = [r for r in path_rows if r.get("path_bucket") != "insufficient_path_data"]
    if not rows_with_paths:
        return []

    # Group by multiple dimensions
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows_with_paths:
        sid = row.get("strategy_id")
        if sid:
            groups[f"strategy:{sid}"].append(row)

        scanner = row.get("scanner_family") or "unknown"
        groups[f"scanner:{scanner}"].append(row)

        fk = factor_key(row)
        groups[f"factor:{fk}"].append(row)

        symbol = row.get("symbol") or "unknown"
        groups[f"symbol:{symbol}"].append(row)

    results: list[dict] = []
    for group_key, group_rows in groups.items():
        group_type, _, key_value = group_key.partition(":")
        advice = _diagnose_group(key_value, group_type, group_rows, min_trades)
        results.append(advice)

    return results


def _diagnose_group(
    key_value: str, group_type: str, rows: list[dict], min_trades: int
) -> dict:
    """Diagnose exit policy for a group of trades."""
    trade_count = len(rows)

    # Compute averages
    avg_realized = _avg(rows, "realized_return_pct")
    avg_mfe = _avg(rows, "mfe_pct")
    avg_mae = _avg(rows, "mae_pct")
    captures = [r["mfe_capture_ratio"] for r in rows if r.get("mfe_capture_ratio") is not None]
    avg_capture = round(sum(captures) / len(captures), 4) if captures else None

    # Common close reason and path bucket
    close_reasons = [r.get("close_reason") for r in rows if r.get("close_reason")]
    common_close_reason = Counter(close_reasons).most_common(1)[0][0] if close_reasons else None

    buckets = [r.get("path_bucket") for r in rows if r.get("path_bucket")]
    common_bucket = Counter(buckets).most_common(1)[0][0] if buckets else None
    bucket_counts = Counter(buckets)

    # Diagnosis
    if trade_count < min_trades:
        diagnosis = "insufficient_evidence"
        suggested = "no_change"
        confidence = "low"
    else:
        diagnosis, suggested, confidence = _classify_exit_problem(
            rows, avg_mfe, avg_mae, avg_realized, avg_capture,
            common_close_reason, bucket_counts, trade_count,
        )

    return {
        "group_key": key_value,
        "group_type": group_type,
        "trade_count": trade_count,
        "avg_realized_return_pct": avg_realized,
        "avg_mfe_pct": avg_mfe,
        "avg_mae_pct": avg_mae,
        "mfe_capture_ratio_avg": avg_capture,
        "common_close_reason": common_close_reason,
        "common_path_bucket": common_bucket,
        "diagnosis": diagnosis,
        "suggested_change": suggested,
        "confidence": confidence,
    }


def _classify_exit_problem(
    rows: list[dict],
    avg_mfe: float | None,
    avg_mae: float | None,
    avg_realized: float | None,
    avg_capture: float | None,
    common_close_reason: str | None,
    bucket_counts: Counter,
    trade_count: int,
) -> tuple[str, str, str]:
    """Classify the exit problem and suggest a change."""
    total = trade_count or 1

    # Count dominant buckets as fractions
    bad_entry_frac = bucket_counts.get("bad_entry", 0) / total
    stop_tight_frac = bucket_counts.get("stop_too_tight", 0) / total
    edge_lost_frac = bucket_counts.get("edge_lost", 0) / total
    time_decay_frac = bucket_counts.get("time_exit_decay", 0) / total
    tp_early_frac = bucket_counts.get("take_profit_too_early", 0) / total

    # Entry problem: signal is bad, not exit
    if bad_entry_frac >= 0.4:
        return (
            "entry_problem",
            "no_change",
            "high" if bad_entry_frac >= 0.6 else "medium",
        )

    # Stop too tight: has edge but gets stopped out
    if stop_tight_frac >= 0.3:
        if avg_mfe and avg_mfe > 1.0:
            return "stop_too_tight", "switch_to_atr_stop", "high"
        return "stop_too_tight", "increase_stop_loss_pct", "medium"

    # Edge lost: had MFE but couldn't capture it
    if edge_lost_frac >= 0.3:
        return "edge_lost", "add_trailing_stop_candidate", "medium"

    # Time decay: max_hold exits with early peak
    if time_decay_frac >= 0.3:
        # Check if MFE came early (first half of bars)
        early_mfe_count = sum(
            1 for r in rows
            if r.get("time_to_mfe_bars") is not None
            and r.get("bars_in_trade") is not None
            and r["time_to_mfe_bars"] < r["bars_in_trade"] * 0.4
        )
        if early_mfe_count / total > 0.5:
            return "max_hold_too_long", "shorten_max_hold", "medium"
        return "max_hold_too_short", "extend_max_hold", "low"

    # Take profit too early
    if tp_early_frac >= 0.3:
        return "take_profit_too_early", "increase_take_profit_pct", "medium"

    # Signal bad but exits are fine
    if avg_realized is not None and avg_realized < 0 and avg_capture and avg_capture > 0.4:
        return "exit_ok_signal_bad", "pause_factor_until_more_data", "medium"

    return "no_clear_issue", "no_change", "low"


def _avg(rows: list[dict], field: str) -> float | None:
    values = [r[field] for r in rows if r.get(field) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)

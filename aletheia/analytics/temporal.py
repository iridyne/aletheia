#!/usr/bin/env python3
"""Temporal PnL analysis — when does the strategy actually make/lose money?

Complements the existing analytics which either report totals (``trade_stats``,
``factor_feedback``), segment by symbol/strategy/reason (``attribution``), or
look at sequence shape (``streaks``). This module slices closed-trade PnL by
**time of day** and **day of week**, answering "which hours / weekdays are my
best and worst" — a dimension none of the above cover.

Inputs are the same ``eligible_strategy_outcomes()`` rows the report already
consumes (each carries ``closed_at`` and ``realized_pnl``). Outcomes without a
parseable ``closed_at`` are skipped from the temporal buckets (they cannot be
placed on a clock) but are still counted so the caller knows how many were
ignored.
"""
from __future__ import annotations

from datetime import datetime
from typing import Sequence

from aletheia.analytics.attribution import segment_stats


def _parse_closed_at(value) -> datetime | None:
    """Parse an ISO ``closed_at`` string into a tz-aware datetime, or None."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # normalize trailing 'Z' (UTC) which fromisoformat <3.11 cannot read
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _bucket(outcomes: Sequence[dict], key_fn) -> dict:
    """Group outcomes by ``key_fn(dt)`` -> list of realized_pnl floats."""
    buckets: dict = {}
    for o in outcomes:
        dt = _parse_closed_at(o.get("closed_at"))
        if dt is None:
            continue
        pnl = float(o.get("realized_pnl") or 0.0)
        key = key_fn(dt)
        buckets.setdefault(key, []).append(pnl)
    return buckets


def bucket_by_hour(outcomes: Sequence[dict]) -> dict[int, list[float]]:
    """Map hour-of-day (0-23) -> list of realized PnL."""
    return _bucket(outcomes, lambda dt: dt.hour)


def bucket_by_weekday(outcomes: Sequence[dict]) -> dict[int, list[float]]:
    """Map weekday (0=Mon .. 6=Sun) -> list of realized PnL."""
    return _bucket(outcomes, lambda dt: dt.weekday())


def _rank(buckets: dict, metric: str = "total_pnl"):
    """Return (best_key, best_val, worst_key, worst_val) by ``metric``."""
    if not buckets:
        return (None, None, None, None)
    scored = {
        k: segment_stats(v).get(metric, 0.0) for k, v in buckets.items()
    }
    best_key = max(scored, key=lambda k: scored[k])
    worst_key = min(scored, key=lambda k: scored[k])
    return (best_key, scored[best_key], worst_key, scored[worst_key])


def temporal_summary(outcomes: Sequence[dict]) -> dict:
    """Full temporal breakdown across hour and weekday dimensions.

    Returns per-bucket segment stats plus best/worst pointers and counts so the
    caller can render or machine-read the result.
    """
    by_hour_raw = bucket_by_hour(outcomes)
    by_weekday_raw = bucket_by_weekday(outcomes)

    by_hour = {h: segment_stats(pnls) for h, pnls in by_hour_raw.items()}
    by_weekday = {d: segment_stats(pnls) for d, pnls in by_weekday_raw.items()}

    best_h, best_hv, worst_h, worst_hv = _rank(by_hour_raw, "total_pnl")
    best_d, best_dv, worst_d, worst_dv = _rank(by_weekday_raw, "total_pnl")

    return {
        "outcome_count": len(outcomes),
        "bucketed_count": sum(len(v) for v in by_hour_raw.values()),
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "best_hour": {"hour": best_h, "total_pnl": best_hv},
        "worst_hour": {"hour": worst_h, "total_pnl": worst_hv},
        "best_weekday": {"weekday": best_d, "total_pnl": best_dv},
        "worst_weekday": {"weekday": worst_d, "total_pnl": worst_dv},
    }

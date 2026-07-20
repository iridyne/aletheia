#!/usr/bin/env python3
"""Win/loss streak analysis over an ordered sequence of trade PnL values.

This is the order-aware, sequence-level counterpart to the loss-only
``_max_consecutive_losses`` helper inside ``factor_feedback.py`` (which is
private, factor-group scoped, and only counts losing runs). Here the input is a
plain ``Sequence[float]`` -- one realized PnL per closed trade, in chronological
order -- and it answers the questions a totals-only report cannot:

* what is the longest run of consecutive wins / losses?
* what is the current (trailing) streak and how long has it run?

All functions are pure and need no database, so they are trivially unit-testable
and are reused directly by ``performance_report.py``.
"""
from __future__ import annotations

from typing import Sequence

WIN = "win"
LOSS = "loss"
FLAT = "flat"


def classify(pnl: float) -> str:
    """Bucket a single PnL into ``win`` / ``loss`` / ``flat``."""
    if pnl > 0:
        return WIN
    if pnl < 0:
        return LOSS
    return FLAT


def runs(pnls: Sequence[float]) -> list[dict]:
    """Split an ordered PnL sequence into consecutive same-sign runs.

    Returns a list of ``{"sign", "length", "start", "end"}`` where ``end`` is
    inclusive. Flat (zero) trades form their own runs -- they are not merged
    into neighbouring wins/losses.
    """
    out: list[dict] = []
    if not pnls:
        return out
    cur_sign = classify(pnls[0])
    start = 0
    length = 1
    for i in range(1, len(pnls)):
        s = classify(pnls[i])
        if s == cur_sign:
            length += 1
            continue
        out.append({"sign": cur_sign, "length": length, "start": start, "end": i - 1})
        cur_sign = s
        start = i
        length = 1
    out.append({"sign": cur_sign, "length": length, "start": start, "end": len(pnls) - 1})
    return out


def longest_streak(pnls: Sequence[float], sign: str) -> int:
    """Longest run length for ``sign`` (one of ``WIN``/``LOSS``/``FLAT``).

    Returns 0 when there are no runs of that sign (or no trades at all).
    """
    return max((r["length"] for r in runs(pnls) if r["sign"] == sign), default=0)


def max_consecutive_wins(pnls: Sequence[float]) -> int:
    return longest_streak(pnls, WIN)


def max_consecutive_losses(pnls: Sequence[float]) -> int:
    return longest_streak(pnls, LOSS)


def current_streak(pnls: Sequence[float]) -> dict:
    """Trailing run descriptor: ``{"sign", "length"}``.

    For an empty sequence returns ``{"sign": None, "length": 0}``. ``sign`` may
    be ``FLAT`` when the most recent trade(s) broke even.
    """
    if not pnls:
        return {"sign": None, "length": 0}
    last = runs(pnls)[-1]
    return {"sign": last["sign"], "length": last["length"]}


def summarize_streaks(pnls: Sequence[float]) -> dict:
    """One-call summary used by the report section."""
    cur = current_streak(pnls)
    return {
        "trade_count": len(pnls),
        "max_consecutive_wins": max_consecutive_wins(pnls),
        "max_consecutive_losses": max_consecutive_losses(pnls),
        "current_streak_sign": cur["sign"],
        "current_streak_length": cur["length"],
    }

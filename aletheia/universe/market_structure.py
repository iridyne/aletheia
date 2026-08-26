"""Deterministic, observation-only 1m market-structure facts."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Literal


ALGORITHM_VERSION = "market-structure/v1"
_TIMEFRAME_MS = 60_000
_PIVOT_SIDE = 2
_MIN_BARS = 30
_MAX_BARS = 120
_MAX_SWING_AGE = 60
# Owner-approved production versions only.  Empty by contract in v1: the
# evaluator is implemented, but no Price Action result has execution authority.
HARD_GATE_ALLOWLIST: frozenset[str] = frozenset()


def hard_gate_decision(
    assessment: dict,
    *,
    allowlist: frozenset[str] = HARD_GATE_ALLOWLIST,
) -> tuple[bool, str]:
    """Shared future gate seam; unqualified or unavailable versions fail closed."""

    version = str(assessment.get("algorithm_version") or "")
    if version not in allowlist:
        return False, f"price_action_gate_unqualified:{version or 'unknown'}"
    verdict = str(assessment.get("verdict") or "unavailable")
    if verdict != "allow":
        return False, f"price_action_gate_{verdict}"
    return True, "price_action_gate_allow"


def _unavailable(reason: str, *, input_fingerprint: str | None = None) -> dict:
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "verdict": "unavailable",
        "reason": reason,
        "input_fingerprint": input_fingerprint,
    }


def evaluate_market_structure(
    candles: list[dict], *, direction: Literal["long", "short"]
) -> dict:
    """Classify point-in-time BOS/CHoCH from continuous closed 1m bars.

    A pivot becomes available only after two later bars are present.  The
    calculation never rewrites a historical pivot timestamp.
    """

    rows: list[dict] = []
    try:
        for raw in candles[-_MAX_BARS:]:
            row = {
                "ts": int(raw["ts"]),
                "open": float(raw["open"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
                "volume": float(raw.get("volume", 0.0)),
            }
            if (
                any(not math.isfinite(row[key]) or row[key] <= 0 for key in ("open", "high", "low", "close"))
                or not math.isfinite(row["volume"])
                or row["volume"] < 0
                or row["high"] < max(row["open"], row["close"])
                or row["low"] > min(row["open"], row["close"])
            ):
                return _unavailable("invalid_candle")
            rows.append(row)
    except (KeyError, TypeError, ValueError):
        return _unavailable("invalid_candle")
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if len(rows) < _MIN_BARS:
        return _unavailable("insufficient_closed_bars", input_fingerprint=fingerprint)
    timestamps = [row["ts"] for row in rows]
    if any(right - left != _TIMEFRAME_MS for left, right in zip(timestamps, timestamps[1:])):
        return _unavailable("candle_gap", input_fingerprint=fingerprint)

    true_ranges: list[float] = []
    for index in range(1, len(rows)):
        row, previous = rows[index], rows[index - 1]
        true_ranges.append(
            max(
                row["high"] - row["low"],
                abs(row["high"] - previous["close"]),
                abs(row["low"] - previous["close"]),
            )
        )
    atr = sum(true_ranges[-14:]) / 14 if len(true_ranges) >= 14 else 0.0
    if not math.isfinite(atr) or atr <= 0:
        return _unavailable("atr_unavailable", input_fingerprint=fingerprint)

    highs: list[tuple[int, float, int]] = []
    lows: list[tuple[int, float, int]] = []
    for index in range(_PIVOT_SIDE, len(rows) - _PIVOT_SIDE):
        high = rows[index]["high"]
        low = rows[index]["low"]
        neighbors = rows[index - _PIVOT_SIDE:index] + rows[index + 1:index + 1 + _PIVOT_SIDE]
        confirmed_at = rows[index + _PIVOT_SIDE]["ts"]
        if all(high > item["high"] for item in neighbors):
            highs.append((index, high, confirmed_at))
        if all(low < item["low"] for item in neighbors):
            lows.append((index, low, confirmed_at))
    if len(highs) < 2 or len(lows) < 2:
        return _unavailable("structure_unconfirmed", input_fingerprint=fingerprint)
    if len(rows) - 1 - max(highs[-1][0], lows[-1][0]) > _MAX_SWING_AGE:
        return _unavailable("swing_stale", input_fingerprint=fingerprint)

    state = "range"
    if highs[-1][1] > highs[-2][1] and lows[-1][1] > lows[-2][1]:
        state = "higher_high_higher_low"
    elif highs[-1][1] < highs[-2][1] and lows[-1][1] < lows[-2][1]:
        state = "lower_high_lower_low"
    tolerance = 0.05 * atr
    close = rows[-1]["close"]
    event = None
    if state == "higher_high_higher_low":
        if close < lows[-1][1] - tolerance:
            event = "bearish_choch"
        elif close > highs[-1][1] + tolerance:
            event = "bullish_bos"
    elif state == "lower_high_lower_low":
        if close > highs[-1][1] + tolerance:
            event = "bullish_choch"
        elif close < lows[-1][1] - tolerance:
            event = "bearish_bos"

    aligned = (
        direction == "long" and state == "higher_high_higher_low"
    ) or (
        direction == "short" and state == "lower_high_lower_low"
    )
    opposed_choch = (direction == "long" and event == "bearish_choch") or (
        direction == "short" and event == "bullish_choch"
    )
    verdict = "block" if opposed_choch else "allow" if aligned else "unavailable"
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "verdict": verdict,
        "reason": event or state,
        "as_of": rows[-1]["ts"],
        "structure_state": state,
        "event": event,
        "atr14": atr,
        "break_tolerance": tolerance,
        "last_confirmed_swing_high": {
            "price": highs[-1][1], "pivot_at": rows[highs[-1][0]]["ts"], "confirmed_at": highs[-1][2]
        },
        "last_confirmed_swing_low": {
            "price": lows[-1][1], "pivot_at": rows[lows[-1][0]]["ts"], "confirmed_at": lows[-1][2]
        },
        "input_fingerprint": fingerprint,
    }

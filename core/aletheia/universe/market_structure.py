"""Fast Trigger compatibility projection over canonical market structure."""

from __future__ import annotations

from typing import Literal

from aletheia.data.market_structure import (
    COMPAT_ALGORITHM_VERSION,
    COMPAT_MAX_SWING_AGE,
    CORE_ALGORITHM_VERSION,
    WINDOW_BARS,
    derive_market_structure,
)
from aletheia.data.providers import Candle


ALGORITHM_VERSION = COMPAT_ALGORITHM_VERSION
_TIMEFRAME_MS = 60_000
# Owner-approved production versions only. Empty by contract: market structure
# remains observation-only shadow evidence with no execution authority.
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
        "core_algorithm_version": CORE_ALGORITHM_VERSION,
        "verdict": "unavailable",
        "reason": reason,
        "input_fingerprint": input_fingerprint,
    }


def evaluate_market_structure(
    candles: list[dict], *, direction: Literal["long", "short"]
) -> dict:
    """Project canonical closed-1m geometry into the legacy shadow dict."""

    try:
        rows = tuple(
            Candle(
                timestamp=int(raw["ts"]),
                open=float(raw["open"]),
                high=float(raw["high"]),
                low=float(raw["low"]),
                close=float(raw["close"]),
                volume=float(raw.get("volume", 0.0)),
            )
            for raw in candles[-WINDOW_BARS:]
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return _unavailable("invalid_candle")

    geometry = derive_market_structure(rows, tf_ms=_TIMEFRAME_MS)
    if geometry.availability == "unavailable":
        return _unavailable(
            geometry.unavailable_reason or "invalid_candle",
            input_fingerprint=geometry.geometry_fingerprint,
        )
    highs = [swing for swing in geometry.swings if swing.kind == "high"]
    lows = [swing for swing in geometry.swings if swing.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return _unavailable(
            "structure_unconfirmed",
            input_fingerprint=geometry.geometry_fingerprint,
        )
    if (
        len(rows) - 1 - max(highs[-1].bar_index, lows[-1].bar_index)
        > COMPAT_MAX_SWING_AGE
    ):
        return _unavailable(
            "swing_stale",
            input_fingerprint=geometry.geometry_fingerprint,
        )

    state_map = {
        "bullish": "higher_high_higher_low",
        "bearish": "lower_high_lower_low",
        "range": "range",
    }
    state = geometry.structure_state
    if state == "unconfirmed" or state is None:
        return _unavailable(
            "structure_unconfirmed",
            input_fingerprint=geometry.geometry_fingerprint,
        )
    compatibility_state = state_map[state]
    final_close_at_ms = rows[-1].timestamp + _TIMEFRAME_MS
    final_event = next(
        (
            event
            for event in reversed(geometry.events)
            if event.break_at_ms == final_close_at_ms
        ),
        None,
    )
    event_name = (
        f"{final_event.direction}_{final_event.type.lower()}"
        if final_event is not None
        else None
    )
    opposed_choch = final_event is not None and (
        final_event.type == "CHOCH"
        and (
            (direction == "long" and final_event.direction == "bearish")
            or (direction == "short" and final_event.direction == "bullish")
        )
    )
    aligned = (
        direction == "long" and state == "bullish"
    ) or (
        direction == "short" and state == "bearish"
    )
    verdict = "block" if opposed_choch else "allow" if aligned else "unavailable"
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "core_algorithm_version": CORE_ALGORITHM_VERSION,
        "verdict": verdict,
        "reason": event_name or compatibility_state,
        "as_of": final_close_at_ms,
        "structure_state": compatibility_state,
        "event": event_name,
        "atr14": geometry.atr14,
        "break_tolerance": geometry.break_tolerance,
        "last_confirmed_swing_high": {
            "price": highs[-1].price,
            "pivot_at": highs[-1].origin_at_ms,
            "confirmed_at": highs[-1].confirmed_at_ms,
        },
        "last_confirmed_swing_low": {
            "price": lows[-1].price,
            "pivot_at": lows[-1].origin_at_ms,
            "confirmed_at": lows[-1].confirmed_at_ms,
        },
        "input_fingerprint": geometry.geometry_fingerprint,
    }

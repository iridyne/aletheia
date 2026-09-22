"""Market-aware exit rule engine for position_manager.

Deterministic, pure functions, no side effects. Supplements (never replaces)
the existing price-based exits. Returns actions for the caller to execute.

Rule priority: urgent_exit > tighten_stop > none.
Conservative: false negatives preferred over false positives (premature close).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from .market_signal_reader import MarketSignalContext


@dataclass
class MarketExitAction:
    action: Literal["urgent_exit", "tighten_stop", "none"]
    reason: str
    new_stop_price: float | None = None
    exit_price: float | None = None


def evaluate_market_exit_rules(
    position: dict,
    price: float,
    signal: MarketSignalContext,
    cfg,
) -> MarketExitAction:
    """Apply market-aware exit rules. Returns highest-priority action."""
    market_cfg = cfg.trading.market_aware_exits
    if not market_cfg.enabled:
        return MarketExitAction(action="none", reason="disabled")

    is_long = position["direction"] in ("long", "buy")
    entry = float(position["entry_price"])
    hold_hours = _hold_hours(position)

    if entry > 0:
        pnl_pct = ((price - entry) / entry * 100.0) if is_long else ((entry - price) / entry * 100.0)
    else:
        pnl_pct = 0.0

    if hold_hours < market_cfg.min_hold_hours_before_market_exit:
        return MarketExitAction(action="none", reason="too_early")

    if signal.direction_bias is None or signal.confidence_score is None:
        return MarketExitAction(action="none", reason="no_signal")

    if signal.signal_age_min is not None and signal.signal_age_min > market_cfg.max_signal_age_min:
        return MarketExitAction(action="none", reason="signal_stale")

    signal_is_counter = (
        (is_long and signal.direction_bias == "short")
        or (not is_long and signal.direction_bias == "long")
    )
    signal_is_neutral = signal.direction_bias == "neutral"
    score = signal.confidence_score

    # Rule: Urgent exit — overwhelming counter-signal while losing
    if (signal_is_counter
            and score >= market_cfg.urgent_exit_score
            and pnl_pct <= market_cfg.urgent_exit_max_pnl_pct):
        return MarketExitAction(
            action="urgent_exit",
            exit_price=price,
            reason=f"counter_signal_urgent: {signal.direction_bias} score={score}",
        )

    # Rule: Tighten stop — confident counter-signal
    if signal_is_counter and score >= market_cfg.tighten_stop_score:
        new_stop = _compute_tightened_stop(price, signal, market_cfg, is_long)
        if new_stop is not None and _is_tighter(new_stop, position.get("stop_price"), is_long):
            return MarketExitAction(
                action="tighten_stop",
                new_stop_price=new_stop,
                reason=f"counter_signal_tighten: {signal.direction_bias} score={score}",
            )

    # Rule: Confidence collapse — neutral + low score + losing
    if (signal_is_neutral
            and score <= market_cfg.confidence_collapse_score
            and pnl_pct < 0):
        new_stop = _compute_tightened_stop(price, signal, market_cfg, is_long)
        if new_stop is not None and _is_tighter(new_stop, position.get("stop_price"), is_long):
            return MarketExitAction(
                action="tighten_stop",
                new_stop_price=new_stop,
                reason=f"confidence_collapse: score={score} pnl={pnl_pct:.1f}%",
            )

    return MarketExitAction(action="none", reason="no_rule_triggered")


def _compute_tightened_stop(
    price: float,
    signal: MarketSignalContext,
    market_cfg,
    is_long: bool,
) -> float | None:
    atr_pct = signal.atr_pct
    if atr_pct and atr_pct > 0:
        distance = price * (atr_pct / 100.0) * market_cfg.tighten_atr_mult
    else:
        distance = price * (market_cfg.tighten_pct / 100.0)
    if is_long:
        return round(price - distance, 8)
    else:
        return round(price + distance, 8)


def _is_tighter(new_stop: float, current_stop, is_long: bool) -> bool:
    if current_stop is None:
        return True
    current = float(current_stop)
    if is_long:
        return new_stop > current
    return new_stop < current


def _hold_hours(position: dict) -> float:
    opened_at = position.get("opened_at")
    if not opened_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except (ValueError, TypeError):
        return 0.0

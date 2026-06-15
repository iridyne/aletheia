"""RuleSet scanner — scan active strategies for live entry/exit signals.

Decouples signal detection from the opportunity pipeline: strategies with a
compiled RuleSet produce signals directly from OHLCV bars, without waiting for
market_observer to create an Opportunity first.

Returns lightweight signal objects; does NOT submit proposals or touch the DB.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from aletheia.storage.repositories import (
    get_ohlcv_candles,
    list_positions,
    list_strategies,
)
from aletheia.strategy.dsl import RuleSet
from aletheia.strategy.evaluator import BarData, compute_stop_price, evaluate_at_bar
from aletheia.trading.strategy_lifecycle import is_active_paper_strategy, is_paper_trading_paused

logger = logging.getLogger("aletheia.trading.ruleset_scanner")

_DEFAULT_TIMEFRAME = "1h"
_MIN_BARS = 30
_BAR_LIMIT = 200


@dataclass
class RuleSetSignal:
    strategy_id: str
    strategy_name: str
    symbol: str
    timeframe: str
    signal_type: str          # "entry" | "exit"
    direction: str            # "long" | "short" | "unknown" (for exit)
    ruleset: RuleSet
    bars: list[BarData]
    position_id: str | None = None   # set for exit signals (existing position)
    stop_price: float | None = None  # computed from RuleSet stop spec + entry price
    take_profit_price: float | None = None
    metadata: dict = field(default_factory=dict)


def scan_active_rulesets(execution_mode: str | None = None) -> list[RuleSetSignal]:
    """Scan all paper_running strategies with a compiled RuleSet.

    Returns entry signals for strategies with no open position on that symbol,
    and exit signals for open positions where the RuleSet's exit rule fires.

    execution_mode: if given, filter open positions to that mode only.
    """
    signals: list[RuleSetSignal] = []
    strategies = _get_active_ruleset_strategies()
    if not strategies:
        return signals

    open_positions = _index_open_positions(execution_mode)

    for strat in strategies:
        try:
            sigs = _scan_strategy(strat, open_positions)
            signals.extend(sigs)
        except Exception as e:
            logger.warning("scan_strategy failed for %s: %s", strat.get("id"), e)

    return signals


# ── internal ──────────────────────────────────────────────────────────────────

def _get_active_ruleset_strategies() -> list[dict]:
    """Return strategies that are paper_running, not paused, and have a RuleSet."""
    active = []
    for s in list_strategies(limit=200):
        if not is_active_paper_strategy(s):
            continue
        if is_paper_trading_paused(s):
            continue
        params = _parse_params(s.get("parameters", "{}"))
        if not params.get("ruleset"):
            continue
        active.append(s)
    return active


def _index_open_positions(execution_mode: str | None) -> dict[tuple[str, str], list[dict]]:
    """Index open positions by (symbol, strategy_id) for fast lookup."""
    index: dict[tuple[str, str], list[dict]] = {}
    for pos in list_positions(status="open"):
        if execution_mode and pos.get("execution_mode") != execution_mode:
            continue
        key = (pos["symbol"], pos.get("strategy_id") or "")
        index.setdefault(key, []).append(pos)
    return index


def _scan_strategy(
    strat: dict,
    open_positions: dict[tuple[str, str], list[dict]],
) -> list[RuleSetSignal]:
    params = _parse_params(strat.get("parameters", "{}"))
    ruleset_json = params.get("ruleset")
    if not ruleset_json:
        return []

    try:
        ruleset = RuleSet.from_json(ruleset_json if isinstance(ruleset_json, str) else json.dumps(ruleset_json))
    except Exception as e:
        logger.debug("RuleSet parse failed for strategy %s: %s", strat["id"], e)
        return []

    symbol = params.get("symbol")
    if not symbol:
        return []

    timeframe = params.get("timeframe") or _DEFAULT_TIMEFRAME
    candles = get_ohlcv_candles(symbol, timeframe, limit=_BAR_LIMIT)
    if len(candles) < _MIN_BARS:
        logger.debug("insufficient bars for %s %s: %d", symbol, timeframe, len(candles))
        return []

    bars = [
        BarData(
            ts=int(c["ts"]),
            open=float(c["open"]),
            high=float(c["high"]),
            low=float(c["low"]),
            close=float(c["close"]),
            volume=float(c["volume"]),
        )
        for c in candles
    ]

    sig = evaluate_at_bar(ruleset, bars, len(bars) - 1)
    entry_signal = sig.get("entry")
    exit_signal = sig.get("exit")

    signals: list[RuleSetSignal] = []
    sid = strat["id"]
    sname = strat.get("name", sid)
    direction = (params.get("direction_bias") or "long").lower()
    if direction not in ("long", "short"):
        direction = "long"

    existing = open_positions.get((symbol, sid), [])

    # Exit signals for open positions
    if exit_signal is True and existing:
        for pos in existing:
            signals.append(RuleSetSignal(
                strategy_id=sid,
                strategy_name=sname,
                symbol=symbol,
                timeframe=timeframe,
                signal_type="exit",
                direction=pos.get("direction", direction),
                ruleset=ruleset,
                bars=bars,
                position_id=pos["id"],
                metadata={"exit_reason": "ruleset_exit_signal"},
            ))

    # Entry signals only when no open position for this strategy+symbol
    if entry_signal is True and not existing:
        # Compute stop/TP from RuleSet spec using current price
        current_price = bars[-1].close
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]

        stop_price = None
        if ruleset.stop_loss:
            try:
                stop_price = compute_stop_price(
                    ruleset.stop_loss, current_price, direction, highs, lows, closes
                )
            except Exception:
                pass

        take_profit_price = None
        if ruleset.take_profit:
            try:
                take_profit_price = _compute_take_profit(
                    ruleset.take_profit, current_price, direction, highs, lows, closes
                )
            except Exception:
                pass

        signals.append(RuleSetSignal(
            strategy_id=sid,
            strategy_name=sname,
            symbol=symbol,
            timeframe=timeframe,
            signal_type="entry",
            direction=direction,
            ruleset=ruleset,
            bars=bars,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            metadata={"entry_reason": "ruleset_entry_signal"},
        ))

    return signals


def _compute_take_profit(tp, entry_price: float, direction: str, highs, lows, closes) -> float | None:
    """Compute take-profit price from TakeProfit spec."""
    from aletheia.trading.indicators import atr as _atr
    if tp.mode == "fixed_pct":
        delta = entry_price * tp.value / 100
        return entry_price + delta if direction == "long" else entry_price - delta
    # atr_multiple
    atr_val = _atr(highs, lows, closes, tp.atr_period)
    if atr_val is None:
        delta = entry_price * 3.0 / 100
    else:
        delta = atr_val * tp.value
    return entry_price + delta if direction == "long" else entry_price - delta


def _parse_params(params_str: str) -> dict:
    try:
        return json.loads(params_str) if isinstance(params_str, str) else (params_str or {})
    except Exception:
        return {}

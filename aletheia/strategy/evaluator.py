"""Rule evaluator: maps a RuleSet onto OHLCV bar data to produce signals.

Reuses the pure functions in aletheia/trading/indicators.py.
No look-ahead: evaluate_at_bar() only sees bars[0:bar_index+1].
Returns None (not False) when data is insufficient for a required indicator.
"""
from __future__ import annotations

from dataclasses import dataclass

from aletheia.strategy.dsl import (
    Comparator, Condition, IndicatorRef, RuleExpr, RuleSet, StopLoss,
)
from aletheia.trading.indicators import (
    atr, bollinger, ema, momentum_pct, rsi, sma, zscore_last,
)


@dataclass
class BarData:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


# ── indicator dispatch ────────────────────────────────────────────────────────

def compute_indicator(
    ref: IndicatorRef,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> float | None:
    """Compute a single indicator value from the series ending at current bar."""
    n = ref.name
    p = ref.params

    if n == "close":
        return closes[-1] if closes else None
    if n == "open":
        # open series not explicitly tracked; approximate with close
        return closes[-1] if closes else None
    if n == "high":
        return highs[-1] if highs else None
    if n == "low":
        return lows[-1] if lows else None
    if n == "volume":
        return volumes[-1] if volumes else None

    if n == "sma":
        return sma(closes, int(p.get("period", 20)))
    if n == "ema":
        return ema(closes, int(p.get("period", 20)))
    if n == "rsi":
        return rsi(closes, int(p.get("period", 14)))
    if n == "atr":
        return atr(highs, lows, closes, int(p.get("period", 14)))
    if n in ("bollinger_upper", "bollinger_lower", "bollinger_mid"):
        bands = bollinger(closes, int(p.get("period", 20)), float(p.get("k", 2.0)))
        if bands is None:
            return None
        return {"bollinger_lower": bands[0], "bollinger_mid": bands[1], "bollinger_upper": bands[2]}[n]
    if n == "momentum_pct":
        return momentum_pct(closes, int(p.get("lookback", 10)))
    if n == "zscore":
        return zscore_last(closes, int(p.get("period", 20)))

    raise ValueError(f"Unknown indicator: {n!r}")


# ── condition evaluation ──────────────────────────────────────────────────────

def _resolve(
    operand: float | IndicatorRef,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> float | None:
    if isinstance(operand, (int, float)):
        return float(operand)
    return compute_indicator(operand, closes, highs, lows, volumes)


def evaluate_condition(
    cond: Condition,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    prev_closes: list[float] | None = None,
    prev_highs: list[float] | None = None,
    prev_lows: list[float] | None = None,
    prev_volumes: list[float] | None = None,
) -> bool | None:
    """Evaluate one Condition. Returns None when data is insufficient."""
    comp = cond.comparator

    if comp in (Comparator.CROSS_ABOVE, Comparator.CROSS_BELOW):
        if not prev_closes:
            return None
        cur_l = _resolve(cond.left, closes, highs, lows, volumes)
        cur_r = _resolve(cond.right, closes, highs, lows, volumes)
        prv_l = _resolve(cond.left, prev_closes, prev_highs or [], prev_lows or [], prev_volumes or [])
        prv_r = _resolve(cond.right, prev_closes, prev_highs or [], prev_lows or [], prev_volumes or [])
        if any(v is None for v in (cur_l, cur_r, prv_l, prv_r)):
            return None
        if comp == Comparator.CROSS_ABOVE:
            return prv_l <= prv_r and cur_l > cur_r  # type: ignore[operator]
        return prv_l >= prv_r and cur_l < cur_r  # type: ignore[operator]

    lv = _resolve(cond.left, closes, highs, lows, volumes)
    rv = _resolve(cond.right, closes, highs, lows, volumes)
    if lv is None or rv is None:
        return None

    if comp == Comparator.LT:
        return lv < rv
    if comp == Comparator.GT:
        return lv > rv
    if comp == Comparator.LTE:
        return lv <= rv
    if comp == Comparator.GTE:
        return lv >= rv
    raise ValueError(f"Unhandled comparator: {comp}")


# ── rule expression evaluation ────────────────────────────────────────────────

def evaluate_rule(
    rule: RuleExpr,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    prev_closes: list[float] | None = None,
    prev_highs: list[float] | None = None,
    prev_lows: list[float] | None = None,
    prev_volumes: list[float] | None = None,
) -> bool | None:
    """Evaluate an AND/OR tree. Returns None if any required indicator is None."""
    results: list[bool | None] = []
    for child in rule.conditions:
        if isinstance(child, RuleExpr):
            r = evaluate_rule(
                child, closes, highs, lows, volumes,
                prev_closes, prev_highs, prev_lows, prev_volumes,
            )
        else:
            r = evaluate_condition(
                child, closes, highs, lows, volumes,
                prev_closes, prev_highs, prev_lows, prev_volumes,
            )
        results.append(r)

    if rule.op == "and":
        if any(r is None for r in results):
            return None
        return all(r for r in results)
    else:  # "or"
        if all(r is None for r in results):
            return None
        return any(r for r in results if r is not None)


# ── bar-level interface ───────────────────────────────────────────────────────

def evaluate_at_bar(
    ruleset: RuleSet,
    bars: list[BarData],
    bar_index: int,
) -> dict[str, bool | None]:
    """Evaluate entry and exit signals at bar_index.

    Uses bars[0:bar_index+1] only (no look-ahead).
    Returns {"entry": True|False|None, "exit": True|False|None}.
    None means "insufficient data for this signal".
    """
    window = bars[: bar_index + 1]
    closes = [b.close for b in window]
    highs = [b.high for b in window]
    lows = [b.low for b in window]
    volumes = [b.volume for b in window]

    prev_closes = closes[:-1] or None
    prev_highs = highs[:-1] or None
    prev_lows = lows[:-1] or None
    prev_volumes = volumes[:-1] or None

    entry = evaluate_rule(
        ruleset.entry, closes, highs, lows, volumes,
        prev_closes, prev_highs, prev_lows, prev_volumes,
    )
    exit_ = evaluate_rule(
        ruleset.exit, closes, highs, lows, volumes,
        prev_closes, prev_highs, prev_lows, prev_volumes,
    )
    return {"entry": entry, "exit": exit_}


def compute_stop_price(
    stop: StopLoss,
    entry_price: float,
    direction: str,
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> float:
    """Compute stop price from StopLoss spec."""
    if stop.mode == "fixed_pct":
        delta = entry_price * stop.value / 100
        return entry_price - delta if direction == "long" else entry_price + delta
    # atr_multiple
    atr_val = atr(highs, lows, closes, stop.atr_period)
    if atr_val is None:
        # fallback to 2% if ATR not available
        delta = entry_price * 2.0 / 100
    else:
        delta = atr_val * stop.value
    return entry_price - delta if direction == "long" else entry_price + delta

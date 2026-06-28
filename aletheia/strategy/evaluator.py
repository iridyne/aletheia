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
    atr, bollinger, donchian_high, donchian_low, ema, momentum_pct,
    obv, rsi, sma, volume_sma_ratio, vwap, zscore_last,
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
    opens: list[float] | None = None,
) -> float | None:
    """Compute a single indicator value from the series ending at current bar."""
    n = ref.name
    p = ref.params

    if n == "close":
        return closes[-1] if closes else None
    if n == "open":
        return opens[-1] if opens else (closes[-1] if closes else None)
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
        # DSL/designer emit {"params": {"period": N}}; honour it. Fall back to the
        # legacy "lookback" key for any old ruleset, then default 10. (Bug fix: this
        # branch used to read only "lookback", so every momentum_pct condition
        # silently ignored the strategy's period — see EV-S01.)
        return momentum_pct(closes, int(p.get("period", p.get("lookback", 10))))
    if n == "zscore":
        return zscore_last(closes, int(p.get("period", 20)))
    if n == "donchian_high":
        return donchian_high(highs, int(p.get("period", 20)))
    if n == "donchian_low":
        return donchian_low(lows, int(p.get("period", 20)))
    if n == "obv":
        return obv(closes, volumes)
    if n == "volume_sma_ratio":
        return volume_sma_ratio(volumes, int(p.get("period", 20)))
    if n == "vwap":
        return vwap(closes, highs, lows, volumes, int(p.get("period", 0)))

    raise ValueError(f"Unknown indicator: {n!r}")


# ── condition evaluation ──────────────────────────────────────────────────────

def _resolve(
    operand: float | IndicatorRef,
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    opens: list[float] | None = None,
) -> float | None:
    if isinstance(operand, (int, float)):
        return float(operand)
    return compute_indicator(operand, closes, highs, lows, volumes, opens)


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
    opens: list[float] | None = None,
    prev_opens: list[float] | None = None,
) -> bool | None:
    """Evaluate one Condition. Returns None when data is insufficient."""
    comp = cond.comparator

    if comp in (Comparator.CROSS_ABOVE, Comparator.CROSS_BELOW):
        if not prev_closes:
            return None
        cur_l = _resolve(cond.left, closes, highs, lows, volumes, opens)
        cur_r = _resolve(cond.right, closes, highs, lows, volumes, opens)
        prv_l = _resolve(cond.left, prev_closes, prev_highs or [], prev_lows or [], prev_volumes or [], prev_opens)
        prv_r = _resolve(cond.right, prev_closes, prev_highs or [], prev_lows or [], prev_volumes or [], prev_opens)
        if any(v is None for v in (cur_l, cur_r, prv_l, prv_r)):
            return None
        if comp == Comparator.CROSS_ABOVE:
            return prv_l <= prv_r and cur_l > cur_r  # type: ignore[operator]
        return prv_l >= prv_r and cur_l < cur_r  # type: ignore[operator]

    lv = _resolve(cond.left, closes, highs, lows, volumes, opens)
    rv = _resolve(cond.right, closes, highs, lows, volumes, opens)
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
    opens: list[float] | None = None,
    prev_opens: list[float] | None = None,
    multi_tf_bars: dict[str, list["BarData"]] | None = None,
) -> bool | None:
    """Evaluate an AND/OR tree. Returns None if any required indicator is None."""
    results: list[bool | None] = []
    for child in rule.conditions:
        if isinstance(child, RuleExpr):
            r = evaluate_rule(
                child, closes, highs, lows, volumes,
                prev_closes, prev_highs, prev_lows, prev_volumes,
                opens, prev_opens, multi_tf_bars,
            )
        elif isinstance(child, Condition) and child.timeframe and multi_tf_bars:
            tf_bars = multi_tf_bars.get(child.timeframe)
            if not tf_bars:
                r = None
            else:
                tf_c = [b.close for b in tf_bars]
                tf_h = [b.high for b in tf_bars]
                tf_l = [b.low for b in tf_bars]
                tf_v = [b.volume for b in tf_bars]
                tf_o = [b.open for b in tf_bars]
                tf_pc = tf_c[:-1] or None
                tf_po = tf_o[:-1] or None
                r = evaluate_condition(
                    child, tf_c, tf_h, tf_l, tf_v,
                    tf_pc, tf_h[:-1] or None, tf_l[:-1] or None, tf_v[:-1] or None,
                    tf_o, tf_po,
                )
        else:
            r = evaluate_condition(
                child, closes, highs, lows, volumes,
                prev_closes, prev_highs, prev_lows, prev_volumes,
                opens, prev_opens,
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
    multi_tf_bars: dict[str, list[BarData]] | None = None,
) -> dict[str, bool | None]:
    """Evaluate entry and exit signals at bar_index.

    Uses bars[0:bar_index+1] only (no look-ahead).
    multi_tf_bars: optional dict mapping timeframe → full bar list for MTF conditions.
    Returns {"entry": True|False|None, "exit": True|False|None}.
    None means "insufficient data for this signal".
    """
    window = bars[: bar_index + 1]
    closes = [b.close for b in window]
    highs = [b.high for b in window]
    lows = [b.low for b in window]
    volumes = [b.volume for b in window]
    opens = [b.open for b in window]

    prev_closes = closes[:-1] or None
    prev_highs = highs[:-1] or None
    prev_lows = lows[:-1] or None
    prev_volumes = volumes[:-1] or None
    prev_opens = opens[:-1] or None

    entry = evaluate_rule(
        ruleset.entry, closes, highs, lows, volumes,
        prev_closes, prev_highs, prev_lows, prev_volumes,
        opens, prev_opens, multi_tf_bars,
    )
    exit_ = evaluate_rule(
        ruleset.exit, closes, highs, lows, volumes,
        prev_closes, prev_highs, prev_lows, prev_volumes,
        opens, prev_opens, multi_tf_bars,
    )
    return {"entry": entry, "exit": exit_}


def collect_referenced_timeframes(ruleset: RuleSet) -> set[str]:
    """Collect every non-null Condition.timeframe referenced anywhere in a RuleSet.

    Walks the entry and exit RuleExpr trees (including nested RuleExpr children).
    The scanner uses this to know which extra timeframes' bars to fetch and pass
    as multi_tf_bars, so cross-timeframe conditions (e.g. "4h" momentum) are
    evaluated on their own bars instead of silently falling back to the primary
    timeframe. See EV-S02.
    """
    found: set[str] = set()

    def _walk(node: RuleExpr | Condition) -> None:
        if isinstance(node, RuleExpr):
            for child in node.conditions:
                _walk(child)
        elif isinstance(node, Condition):
            if node.timeframe:
                found.add(node.timeframe)

    _walk(ruleset.entry)
    _walk(ruleset.exit)
    return found


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

"""Pure per-signal replay over closed 1m OHLCV bars, plus baseline/bootstrap helpers.

One fill/cost model for every counterfactual that replays a signal on bars:

* entries: ``market`` fills at the open of bar ``delay_bars`` after the decision
  bar; ``limit`` fills only when price trades *through* the limit (a touch is not
  a fill); ``stop`` (stop-entry) fills when price reaches the trigger. A gap past
  the price fills at the bar open. Limit/stop entries expire after
  ``entry_lifetime_bars``. A stop breach before the fill cancels the signal.
* exits: the stop is checked first, so a bar that holds both stop and target
  exits at the stop (flagged ambiguous). Gaps through the stop/target fill at the
  bar open. The time barrier exits at the open of bar ``time_barrier_bars`` after
  the fill bar.
* costs: adverse slippage on every fill (embedded in gross), per-side fees and
  hourly funding as explicit costs.

Bars are mappings with ``ts`` (bar open, ms), ``open``, ``high``, ``low``,
``close``; one bar is one minute. Nothing here reads the DB or the runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Callable, Mapping, Sequence

import numpy as np

Bar = Mapping[str, float]


@dataclass(frozen=True, slots=True)
class Signal:
    instrument: str
    direction: str  # "long" | "short"
    decision_ts: int  # ms; the first bar with ts >= decision_ts is the decision bar
    stop_price: float
    entry_type: str = "market"  # "market" | "limit" | "stop"
    entry_price: float | None = None  # required for limit/stop entries
    delay_bars: int = 0
    entry_lifetime_bars: int | None = None  # required for limit/stop entries
    target_price: float | None = None
    time_barrier_bars: int | None = None
    entry_fee_bps: float = 0.0
    exit_fee_bps: float = 0.0
    slippage_bps: float = 0.0
    funding_bps_per_hour: float = 0.0
    breakeven_after_r: float | None = None  # move the stop to entry after this favourable excursion


@dataclass(frozen=True, slots=True)
class ReplayResult:
    status: str  # "evaluated" | "not_evaluable"
    exit_reason: str  # stop/target/time/invalidation/expired/unfilled, or the not_evaluable reason
    filled: bool = False
    entry_ts: int | None = None
    entry_price: float | None = None
    exit_ts: int | None = None
    exit_price: float | None = None
    held_bars: int | None = None
    gross_return_pct: float | None = None
    explicit_cost_pct: float | None = None
    net_return_pct: float | None = None
    gross_r: float | None = None
    net_r: float | None = None
    mae_pct: float | None = None
    mfe_pct: float | None = None
    mae_r: float | None = None
    mfe_r: float | None = None
    ambiguity: tuple[str, ...] = ()


def _return_pct(direction: str, entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry * 100.0 if direction == "long" else (entry - exit_price) / entry * 100.0


def _adverse_slippage(price: float, direction: str, bps: float, *, entry: bool) -> float:
    adverse_up = (direction == "long") == entry
    multiplier = 1.0 + bps / 10_000.0 if adverse_up else 1.0 - bps / 10_000.0
    return price * multiplier


def _stop_hit(bar: Bar, long: bool, stop: float) -> bool:
    return bar["low"] <= stop if long else bar["high"] >= stop


def _entry_fill(signal: Signal, bar: Bar, long: bool) -> float | None:
    price = float(signal.entry_price or math.nan)
    if signal.entry_type == "limit":  # must trade through, a touch is not a fill
        if long and bar["low"] < price:
            return min(bar["open"], price)
        if not long and bar["high"] > price:
            return max(bar["open"], price)
        return None
    if long and bar["high"] >= price:
        return max(bar["open"], price)
    if not long and bar["low"] <= price:
        return min(bar["open"], price)
    return None


def replay_signal(
    signal: Signal,
    bars: Sequence[Bar],
    *,
    exit_check: Callable[[int], str] | None = None,
) -> ReplayResult:
    """Replay one signal. ``exit_check(n)`` sees only ``bars[:n]`` (completed bars) and
    returns ``"hold"``, ``"exit"`` (exit at the next bar open) or ``"not_evaluable"``."""
    long = signal.direction == "long"
    if signal.direction not in {"long", "short"}:
        raise ValueError(f"direction must be long or short: {signal.direction!r}")
    if signal.entry_type not in {"market", "limit", "stop"}:
        raise ValueError(f"unknown entry_type: {signal.entry_type!r}")
    if signal.entry_type != "market" and (signal.entry_price is None or signal.entry_lifetime_bars is None):
        raise ValueError("limit/stop entries need entry_price and entry_lifetime_bars")
    start = next((i for i, bar in enumerate(bars) if bar["ts"] >= signal.decision_ts), None)
    if start is None:
        return ReplayResult("not_evaluable", "decision_bar_unavailable")

    stop = signal.stop_price
    first = start + signal.delay_bars
    fill_index: int | None = None
    raw_fill = math.nan
    for index in range(start, len(bars)):
        bar = bars[index]
        if index >= first:
            if signal.entry_type == "market":
                fill_index, raw_fill = index, bar["open"]
                break
            if index >= first + int(signal.entry_lifetime_bars or 0):
                return ReplayResult("evaluated", "expired")
            price = _entry_fill(signal, bar, long)
            if price is not None:
                fill_index, raw_fill = index, price
                break
        if _stop_hit(bar, long, stop):
            return ReplayResult("evaluated", "unfilled")
    if fill_index is None:
        return ReplayResult("not_evaluable", "entry_coverage_unavailable")

    entry = _adverse_slippage(raw_fill, signal.direction, signal.slippage_bps, entry=True)
    if (long and entry <= stop) or (not long and entry >= stop):
        # ponytail: a fill already through the stop is treated as cancelled, not as an instant loss.
        return ReplayResult("evaluated", "unfilled")
    initial_r = abs(entry - stop)
    if not initial_r > 0:
        return ReplayResult("not_evaluable", "initial_risk_unavailable")
    sign = 1.0 if long else -1.0
    has_target = signal.target_price is not None
    target = signal.target_price if signal.target_price is not None else math.nan
    intrabar_fill = signal.entry_type != "market"
    active_stop = stop
    ambiguity: list[str] = []
    favourable_marks = [entry]
    adverse_marks = [entry]
    exit_index, exit_price = fill_index, math.nan
    exit_reason = ""
    held = 0
    index = fill_index
    while True:
        if signal.time_barrier_bars is not None and index == fill_index + signal.time_barrier_bars:
            if index >= len(bars):
                return ReplayResult("not_evaluable", "time_barrier_coverage_unavailable")
            exit_index, exit_price, exit_reason = index, bars[index]["open"], "time"
            break
        if index >= len(bars):
            return ReplayResult("not_evaluable", "path_coverage_ended_before_exit")
        bar = bars[index]
        # The fill bar of a resting entry may have printed its favourable extreme before the fill.
        can_favour = not (intrabar_fill and index == fill_index)
        held += 1
        fav_extreme = bar["high"] if long else bar["low"]
        adv_extreme = bar["low"] if long else bar["high"]
        target_hit = can_favour and has_target and (fav_extreme >= target if long else fav_extreme <= target)
        if _stop_hit(bar, long, active_stop):
            gap = can_favour and (bar["open"] < active_stop if long else bar["open"] > active_stop)
            exit_index, exit_price, exit_reason = index, bar["open"] if gap else active_stop, "stop"
            if target_hit and not gap:
                ambiguity.append("stop_target_same_bar")
            if intrabar_fill and index == fill_index and signal.entry_type == "stop":
                ambiguity.append("entry_stop_same_bar")
            adverse_marks.append(exit_price)
            break
        breakeven_level = entry + sign * initial_r * (signal.breakeven_after_r or 0.0)
        favourable = can_favour and (fav_extreme >= breakeven_level if long else fav_extreme <= breakeven_level)
        entry_touched = adv_extreme <= entry if long else adv_extreme >= entry
        if signal.breakeven_after_r is not None and favourable and entry_touched:
            exit_index, exit_price, exit_reason = index, entry, "stop"
            ambiguity.append("breakeven_same_bar")
            favourable_marks.append(fav_extreme)
            adverse_marks.append(adv_extreme)
            break
        if target_hit:
            gap = (bar["open"] > target) if long else (bar["open"] < target)
            exit_index, exit_price, exit_reason = index, bar["open"] if gap else target, "target"
            favourable_marks.append(exit_price)
            adverse_marks.append(adv_extreme)
            break
        adverse_marks.append(adv_extreme)
        if can_favour:
            favourable_marks.append(fav_extreme)
        if exit_check is not None:
            state = exit_check(index + 1)
            if state == "not_evaluable":
                return ReplayResult("not_evaluable", "exit_check_not_evaluable")
            if state == "exit":
                if index + 1 >= len(bars):
                    return ReplayResult("not_evaluable", "invalidation_exit_coverage_unavailable")
                exit_index, exit_price, exit_reason = index + 1, bars[index + 1]["open"], "invalidation"
                break
        if signal.breakeven_after_r is not None and favourable:
            active_stop = entry
        index += 1

    exit_fill = _adverse_slippage(exit_price, signal.direction, signal.slippage_bps, entry=False)
    gross_pct = _return_pct(signal.direction, entry, exit_fill)
    held_bars = max(1, held)
    explicit_cost_pct = (
        signal.entry_fee_bps + signal.exit_fee_bps + signal.funding_bps_per_hour * held_bars / 60.0
    ) / 100.0
    net_pct = gross_pct - explicit_cost_pct
    risk_pct = initial_r / entry * 100.0
    mfe_pct = max(_return_pct(signal.direction, entry, price) for price in favourable_marks)
    mae_pct = -min(_return_pct(signal.direction, entry, price) for price in adverse_marks)
    return ReplayResult(
        status="evaluated",
        exit_reason=exit_reason,
        filled=True,
        entry_ts=int(bars[fill_index]["ts"]),
        entry_price=entry,
        exit_ts=int(bars[exit_index]["ts"]),
        exit_price=exit_fill,
        held_bars=held_bars,
        gross_return_pct=gross_pct,
        explicit_cost_pct=explicit_cost_pct,
        net_return_pct=net_pct,
        gross_r=gross_pct / risk_pct,
        net_r=net_pct / risk_pct,
        mae_pct=max(0.0, mae_pct),
        mfe_pct=max(0.0, mfe_pct),
        mae_r=max(0.0, mae_pct) / risk_pct,
        mfe_r=max(0.0, mfe_pct) / risk_pct,
        ambiguity=tuple(ambiguity),
    )


def _rebase(ref: float, new_ref: float, flip: bool, price: float) -> float:
    # Keep the distance as a fraction of the reference open; a flipped side mirrors it.
    offset = (price - ref) / ref
    return new_ref * (1.0 - offset if flip else 1.0 + offset)


def random_entry_baseline(
    signals: Sequence[Signal],
    bars_by_instrument: Mapping[str, Sequence[Bar]],
    *,
    seed: int,
    direction_mode: str = "random",
    randomize_time: bool = True,
    windows: Mapping[str, tuple[int, int]] | None = None,
) -> list[Signal]:
    """Baseline signals with the same instruments and geometry as ``signals``.

    Each baseline signal keeps its source's stop/target/entry distances as fractions
    of the decision-bar open, re-anchored at a random decision bar inside the
    instrument's window (default: its bar coverage). ``direction_mode`` is
    ``"random"`` (seeded coin flip) or ``"mirror"`` (the opposite side);
    ``randomize_time=False`` with ``"mirror"`` is the same-time opposite-side test.
    Pass ``windows`` that end early enough to leave exit coverage, or the tail draws
    replay as ``not_evaluable``. Multiple-comparison control across baselines reuses
    ``aletheia.universe.discovery_benchmark._holm``; this module does not copy it.
    """
    if direction_mode not in {"random", "mirror"}:
        raise ValueError(f"unknown direction_mode: {direction_mode!r}")
    rng = np.random.default_rng(seed)
    out: list[Signal] = []
    for signal in signals:
        bars = bars_by_instrument[signal.instrument]
        anchor = next((bar for bar in bars if bar["ts"] >= signal.decision_ts), None)
        if anchor is None:
            continue
        ref = anchor["open"]
        if randomize_time:
            lo, hi = (windows or {}).get(signal.instrument, (bars[0]["ts"], bars[-1]["ts"]))
            eligible = [bar for bar in bars if lo <= bar["ts"] <= hi]
            new_anchor = eligible[int(rng.integers(len(eligible)))]
        else:
            new_anchor = anchor
        flip = bool(rng.integers(2)) if direction_mode == "random" else True
        new_ref = new_anchor["open"]
        entry, target = signal.entry_price, signal.target_price
        out.append(
            replace(
                signal,
                direction=("short" if signal.direction == "long" else "long") if flip else signal.direction,
                decision_ts=int(new_anchor["ts"]),
                stop_price=_rebase(ref, new_ref, flip, signal.stop_price),
                entry_price=None if entry is None else _rebase(ref, new_ref, flip, entry),
                target_price=None if target is None else _rebase(ref, new_ref, flip, target),
            )
        )
    return out


def block_bootstrap_mean_ci(
    values: Sequence[float],
    *,
    block_length: int,
    seed: int,
    draws: int = 10_000,
    stationary: bool = False,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Moving-block (or stationary, geometric block lengths) bootstrap CI of the mean.

    ``values`` must be in time order; blocks keep serial dependence that an iid
    bootstrap would destroy. Stationary blocks wrap circularly (Politis-Romano).
    """
    data = np.asarray(values, dtype=float)
    n = len(data)
    if n == 0 or block_length < 1:
        raise ValueError("need values and block_length >= 1")
    rng = np.random.default_rng(seed)
    length = min(block_length, n)
    means = np.empty(draws)
    for draw in range(draws):
        if stationary:
            index = np.empty(n, dtype=int)
            index[0] = rng.integers(n)
            restart = rng.random(n) < 1.0 / length
            starts = rng.integers(n, size=n)
            for i in range(1, n):
                index[i] = starts[i] if restart[i] else (index[i - 1] + 1) % n
        else:
            blocks = rng.integers(n - length + 1, size=-(-n // length))
            index = (blocks[:, None] + np.arange(length)).ravel()[:n]
        means[draw] = data[index].mean()
    return {
        "mean": float(data.mean()),
        "ci": [float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2))],
        "n": n,
        "block_length": length,
        "draws": draws,
    }

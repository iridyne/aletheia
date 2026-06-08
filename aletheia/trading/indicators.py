"""Stdlib-only technical indicators for the market context layer.

Why stdlib (not numpy/pandas/ta-lib):
  - Aletheia keeps its dependency surface minimal (red line: no heavy libs).
  - The indicators here are point-in-time scalars over short windows
    (≤ 200 closes), where Python list math is plenty fast.
  - Every function is pure, takes a list[float], and returns either a single
    float or a list of floats — easy to unit-test, easy to render in markdown.

Inputs are series with **oldest first, newest last** (consistent with how
get_ohlcv returns candles after we sort by timestamp ascending). Functions
that need N samples but get fewer return None — callers must check.
"""

from __future__ import annotations

from statistics import mean, stdev


def _safe_window(values: list[float], n: int) -> list[float] | None:
    """Take the last n values; return None if too short."""
    if len(values) < n:
        return None
    return values[-n:]


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average over the last `period` samples."""
    win = _safe_window(values, period)
    return mean(win) if win else None


def ema(values: list[float], period: int) -> float | None:
    """Exponential moving average. Bootstrapped with SMA on the first `period`."""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    seed = mean(values[:period])
    out = seed
    for v in values[period:]:
        out = v * k + out * (1 - k)
    return out


def rsi(values: list[float], period: int = 14) -> float | None:
    """Relative Strength Index using Wilder's smoothing.

    Returns 0..100. None if the series is shorter than period+1 (we need at
    least one delta to compute gain/loss). Wilder uses an exponential-style
    smoothing (alpha=1/period) after the initial SMA seed.
    """
    if len(values) < period + 1:
        return None
    deltas = [values[i + 1] - values[i] for i in range(len(values) - 1)]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    # Seed with simple averages of the first `period` deltas.
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder smoothing for the rest.
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> float | None:
    """Average True Range — Wilder's volatility scalar (in price units).

    True Range = max(high-low, |high-prev_close|, |low-prev_close|).
    """
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    # Wilder seed = mean of first `period` TRs, then exponential smoothing.
    out = sum(trs[:period]) / period
    for tr in trs[period:]:
        out = (out * (period - 1) + tr) / period
    return out


def bollinger(values: list[float], period: int = 20, k: float = 2.0
              ) -> tuple[float, float, float] | None:
    """Bollinger bands: (lower, mid=SMA, upper). None if too short."""
    win = _safe_window(values, period)
    if not win:
        return None
    mid = mean(win)
    if len(win) < 2:
        return (mid, mid, mid)
    sd = stdev(win)
    return (mid - k * sd, mid, mid + k * sd)


def momentum_pct(values: list[float], lookback: int) -> float | None:
    """% change over `lookback` bars: (last - past) / past * 100."""
    if len(values) <= lookback:
        return None
    past = values[-lookback - 1]
    last = values[-1]
    if past == 0:
        return None
    return (last - past) / past * 100


def zscore_last(values: list[float], period: int) -> float | None:
    """Z-score of the most recent value within the last `period` window.

    Useful for "is current volume unusually high" without committing to a
    threshold up front.
    """
    win = _safe_window(values, period)
    if not win or len(win) < 2:
        return None
    m = mean(win)
    sd = stdev(win)
    if sd == 0:
        return 0.0
    return (win[-1] - m) / sd


def relative_strength_rank(returns: dict[str, float]) -> list[tuple[str, int]]:
    """Rank symbols by return desc; returns [(symbol, rank_1based), ...].

    Returns dict like {"BTCUSDT": 0.5, "ETHUSDT": -0.2}. Symbols with None
    return are ranked last (skipped from sort, appended).
    """
    valid = [(s, r) for s, r in returns.items() if r is not None]
    invalid = [s for s, r in returns.items() if r is None]
    valid.sort(key=lambda x: x[1], reverse=True)
    ranked = [(s, i + 1) for i, (s, _) in enumerate(valid)]
    ranked.extend((s, len(valid) + 1) for s in invalid)
    return ranked

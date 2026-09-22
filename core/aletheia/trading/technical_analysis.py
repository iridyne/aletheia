"""Versioned, point-in-time technical analysis snapshots.

The batch analyzer is the numerical reference implementation.  The incremental
engine is the runtime fast path: an append-only closed bar updates rolling
statistics in O(1), while corrections and out-of-order input deliberately fall
back to a bounded rebuild.  Consumers receive the same immutable snapshot shape
from either path, including enough provenance to reproduce the calculation.

This module has no strategy or execution authority.  It describes market data;
it does not interpret a value as a trade instruction.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from statistics import mean, stdev
from typing import Any, Iterable

from aletheia.trading.indicators import (
    atr,
    bollinger,
    donchian_high,
    donchian_low,
    ema,
    momentum_pct,
    obv,
    rsi,
    sma,
    volume_sma_ratio,
    vwap,
    zscore_last,
)


TECHNICAL_ANALYSIS_VERSION = "technical-analysis-v1"
DEFAULT_MAX_HISTORY = 256

_TIMEFRAME_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def timeframe_milliseconds(timeframe: str) -> int | None:
    """Return the canonical interval length used for closure/gap validation."""
    return _TIMEFRAME_MS.get(str(timeframe))


@dataclass(frozen=True, slots=True)
class TechnicalBar:
    """Canonical closed OHLCV input, oldest-to-newest when used in a series."""

    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True

    def __post_init__(self) -> None:
        values = (self.open, self.high, self.low, self.close, self.volume)
        if self.ts < 0 or not all(math.isfinite(value) for value in values):
            raise ValueError("technical bar contains a non-finite value")
        if self.volume < 0:
            raise ValueError("technical bar volume must be non-negative")
        if self.high < self.low:
            raise ValueError("technical bar high must be >= low")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("technical bar OHLC values are inconsistent")

    @classmethod
    def from_value(cls, value: Any) -> "TechnicalBar":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(
                ts=int(value.get("ts", value.get("timestamp"))),
                open=float(value["open"]),
                high=float(value["high"]),
                low=float(value["low"]),
                close=float(value["close"]),
                volume=float(value["volume"]),
                is_closed=bool(value.get("is_closed", True)),
            )
        return cls(
            ts=int(getattr(value, "timestamp", getattr(value, "ts", 0))),
            open=float(value.open),
            high=float(value.high),
            low=float(value.low),
            close=float(value.close),
            volume=float(value.volume),
            is_closed=bool(getattr(value, "is_closed", True)),
        )


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    """Immutable technical facts for one exact instrument and timeframe."""

    exchange: str
    symbol: str
    market_type: str
    timeframe: str
    bar_close_ts: int | None
    bars_used: int
    history_bars_retained: int
    warmup_complete: bool
    input_fingerprint: str
    algorithm_version: str
    data_quality: str
    quality_reasons: tuple[str, ...] = ()
    values: dict[str, float | bool | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _closed_canonical_bars(values: Iterable[Any]) -> tuple[list[TechnicalBar], list[str]]:
    """Normalize inputs and make duplicate/out-of-order behavior explicit."""

    normalized = [TechnicalBar.from_value(value) for value in values]
    closed = [bar for bar in normalized if bar.is_closed]
    reasons: list[str] = []
    if len(closed) != len(normalized):
        reasons.append("forming_bars_excluded")
    if any(closed[index].ts >= closed[index + 1].ts for index in range(len(closed) - 1)):
        reasons.append("input_reordered_or_deduplicated")
    by_ts = {bar.ts: bar for bar in closed}
    return [by_ts[ts] for ts in sorted(by_ts)], reasons


def _series_fingerprint(bars: list[TechnicalBar]) -> str:
    digest = hashlib.sha256()
    for bar in bars:
        digest.update(
            json.dumps(
                (bar.ts, bar.open, bar.high, bar.low, bar.close, bar.volume),
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def _gap_reasons(bars: list[TechnicalBar], timeframe: str) -> list[str]:
    interval = _TIMEFRAME_MS.get(timeframe)
    if interval is None or len(bars) < 2:
        return []
    # Some venues timestamp around a boundary.  Ten percent tolerance catches a
    # missing bar without treating harmless millisecond drift as a data gap.
    tolerance = max(1_000, interval // 10)
    if any((right.ts - left.ts) > interval + tolerance for left, right in zip(bars, bars[1:])):
        return ["timeframe_gap_detected"]
    return []


def _safe_atr_pct(atr_value: float | None, close: float | None) -> float | None:
    if atr_value is None or close is None or close <= 0:
        return None
    return atr_value / close * 100.0


def _indicator_values(bars: list[TechnicalBar]) -> dict[str, float | bool | None]:
    closes = [bar.close for bar in bars]
    highs = [bar.high for bar in bars]
    lows = [bar.low for bar in bars]
    volumes = [bar.volume for bar in bars]
    last = closes[-1] if closes else None
    atr_14 = atr(highs, lows, closes, 14)
    bb = bollinger(closes, 20, 2.0)
    bb_lower = bb_mid = bb_upper = bb_pct_b = bb_width_pct = None
    if bb is not None:
        bb_lower, bb_mid, bb_upper = bb
        width = bb_upper - bb_lower
        bb_pct_b = (last - bb_lower) / width if last is not None and width else 0.5
        bb_width_pct = width / bb_mid * 100.0 if bb_mid else None
    return {
        "last_close": last,
        "sma_20": sma(closes, 20),
        "sma_50": sma(closes, 50),
        "ema_20": ema(closes, 20),
        "ema_50": ema(closes, 50),
        "rsi_14": rsi(closes, 14),
        "atr_14": atr_14,
        "atr_pct": _safe_atr_pct(atr_14, last),
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
        "bb_upper": bb_upper,
        "bb_pct_b": bb_pct_b,
        "bb_width_pct": bb_width_pct,
        "momentum_10": momentum_pct(closes, 10),
        "volume_zscore_20": zscore_last(volumes, 20),
        "volume_ratio_20": volume_sma_ratio(volumes, 20),
        "vwap_20": vwap(closes, highs, lows, volumes, 20),
        "donchian_high_20": donchian_high(highs, 20),
        "donchian_low_20": donchian_low(lows, 20),
        "obv_normalized": obv(closes, volumes),
    }


class BatchTechnicalAnalyzer:
    """Deterministic reference calculator for a bounded closed-bar series."""

    def compute(
        self,
        bars: Iterable[Any],
        *,
        exchange: str,
        symbol: str,
        market_type: str,
        timeframe: str,
    ) -> TechnicalSnapshot:
        canonical, reasons = _closed_canonical_bars(bars)
        reasons.extend(_gap_reasons(canonical, timeframe))
        quality = "empty" if not canonical else ("degraded" if reasons else "complete")
        return TechnicalSnapshot(
            exchange=str(exchange or "unknown").lower(),
            symbol=str(symbol),
            market_type=str(market_type or "spot").lower(),
            timeframe=str(timeframe),
            bar_close_ts=(
                canonical[-1].ts + _TIMEFRAME_MS[timeframe]
                if canonical and timeframe in _TIMEFRAME_MS
                else None
            ),
            bars_used=len(canonical),
            history_bars_retained=len(canonical),
            warmup_complete=len(canonical) >= 50,
            input_fingerprint=_series_fingerprint(canonical),
            algorithm_version=TECHNICAL_ANALYSIS_VERSION,
            data_quality=quality,
            quality_reasons=tuple(dict.fromkeys(reasons)),
            values=_indicator_values(canonical),
        )


class IncrementalTechnicalAnalyzer:
    """Append-fast analyzer with bounded correction rebuilds.

    The retained history is intentionally bounded and is also the recovery
    authority for revised/out-of-order bars.  Normal appends and snapshots are
    O(1); only a correction, out-of-order insert, or retention-boundary rollover
    rebuilds the bounded state.  ``verify()`` compares the fast projection with
    the independent batch reference.
    """

    def __init__(
        self,
        *,
        exchange: str,
        symbol: str,
        market_type: str,
        timeframe: str,
        max_history: int = DEFAULT_MAX_HISTORY,
    ) -> None:
        if max_history < 64:
            raise ValueError("max_history must be at least 64 bars")
        self.exchange = str(exchange or "unknown").lower()
        self.symbol = symbol
        self.market_type = str(market_type or "spot").lower()
        self.timeframe = timeframe
        self.max_history = max_history
        self._bars: deque[TechnicalBar] = deque(maxlen=max_history)
        self._revision_count = 0
        self._append_count = 0
        self._reset_rolling_state()

    def _reset_rolling_state(self) -> None:
        self._close20: deque[float] = deque(maxlen=20)
        self._close50: deque[float] = deque(maxlen=50)
        self._close11: deque[float] = deque(maxlen=11)
        self._volume20: deque[float] = deque(maxlen=20)
        self._tpv20: deque[float] = deque(maxlen=20)
        self._high20: deque[tuple[int, float]] = deque()
        self._low20: deque[tuple[int, float]] = deque()
        self._obv_contribs: deque[float] = deque(maxlen=self.max_history - 1)
        self._all_volumes: deque[float] = deque(maxlen=self.max_history)
        self._sum_close20 = self._sum_close20_sq = 0.0
        self._sum_close50 = 0.0
        self._sum_volume20 = self._sum_volume20_sq = 0.0
        self._close20_mean = self._close20_m2 = 0.0
        self._volume20_mean = self._volume20_m2 = 0.0
        self._sum_tpv20 = 0.0
        self._sum_obv = 0.0
        self._sum_all_volume = 0.0
        self._ema20: float | None = None
        self._ema50: float | None = None
        self._ema20_seed: list[float] = []
        self._ema50_seed: list[float] = []
        self._rsi_avg_gain: float | None = None
        self._rsi_avg_loss: float | None = None
        self._rsi_seed_gains: list[float] = []
        self._rsi_seed_losses: list[float] = []
        self._atr14: float | None = None
        self._atr_seed: list[float] = []
        self._previous_close: float | None = None
        self._gap_right_timestamps: deque[int] = deque()
        self._digest = hashlib.sha256()

    @staticmethod
    def _roll_append(window: deque[float], value: float) -> float | None:
        removed = window[0] if len(window) == window.maxlen else None
        window.append(value)
        return removed

    def _append_fast(self, bar: TechnicalBar) -> None:
        index = self._append_count
        close_count_before = len(self._close20)
        old = self._roll_append(self._close20, bar.close)
        if old is not None:
            self._sum_close20 -= old
            self._sum_close20_sq -= old * old
        self._sum_close20 += bar.close
        self._sum_close20_sq += bar.close * bar.close
        self._close20_mean, self._close20_m2 = self._replace_rolling_moment(
            self._close20_mean,
            self._close20_m2,
            close_count_before,
            old,
            bar.close,
        )

        old = self._roll_append(self._close50, bar.close)
        if old is not None:
            self._sum_close50 -= old
        self._sum_close50 += bar.close
        self._roll_append(self._close11, bar.close)

        volume_count_before = len(self._volume20)
        old = self._roll_append(self._volume20, bar.volume)
        if old is not None:
            self._sum_volume20 -= old
            self._sum_volume20_sq -= old * old
        self._sum_volume20 += bar.volume
        self._sum_volume20_sq += bar.volume * bar.volume
        self._volume20_mean, self._volume20_m2 = self._replace_rolling_moment(
            self._volume20_mean,
            self._volume20_m2,
            volume_count_before,
            old,
            bar.volume,
        )

        tpv = ((bar.high + bar.low + bar.close) / 3.0) * bar.volume
        old_tpv = self._roll_append(self._tpv20, tpv)
        if old_tpv is not None:
            self._sum_tpv20 -= old_tpv
        self._sum_tpv20 += tpv

        cutoff = index - 19
        while self._high20 and self._high20[-1][1] <= bar.high:
            self._high20.pop()
        self._high20.append((index, bar.high))
        while self._high20 and self._high20[0][0] < cutoff:
            self._high20.popleft()
        while self._low20 and self._low20[-1][1] >= bar.low:
            self._low20.pop()
        self._low20.append((index, bar.low))
        while self._low20 and self._low20[0][0] < cutoff:
            self._low20.popleft()

        old_volume = self._roll_append(self._all_volumes, bar.volume)
        if old_volume is not None:
            self._sum_all_volume -= old_volume
        self._sum_all_volume += bar.volume

        previous = self._previous_close
        if previous is not None:
            delta = bar.close - previous
            gain, loss = max(delta, 0.0), max(-delta, 0.0)
            if self._rsi_avg_gain is None:
                self._rsi_seed_gains.append(gain)
                self._rsi_seed_losses.append(loss)
                if len(self._rsi_seed_gains) == 14:
                    self._rsi_avg_gain = sum(self._rsi_seed_gains) / 14.0
                    self._rsi_avg_loss = sum(self._rsi_seed_losses) / 14.0
            else:
                self._rsi_avg_gain = (self._rsi_avg_gain * 13.0 + gain) / 14.0
                self._rsi_avg_loss = (self._rsi_avg_loss * 13.0 + loss) / 14.0

            tr = max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
            if self._atr14 is None:
                self._atr_seed.append(tr)
                if len(self._atr_seed) == 14:
                    self._atr14 = sum(self._atr_seed) / 14.0
            else:
                self._atr14 = (self._atr14 * 13.0 + tr) / 14.0

            signed_volume = bar.volume if delta > 0 else (-bar.volume if delta < 0 else 0.0)
            removed = self._roll_append(self._obv_contribs, signed_volume)
            if removed is not None:
                self._sum_obv -= removed
            self._sum_obv += signed_volume

            interval = _TIMEFRAME_MS.get(self.timeframe)
            if interval is not None and self._bars:
                tolerance = max(1_000, interval // 10)
                if bar.ts - self._bars[-1].ts > interval + tolerance:
                    self._gap_right_timestamps.append(bar.ts)

        self._update_ema(bar.close, 20)
        self._update_ema(bar.close, 50)
        self._previous_close = bar.close
        self._digest.update(
            json.dumps(
                (bar.ts, bar.open, bar.high, bar.low, bar.close, bar.volume),
                separators=(",", ":"),
                allow_nan=False,
            ).encode("ascii")
        )
        self._digest.update(b"\n")

    @staticmethod
    def _replace_rolling_moment(
        current_mean: float,
        current_m2: float,
        count_before: int,
        removed: float | None,
        added: float,
    ) -> tuple[float, float]:
        """Numerically stable Welford remove+add for a bounded window."""
        count = count_before
        mean_value = current_mean
        m2 = current_m2
        if removed is not None:
            if count <= 1:
                count, mean_value, m2 = 0, 0.0, 0.0
            else:
                next_count = count - 1
                next_mean = (count * mean_value - removed) / next_count
                m2 = max(m2 - (removed - mean_value) * (removed - next_mean), 0.0)
                count, mean_value = next_count, next_mean
        next_count = count + 1
        delta = added - mean_value
        next_mean = mean_value + delta / next_count
        m2 = max(m2 + delta * (added - next_mean), 0.0)
        return next_mean, m2

    def _update_ema(self, value: float, period: int) -> None:
        seed = self._ema20_seed if period == 20 else self._ema50_seed
        current = self._ema20 if period == 20 else self._ema50
        if current is None:
            seed.append(value)
            if len(seed) == period:
                current = sum(seed) / period
        else:
            k = 2.0 / (period + 1.0)
            current = value * k + current * (1.0 - k)
        if period == 20:
            self._ema20 = current
        else:
            self._ema50 = current

    def _rebuild(self, bars: list[TechnicalBar]) -> None:
        self._bars = deque(maxlen=self.max_history)
        self._append_count = 0
        self._reset_rolling_state()
        for bar in bars[-self.max_history :]:
            self._append_fast(bar)
            self._bars.append(bar)
            self._append_count += 1
        self._prune_retired_gaps()

    def _prune_retired_gaps(self) -> None:
        if not self._bars:
            self._gap_right_timestamps.clear()
            return
        first_retained_ts = self._bars[0].ts
        while (
            self._gap_right_timestamps
            and self._gap_right_timestamps[0] <= first_retained_ts
        ):
            self._gap_right_timestamps.popleft()

    @property
    def bars(self) -> tuple[TechnicalBar, ...]:
        return tuple(self._bars)

    @property
    def revision_count(self) -> int:
        return self._revision_count

    def update(self, value: Any) -> TechnicalSnapshot:
        bar = TechnicalBar.from_value(value)
        if not bar.is_closed:
            return self.snapshot(extra_reasons=("forming_bar_ignored",))
        if not self._bars or bar.ts > self._bars[-1].ts:
            # Rolling windows evict in O(1).  Wilder/EMA states intentionally
            # retain their full stream history, which is the standard online
            # definition; they are not reseeded whenever the recovery buffer
            # drops its oldest bar.
            self._append_fast(bar)
            self._bars.append(bar)
            self._append_count += 1
            self._prune_retired_gaps()
        else:
            by_ts = {existing.ts: existing for existing in self._bars}
            by_ts[bar.ts] = bar
            ordered = [by_ts[ts] for ts in sorted(by_ts)][-self.max_history :]
            self._rebuild(ordered)
            self._revision_count += 1
        return self.snapshot()

    def extend(self, values: Iterable[Any]) -> TechnicalSnapshot:
        snapshot = self.snapshot()
        for value in values:
            snapshot = self.update(value)
        return snapshot

    def snapshot(self, *, extra_reasons: tuple[str, ...] = ()) -> TechnicalSnapshot:
        history_bars_retained = len(self._bars)
        bars_used = self._append_count
        last = self._bars[-1].close if self._bars else None
        bb_mid = self._close20_mean if len(self._close20) == 20 else None
        bb_lower = bb_upper = bb_pct_b = bb_width_pct = None
        if bb_mid is not None:
            variance = self._close20_m2 / 19.0
            sd = math.sqrt(variance)
            bb_lower, bb_upper = bb_mid - 2.0 * sd, bb_mid + 2.0 * sd
            width = bb_upper - bb_lower
            bb_pct_b = (last - bb_lower) / width if last is not None and width else 0.5
            bb_width_pct = width / bb_mid * 100.0 if bb_mid else None
        rsi14 = None
        if self._rsi_avg_gain is not None and self._rsi_avg_loss is not None:
            if self._rsi_avg_loss == 0:
                rsi14 = 100.0 if self._rsi_avg_gain > 0 else 50.0
            else:
                rsi14 = 100.0 - 100.0 / (1.0 + self._rsi_avg_gain / self._rsi_avg_loss)
        vol_z = None
        if len(self._volume20) == 20:
            vol_mean = self._volume20_mean
            vol_var = self._volume20_m2 / 19.0
            vol_sd = math.sqrt(vol_var)
            vol_z = (self._volume20[-1] - vol_mean) / vol_sd if vol_sd else 0.0
        reasons: list[str] = []
        if self._gap_right_timestamps:
            reasons.append("timeframe_gap_detected")
        if self._revision_count:
            reasons.append("bounded_rebuild_after_revision")
        reasons.extend(extra_reasons)
        volume_mean = self._sum_all_volume / history_bars_retained if history_bars_retained else None
        values = {
            "last_close": last,
            "sma_20": self._sum_close20 / 20.0 if len(self._close20) == 20 else None,
            "sma_50": self._sum_close50 / 50.0 if len(self._close50) == 50 else None,
            "ema_20": self._ema20,
            "ema_50": self._ema50,
            "rsi_14": rsi14,
            "atr_14": self._atr14,
            "atr_pct": _safe_atr_pct(self._atr14, last),
            "bb_lower": bb_lower,
            "bb_mid": bb_mid,
            "bb_upper": bb_upper,
            "bb_pct_b": bb_pct_b,
            "bb_width_pct": bb_width_pct,
            "momentum_10": (
                (self._close11[-1] - self._close11[0]) / self._close11[0] * 100.0
                if len(self._close11) == 11 and self._close11[0] else None
            ),
            "volume_zscore_20": vol_z,
            "volume_ratio_20": (
                self._volume20[-1] / (self._sum_volume20 / 20.0)
                if len(self._volume20) == 20 and self._sum_volume20 else None
            ),
            "vwap_20": self._sum_tpv20 / self._sum_volume20 if len(self._tpv20) == 20 and self._sum_volume20 else None,
            "donchian_high_20": self._high20[0][1] if len(self._bars) >= 20 and self._high20 else None,
            "donchian_low_20": self._low20[0][1] if len(self._bars) >= 20 and self._low20 else None,
            "obv_normalized": self._sum_obv / volume_mean if volume_mean and history_bars_retained >= 2 else None,
        }
        return TechnicalSnapshot(
            exchange=self.exchange,
            symbol=self.symbol,
            market_type=self.market_type,
            timeframe=self.timeframe,
            bar_close_ts=(
                self._bars[-1].ts + _TIMEFRAME_MS[self.timeframe]
                if self._bars and self.timeframe in _TIMEFRAME_MS
                else None
            ),
            bars_used=bars_used,
            history_bars_retained=history_bars_retained,
            warmup_complete=bars_used >= 50,
            input_fingerprint=self._digest.hexdigest(),
            algorithm_version=TECHNICAL_ANALYSIS_VERSION,
            data_quality="empty" if not bars_used else ("degraded" if reasons else "complete"),
            quality_reasons=tuple(dict.fromkeys(reasons)),
            values=values,
        )

    def verify(self) -> dict[str, Any]:
        reference = BatchTechnicalAnalyzer().compute(
            self._bars,
            exchange=self.exchange,
            symbol=self.symbol,
            market_type=self.market_type,
            timeframe=self.timeframe,
        )
        current = self.snapshot()
        mismatches = {
            key: (reference.values.get(key), current.values.get(key))
            for key in reference.values
            if not _values_equal(reference.values.get(key), current.values.get(key))
        }
        return {
            "ok": not mismatches,
            "algorithm_version": TECHNICAL_ANALYSIS_VERSION,
            "bars_used": self._append_count,
            "history_bars_retained": len(self._bars),
            "append_count": self._append_count,
            "revision_count": self._revision_count,
            "mismatches": mismatches,
            "reference_scope": "retained_history",
            "stream_history_exceeds_reference": self._append_count > len(self._bars),
        }


def _values_equal(left: Any, right: Any) -> bool:
    if left is None or right is None or isinstance(left, bool) or isinstance(right, bool):
        return left is right
    # Rolling sum/sum-of-squares and the reference's two-pass stdev differ by a
    # few ulps on long decimal series.  This tolerance is far below exchange
    # tick precision while still catching any material algorithm drift.
    return math.isclose(float(left), float(right), rel_tol=1e-10, abs_tol=1e-12)

"""Process-wide technical snapshot reuse for all decision consumers."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock
from typing import Any, Iterable

from aletheia.trading.technical_analysis import (
    DEFAULT_MAX_HISTORY,
    IncrementalTechnicalAnalyzer,
    TechnicalBar,
    TechnicalSnapshot,
)


@dataclass(frozen=True, slots=True)
class TechnicalSeriesKey:
    exchange: str
    symbol: str
    market_type: str
    timeframe: str

    @classmethod
    def build(
        cls, *, exchange: str, symbol: str, market_type: str, timeframe: str
    ) -> "TechnicalSeriesKey":
        raw_market_type = str(market_type or "spot").lower()
        normalized_market_type = {
            "swap": "perp",
            "usdt_futures": "perp",
        }.get(raw_market_type, raw_market_type)
        return cls(
            exchange=str(exchange or "unknown").lower(),
            symbol=str(symbol),
            market_type=normalized_market_type,
            timeframe=str(timeframe),
        )


class TechnicalSnapshotService:
    """Synchronize one incremental state per exact market-data series.

    A provider may resend its complete rolling window on every poll.  Identical
    windows become cache hits; a strict suffix extension updates only new bars;
    revisions or a changed window rebuild bounded state.  The service is
    process-local by design and contains no persistence or trading authority.
    """

    def __init__(self, *, max_series: int = 512, max_history: int = DEFAULT_MAX_HISTORY):
        self.max_series = max(1, int(max_series))
        self.max_history = max(64, int(max_history))
        self._states: OrderedDict[TechnicalSeriesKey, IncrementalTechnicalAnalyzer] = OrderedDict()
        self._lock = RLock()
        self._hits = 0
        self._incremental_updates = 0
        self._rebuilds = 0

    @staticmethod
    def _canonical(values: Iterable[Any]) -> list[TechnicalBar]:
        by_ts: dict[int, TechnicalBar] = {}
        for value in values:
            bar = TechnicalBar.from_value(value)
            if bar.is_closed:
                by_ts[bar.ts] = bar
        return [by_ts[ts] for ts in sorted(by_ts)]

    def snapshot_from_bars(
        self,
        bars: Iterable[Any],
        *,
        exchange: str,
        symbol: str,
        market_type: str,
        timeframe: str,
    ) -> TechnicalSnapshot:
        incoming = self._canonical(bars)[-self.max_history :]
        key = TechnicalSeriesKey.build(
            exchange=exchange,
            symbol=symbol,
            market_type=market_type,
            timeframe=timeframe,
        )
        with self._lock:
            state = self._states.get(key)
            current = list(state.bars) if state is not None else []
            if state is not None and incoming == current:
                self._hits += 1
                self._states.move_to_end(key)
                return state.snapshot()

            if state is not None and current and len(incoming) >= len(current) and incoming[: len(current)] == current:
                self._incremental_updates += len(incoming) - len(current)
                state.extend(incoming[len(current) :])
            elif state is not None and current and incoming:
                # Providers normally return a fixed-size rolling window.  Match
                # the retained suffix to the new prefix so the one newly closed
                # bar stays O(1) instead of looking like a full-window rewrite.
                current_by_ts = {bar.ts: index for index, bar in enumerate(current)}
                start = current_by_ts.get(incoming[0].ts)
                overlap = current[start:] if start is not None else []
                if overlap and incoming[: len(overlap)] == overlap:
                    tail = incoming[len(overlap) :]
                    self._incremental_updates += len(tail)
                    state.extend(tail)
                else:
                    state = self._new_state(key, incoming)
                    self._states[key] = state
                    self._rebuilds += 1
            else:
                state = self._new_state(key, incoming)
                self._states[key] = state
                self._rebuilds += 1

            self._states.move_to_end(key)
            while len(self._states) > self.max_series:
                self._states.popitem(last=False)
            return state.snapshot()

    def _new_state(
        self, key: TechnicalSeriesKey, bars: list[TechnicalBar]
    ) -> IncrementalTechnicalAnalyzer:
        state = IncrementalTechnicalAnalyzer(
            exchange=key.exchange,
            symbol=key.symbol,
            market_type=key.market_type,
            timeframe=key.timeframe,
            max_history=self.max_history,
        )
        state.extend(bars)
        return state

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "series": len(self._states),
                "cache_hits": self._hits,
                "incremental_bar_updates": self._incremental_updates,
                "bounded_rebuilds": self._rebuilds,
            }

    def clear(self) -> None:
        with self._lock:
            self._states.clear()
            self._hits = self._incremental_updates = self._rebuilds = 0


_SHARED_SERVICE = TechnicalSnapshotService()


def get_technical_snapshot_service() -> TechnicalSnapshotService:
    return _SHARED_SERVICE

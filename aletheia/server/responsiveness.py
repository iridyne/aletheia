"""In-memory runtime responsiveness signals for cheap liveness probes."""

from __future__ import annotations

import asyncio
from collections import deque
import time

import anyio.to_thread


class EventLoopLagMonitor:
    """Track recent event-loop scheduling lag without performing I/O."""

    def __init__(self, *, interval_sec: float = 0.1, window_sec: float = 60.0):
        self.interval_sec = interval_sec
        self.window_sec = window_sec
        # Hard ceiling as a backstop: at 10 Hz sampling + 60 s window the
        # active-popleft sweep in record() keeps ~600 entries; maxlen=700
        # guarantees bounded memory even if sampling jitter or a window_sec
        # reconfiguration lets the soft sweep lag (L1 fix).
        self._samples: deque[tuple[float, float]] = deque(maxlen=700)
        self._last_sample_at: float | None = None

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        expected = loop.time() + self.interval_sec
        while True:
            await asyncio.sleep(max(expected - loop.time(), 0.0))
            observed = loop.time()
            self.record(max(observed - expected, 0.0), observed_at=observed)
            expected = observed + self.interval_sec

    def record(self, lag_sec: float, *, observed_at: float | None = None) -> None:
        now = time.monotonic() if observed_at is None else observed_at
        self._last_sample_at = now
        self._samples.append((now, max(lag_sec, 0.0)))
        cutoff = now - self.window_sec
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def snapshot(self) -> dict:
        now = time.monotonic()
        if self._last_sample_at is None:
            return {
                "last_ms": None,
                "max_60s_ms": None,
                "sample_age_ms": None,
            }
        lags = [lag for _, lag in self._samples]
        return {
            "last_ms": round(lags[-1] * 1000.0, 1) if lags else None,
            "max_60s_ms": round(max(lags) * 1000.0, 1) if lags else None,
            "sample_age_ms": round((now - self._last_sample_at) * 1000.0, 1),
        }


event_loop_lag_monitor = EventLoopLagMonitor()


def responsiveness_snapshot() -> dict:
    """Return only in-memory scheduler state; safe for an async liveness route."""

    limiter = anyio.to_thread.current_default_thread_limiter()
    return {
        "event_loop": event_loop_lag_monitor.snapshot(),
        "sync_workers": {
            "borrowed": limiter.borrowed_tokens,
            "total": limiter.total_tokens,
        },
    }

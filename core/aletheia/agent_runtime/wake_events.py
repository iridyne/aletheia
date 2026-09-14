"""Wake-event coalescing + per-position rate limiting for the System Operator daemon.

The exit-intent plan (M1.5, decision D6) adds several event sources that can wake the
LLM-backed System Operator — stop/target proximity, horizon due, thesis invalidation,
adverse market signal, catastrophic breach. Waking the LLM is expensive, so this
module bounds and shapes those wakes:

  • Coalescing  — events for the same position inside a fixed window collapse into ONE
    batch, whose combined context is handed to the LLM in a single evaluation.
  • Rate limit  — a per-position token bucket caps waking at capacity/refill (default
    15 per hour per position), while allowing short bursts on volatile markets.
  • Priority    — P0 catastrophic bypasses the limit and pre-empts; P1 urgent
    (stop-loss / thesis / horizon-due, the D3 "must-answer" events) is never dropped
    (it requeues until a token refills); P3 low (take-profit proximity) is dropped
    under pressure and picked up by the daemon heartbeat.

Pure and time-injected (``now`` passed in), so it is fully unit-testable without the
async daemon. The daemon (pi_daemon.py) feeds events in and drains ready batches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Event kinds and priority tiers ───────────────────────────────────────────
# Lower number = higher priority. P0 pre-empts and bypasses the rate limit.
CATASTROPHIC = "catastrophic"
STOP_LOSS_NEAR = "stop_loss_near"
THESIS_INVALIDATED = "thesis_invalidated"
HORIZON_DUE = "horizon_due"
ADVERSE_SIGNAL = "adverse_signal"
TAKE_PROFIT_NEAR = "take_profit_near"

P0, P1, P2, P3 = 0, 1, 2, 3
_DEFAULT_PRIORITY = P2

PRIORITY: dict[str, int] = {
    CATASTROPHIC: P0,
    STOP_LOSS_NEAR: P1,
    THESIS_INVALIDATED: P1,
    HORIZON_DUE: P1,        # D3: horizon-due is a "must-answer" wake → never dropped
    ADVERSE_SIGNAL: P2,     # caller may override from signal score (see below)
    TAKE_PROFIT_NEAR: P3,
}

# Priority at/below this tier is never dropped under rate pressure — it requeues
# until a token refills (P0 catastrophic, P1 urgent/must-answer).
_MUST_NOT_DROP_MAX = P1


def adverse_signal_priority(score: float | int | None) -> int:
    """Map a counter-signal confidence score to a wake priority, reusing the existing
    market-aware thresholds (urgent_exit=80, tighten_stop=60)."""
    if score is None:
        return P3
    if score >= 80:
        return P1
    if score >= 60:
        return P2
    return P3


@dataclass
class WakeEvent:
    """One reason to wake the Operator about a position. ``priority`` defaults from the
    kind but may be overridden (e.g. an adverse signal scaled by its score)."""

    position_id: str
    kind: str
    ts: float
    payload: dict = field(default_factory=dict)
    priority: int = -1

    def __post_init__(self) -> None:
        if self.priority < 0:
            self.priority = PRIORITY.get(self.kind, _DEFAULT_PRIORITY)

    def as_context(self) -> dict[str, Any]:
        return {"kind": self.kind, "priority": self.priority, "payload": self.payload}


@dataclass
class WakeBatch:
    """A coalesced set of events for one position, ready to hand to one evaluation."""

    position_id: str
    events: list[WakeEvent]

    @property
    def max_priority(self) -> int:
        return min(e.priority for e in self.events)

    def to_context(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "max_priority": self.max_priority,
            "events": [e.as_context() for e in self.events],
        }


class TokenBucket:
    """Classic token bucket. Capacity=15 + refill=15/h bounds any 1-hour window to 15
    while permitting a burst up to capacity. ``now`` (monotonic seconds) is injected."""

    def __init__(self, capacity: float, refill_per_sec: float, tokens: float | None = None):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = self.capacity if tokens is None else float(tokens)
        self._last: float | None = None

    def _refill(self, now: float) -> None:
        if self._last is None:
            self._last = now
            return
        elapsed = now - self._last
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self._last = now

    def take(self, now: float, amount: float = 1.0) -> bool:
        self._refill(now)
        if self.tokens >= amount:
            self.tokens -= amount
            return True
        return False


@dataclass
class _Pending:
    events: list[WakeEvent]
    first_seen: float


class WakeCoalescer:
    """Buffers events per position, then emits rate-limited, coalesced batches.

    Semantics of :meth:`ready_batches`:
      • A position's buffer becomes eligible when its coalesce window has elapsed
        (fixed window from first event — not sliding, so a steady stream can't starve
        it), OR immediately if it holds a P0 event.
      • P0 batches emit immediately, bypassing the token bucket.
      • Other eligible batches consume one token. If none is available:
          – priority ≤ P1 (must-not-drop) → requeue (kept, retried when a token refills),
          – priority ≥ P2 → dropped (heartbeat will pick the position up).
    """

    def __init__(
        self,
        capacity: float = 15,
        refill_per_hour: float = 15.0,
        coalesce_window_sec: float = 3.0,
    ):
        self.capacity = capacity
        self.refill_per_sec = refill_per_hour / 3600.0
        self.coalesce_window_sec = coalesce_window_sec
        self._pending: dict[str, _Pending] = {}
        self._buckets: dict[str, TokenBucket] = {}

    def add(self, event: WakeEvent, now: float) -> None:
        p = self._pending.get(event.position_id)
        if p is None:
            self._pending[event.position_id] = _Pending([event], now)
        else:
            p.events.append(event)

    def _bucket(self, position_id: str) -> TokenBucket:
        b = self._buckets.get(position_id)
        if b is None:
            b = TokenBucket(self.capacity, self.refill_per_sec)
            self._buckets[position_id] = b
        return b

    def ready_batches(self, now: float) -> list[WakeBatch]:
        out: list[WakeBatch] = []
        for pid in list(self._pending.keys()):
            pending = self._pending[pid]
            has_p0 = any(e.priority == P0 for e in pending.events)
            window_elapsed = (now - pending.first_seen) >= self.coalesce_window_sec
            if not (has_p0 or window_elapsed):
                continue

            batch = WakeBatch(pid, list(pending.events))
            if has_p0:
                out.append(batch)
                del self._pending[pid]
                continue

            if self._bucket(pid).take(now):
                out.append(batch)
                del self._pending[pid]
            elif batch.max_priority > _MUST_NOT_DROP_MAX:
                # P2/P3 under rate pressure → drop; heartbeat will re-scan.
                del self._pending[pid]
            # else: P1 must-not-drop → keep buffered, retry when a token refills.
        return out

    def pending_count(self) -> int:
        return len(self._pending)

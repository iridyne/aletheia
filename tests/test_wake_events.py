"""Tests for wake-event coalescing + per-position rate limiting (M1.5, exit-intent plan).

Pure and time-injected, so no async / no daemon needed here.
"""

from aletheia.agent_runtime.wake_events import (
    ADVERSE_SIGNAL,
    CATASTROPHIC,
    HORIZON_DUE,
    STOP_LOSS_NEAR,
    TAKE_PROFIT_NEAR,
    TokenBucket,
    WakeCoalescer,
    WakeEvent,
    adverse_signal_priority,
)


def _ev(kind, pid="p1", ts=0.0, priority=-1, **payload):
    return WakeEvent(position_id=pid, kind=kind, ts=ts, payload=payload, priority=priority)


# ── priority assignment ──────────────────────────────────────────────────────

def test_default_priority_from_kind():
    assert _ev(CATASTROPHIC).priority == 0
    assert _ev(STOP_LOSS_NEAR).priority == 1
    assert _ev(HORIZON_DUE).priority == 1        # D3: must-answer
    assert _ev(ADVERSE_SIGNAL).priority == 2
    assert _ev(TAKE_PROFIT_NEAR).priority == 3


def test_priority_override_respected():
    assert _ev(ADVERSE_SIGNAL, priority=1).priority == 1


def test_adverse_signal_priority_thresholds():
    assert adverse_signal_priority(85) == 1
    assert adverse_signal_priority(60) == 2
    assert adverse_signal_priority(59) == 3
    assert adverse_signal_priority(None) == 3


# ── token bucket ─────────────────────────────────────────────────────────────

def test_token_bucket_burst_then_block_then_refill():
    b = TokenBucket(capacity=2, refill_per_sec=1.0 / 240.0)  # 15/hour
    assert b.take(1000.0) is True   # 2 -> 1
    assert b.take(1000.0) is True   # 1 -> 0
    assert b.take(1000.0) is False  # empty
    # after 240s one token refills
    assert b.take(1000.0 + 240.0) is True
    assert b.take(1000.0 + 240.0) is False


# ── coalescing ───────────────────────────────────────────────────────────────

def test_coalesces_burst_into_one_batch_after_window():
    c = WakeCoalescer(coalesce_window_sec=3.0)
    c.add(_ev(STOP_LOSS_NEAR, ts=0.0), now=0.0)
    c.add(_ev(ADVERSE_SIGNAL, ts=1.0), now=1.0)
    # window not elapsed yet
    assert c.ready_batches(now=2.0) == []
    batches = c.ready_batches(now=3.0)
    assert len(batches) == 1
    assert len(batches[0].events) == 2                 # both events in ONE batch
    assert batches[0].max_priority == 1                # STOP_LOSS_NEAR dominates
    assert c.pending_count() == 0


def test_p0_emits_immediately_bypassing_window_and_limit():
    c = WakeCoalescer(capacity=0, coalesce_window_sec=3.0)  # zero tokens
    c.add(_ev(CATASTROPHIC, ts=0.0), now=0.0)
    batches = c.ready_batches(now=0.0)                  # no window wait, no token
    assert len(batches) == 1
    assert batches[0].max_priority == 0


def test_separate_positions_have_independent_buckets():
    c = WakeCoalescer(capacity=1, refill_per_hour=0.0001, coalesce_window_sec=0.0)
    c.add(_ev(ADVERSE_SIGNAL, pid="a"), now=0.0)
    c.add(_ev(ADVERSE_SIGNAL, pid="b"), now=0.0)
    ids = {b.position_id for b in c.ready_batches(now=0.0)}
    assert ids == {"a", "b"}   # each position spends its own token


# ── rate-limit over-limit tiers ──────────────────────────────────────────────

def _drain_one(c, ev, t):
    c.add(ev, now=t)
    return c.ready_batches(now=t + 0.001)   # window 0 → immediately eligible


def test_p3_dropped_under_rate_pressure():
    c = WakeCoalescer(capacity=1, refill_per_hour=0.00001, coalesce_window_sec=0.0)
    assert len(_drain_one(c, _ev(TAKE_PROFIT_NEAR, ts=0), 0.0)) == 1   # spends the token
    # next P3 has no token → dropped, not requeued
    assert _drain_one(c, _ev(TAKE_PROFIT_NEAR, ts=10), 10.0) == []
    assert c.pending_count() == 0


def test_p1_requeues_then_emits_after_refill():
    c = WakeCoalescer(capacity=1, refill_per_hour=15.0, coalesce_window_sec=0.0)
    assert len(_drain_one(c, _ev(STOP_LOSS_NEAR, ts=0), 0.0)) == 1     # spends the token
    # next P1 has no token → requeued (kept), NOT dropped
    c.add(_ev(HORIZON_DUE, ts=10), now=10.0)
    assert c.ready_batches(now=10.1) == []
    assert c.pending_count() == 1
    # after ~240s a token refills → the requeued must-answer event emits
    batches = c.ready_batches(now=260.0)
    assert len(batches) == 1
    assert batches[0].max_priority == 1
    assert c.pending_count() == 0


def test_requeued_p1_keeps_accumulating_events():
    c = WakeCoalescer(capacity=1, refill_per_hour=15.0, coalesce_window_sec=0.0)
    _drain_one(c, _ev(STOP_LOSS_NEAR, ts=0), 0.0)       # spend token
    c.add(_ev(HORIZON_DUE, pid="p1", ts=10), now=10.0)  # requeued (no token)
    c.ready_batches(now=10.1)
    c.add(_ev(STOP_LOSS_NEAR, pid="p1", ts=20), now=20.0)  # coalesces into the requeued buffer
    c.ready_batches(now=20.1)
    batches = c.ready_batches(now=300.0)                # token refilled
    assert len(batches) == 1
    assert len(batches[0].events) == 2                  # both accumulated events delivered

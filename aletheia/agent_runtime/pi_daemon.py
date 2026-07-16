"""System Operator event-driven daemon.

Runs as a long-lived asyncio.Task inside the uvicorn event loop.
Wakes immediately when any signal producer calls notify_pi_daemon(),
rather than waiting for the APScheduler 15-minute poll.

Architecture:
  Signal producers (sync APScheduler threads)
      └─ notify_pi_daemon()  ← call_soon_threadsafe, fire-and-forget
            ↓
  _pi_daemon_loop  (asyncio.Task in uvicorn loop)
      ├─ debounce  — collect burst signals before evaluating
      ├─ evaluate_trading_opportunity()
      └─ cooldown  — prevent LLM hammering

The APScheduler heartbeat job is kept at 15-min interval as a safety net;
when the daemon is running it simply calls notify_pi_daemon("heartbeat").
"""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from aletheia.agent_runtime.wake_events import WakeCoalescer, WakeEvent

logger = logging.getLogger("aletheia.pi_daemon")

# Module-level state — set at start_pi_daemon(), cleared at stop_pi_daemon().
_daemon_task: asyncio.Task | None = None
_signal_event: asyncio.Event | None = None    # created inside the daemon loop
_eval_lock: asyncio.Lock | None = None        # prevents self-overlap
_uvicorn_loop: asyncio.AbstractEventLoop | None = None  # captured at startup
# Wake-event pipeline (exit-intent plan M1.5). Events carry a position_id/kind/
# priority payload and flow through per-position coalescing + a token-bucket rate
# limit; source-only notifies set the generic-wake flag (the legacy portfolio wake).
# All three are mutated only on the loop thread (via call_soon_threadsafe).
_coalescer: WakeCoalescer | None = None
_pending_events: list[WakeEvent] = []
_generic_wake_pending: bool = False

# A negative value cannot collide with the non-negative queue depth returned by
# evaluate_trading_opportunity(). The loop treats it as work still pending so a
# transient evaluation failure is retried after cooldown without requiring a
# fresh producer notification.
_EVALUATION_RETRY_PENDING = -1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def notify_pi_daemon(source: str = "unknown", event: WakeEvent | None = None) -> bool:
    """Signal the daemon to wake up and evaluate.

    Safe to call from any thread (including APScheduler executor threads).
    Fire-and-forget: never blocks, never raises. Returns whether delivery to the
    event loop was successfully scheduled.

    ``event`` (optional, exit-intent plan M1.5) attaches a per-position WakeEvent that
    is coalesced + rate-limited before it reaches the LLM. A source-only call (no
    event) is the legacy generic portfolio wake and always evaluates this cycle.
    """
    global _uvicorn_loop, _signal_event
    if _uvicorn_loop is None or _signal_event is None:
        return False

    def _apply() -> None:
        # Runs on the loop thread → no lock needed for the shared wake state.
        global _generic_wake_pending
        if event is not None:
            _pending_events.append(event)
        else:
            _generic_wake_pending = True
        if _signal_event is not None:
            _signal_event.set()

    try:
        _uvicorn_loop.call_soon_threadsafe(_apply)
        logger.debug("pi_daemon: notified (source=%s, event=%s)", source, getattr(event, "kind", None))
        return True
    except Exception as exc:
        logger.debug("pi_daemon: notify failed (source=%s): %s", source, exc)
        return False


def is_daemon_running() -> bool:
    return _daemon_task is not None and not _daemon_task.done()


def daemon_status() -> dict:
    return {
        "running": is_daemon_running(),
        "event_set": _signal_event.is_set() if _signal_event else False,
        "lock_held": _eval_lock.locked() if _eval_lock else False,
    }


def start_pi_daemon(
    loop: asyncio.AbstractEventLoop,
    debounce_sec: float = 8.0,
    cooldown_sec: float = 60.0,
    heartbeat_sec: float = 300.0,
    coalesce_window_sec: float = 3.0,
    wake_capacity: float = 15,
    wake_refill_per_hour: float = 15.0,
) -> "asyncio.Task | None":
    global _daemon_task, _uvicorn_loop
    if _daemon_task is not None and not _daemon_task.done():
        return _daemon_task
    _uvicorn_loop = loop
    _daemon_task = loop.create_task(
        _pi_daemon_loop(
            debounce_sec, cooldown_sec, heartbeat_sec,
            coalesce_window_sec, wake_capacity, wake_refill_per_hour,
        ),
        name="pi_daemon",
    )
    logger.info(
        "pi_daemon: started (debounce=%.0fs, cooldown=%.0fs, heartbeat=%.0fs, "
        "coalesce=%.1fs, wake_bucket=%.0f/%.0fh)",
        debounce_sec, cooldown_sec, heartbeat_sec,
        coalesce_window_sec, wake_capacity, wake_refill_per_hour,
    )
    return _daemon_task


def stop_pi_daemon() -> None:
    global _daemon_task, _uvicorn_loop
    if _daemon_task is not None and not _daemon_task.done():
        _daemon_task.cancel()
        logger.info("pi_daemon: cancelled")
    _daemon_task = None
    _uvicorn_loop = None


# ---------------------------------------------------------------------------
# Internal loop
# ---------------------------------------------------------------------------

async def _pi_daemon_loop(
    debounce_sec: float,
    cooldown_sec: float,
    heartbeat_sec: float,
    coalesce_window_sec: float = 3.0,
    wake_capacity: float = 15,
    wake_refill_per_hour: float = 15.0,
) -> None:
    global _signal_event, _eval_lock, _coalescer, _generic_wake_pending
    _signal_event = asyncio.Event()
    _eval_lock = asyncio.Lock()
    _coalescer = WakeCoalescer(
        capacity=wake_capacity,
        refill_per_hour=wake_refill_per_hour,
        coalesce_window_sec=coalesce_window_sec,
    )
    _pending_events.clear()
    _generic_wake_pending = False

    logger.info("pi_daemon: loop started")
    # A process restart clears in-memory producer notifications.  Re-arm one
    # ordinary wake only when durable queued work exists, so a restart cannot
    # strand candidates until the next producer happens to emit a signal.
    if _has_pending_signal_work():
        _generic_wake_pending = True
        _signal_event.set()
        logger.info("pi_daemon: rearmed durable pending queue at startup")

    while True:
        # Wait for signal event OR heartbeat timeout
        try:
            await asyncio.wait_for(asyncio.shield(_signal_event.wait()), timeout=heartbeat_sec)
            triggered_by = "signal"
        except asyncio.TimeoutError:
            triggered_by = "heartbeat"

        _signal_event.clear()

        # Debounce: after a signal fires, sleep briefly to collect burst signals
        if triggered_by == "signal" and debounce_sec > 0:
            await asyncio.sleep(debounce_sec)
            _signal_event.clear()

        # Check runtime guards before acquiring lock
        try:
            from aletheia.config.loader import load_config
            cfg = load_config()
            if cfg.runtime.paused:
                logger.debug("pi_daemon: skipped (runtime.paused=True)")
                continue
            if cfg.agent.permission_mode not in ("Paper", "Full"):
                logger.debug("pi_daemon: skipped (permission_mode=%s)", cfg.agent.permission_mode)
                continue
        except Exception as exc:
            logger.warning("pi_daemon: config load failed: %s", exc)
            continue

        # Skip if a prior evaluation is still running. Do this BEFORE draining the
        # coalescer so buffered wake events survive for the next cycle instead of
        # being consumed and lost while an eval is mid-flight.
        if _eval_lock.locked():
            logger.debug("pi_daemon: evaluation already in progress, skipping")
            continue

        # Drain pending wake events into the coalescer and pull rate-limited,
        # coalesced batches. The generic-wake flag is the legacy portfolio wake.
        now = asyncio.get_running_loop().time()
        generic = _generic_wake_pending
        _generic_wake_pending = False
        if _pending_events:
            drained = _pending_events[:]
            _pending_events.clear()
            for ev in drained:
                _coalescer.add(ev, now)
        batches = _coalescer.ready_batches(now) if _coalescer else []

        # Evaluate on: a legacy generic wake, a heartbeat, or at least one position
        # whose events passed the rate limit. Purely rate-limited/coalescing events
        # (no ready batch) do NOT wake the LLM this cycle.
        if not (generic or triggered_by == "heartbeat" or batches):
            continue

        wake_context = _build_wake_context(triggered_by, batches)
        async with _eval_lock:
            pending_queue_remaining = await _run_evaluation(triggered_by, wake_context)

        # Cooldown bounds LLM cadence. Notifications that arrive during the rest
        # period remain set, and a reported queue backlog schedules the next item
        # instead of silently discarding work.
        if cooldown_sec > 0:
            await asyncio.sleep(cooldown_sec)
        if pending_queue_remaining != 0:
            _generic_wake_pending = True
            _signal_event.set()


def _has_pending_signal_work() -> bool:
    """Return whether one durable queued candidate still needs a final PI action.

    This is intentionally a read-only liveness probe.  It does not rank, score,
    or alter any candidate and it excludes expired or already-finalized sources.
    """
    try:
        from aletheia.storage.repositories import count_unresolved_signal_pending

        return count_unresolved_signal_pending() > 0
    except Exception as exc:
        logger.debug("pi_daemon: startup pending probe unavailable: %s", exc)
        return False


def _build_wake_context(triggered_by: str, batches: list) -> dict:
    """Assemble the context handed to one evaluation from the coalesced batches."""
    ctx: dict = {"trigger": triggered_by}
    if batches:
        ctx["wake_batches"] = [b.to_context() for b in batches]
    return ctx


async def _run_evaluation(triggered_by: str, wake_context: dict | None = None) -> int:
    try:
        from aletheia.agent_runtime.run_context import bind_economic_attribution
        from aletheia.agent_shell.commands import evaluate_trading_opportunity
        from aletheia.agent_runtime.scheduler import _record_job_success

        logger.info(
            "pi_daemon: evaluating (trigger=%s, positions=%d)",
            triggered_by, len((wake_context or {}).get("wake_batches", [])),
        )
        evaluation_params = dict(wake_context or {})
        evaluation_params["_trigger"] = triggered_by
        evaluation_id = "operator_eval_" + uuid4().hex[:16]
        with bind_economic_attribution(
            logical_agent="pi_trading_decision",
            evaluation_id=evaluation_id,
            trigger=triggered_by,
        ):
            result = await asyncio.wait_for(
                evaluate_trading_opportunity(evaluation_params),
                timeout=300.0,
            )
        action = result.get("action", "unknown") if isinstance(result, dict) else "unknown"
        remaining = (
            result.get("pending_queue_remaining", 0)
            if isinstance(result, dict) else 0
        )
        logger.info("pi_daemon: evaluation done (trigger=%s, action=%s)", triggered_by, action)
        _record_job_success("pi_trading_decision")
        return max(int(remaining or 0), 0)
    except asyncio.TimeoutError:
        from aletheia.agent_runtime.scheduler import _record_job_failure
        logger.warning("pi_daemon: evaluate_trading_opportunity timed out after 300s")
        _record_job_failure("pi_trading_decision", "daemon_evaluation_timeout")
        return _EVALUATION_RETRY_PENDING
    except asyncio.CancelledError:
        raise  # propagate shutdown
    except Exception as exc:
        from aletheia.agent_runtime.scheduler import _record_job_failure
        logger.warning("pi_daemon: evaluation failed: %s", exc)
        _record_job_failure("pi_trading_decision", "daemon_evaluation_failed")
        return _EVALUATION_RETRY_PENDING
    finally:
        # Release thread-local SQLite connection to prevent WAL lock across cooldown
        try:
            from aletheia.storage.sqlite import rollback_if_in_transaction, close_db
            rollback_if_in_transaction()
            close_db()
        except Exception:
            pass

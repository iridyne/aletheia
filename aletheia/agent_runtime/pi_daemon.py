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
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from uuid import uuid4

from aletheia.agent_runtime.wake_events import WakeCoalescer, WakeEvent

logger = logging.getLogger("aletheia.pi_daemon")

_PORTFOLIO_RETRY_BASE_SEC = 300.0
_PORTFOLIO_RETRY_MAX_SEC = 1800.0
_DEFER_GUARD_BACKOFF_MAX_SEC = 1.0
_NO_MODEL_COOLDOWN_MAX_SEC = 1.0


@dataclass(frozen=True)
class _RetryWakeState:
    batch: dict
    attempt: int
    not_before: float

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
_retry_wake_batches: dict[str, _RetryWakeState] = {}
_generic_wake_pending: bool = False
# Runtime tracking: updated by start/stop, exposed via daemon_status().
_task_generation: int = 0
_started_at: float | None = None   # monotonic timestamp of last start
_active_params: dict | None = None  # debounce/cooldown/heartbeat in effect
_last_error_summary: str | None = None  # bounded, non-secret operator diagnostic

# A negative value cannot collide with the non-negative queue depth returned by
# evaluate_trading_opportunity(). The loop treats it as work still pending so a
# transient evaluation failure is retried after cooldown without requiring a
# fresh producer notification.
_EVALUATION_RETRY_PENDING = -1
# A no-model defer does not need the LLM cooldown. Keep a short bounded delay so
# a due cohort drains without waiting the LLM interval or busy-spinning.
_EVALUATION_NO_MODEL_PENDING = -2


def _evaluation_cooldown_delay(remaining: int, configured_sec: float) -> float:
    if remaining == _EVALUATION_NO_MODEL_PENDING:
        return (
            min(configured_sec, _NO_MODEL_COOLDOWN_MAX_SEC)
            if configured_sec > 0
            else _NO_MODEL_COOLDOWN_MAX_SEC
        )
    return max(configured_sec, 0.0)


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


def current_daemon_task() -> asyncio.Task | None:
    return _daemon_task


def daemon_status() -> dict:
    return {
        "running": is_daemon_running(),
        "event_set": _signal_event.is_set() if _signal_event else False,
        "lock_held": _eval_lock.locked() if _eval_lock else False,
        "task_generation": _task_generation,
        "started_at": _started_at,
        "active_params": _active_params,
        "last_error_summary": _last_error_summary,
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
    global _daemon_task, _uvicorn_loop, _task_generation, _started_at, _active_params
    if _daemon_task is not None and not _daemon_task.done():
        return _daemon_task
    _uvicorn_loop = loop
    _task_generation += 1
    _started_at = time.monotonic()
    _active_params = {
        "debounce_sec": debounce_sec,
        "cooldown_sec": cooldown_sec,
        "heartbeat_sec": heartbeat_sec,
    }
    _daemon_task = loop.create_task(
        _pi_daemon_loop(
            debounce_sec, cooldown_sec, heartbeat_sec,
            coalesce_window_sec, wake_capacity, wake_refill_per_hour,
        ),
        name="pi_daemon",
    )
    logger.info(
        "pi_daemon: started (gen=%d, debounce=%.0fs, cooldown=%.0fs, heartbeat=%.0fs, "
        "coalesce=%.1fs, wake_bucket=%.0f/%.0fh)",
        _task_generation, debounce_sec, cooldown_sec, heartbeat_sec,
        coalesce_window_sec, wake_capacity, wake_refill_per_hour,
    )
    return _daemon_task


def stop_pi_daemon() -> None:
    global _daemon_task, _uvicorn_loop, _started_at, _active_params
    if _daemon_task is not None and not _daemon_task.done():
        _daemon_task.cancel()
        logger.info("pi_daemon: cancelled")
    _daemon_task = None
    _uvicorn_loop = None
    _started_at = None
    _active_params = None


async def reconfigure_pi_daemon(
    enabled: bool,
    debounce_sec: float,
    cooldown_sec: float,
    heartbeat_sec: float,
    loop: asyncio.AbstractEventLoop,
) -> dict:
    """Apply new pi_daemon config to the running process without a gateway restart.

    Called from POST /api/settings/pi_daemon after the config is persisted.
    Returns {"configured": bool, "running": bool, "blocker": str | None}.
    A non-None blocker names the gate that prevented the requested state.

    Pending wake events (_pending_events / _generic_wake_pending) survive a
    controlled restart because they are module-level, not owned by the task.
    """
    global _daemon_task, _uvicorn_loop, _started_at, _active_params

    currently_running = is_daemon_running()

    if not enabled:
        if currently_running:
            _daemon_task.cancel()
            try:
                await _daemon_task
            except (asyncio.CancelledError, Exception):
                pass
            _daemon_task = None
            _uvicorn_loop = None
            _started_at = None
            _active_params = None
            logger.info("pi_daemon: stopped via settings reconfigure (enabled=false)")
        return {"configured": False, "running": False, "blocker": None}

    # Gates must pass before starting or restarting.
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
    except Exception as exc:
        return {"configured": True, "running": currently_running, "blocker": f"config_load_error:{exc}"}

    try:
        from aletheia.agent_runtime.startup_behavior_gate import gate_allows_startup
        if not gate_allows_startup(cfg):
            logger.info("pi_daemon: enable blocked by startup_behavior_gate")
            return {"configured": True, "running": False, "blocker": "startup_behavior_gate"}
    except Exception:
        pass  # permissive on gate-check failure

    if cfg.runtime.paused:
        return {"configured": True, "running": False, "blocker": "runtime_paused"}
    if cfg.agent.permission_mode not in ("Paper", "Full"):
        return {
            "configured": True,
            "running": False,
            "blocker": f"permission_mode:{cfg.agent.permission_mode}",
        }

    # Skip restart when already running with the exact same parameters.
    requested = {
        "debounce_sec": debounce_sec,
        "cooldown_sec": cooldown_sec,
        "heartbeat_sec": heartbeat_sec,
    }
    if currently_running and _active_params == requested:
        return {"configured": True, "running": True, "blocker": None}

    # Controlled restart: cancel the current task but keep _uvicorn_loop intact
    # so notify_pi_daemon() stays callable between the cancel and the new start.
    if currently_running:
        _daemon_task.cancel()
        try:
            await _daemon_task
        except (asyncio.CancelledError, Exception):
            pass
        _daemon_task = None
        _started_at = None
        _active_params = None
        logger.info("pi_daemon: stopped for parameter reconfigure")

    task = start_pi_daemon(
        loop=loop,
        debounce_sec=debounce_sec,
        cooldown_sec=cooldown_sec,
        heartbeat_sec=heartbeat_sec,
    )
    return {"configured": True, "running": task is not None, "blocker": None}


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
        # Wait for a producer signal, portfolio retry, durable Operator defer
        # deadline, or the heartbeat backstop.  A deadline is queried from
        # SQLite every cycle so signal traffic and process restarts cannot reset
        # or forget the promised re-evaluation time.
        loop = asyncio.get_running_loop()
        retry_delay = _next_retry_delay(loop.time())
        defer_delay = _next_pending_signal_defer_delay()
        if retry_delay is not None and retry_delay <= 0:
            triggered_by = "retry"
        elif defer_delay is not None and defer_delay <= 0:
            triggered_by = "defer_deadline"
        else:
            timeout_options = [
                (retry_delay, "retry"),
                (defer_delay, "defer_deadline"),
                (heartbeat_sec, "heartbeat"),
            ]
            wait_timeout, timeout_trigger = min(
                (item for item in timeout_options if item[0] is not None),
                key=lambda item: item[0],
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(_signal_event.wait()), timeout=wait_timeout
                )
                triggered_by = "signal"
            except asyncio.TimeoutError:
                triggered_by = timeout_trigger

        _signal_event.clear()

        # Debounce: after a signal fires, sleep briefly to collect burst signals
        if triggered_by == "signal" and debounce_sec > 0:
            await asyncio.sleep(debounce_sec)
            _signal_event.clear()

        # Check runtime guards before acquiring lock
        defer_guard_backoff = min(
            max(heartbeat_sec, 0.05), _DEFER_GUARD_BACKOFF_MAX_SEC
        )
        try:
            from aletheia.config.loader import load_config
            cfg = load_config()
            if cfg.runtime.paused:
                logger.debug("pi_daemon: skipped (runtime.paused=True)")
                if defer_delay is not None and defer_delay <= 0:
                    await asyncio.sleep(defer_guard_backoff)
                continue
            if cfg.agent.permission_mode not in ("Paper", "Full"):
                logger.debug("pi_daemon: skipped (permission_mode=%s)", cfg.agent.permission_mode)
                if defer_delay is not None and defer_delay <= 0:
                    await asyncio.sleep(defer_guard_backoff)
                continue
        except Exception as exc:
            logger.warning("pi_daemon: config load failed: %s", exc)
            if defer_delay is not None and defer_delay <= 0:
                await asyncio.sleep(defer_guard_backoff)
            continue

        # Skip if a prior evaluation is still running. Do this BEFORE draining the
        # coalescer so buffered wake events survive for the next cycle instead of
        # being consumed and lost while an eval is mid-flight.
        if _eval_lock.locked():
            logger.debug("pi_daemon: evaluation already in progress, skipping")
            if defer_delay is not None and defer_delay <= 0:
                await asyncio.sleep(0.05)
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
        wake_batches = _ready_wake_batches(
            [batch.to_context() for batch in batches],
            now=now,
        )

        # Evaluate on: a legacy generic wake, a heartbeat, or at least one position
        # whose events passed the rate limit. Purely rate-limited/coalescing events
        # (no ready batch) do NOT wake the LLM this cycle.
        if not (
            generic
            or triggered_by in {"heartbeat", "defer_deadline"}
            or wake_batches
        ):
            continue

        wake_context = _build_wake_context(triggered_by, wake_batches)
        async with _eval_lock:
            pending_queue_remaining = await _run_evaluation(triggered_by, wake_context)

        # Cooldown bounds LLM cadence. Notifications that arrive during the rest
        # period remain set, and a reported queue backlog schedules the next item
        # instead of silently discarding work.
        cooldown_delay = _evaluation_cooldown_delay(
            pending_queue_remaining, cooldown_sec,
        )
        if cooldown_delay > 0:
            await asyncio.sleep(cooldown_delay)
        if (
            pending_queue_remaining == _EVALUATION_RETRY_PENDING
            and not _retry_wake_batches
        ):
            # Generic entry-evaluation failures have no exact portfolio batch to
            # schedule, so preserve their existing cooldown-bounded retry path.
            _generic_wake_pending = True
            _signal_event.set()
        elif (
            pending_queue_remaining != _EVALUATION_RETRY_PENDING
            and pending_queue_remaining != 0
        ):
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


def _next_pending_signal_defer_delay() -> float | None:
    """Seconds until the next still-owed durable Operator defer, or ``None``.

    Overdue promises return ``0`` and are therefore evaluated immediately.  A
    malformed or unavailable deadline fails closed to the ordinary heartbeat;
    it never causes an early model call.
    """
    try:
        from aletheia.storage.repositories import next_pending_signal_defer_deadline

        raw = next_pending_signal_defer_deadline()
        if not raw:
            return None
        deadline = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            return None
        return max(
            (deadline.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds(),
            0.0,
        )
    except Exception as exc:
        logger.debug("pi_daemon: defer deadline unavailable: %s", exc)
        return None


def _build_wake_context(triggered_by: str, batches: list) -> dict:
    """Assemble the context handed to one evaluation from the coalesced batches."""
    ctx: dict = {"trigger": triggered_by}
    if batches:
        ctx["wake_batches"] = [
            batch if isinstance(batch, dict) else batch.to_context()
            for batch in batches
        ]
    return ctx


def _wake_batch_key(batch: dict) -> str | None:
    position_id = str(batch.get("position_id") or "").strip()
    if not position_id:
        return None
    kinds = sorted({
        str(event.get("kind") or "unknown")
        for event in (batch.get("events") or [])
        if isinstance(event, dict)
    })
    return f"{position_id}|{','.join(kinds) or 'unknown'}"


def _next_retry_delay(now: float) -> float | None:
    if not _retry_wake_batches:
        return None
    return max(
        min(state.not_before for state in _retry_wake_batches.values()) - now,
        0.0,
    )


def _ready_wake_batches(batches: list[dict], *, now: float) -> list[dict]:
    """Return new or due batches without letting duplicates bypass backoff."""
    ready: list[dict] = []
    seen: set[str] = set()
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        key = _wake_batch_key(batch)
        if key is None or key in seen:
            continue
        state = _retry_wake_batches.get(key)
        if state is not None:
            if state.not_before > now:
                continue
            ready.append(state.batch)
        else:
            ready.append(batch)
        seen.add(key)
    for key, state in _retry_wake_batches.items():
        if key not in seen and state.not_before <= now:
            ready.append(state.batch)
            seen.add(key)
    return ready


def _clear_retry_wake_context(wake_context: dict | None) -> None:
    for batch in (wake_context or {}).get("wake_batches", []):
        if isinstance(batch, dict) and (key := _wake_batch_key(batch)) is not None:
            _retry_wake_batches.pop(key, None)


def _restore_wake_context(wake_context: dict | None, *, now: float | None = None) -> int:
    """Retain drained batches after a non-terminal portfolio review.

    These batches already passed coalescing and rate limiting. Keeping the exact
    context avoids degradation into a generic entry evaluation, while capped
    exponential backoff prevents a provider failure from creating a retry storm.
    """
    if now is None:
        now = asyncio.get_running_loop().time()
    restored = 0
    seen: set[str] = set()
    for batch in (wake_context or {}).get("wake_batches", []):
        if not isinstance(batch, dict):
            continue
        key = _wake_batch_key(batch)
        if key is None or key in seen:
            continue
        previous = _retry_wake_batches.get(key)
        attempt = previous.attempt + 1 if previous is not None else 1
        delay = min(
            _PORTFOLIO_RETRY_BASE_SEC * (2 ** (attempt - 1)),
            _PORTFOLIO_RETRY_MAX_SEC,
        )
        _retry_wake_batches[key] = _RetryWakeState(
            batch=previous.batch if previous is not None else batch,
            attempt=attempt,
            not_before=now + delay,
        )
        seen.add(key)
        restored += 1
    return restored


async def _run_evaluation(triggered_by: str, wake_context: dict | None = None) -> int:
    global _last_error_summary
    try:
        from aletheia.agent_runtime.run_context import bind_economic_attribution
        from aletheia.agent_shell.commands import (
            evaluate_trading_opportunity,
            review_portfolio_state,
        )
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
            evaluator = (
                review_portfolio_state
                if evaluation_params.get("wake_batches")
                else evaluate_trading_opportunity
            )
            result = await asyncio.wait_for(evaluator(evaluation_params), timeout=300.0)
        action = result.get("action", "unknown") if isinstance(result, dict) else "unknown"
        remaining = result.get("pending_queue_remaining") if isinstance(result, dict) else None
        if remaining is None:
            remaining = 1 if _has_pending_signal_work() else 0
        logger.info("pi_daemon: evaluation done (trigger=%s, action=%s)", triggered_by, action)
        is_portfolio_review = bool(evaluation_params.get("wake_batches"))
        review_completed = (
            result.get("review_completed") if isinstance(result, dict) else None
        )
        if is_portfolio_review and review_completed is not True:
            from aletheia.agent_runtime.scheduler import _record_job_failure

            restored = _restore_wake_context(wake_context)
            failure_reason = (
                "operator_position_review_unavailable"
                if action == "review_unavailable"
                else "operator_position_review_incomplete"
            )
            _record_job_failure(
                "pi_trading_decision", failure_reason
            )
            error_code = (
                str(result.get("review_error_code") or "review_incomplete")[:80]
                if isinstance(result, dict)
                else "review_incomplete"
            )
            _last_error_summary = f"portfolio_review:{error_code}"
            logger.warning(
                "pi_daemon: portfolio review incomplete "
                "(action=%s, restored=%d, next_retry_sec=%.1f)",
                action,
                restored,
                _next_retry_delay(asyncio.get_running_loop().time()) or 0.0,
            )
            return _EVALUATION_RETRY_PENDING
        if is_portfolio_review:
            _clear_retry_wake_context(wake_context)
        _record_job_success("pi_trading_decision")
        _last_error_summary = None
        if (
            not is_portfolio_review
            and isinstance(result, dict)
            and result.get("model_invoked") is False
            and int(remaining or 0) > 0
        ):
            return _EVALUATION_NO_MODEL_PENDING
        return max(int(remaining or 0), 0)
    except asyncio.TimeoutError:
        from aletheia.agent_runtime.scheduler import _record_job_failure
        logger.warning("pi_daemon: evaluate_trading_opportunity timed out after 300s")
        _last_error_summary = "evaluation_timeout"
        _record_job_failure("pi_trading_decision", "daemon_evaluation_timeout")
        _restore_wake_context(wake_context)
        return _EVALUATION_RETRY_PENDING
    except asyncio.CancelledError:
        raise  # propagate shutdown
    except Exception as exc:
        from aletheia.agent_runtime.scheduler import _record_job_failure
        logger.warning("pi_daemon: evaluation failed: %s", exc)
        _last_error_summary = f"{type(exc).__name__}"[:120]
        _record_job_failure("pi_trading_decision", "daemon_evaluation_failed")
        _restore_wake_context(wake_context)
        return _EVALUATION_RETRY_PENDING
    finally:
        # Release thread-local SQLite connection to prevent WAL lock across cooldown
        try:
            from aletheia.storage.sqlite import rollback_if_in_transaction, close_db
            rollback_if_in_transaction()
            close_db()
        except Exception:
            pass

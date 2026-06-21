"""PI Agent event-driven daemon.

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

The APScheduler heartbeat job is kept at 5-min interval as a safety net;
when the daemon is running it simply calls notify_pi_daemon("heartbeat").
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger("aletheia.pi_daemon")

# Module-level state — set at start_pi_daemon(), cleared at stop_pi_daemon().
_daemon_task: asyncio.Task | None = None
_signal_event: asyncio.Event | None = None    # created inside the daemon loop
_eval_lock: asyncio.Lock | None = None        # prevents self-overlap
_uvicorn_loop: asyncio.AbstractEventLoop | None = None  # captured at startup


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def notify_pi_daemon(source: str = "unknown") -> None:
    """Signal the daemon to wake up and evaluate.

    Safe to call from any thread (including APScheduler executor threads).
    Fire-and-forget: never blocks, never raises.
    """
    global _uvicorn_loop, _signal_event
    if _uvicorn_loop is None or _signal_event is None:
        return
    try:
        _uvicorn_loop.call_soon_threadsafe(_signal_event.set)
        logger.debug("pi_daemon: notified (source=%s)", source)
    except Exception as exc:
        logger.debug("pi_daemon: notify failed (source=%s): %s", source, exc)


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
) -> "asyncio.Task | None":
    global _daemon_task, _uvicorn_loop
    if _daemon_task is not None and not _daemon_task.done():
        return _daemon_task
    _uvicorn_loop = loop
    _daemon_task = loop.create_task(
        _pi_daemon_loop(debounce_sec, cooldown_sec, heartbeat_sec),
        name="pi_daemon",
    )
    logger.info(
        "pi_daemon: started (debounce=%.0fs, cooldown=%.0fs, heartbeat=%.0fs)",
        debounce_sec, cooldown_sec, heartbeat_sec,
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
) -> None:
    global _signal_event, _eval_lock
    _signal_event = asyncio.Event()
    _eval_lock = asyncio.Lock()

    logger.info("pi_daemon: loop started")

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

        # Skip if a prior evaluation is still running
        if _eval_lock.locked():
            logger.debug("pi_daemon: evaluation already in progress, skipping")
            continue

        async with _eval_lock:
            await _run_evaluation(triggered_by)

        # Cooldown: discard signals that arrive during rest period
        if cooldown_sec > 0:
            await asyncio.sleep(cooldown_sec)
            _signal_event.clear()


async def _run_evaluation(triggered_by: str) -> None:
    try:
        from aletheia.agent_shell.commands import evaluate_trading_opportunity
        logger.info("pi_daemon: evaluating (trigger=%s)", triggered_by)
        result = await asyncio.wait_for(
            evaluate_trading_opportunity({}),
            timeout=300.0,
        )
        action = result.get("action", "unknown") if isinstance(result, dict) else "unknown"
        logger.info("pi_daemon: evaluation done (trigger=%s, action=%s)", triggered_by, action)
    except asyncio.TimeoutError:
        logger.warning("pi_daemon: evaluate_trading_opportunity timed out after 300s")
    except asyncio.CancelledError:
        raise  # propagate shutdown
    except Exception as exc:
        logger.warning("pi_daemon: evaluation failed: %s", exc)
    finally:
        # Release thread-local SQLite connection to prevent WAL lock across cooldown
        try:
            from aletheia.storage.sqlite import rollback_if_in_transaction, close_db
            rollback_if_in_transaction()
            close_db()
        except Exception:
            pass

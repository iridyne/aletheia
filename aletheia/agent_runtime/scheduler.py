"""Scheduler for periodic agent + market-data tasks using APScheduler."""

import asyncio
import concurrent.futures
from contextvars import ContextVar
from functools import partial
import logging
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable
from zoneinfo import ZoneInfo
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING, STATE_STOPPED
from apscheduler.executors.pool import ThreadPoolExecutor as APSchedulerThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

from aletheia.config.loader import load_config

logger = logging.getLogger("aletheia.scheduler")

_scheduler: BackgroundScheduler | None = None
_SCHEDULER_JOB_LOCK = threading.RLock()
_JOB_RUN_STATE = threading.local()

# Jobs that continue running during a layered pause (no LLM dependency).
# These are pure data-collection / infrastructure jobs whose output is valuable
# even when AI-dependent decision-making is offline.
_ESSENTIAL_JOBS: set[str] = {
    "market_data",
    "data_collectors",
    "data_collector_context",
    "connectivity_monitor",
    "portfolio_snapshot",
    "price_feed",
    "ohlcv_nightly_backfill",
    "ohlcv_active_backfill",
    "exchange_sync",
    "health_check",
    "cleanup",
    "news_fetch",
    "okx_demo_symbols",
    "reconciliation",
    "position_reconcile",
    "tpsl_fill_check",
    "outcome_settler",
    "paper_funding_settler",
    "outcome_hook_replay",
    "shadow_resolver",
    "learning_contribution_evaluator",
    "learning_measurement_startup_catchup",
}

# Track which jobs were selectively paused (for targeted resume).
_selectively_paused_jobs: set[str] = set()

# Hard time budgets for scheduler jobs that depend on external IO or LLM calls.
# APScheduler's max_instances=1 prevents overlap, but it cannot recover a job
# that stays inside an awaited network call forever. Timeboxing these coroutines
# keeps one stuck collector/model request from starving the scheduler pools and
# holding SQLite thread-local state indefinitely.
_ASYNC_JOB_TIMEOUT_SEC: dict[str, float] = {
    "market_data": 90.0,
    "market_observer": 500.0,
    "dreaming_agent": 300.0,
    "paper_trader": 300.0,
    "pi_trading_decision": 300.0,
    "equity_observer": 240.0,
    "stock_observer": 240.0,  # kept for backwards-compat; superseded by equity_observer
    "prediction_observer": 240.0,
    "divergence_scanner": 60.0,
    "post_trade_reviewer": 300.0,
    "risk_critic": 300.0,
    "universe_refresh": 600.0,
    "data_collectors": 55.0,
    "data_collector_context": 60.0,
    "exchange_sync": 60.0,
    "portfolio_snapshot": 45.0,
    "price_feed": 45.0,
    "daily_summary": 60.0,
    "ohlcv_nightly_backfill": 3600.0,
    "ohlcv_active_backfill": 300.0,
    "pi_operator_loop": 120.0,
    "insight_synthesizer": 120.0,
    "confidence_calibration": 30.0,
    "reasoning_feedback": 60.0,
    "connectivity_monitor": 15.0,
    "news_fetch": 120.0,
    "news_analyze": 105.0,
    "lesson_reviewer": 300.0,
    "shadow_resolver": 60.0,
    "outcome_settler": 120.0,
    "paper_funding_settler": 60.0,
    "learning_contribution_evaluator": 60.0,
    "learning_lifecycle_controller": 60.0,
    # #90 fix: tpsl_fill_check runs every 90s and calls Bitget via
    # asyncio.run() internally. Without a bound, a stuck exchange session can
    # block the scheduler pool for the full IOCP drain (~255s on Windows) and
    # starve other io-executor jobs. 45s cap matches other network-bound polls.
    "tpsl_fill_check": 45.0,
}


# Process-owned timeout workers.  The previous implementation allocated one
# ThreadPoolExecutor and one asyncio loop for every invocation, then abandoned
# the worker thread whenever teardown exceeded the grace period.  A degraded
# provider could therefore grow the process by one thread per scheduler tick.
# These fixed workers bound both threads and pending work for the process life.
_TIMEOUT_WORKER_CAPACITY = 16
_SYNC_TIMEOUT_WORKERS = 1
_timeout_metrics_lock = threading.Lock()
_timeout_workers_lock = threading.Lock()
_timeout_metrics: dict[str, int] = {
    "async_submitted": 0,
    "async_completed": 0,
    "async_timed_out": 0,
    "async_cancelled": 0,
    "async_rejected": 0,
    "sync_submitted": 0,
    "sync_completed": 0,
    "sync_timed_out": 0,
    "sync_rejected": 0,
}


def _bump_timeout_metric(name: str) -> None:
    with _timeout_metrics_lock:
        _timeout_metrics[name] = _timeout_metrics.get(name, 0) + 1


def scheduler_timeout_metrics_snapshot() -> dict[str, object]:
    """Return bounded-worker state without exposing job inputs or credentials."""

    with _timeout_metrics_lock:
        metrics = dict(_timeout_metrics)
    worker = _async_timeout_worker
    metrics.update(
        {
            "async_worker_running": worker.running,
            "async_pending": worker.pending,
            "async_capacity": _TIMEOUT_WORKER_CAPACITY,
            "news_analyzer_worker_running": _news_analyzer_timeout_worker.running,
            "news_analyzer_pending": _news_analyzer_timeout_worker.pending,
            "sync_pending": _sync_timeout_worker.pending,
            "sync_workers": _SYNC_TIMEOUT_WORKERS,
            # Kept for compatibility with health/reporting consumers.  The new
            # design never abandons a per-invocation thread.
            "abandoned_threads": 0,
        }
    )
    return metrics


class _PersistentAsyncTimeoutWorker:
    """One persistent event loop with bounded cross-thread admission."""

    def __init__(self, thread_name: str = "aletheia-scheduler-async") -> None:
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending = 0
        self._thread_name = thread_name
        self._closed = False
        self._stopping = False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._loop)

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    @property
    def stopping(self) -> bool:
        with self._lock:
            return self._stopping

    @property
    def thread_alive(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive())

    def _start(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._closed:
                raise RuntimeError("scheduler async timeout worker is closed")
            if self._stopping:
                raise RuntimeError("scheduler async timeout worker is stopping")
            if self._thread is None or not self._thread.is_alive():
                self._started.clear()
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name=self._thread_name,
                    daemon=True,
                )
                self._thread.start()
        if not self._started.wait(timeout=5):
            raise RuntimeError("scheduler async worker did not start")
        with self._lock:
            if self._closed:
                raise RuntimeError("scheduler async timeout worker is closed")
            if self._stopping:
                raise RuntimeError("scheduler async timeout worker is stopping")
            if self._loop is None or self._thread is None or not self._thread.is_alive():
                raise RuntimeError("scheduler async worker stopped during startup")
            return self._loop

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            should_stop = self._stopping
        self._started.set()
        if should_stop:
            loop.call_soon(loop.stop)
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            with self._lock:
                self._loop = None
                self._stopping = False

    def submit(self, coroutine) -> concurrent.futures.Future:
        try:
            loop = self._start()
        except BaseException:
            if asyncio.iscoroutine(coroutine):
                coroutine.close()
            raise
        with self._lock:
            if self._closed or self._stopping:
                if asyncio.iscoroutine(coroutine):
                    coroutine.close()
                state = "stopping" if self._stopping else "closed"
                raise RuntimeError(f"scheduler async timeout worker is {state}")
            if self._pending >= _TIMEOUT_WORKER_CAPACITY:
                if asyncio.iscoroutine(coroutine):
                    coroutine.close()
                raise RuntimeError("scheduler async timeout queue is full")
            self._pending += 1
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except BaseException:
            with self._lock:
                self._pending -= 1
            if asyncio.iscoroutine(coroutine):
                coroutine.close()
            raise

        def _release(_future) -> None:
            with self._lock:
                self._pending = max(0, self._pending - 1)

        future.add_done_callback(_release)
        return future

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._stopping = True
            loop = self._loop
            thread = self._thread
        if thread is None:
            with self._lock:
                self._stopping = False
            return
        if loop is None and thread.is_alive():
            self._started.wait(timeout=5)
            with self._lock:
                loop = self._loop
        try:
            if loop is not None:
                loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass
        thread.join(timeout=5)
        with self._lock:
            if not thread.is_alive():
                self._stopping = False


_async_timeout_worker = _PersistentAsyncTimeoutWorker()
_news_analyzer_timeout_worker = _PersistentAsyncTimeoutWorker(
    "aletheia-news-analyzer-async"
)


def _ensure_timeout_workers_ready() -> None:
    """Replace closed workers; never submit into a loop already draining stop."""
    global _async_timeout_worker, _news_analyzer_timeout_worker

    with _timeout_workers_lock:
        if _async_timeout_worker.closed:
            if _async_timeout_worker.stopping or _async_timeout_worker.thread_alive:
                raise RuntimeError(
                    "scheduler async timeout worker shutdown incomplete; "
                    "refusing unsafe replacement"
                )
            _async_timeout_worker = _PersistentAsyncTimeoutWorker()
        if _news_analyzer_timeout_worker.closed:
            if (
                _news_analyzer_timeout_worker.stopping
                or _news_analyzer_timeout_worker.thread_alive
            ):
                raise RuntimeError(
                    "news analyzer timeout worker shutdown incomplete; "
                    "refusing unsafe replacement"
                )
            _news_analyzer_timeout_worker = _PersistentAsyncTimeoutWorker(
                "aletheia-news-analyzer-async"
            )


class _PersistentSyncTimeoutWorker:
    """One daemon sync worker; equivalent to ``max_workers=1`` with a hard queue cap."""

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[object, concurrent.futures.Future] | None] = (
            queue.Queue(maxsize=_TIMEOUT_WORKER_CAPACITY)
        )
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._pending = 0

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._worker_main,
                name="aletheia-scheduler-sync",
                daemon=True,
            )
            self._thread.start()

    def submit(self, fn) -> concurrent.futures.Future:
        self._start()
        future: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            if self._pending >= _TIMEOUT_WORKER_CAPACITY:
                raise RuntimeError("scheduler sync timeout queue is full")
            self._pending += 1

        def _release(_future) -> None:
            with self._lock:
                self._pending = max(0, self._pending - 1)

        future.add_done_callback(_release)
        try:
            self._queue.put_nowait((fn, future))
        except queue.Full as exc:
            future.cancel()
            raise RuntimeError("scheduler sync timeout queue is full") from exc
        return future

    def _worker_main(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                self._queue.task_done()
                return
            fn, future = item
            try:
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(fn())  # type: ignore[operator]
                    except BaseException as exc:  # noqa: BLE001 - preserve job failure
                        future.set_exception(exc)
            finally:
                self._queue.task_done()


_sync_timeout_worker = _PersistentSyncTimeoutWorker()


class _AsyncJobDeadline(TimeoutError):
    """The coroutine cooperatively reached its configured job deadline."""

# Consecutive failure counter per IO job. Resets on success; emits infra_event
# at threshold so failures surface in the dashboard status popover.
_consecutive_failures: dict[str, int] = {}
_FAILURE_ALERT_THRESHOLD = 3


def _record_job_success(job_id: str) -> None:
    _consecutive_failures[job_id] = 0


def _record_job_failure(job_id: str, error: str) -> None:
    _JOB_RUN_STATE.failure_recorded = True
    _consecutive_failures[job_id] = _consecutive_failures.get(job_id, 0) + 1
    from aletheia.learning.control import record_learning_job_failure
    record_learning_job_failure(job_id, error)
    count = _consecutive_failures[job_id]
    if count >= _FAILURE_ALERT_THRESHOLD:
        from aletheia.infra.events import emit_infra_event
        emit_infra_event(
            severity="warning",
            source="scheduler",
            code=f"job_consecutive_failure:{job_id}",
            message=f"{job_id} has failed {count} consecutive times: {error[:200]}",
            action="Check logs or config; job will retry next cycle",
            dedupe_sec=300,
        )
        try:
            from aletheia.infra.errors import record_error
            record_error(
                source="scheduler",
                message=f"{job_id} failed {count} consecutive times: {error[:200]}",
                level="warning",
                extra={"job_id": job_id, "consecutive": count},
            )
        except Exception:
            pass


# Volume scanner results cache (populated by data_collector_context, ~10 min cadence).
_latest_volume_ranking: dict = {}


def get_latest_volume_ranking() -> dict:
    """Return latest volume scanner results; falls back to DB snapshot after restart."""
    if _latest_volume_ranking:
        return _latest_volume_ranking.copy()
    try:
        from aletheia.storage import repositories as _repo
        return _repo.get_volume_ranking_snapshot()
    except Exception:
        return {}


# Isolate latency-sensitive infrastructure jobs from long LLM/agent jobs. A
# single APScheduler default ThreadPool lets a slow post-trade review or shell
# provider call occupy the same queue that market data, health, and operator
# support jobs depend on. Multiple small pools preserve the SQLite concurrency
# cap while preventing starvation of the boot catch-up observer.
_EXECUTOR_WORKERS = {
    "default": 2,
    # SQLite has one writer. In a local-first MVP, a conservative IO lane is
    # more reliable than parallel scheduled writers that frequently collide and
    # cascade into ``database is locked`` across unrelated jobs.
    "io": 1,
    "fast_io": 1,
    "observer": 1,
    # LLM-backed jobs also write run logs/artifacts. Keep them serial so a slow
    # model call cannot make two agent jobs compete for SQLite at once.
    "agents": 1,
}


def _run_job_guarded(job_id: str, fn):
    """Run one scheduled job and release any leaked SQLite writer state.

    The global _SCHEDULER_JOB_LOCK is held ONLY for the pre/post-job DB cleanup
    (rollback_if_in_transaction + close_db) — NOT around the job body. Network
    I/O, LLM calls, and asyncio.run() all execute outside the lock. SQLite
    single-writer ordering falls back to busy_timeout=30000 (WAL pillar 1),
    which is the correct mechanism for cross-writer contention.

    Previously the lock wrapped the entire job — including 45–240s async timeouts.
    On Windows, if asyncio.run() teardown hung (ProactorEventLoop IOCP drain),
    the executor thread held the RLock forever, starving every other job.
    """
    from aletheia.storage.sqlite import close_db, rollback_if_in_transaction

    with _SCHEDULER_JOB_LOCK:
        if rollback_if_in_transaction():
            logger.warning("Rolled back stale SQLite transaction before scheduler job %s", job_id)
    _JOB_RUN_STATE.job_id = job_id
    _JOB_RUN_STATE.failure_recorded = False
    try:
        from aletheia.learning.control import (
            is_autonomous_learning_job,
            learning_job_gate_reason,
            record_learning_job_skipped,
            record_learning_job_started,
            record_learning_job_success,
        )
        if is_autonomous_learning_job(job_id):
            gate_reason = learning_job_gate_reason(job_id)
            if gate_reason is not None:
                record_learning_job_skipped(job_id, gate_reason)
                logger.info("Learning job %s skipped: %s", job_id, gate_reason)
                return None
            record_learning_job_started(job_id)
        result = fn()
        if not getattr(_JOB_RUN_STATE, "failure_recorded", False):
            _record_job_success(job_id)
            record_learning_job_success(job_id)
        return result
    except Exception as e:
        logger.warning("%s job failed outside wrapper: %s", job_id, e)
        _record_job_failure(job_id, str(e))
    finally:
        with _SCHEDULER_JOB_LOCK:
            if rollback_if_in_transaction():
                logger.warning("Rolled back leaked SQLite transaction after scheduler job %s", job_id)
            close_db()
        for attr in ("job_id", "failure_recorded"):
            try:
                delattr(_JOB_RUN_STATE, attr)
            except AttributeError:
                pass


def _guarded(job_id: str, fn):
    return partial(_run_job_guarded, job_id, fn)


def _run_sync_with_timeout(job_id: str, fn) -> object:
    """Run sync work on one process-owned, capacity-bounded worker lane.

    Python cannot safely interrupt a running thread.  Keeping a single fixed
    worker therefore contains a non-cooperative call to one thread instead of
    allocating and abandoning a new thread on every retry.  The bounded slot
    gate also prevents an unresponsive lane from accumulating an unbounded
    future queue.
    """
    from aletheia.storage.sqlite import close_db, rollback_if_in_transaction

    timeout = _ASYNC_JOB_TIMEOUT_SEC[job_id]
    thread_timeout = timeout + 15.0

    def _run():
        try:
            return fn()
        finally:
            rollback_if_in_transaction()
            close_db()

    _bump_timeout_metric("sync_submitted")
    try:
        future = _sync_timeout_worker.submit(_run)
    except RuntimeError:
        _bump_timeout_metric("sync_rejected")
        raise
    try:
        result = future.result(timeout=thread_timeout)
        _bump_timeout_metric("sync_completed")
        return result
    except concurrent.futures.TimeoutError:
        # Cancel queued (not-yet-running) work. A running Python call cannot be
        # interrupted, but it occupies only the one process-owned daemon lane.
        future.cancel()
        _bump_timeout_metric("sync_timed_out")
        logger.error(
            "job %s: sync function hung after %.0fs — "
            "fixed worker remains occupied; new work stays bounded",
            job_id, thread_timeout,
        )
        raise TimeoutError(f"job {job_id} hung after {thread_timeout}s")


def _ensure_agent_run_ended(job_id: str, target_status: str) -> None:
    """Safety-net: mark any still-running agent_run row as ended from caller thread.

    Handles the case where asyncio.run() teardown hangs (IOCP drain) after the
    coroutine already completed successfully — the daemon thread's finally block
    never reaches its DB update, leaving a ghost 'running' row that the periodic
    cleanup later marks as 'stale'.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE agent_runs SET status = ?, ended_at = datetime('now') "
            "WHERE agent_name = ? AND status = 'running'",
            (target_status, job_id),
        )
        if not was_in_tx:
            db.commit()
    except Exception as e:
        logger.debug("_ensure_agent_run_ended(%s, %s) failed: %s", job_id, target_status, e)


def _run_async_with_timeout(job_id: str, awaitable: Awaitable[object]) -> object:
    """Run an async job with a hard wall-clock bound.

    Every invocation is admitted to a fixed process-owned event loop. News
    analysis has one isolated loop because a synchronous provider can otherwise
    block RSS and all other async jobs. Timeout cancellation never creates a
    disposable thread; pending admission remains bounded.

    Critical: DB cleanup (rollback + close) happens INSIDE the event loop via a
    wrapper coroutine's finally block so timeout cancellation cannot leave the
    worker's thread-local SQLite connection holding a write transaction.
    """
    timeout = _ASYNC_JOB_TIMEOUT_SEC[job_id]
    _ensure_timeout_workers_ready()

    async def _guarded_coro():
        from aletheia.storage.sqlite import close_db, rollback_if_in_transaction
        try:
            try:
                return await asyncio.wait_for(awaitable, timeout=timeout)
            except TimeoutError as exc:
                raise _AsyncJobDeadline from exc
        finally:
            rollback_if_in_transaction()
            close_db()

    worker = (
        _news_analyzer_timeout_worker
        if job_id == "news_analyze"
        else _async_timeout_worker
    )
    try:
        future = worker.submit(_guarded_coro())
    except RuntimeError:
        if asyncio.iscoroutine(awaitable):
            awaitable.close()
        _bump_timeout_metric("async_rejected")
        raise
    _bump_timeout_metric("async_submitted")
    try:
        result = future.result(timeout=timeout + 5.0)
        _bump_timeout_metric("async_completed")
        _ensure_agent_run_ended(job_id, "completed")
        return result
    except _AsyncJobDeadline as exc:
        # asyncio.wait_for enforced the actual job deadline on the worker loop.
        _bump_timeout_metric("async_timed_out")
        _ensure_agent_run_ended(job_id, "timed_out")
        raise TimeoutError(f"job {job_id} timed out after {timeout}s") from exc
    except concurrent.futures.TimeoutError:
        # The persistent loop itself did not respond to cancellation in time.
        # Cancel the exact future but keep the one fixed worker thread; capacity
        # remains accounted until its done callback actually runs.
        future.cancel()
        _bump_timeout_metric("async_timed_out")
        _bump_timeout_metric("async_cancelled")
        logger.error(
            "job %s: persistent async worker did not respond within %.0fs; "
            "future cancelled and worker capacity remains bounded",
            job_id, timeout + 5.0,
        )
        _ensure_agent_run_ended(job_id, "timed_out")
        raise TimeoutError(f"job {job_id} worker unresponsive after {timeout + 5.0}s")


def _serenity_schedule_boundary(now: datetime | None = None) -> datetime:
    """Return the latest daily 02:00 CST research boundary in UTC."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cst = ZoneInfo("Asia/Shanghai")
    local = current.astimezone(cst)
    boundary = local.replace(hour=2, minute=0, second=0, microsecond=0)
    if local < boundary:
        boundary -= timedelta(days=1)
    return boundary.astimezone(timezone.utc)


def _parse_scheduler_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _recover_interrupted_serenity_run() -> list[str]:
    """Mark research work owned by the previous gateway process as failed."""
    try:
        from aletheia.storage import repositories as repo
        from aletheia.storage.models import SerenityRunEvent

        active_runs = repo.list_active_serenity_research_runs()
        if not active_runs:
            return []
        now = datetime.now(timezone.utc).isoformat()
        recovered: list[str] = []
        for run in active_runs:
            run_id = run["id"]
            repo.update_serenity_research_run(
                run_id,
                status="failed",
                error_type="gateway_restart_interrupted",
                error_message="Gateway restarted before the research run completed",
                error_stage=run.get("stage"),
                finished_at=now,
                updated_at=now,
            )
            try:
                repo.insert_serenity_run_event(SerenityRunEvent(
                    run_id=run_id,
                    event_type="run.interrupted",
                    stage=run.get("stage"),
                    message="Gateway restart interrupted this research run",
                ))
            except Exception as event_exc:
                logger.warning(
                    "Could not record interruption event for Serenity run %s: %s",
                    run_id,
                    event_exc,
                )
            recovered.append(run_id)
        logger.warning(
            "Recovered %d interrupted Serenity research run(s) from the previous process",
            len(recovered),
        )
        return recovered
    except Exception as exc:
        logger.warning("Serenity startup recovery failed (non-fatal): %s", exc)
        return []


def _enqueue_serenity_research_if_due(now: datetime | None = None) -> dict:
    """Enqueue at most one durable research attempt for the latest CST batch."""
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityResearchRun

    boundary = _serenity_schedule_boundary(now)
    local_date = boundary.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    batch_key = f"serenity:scheduled:{local_date}"

    latest = repo.get_latest_successful_serenity_research_run()
    latest_at = _parse_scheduler_timestamp(
        (latest or {}).get("finished_at") or (latest or {}).get("created_at")
    )
    if latest_at is not None and latest_at >= boundary:
        return {"status": "fresh", "run_id": latest["id"], "batch_key": batch_key}

    attempted = repo.get_serenity_research_run_by_idempotency_key(batch_key)
    if attempted is not None:
        return {
            "status": "already_attempted",
            "run_id": attempted["id"],
            "batch_key": batch_key,
        }

    active = repo.get_active_serenity_research_run()
    if active is not None:
        return {"status": "active", "run_id": active["id"], "batch_key": batch_key}

    run = SerenityResearchRun(
        trigger_type="scheduled",
        mode="standard",
        status="queued",
        idempotency_key=batch_key,
    )
    repo.insert_serenity_research_run(run)
    try:
        from aletheia.agents.research_worker import enqueue_research_run
        enqueue_research_run(run.id)
    except Exception as exc:
        failed_at = datetime.now(timezone.utc).isoformat()
        repo.update_serenity_research_run(
            run.id,
            status="failed",
            error_type="enqueue_failed",
            error_message=str(exc)[:500],
            finished_at=failed_at,
            updated_at=failed_at,
        )
        raise
    return {"status": "enqueued", "run_id": run.id, "batch_key": batch_key}


def _run_serenity_startup_catchup() -> None:
    cfg = load_config()
    if cfg.runtime.paused or not cfg.serenity.enabled:
        return
    try:
        result = _enqueue_serenity_research_if_due()
        logger.info("Serenity startup catch-up: %s", result["status"])
    except Exception as exc:
        logger.warning("Serenity startup catch-up failed: %s", exc)
        try:
            from aletheia.infra.errors import record_error
            record_error(
                source="serenity",
                message=f"serenity startup catch-up failed: {exc}",
                exc=exc,
                level="warning",
                extra={"stage": "startup_catchup"},
            )
        except Exception:
            pass
        _record_job_failure("serenity_startup_catchup", str(exc))


def _operator_assessment_due(
    now: datetime | None = None,
    min_interval_minutes: int = 225,
) -> bool:
    """Return whether persisted Operator output is old enough for another run.

    The cron is four-hourly, but prior runs can start several minutes late while
    waiting for the serial agents executor. A 3h45m floor preserves the next
    nominal cron while deduplicating restart catch-ups and closely spaced runs.
    Missing, malformed, or unreadable history fails open: the Operator retains
    its own permission and per-action rate limits.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    try:
        from aletheia.storage import repositories as repo

        rows = repo.list_knowledge_items(
            item_type="operator_assessment",
            status="active",
            limit=1,
        )
        if not rows:
            return True
        latest = _parse_scheduler_timestamp(rows[0].get("created_at"))
        if latest is None or latest > current + timedelta(minutes=5):
            return True
        return current - latest >= timedelta(minutes=min_interval_minutes)
    except Exception as exc:
        logger.warning("Operator assessment freshness check failed open: %s", exc)
        return True


def init_scheduler() -> BackgroundScheduler | None:
    """Initialize and start the background scheduler.

    Returns None if scheduler is disabled in config. Honors runtime.paused at boot.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    cfg = load_config()
    # Research workers are process-owned daemon threads. Their persisted active
    # rows are interrupted after any restart even when scheduling is disabled.
    _recover_interrupted_serenity_run()
    if not cfg.runtime.scheduler_enabled:
        logger.info("Scheduler disabled by config; not starting.")
        return None
    _ensure_timeout_workers_ready()

    # Keep each pool small for SQLite, but split queues by job class:
    # - io: short read/write infrastructure jobs (market data, health, sync)
    # - observer: the boot catch-up market observer; must not starve behind LLM jobs
    # - agents: slower LLM / review / repair jobs
    # Each job is wrapped by _run_job_guarded(), which rolls back leaked
    # per-thread transactions and closes thread-local SQLite connections at job
    # end so one failed job cannot pin the writer lock for the next jobs.
    _scheduler = BackgroundScheduler(
        executors={
            name: APSchedulerThreadPoolExecutor(workers)
            for name, workers in _EXECUTOR_WORKERS.items()
        },
        job_defaults={"coalesce": True, "max_instances": 1},
    )

    _scheduler.add_job(
        _guarded("market_data", _run_market_data), "interval", minutes=5, id="market_data",
        misfire_grace_time=60, executor="io",
    )
    _scheduler.add_job(
        _guarded("market_observer", _run_market_observer), "interval", minutes=10, id="market_observer",
        misfire_grace_time=60, next_run_time=datetime.now() + timedelta(seconds=75),
        executor="observer",
    )
    _scheduler.add_job(
        _guarded("dreaming_daily", _run_dreaming_agent), "cron", hour=2, minute=0, id="dreaming_daily",
        misfire_grace_time=300, executor="agents", timezone="Asia/Shanghai",
    )
    _scheduler.add_job(
        _guarded("serenity_startup_catchup", _run_serenity_startup_catchup),
        "date",
        run_date=datetime.now() + timedelta(minutes=4),
        id="serenity_startup_catchup",
        misfire_grace_time=None,
        executor="agents",
    )
    # Evidence-gated review of the lessons dreaming produced — runs after the 2am
    # consolidation so it sees the night's fresh proposed lessons.
    _scheduler.add_job(
        _guarded("lesson_reviewer", _run_lesson_reviewer), "cron", hour=3, minute=0,
        id="lesson_reviewer", misfire_grace_time=300, executor="agents", timezone="Asia/Shanghai",
    )
    _scheduler.add_job(
        _guarded("insight_synthesizer", _run_insight_synthesizer), "cron", hour=2, minute=30,
        id="insight_synthesizer", misfire_grace_time=600, executor="agents", timezone="Asia/Shanghai",
    )
    # Repair at most the latest missed daily Learning window. Successful window
    # keys live in learning_job_health, so ordinary restarts do not duplicate work.
    _scheduler.add_job(
        _guarded("learning_startup_catchup", _run_learning_startup_catchup),
        "date", run_date=datetime.now() + timedelta(minutes=5),
        id="learning_startup_catchup", misfire_grace_time=None, executor="agents",
    )
    _scheduler.add_job(
        _guarded("confidence_calibration", _run_confidence_calibration), "interval", hours=6,
        id="confidence_calibration", misfire_grace_time=300, executor="io",
    )
    _scheduler.add_job(
        _guarded("reconciliation", _run_reconciliation), "interval", minutes=2, id="reconciliation",
        misfire_grace_time=60, executor="io",
    )
    _scheduler.add_job(
        _guarded("portfolio_snapshot", _run_portfolio_snapshot), "interval", minutes=10, id="portfolio_snapshot",
        misfire_grace_time=120, executor="default",
        next_run_time=datetime.now() + timedelta(seconds=30),
    )
    _scheduler.add_job(
        _guarded("exchange_sync", _run_exchange_sync), "interval", minutes=5, id="exchange_sync",
        misfire_grace_time=60, next_run_time=datetime.now() + timedelta(seconds=10),
        executor="io",
    )
    # 3-min cadence gives momentum (30min TTL) ~10 review chances, mean_reversion
    # (60min TTL) ~20. Was 5-min (6 / 12), which combined with the change-suppression
    # TTL bug (fixed in repositories.refresh_active_snapshot_ttls) starved fast
    # families of review opportunities. Well below any SQLite/agent-executor budget.
    _scheduler.add_job(
        _guarded("paper_trader", _run_paper_trader), "interval", minutes=3, id="paper_trader",
        misfire_grace_time=60, executor="agents",
    )
    # pi_trading_decision is the ~15min safety-net fallback (agent_contracts.md row 6).
    # When the daemon is enabled it is the primary path; this scheduled job is only a
    # backstop, so it stays at ~15min. It must NOT be coupled to pi_daemon.heartbeat_sec
    # (that is the daemon's internal fallback-poll timeout, default 60s) — doing so made
    # this job fire every 1min, far more often than the contract's ~15min (A5 fix).
    _pi_fallback_min = 15
    _scheduler.add_job(
        _guarded("pi_trading_heartbeat", _run_pi_trading_decision), "interval", minutes=_pi_fallback_min,
        id="pi_trading_decision", misfire_grace_time=120,
        next_run_time=datetime.now() + timedelta(minutes=7), executor="agents",
    )
    _scheduler.add_job(
        _guarded("price_feed", _run_price_feed), "interval", seconds=30, id="price_feed",
        misfire_grace_time=15, executor="fast_io",
    )
    _scheduler.add_job(
        _guarded("connectivity_monitor", _run_connectivity_monitor), "interval",
        seconds=cfg.health.exchange_probe_interval_sec, id="connectivity_monitor",
        misfire_grace_time=15, next_run_time=datetime.now() + timedelta(seconds=10),
        executor="fast_io",
    )
    _scheduler.add_job(
        _guarded("position_manager", _run_position_manager), "interval", minutes=5, id="position_manager",
        misfire_grace_time=60, executor="io",
    )
    _scheduler.add_job(
        _guarded("position_reconcile", _run_position_reconcile), "interval", minutes=5, id="position_reconcile",
        misfire_grace_time=60, next_run_time=datetime.now() + timedelta(seconds=20),
        executor="io",
    )
    _scheduler.add_job(
        _guarded("tpsl_fill_check", _run_tpsl_fill_check), "interval", seconds=90, id="tpsl_fill_check",
        misfire_grace_time=30, next_run_time=datetime.now() + timedelta(seconds=45),
        executor="io",
    )
    _scheduler.add_job(
        _guarded("cleanup", _run_cleanup), "cron", hour=3, minute=17, id="cleanup",
        misfire_grace_time=600, executor="io", timezone="Asia/Shanghai",
    )
    # Forward catch-up for shadow trades that the event-driven resolver missed
    # (parent positions with no opportunity_id). DB-only; cheap; bounded per run.
    _scheduler.add_job(
        _guarded("shadow_resolver", _run_shadow_resolver), "interval", hours=6,
        id="shadow_resolver", misfire_grace_time=300, executor="io",
    )
    _scheduler.add_job(
        _guarded("outcome_hook_replay", _run_outcome_hook_replay),
        "interval",
        minutes=1,
        id="outcome_hook_replay",
        misfire_grace_time=60,
        max_instances=1,
        executor="io",
        next_run_time=datetime.now() + timedelta(seconds=15),
    )
    _scheduler.add_job(
        _guarded("health_check", _run_health_check), "interval", minutes=10, id="health_check",
        misfire_grace_time=120, executor="io",
    )
    # Persona rollout progression (P4 ops): every 8h, advance the dynamic-sizing
    # ladder one health-gated step (no-op unless persona_auto_advance_enabled). Durable
    # cross-session because it lives in the gateway (watchdog-backed). See persona_rollout.py.
    _scheduler.add_job(
        _guarded("persona_rollout", _run_persona_rollout), "interval", hours=8,
        id="persona_rollout", misfire_grace_time=600, executor="io",
    )
    # Filter feedback controller (Phase 1, SHADOW): every N hours, compute exact
    # filter-version pass/reject findings from cost-aware net outcomes and append
    # them to controller_recommendations. Read-only — writes NO
    # trading config. No-op unless cfg.controller.shadow_enabled. See filter_controller.py.
    if getattr(cfg, "controller", None) and cfg.controller.shadow_enabled:
        _scheduler.add_job(
            _guarded("filter_controller_observe", _run_filter_controller_observe), "interval",
            hours=max(1, int(cfg.controller.observe_interval_hours)),
            id="filter_controller_observe", misfire_grace_time=600, executor="io",
            next_run_time=datetime.now() + timedelta(minutes=2),
        )
    # News RSS feed collector — fetches enabled feeds every N minutes (default 15).
    _scheduler.add_job(
        _guarded("news_fetch", _run_news_fetch), "interval",
        minutes=cfg.news.fetch_interval_minutes, id="news_fetch",
        misfire_grace_time=120, executor="io",
        next_run_time=datetime.now() + timedelta(seconds=30),
    )
    _scheduler.add_job(
        _guarded("news_analyze", _run_news_analyze), "interval",
        minutes=5, id="news_analyze",
        misfire_grace_time=120, executor="agents",
        next_run_time=datetime.now() + timedelta(seconds=90),
    )
    # US equity observer — runs every 30 min Mon–Fri during US market hours
    # (14:30–21:00 UTC = 09:30–16:00 Eastern). Unified agent: yfinance daily bars
    # + Bitget xStocks real-time price cross-validation. Replaces stock_observer.
    # hour="14-21" so the last fire at 21:00 covers the closing auction (highest volume).
    _scheduler.add_job(
        _guarded("equity_observer", _run_equity_observer), "cron",
        day_of_week="mon-fri", hour="14-21", minute="*/30",
        id="equity_observer", misfire_grace_time=300, executor="agents", timezone="UTC",
    )
    # Prediction market observer — runs every 30 min around the clock.
    _scheduler.add_job(
        _guarded("prediction_observer", _run_prediction_observer), "interval", minutes=30,
        id="prediction_observer", misfire_grace_time=120, executor="agents",
    )
    # Post-trade reviewer — reviews closed positions every 4 hours.
    # Delay first run by 5 min so LLM-heavy jobs don't compete with market_data
    # at startup (ThreadPool=3, and LLM calls can block a slot for minutes).
    _scheduler.add_job(
        _guarded("post_trade_reviewer", _run_post_trade_reviewer), "interval", hours=4,
        id="post_trade_reviewer", misfire_grace_time=300,
        next_run_time=datetime.now() + timedelta(minutes=5), executor="agents",
    )
    # Risk critic — reviews risk decisions and writes lessons every 6 hours.
    _scheduler.add_job(
        _guarded("risk_critic", _run_risk_critic), "interval", hours=6,
        id="risk_critic", misfire_grace_time=300,
        next_run_time=datetime.now() + timedelta(minutes=10), executor="agents",
    )
    # Universe Generator — dynamic asset pool refresh.
    _scheduler.add_job(
        _guarded("universe_refresh", _run_universe_refresh), "interval", minutes=10,
        id="universe_refresh", misfire_grace_time=120, max_instances=1, executor="io",
        next_run_time=datetime.now() + timedelta(seconds=90),
    )
    # Independent outcome settler — settles overdue forward-outcome rows that feed
    # the learning pipeline.  Runs unconditionally (not gated on universe.enabled)
    # so the attribution chain keeps ticking even when the universe scan is off.
    _scheduler.add_job(
        _guarded("outcome_settler", _run_outcome_settler), "interval", minutes=10,
        id="outcome_settler", misfire_grace_time=120, max_instances=1, executor="io",
        next_run_time=datetime.now() + timedelta(seconds=120),
    )
    _scheduler.add_job(
        _guarded("paper_funding_settler", _run_paper_funding_settler),
        "interval", minutes=30,
        id="paper_funding_settler", misfire_grace_time=120, max_instances=1,
        executor="io",
        next_run_time=datetime.now() + timedelta(minutes=3),
    )
    # AT-01 evaluator is measurement-only and remains available while autonomous
    # Learning is disabled or layered pause is active.  Lifecycle control is a
    # separate autonomous job and is stopped by the shared Learning gate.
    _learning_interval = max(15, int(cfg.learning.evaluation_interval_minutes))
    _scheduler.add_job(
        _guarded("learning_contribution_evaluator", _run_learning_contribution_evaluator),
        "interval", minutes=_learning_interval, id="learning_contribution_evaluator",
        misfire_grace_time=300, max_instances=1, coalesce=True, executor="io",
        next_run_time=datetime.now() + timedelta(minutes=_learning_interval),
    )
    _scheduler.add_job(
        _guarded("learning_measurement_startup_catchup", _run_measurement_startup_catchup),
        "date", run_date=datetime.now() + timedelta(minutes=2),
        id="learning_measurement_startup_catchup", misfire_grace_time=None, executor="io",
    )
    _scheduler.add_job(
        _guarded("learning_lifecycle_controller", _run_learning_lifecycle_controller),
        "interval", minutes=_learning_interval, id="learning_lifecycle_controller",
        misfire_grace_time=300, max_instances=1, coalesce=True, executor="io",
        next_run_time=datetime.now() + timedelta(minutes=4),
    )
    # Core collector lane: primary-venue orderbook/funding/OI every two minutes.
    # Cross-venue and macro context has a separate job below so it cannot inherit
    # a consumed primary-venue deadline.
    _scheduler.add_job(
        _guarded("data_collectors", _run_data_collectors), "interval", minutes=2,
        id="data_collectors", misfire_grace_time=60,
        next_run_time=datetime.now() + timedelta(seconds=30), executor="io",
    )
    _scheduler.add_job(
        _guarded("data_collector_context", _run_data_collector_context),
        "interval", minutes=10, id="data_collector_context", misfire_grace_time=60,
        next_run_time=datetime.now() + timedelta(seconds=75), executor="io",
    )
    # Guard promotion — deterministic learning task (candidate guards → active).
    # Re-homed from the deleted backtest_orchestrator cycle (which used to drive it
    # via lifecycle_automation). Same daily 04:00 CST cadence, gated by runtime.paused.
    _scheduler.add_job(
        _guarded("guard_promotion", _run_guard_promotion), "cron", hour=4, minute=0,
        id="guard_promotion", misfire_grace_time=600, executor="io", timezone="Asia/Shanghai",
    )
    # Nightly OHLCV backfill — bulk-download history for universe + observed symbols.
    if cfg.historical_data.nightly_backfill_enabled:
        _scheduler.add_job(
            _guarded("ohlcv_nightly_backfill", _run_ohlcv_nightly_backfill),
            "cron", hour=cfg.historical_data.nightly_backfill_hour_cst, minute=30,
            id="ohlcv_nightly_backfill", misfire_grace_time=600,
            executor="io", timezone="Asia/Shanghai",
        )
    if (
        getattr(cfg.historical_data, "enabled", True)
        and getattr(cfg.historical_data, "active_universe_backfill_enabled", True)
    ):
        _scheduler.add_job(
            _guarded("ohlcv_active_backfill", _run_active_ohlcv_backfill),
            "interval",
            minutes=getattr(
                cfg.historical_data, "active_universe_backfill_interval_minutes", 10
            ),
            id="ohlcv_active_backfill",
            misfire_grace_time=120,
            next_run_time=datetime.now() + timedelta(minutes=3),
            executor="io",
        )
    # OKX demo symbol list refresh — keeps the O(1) filter cache warm.
    _scheduler.add_job(
        _guarded("okx_demo_symbols", _run_okx_demo_symbols), "interval", hours=4,
        id="okx_demo_symbols", misfire_grace_time=600, executor="io",
        next_run_time=datetime.now() + timedelta(seconds=45),
    )
    # Daily PnL summary pushed to Telegram (configurable hour, CST/UTC+8).
    _summary_hour = cfg.telegram.daily_summary_hour_cst if hasattr(cfg.telegram, 'daily_summary_hour_cst') else 8
    _scheduler.add_job(
        _guarded("daily_summary", _run_daily_summary), "cron", hour=_summary_hour, minute=0,
        id="daily_summary", misfire_grace_time=600, executor="io", timezone="Asia/Shanghai",
    )

    # PI Operator Loop — system governance assessment + auto-actions every 4h.
    _scheduler.add_job(
        _guarded("pi_operator_loop", _run_operator_loop), "cron",
        hour="0,4,8,12,16,20", minute=15,
        id="pi_operator_loop", misfire_grace_time=3600, executor="agents",
        next_run_time=datetime.now() + timedelta(minutes=2),
        timezone="Asia/Shanghai",
    )

    _scheduler.start()
    # If config says paused at boot, apply pause (layered or blanket).
    if cfg.runtime.paused:
        if cfg.health.layered_pause_enabled:
            for job in _scheduler.get_jobs():
                if job.id not in _ESSENTIAL_JOBS:
                    _scheduler.pause_job(job.id)
                    _selectively_paused_jobs.add(job.id)
            logger.info(
                "Scheduler started LAYERED-PAUSED (runtime.paused=true) with %d jobs "
                "(%d essential running, %d paused)",
                len(_scheduler.get_jobs()),
                len([j for j in _scheduler.get_jobs() if j.id in _ESSENTIAL_JOBS]),
                len(_selectively_paused_jobs),
            )
        else:
            _scheduler.pause()
            logger.info("Scheduler started PAUSED (runtime.paused=true) with %d jobs", len(_scheduler.get_jobs()))
    else:
        logger.info("Scheduler started with %d jobs", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
    _async_timeout_worker.close()
    _news_analyzer_timeout_worker.close()


def pause_scheduler(layered: bool | None = None) -> None:
    """Pause job execution.

    When layered=True (or config layered_pause_enabled and layered is None),
    only non-essential jobs are paused — data collection continues.
    When layered=False, all jobs are paused (legacy blanket behavior).
    """
    if not _scheduler:
        return

    if layered is None:
        cfg = load_config()
        layered = cfg.health.layered_pause_enabled

    if not layered:
        _scheduler.pause()
        logger.info("Scheduler paused (blanket)")
        return

    # Selective pause: only pause non-essential jobs
    _selectively_paused_jobs.clear()
    for job in _scheduler.get_jobs():
        if job.id not in _ESSENTIAL_JOBS:
            _scheduler.pause_job(job.id)
            _selectively_paused_jobs.add(job.id)
    logger.info(
        "Scheduler layered-pause: %d jobs paused, %d essential jobs continue",
        len(_selectively_paused_jobs),
        len([j for j in _scheduler.get_jobs() if j.id in _ESSENTIAL_JOBS]),
    )


def resume_scheduler() -> None:
    """Resume all paused jobs (whether blanket or selectively paused)."""
    global _scheduler
    if _scheduler is None:
        init_scheduler()
        if _scheduler is None:
            logger.warning("resume_scheduler: scheduler unavailable (disabled or init failed)")
        return
    # If blanket-paused, resume the whole scheduler
    if _scheduler.state == STATE_PAUSED:
        _scheduler.resume()
        logger.info("Scheduler resumed (from blanket pause)")
    # Also resume any selectively-paused jobs
    if _selectively_paused_jobs:
        for job_id in list(_selectively_paused_jobs):
            try:
                _scheduler.resume_job(job_id)
            except Exception:
                pass
        logger.info("Scheduler resumed %d selectively-paused jobs", len(_selectively_paused_jobs))
        _selectively_paused_jobs.clear()


def get_pause_state() -> dict:
    """Return current pause state for API/dashboard consumers."""
    if _scheduler is None:
        return {"scheduler_active": False, "mode": "stopped"}
    if _scheduler.state == STATE_PAUSED:
        return {"scheduler_active": True, "mode": "blanket_paused", "paused_jobs": "all"}
    if _selectively_paused_jobs:
        return {
            "scheduler_active": True,
            "mode": "layered_paused",
            "paused_jobs": sorted(_selectively_paused_jobs),
            "running_jobs": sorted(
                j.id for j in _scheduler.get_jobs() if j.id in _ESSENTIAL_JOBS
            ),
        }
    return {"scheduler_active": True, "mode": "running"}


_PAPER_TRADER_NUDGE_DEBOUNCE_SEC = 3.0
_PAPER_TRADER_NUDGE_DELAY_SEC = 0.5
_PAPER_TRADER_WAKE_STATE: dict[str, object] = {
    "last_nudge_mono": 0.0,
    "last_requested_at": None,
    "pending_sources": set(),
    "burst_count": 0,
    "last_dispatched_at": None,
    "last_completed_at": None,
    "last_result": None,
}


def _reset_paper_trader_wake_state_for_tests() -> None:
    """Reset bounded process-local wake telemetry without touching scheduler state."""
    with _SCHEDULER_JOB_LOCK:
        _PAPER_TRADER_WAKE_STATE.update({
            "last_nudge_mono": 0.0,
            "last_requested_at": None,
            "pending_sources": set(),
            "burst_count": 0,
            "last_dispatched_at": None,
            "last_completed_at": None,
            "last_result": None,
        })


def _paper_trader_job_next_run_at() -> str | None:
    if _scheduler is None or not _scheduler.running:
        return None
    try:
        job = _scheduler.get_job("paper_trader")
        value = getattr(job, "next_run_time", None) if job else None
        return value.isoformat() if value else None
    except Exception:
        return None


def paper_trader_wake_status() -> dict[str, object]:
    """Return sanitized Ranker wake state for Overview/CLI diagnostics."""
    with _SCHEDULER_JOB_LOCK:
        sources = sorted(str(value) for value in _PAPER_TRADER_WAKE_STATE["pending_sources"])
        return {
            "scheduler_active": bool(_scheduler is not None and _scheduler.running),
            "pending": bool(sources),
            "pending_sources": sources,
            "burst_count": int(_PAPER_TRADER_WAKE_STATE["burst_count"]),
            "last_requested_at": _PAPER_TRADER_WAKE_STATE["last_requested_at"],
            "last_dispatched_at": _PAPER_TRADER_WAKE_STATE["last_dispatched_at"],
            "last_completed_at": _PAPER_TRADER_WAKE_STATE["last_completed_at"],
            "last_result": _PAPER_TRADER_WAKE_STATE["last_result"],
            "next_run_at": _paper_trader_job_next_run_at(),
        }


def _consume_paper_trader_wake() -> list[str]:
    with _SCHEDULER_JOB_LOCK:
        sources = sorted(str(value) for value in _PAPER_TRADER_WAKE_STATE["pending_sources"])
        _PAPER_TRADER_WAKE_STATE["pending_sources"] = set()
        if sources:
            _PAPER_TRADER_WAKE_STATE["last_dispatched_at"] = datetime.now(timezone.utc).isoformat()
        return sources


def _record_paper_trader_wake_result(result: object, *, error: str | None = None) -> None:
    summary: dict[str, object]
    if isinstance(result, dict):
        summary = {
            "run_id": result.get("run_id"),
            "status": result.get("status"),
            "outputs": list(result.get("outputs") or [])[:20],
            "errors": list(result.get("errors") or [])[:10],
        }
    else:
        summary = {"run_id": None, "status": "failed" if error else "unknown", "outputs": [], "errors": []}
    if error:
        summary["errors"] = [error]
    with _SCHEDULER_JOB_LOCK:
        _PAPER_TRADER_WAKE_STATE["last_completed_at"] = datetime.now(timezone.utc).isoformat()
        _PAPER_TRADER_WAKE_STATE["last_result"] = summary


def _arm_paper_trader_job() -> bool:
    if _scheduler is None or not _scheduler.running:
        return False
    try:
        job = _scheduler.get_job("paper_trader")
        if job is None:
            return False
        _scheduler.modify_job(
            "paper_trader",
            next_run_time=datetime.now() + timedelta(seconds=_PAPER_TRADER_NUDGE_DELAY_SEC),
        )
        return True
    except Exception:
        return False


def notify_paper_trader(source: str = "unknown") -> bool:
    """Pull paper_trader's next fire time forward to now — inline event trigger.

    Fire-and-forget: safe to call from any thread, never blocks, never raises.
    APScheduler's max_instances=1 gate handles overlap: if paper_trader is
    already running, the modify is a no-op and the current run absorbs the
    pending work. If not running, it fires within ~1s.

    Debounced: a burst of notifications within _PAPER_TRADER_NUDGE_DEBOUNCE_SEC
    collapses into a single wake so a universe refresh emitting N opportunities
    in tight succession does not thrash the modify path.

    Guards: skips silently when the scheduler is down, paused, or the job is
    absent. Layered-pause / kill-switch state is respected via the same executor
    the regular tick uses — no bypass.

    Returns True if a modify was issued, False otherwise. The boolean is
    diagnostic; the caller should not treat False as a failure.
    """
    if _scheduler is None or not _scheduler.running:
        return False
    now_mono = time.monotonic()
    with _SCHEDULER_JOB_LOCK:
        try:
            job = _scheduler.get_job("paper_trader")
        except Exception:
            return False
        if job is None:
            return False
        pending_sources = _PAPER_TRADER_WAKE_STATE["pending_sources"]
        assert isinstance(pending_sources, set)
        pending_sources.add(str(source or "unknown")[:120])
        _PAPER_TRADER_WAKE_STATE["burst_count"] = int(
            _PAPER_TRADER_WAKE_STATE["burst_count"]
        ) + 1
        _PAPER_TRADER_WAKE_STATE["last_requested_at"] = datetime.now(timezone.utc).isoformat()
        last_nudge = float(_PAPER_TRADER_WAKE_STATE["last_nudge_mono"])
        if now_mono - last_nudge < _PAPER_TRADER_NUDGE_DEBOUNCE_SEC:
            return False
        if not _arm_paper_trader_job():
            return False
        _PAPER_TRADER_WAKE_STATE["last_nudge_mono"] = now_mono
        logger.debug("paper_trader: nudged forward (source=%s)", source)
        return True


def reschedule_universe_refresh(interval_min: int) -> bool:
    """Apply a new universe_refresh interval to the live scheduler.

    APScheduler's IntervalTrigger is immutable once added, so changing
    ``universe.refresh_interval_min`` in config does nothing until the next
    gateway restart unless the job is explicitly rescheduled. Called from the
    settings route after a config save so the UI takes effect immediately.

    Returns True if the live job was rescheduled, False if there was no running
    scheduler/job to update (the new interval still applies at next boot via
    config). Never raises into the request path.
    """
    if _scheduler is None:
        return False
    try:
        with _SCHEDULER_JOB_LOCK:
            if _scheduler.get_job("universe_refresh") is None:
                return False
            _scheduler.reschedule_job(
                "universe_refresh", trigger="interval", minutes=interval_min,
            )
        logger.info("Rescheduled universe_refresh to every %d min", interval_min)
        return True
    except Exception as e:  # noqa: BLE001 — config is already saved; never 500 the save
        logger.warning("Failed to reschedule universe_refresh (applies at next restart): %s", e)
        return False


def is_running() -> bool:
    return _scheduler is not None and _scheduler.running


def status_snapshot() -> dict:
    """Return an honest, read-only snapshot of the in-process scheduler.

    `runtime.scheduler_enabled` is only the operator's desired configuration.
    This snapshot reports the actual APScheduler object state so the dashboard
    does not show "active" when init failed or the scheduler is paused.
    """
    if _scheduler is None:
        return {"running": False, "state": "not_started", "job_count": 0}
    state = getattr(_scheduler, "state", None)
    if state == STATE_RUNNING:
        label = "running"
    elif state == STATE_PAUSED:
        label = "paused"
    elif state == STATE_STOPPED:
        label = "stopped"
    else:
        label = f"unknown:{state}"
    try:
        job_count = len(_scheduler.get_jobs())
    except Exception:
        job_count = 0
    return {
        "running": state == STATE_RUNNING,
        "state": label,
        "job_count": job_count,
    }


def _run_price_feed():
    try:
        from aletheia.data.price_feed import refresh_watched_prices
        result = _run_async_with_timeout("price_feed", refresh_watched_prices())
        if isinstance(result, dict) and result.get("errors"):
            logger.warning(
                "price_feed completed with %d reported errors",
                len(result["errors"]),
            )
            _record_job_failure("price_feed", "price_feed_reported_errors")
    except TimeoutError:
        logger.debug("price_feed job timed out")
        _record_job_failure("price_feed", "timeout")
    except Exception as e:
        logger.debug("price_feed job failed: %s", e)
        _record_job_failure("price_feed", str(e))


def _run_connectivity_monitor():
    try:
        from aletheia.agent_runtime.connectivity import run_connectivity_check
        _run_async_with_timeout("connectivity_monitor", run_connectivity_check())
    except TimeoutError:
        logger.debug("connectivity_monitor job timed out")
        _record_job_failure("connectivity_monitor", "timeout")
    except Exception as e:
        logger.debug("connectivity_monitor job failed: %s", e)
        _record_job_failure("connectivity_monitor", str(e))


def _run_daily_summary():
    try:
        from aletheia.notifications import notify_daily_summary
        notify_daily_summary()
    except Exception as e:
        logger.warning("daily_summary job failed: %s", e)
        _record_job_failure("daily_summary", str(e))


def _run_market_data():
    try:
        from aletheia.trading.market_data import collect_market_snapshots
        result = _run_async_with_timeout("market_data", collect_market_snapshots())
        if isinstance(result, dict) and result.get("errors"):
            logger.warning(
                "market_data completed with %d reported errors",
                len(result["errors"]),
            )
            _record_job_failure("market_data", "market_data_reported_errors")
    except TimeoutError:
        logger.warning(
            "market_data job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["market_data"],
        )
        _record_job_failure("market_data", "timeout")
    except Exception as e:
        logger.warning("market_data job failed: %s", e)
        _record_job_failure("market_data", str(e))


def _run_market_observer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    # Retry once on database-is-locked — the WAL contention window is short
    # (exchange_sync / data_collectors / uvicorn handlers), so a brief wait
    # usually clears it without waiting a full 15-min cycle.
    for attempt in range(2):
        try:
            _run_async_with_timeout("market_observer", _async_run("market_observer"))
            return
        except TimeoutError:
            logger.warning(
                "market_observer job timed out after %.0fs",
                _ASYNC_JOB_TIMEOUT_SEC["market_observer"],
            )
            _record_job_failure("market_observer", "timeout")
            return
        except Exception as e:
            if "database is locked" in str(e) and attempt == 0:
                import time as _time
                logger.warning("market_observer: database locked, retrying in 10s…")
                _time.sleep(10)
                continue
            logger.warning("market_observer job failed: %s", e)
            _record_job_failure("market_observer", str(e))
            return


def _run_dreaming_agent():
    cfg = load_config()
    if cfg.runtime.paused:
        return

    # Step 1: Serenity research pipeline (evidence collection, theme clustering, opps).
    # The durable CST batch key makes the daily cron and startup catch-up converge
    # on one attempt instead of duplicating research after every gateway restart.
    if cfg.serenity.enabled:
        try:
            result = _enqueue_serenity_research_if_due()
            logger.info("dreaming_agent: Serenity research %s", result["status"])
        except Exception as e:
            logger.warning("dreaming_agent: serenity research enqueue failed: %s", e)
            _record_job_failure("dreaming_daily", str(e))
    else:
        logger.debug("dreaming_agent: Serenity research skipped (disabled)")

    # Step 2: Lesson extraction — mines actionable lessons from recent trade case studies
    # and writes them to ps_lessons (proposed). lesson_reviewer at 03:00 then reviews
    # and marks them applied. This step was previously only reachable via fallback
    # (断口U6 fix: run it explicitly, independent of the serenity pipeline).
    try:
        from aletheia.agents.dreaming_agent import _extract_lessons
        outputs: list[str] = []
        errors: list[str] = []
        _run_async_with_timeout(
            "dreaming_agent",  # reuse existing timeout (300s)
            _extract_lessons("dreaming_daily_sched", outputs, errors),
        )
        if outputs:
            logger.info("dreaming_agent: lesson extraction outputs: %s", outputs)
        if errors:
            logger.warning("dreaming_agent: lesson extraction errors: %s", errors)
    except TimeoutError:
        logger.warning("dreaming_agent: lesson extraction timed out")
        _record_job_failure("dreaming_daily", "lesson extraction timed out")
    except Exception as e:
        logger.warning("dreaming_agent: lesson extraction failed: %s", e)
        _record_job_failure("dreaming_daily", str(e))


def _run_lesson_reviewer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        _run_async_with_timeout("lesson_reviewer", _async_run("lesson_reviewer"))
    except TimeoutError:
        logger.warning(
            "lesson_reviewer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["lesson_reviewer"],
        )
        _record_job_failure("lesson_reviewer", "timeout")
    except Exception as e:
        logger.warning("lesson_reviewer job failed: %s", e)
        _record_job_failure("lesson_reviewer", str(e))


def _run_shadow_resolver():
    """Forward catch-up for shadow trades whose parent never linked an opportunity_id.

    The event-driven resolver only fires at parent-position close and only when the
    position carries an opportunity_id; this periodic sweep resolves anything that
    slipped through, using each shadow's own 24h forward horizon (historical OHLCV).
    """
    try:
        from aletheia.analytics.shadow_trades import resolve_stale_shadows
        counts = resolve_stale_shadows()
        if counts.get("resolved"):
            logger.info(
                "shadow_resolver: resolved=%d skipped_no_ohlcv=%d skipped_bad_entry=%d",
                counts["resolved"], counts["skipped_no_ohlcv"], counts["skipped_bad_entry"],
            )
    except Exception as e:
        logger.warning("shadow_resolver job failed: %s", e)
        _record_job_failure("shadow_resolver", str(e))


def _run_outcome_hook_replay():
    """Replay committed Outcome hook intents even while decision jobs are paused."""
    try:
        from aletheia.portfolio.outcome_hooks import process_outcome_hook_jobs

        summary = process_outcome_hook_jobs(limit=50)
        if summary["failed"]:
            message = f"{summary['failed']} Outcome hook deliveries remain retryable"
            logger.warning("outcome_hook_replay: %s", message)
            _record_job_failure("outcome_hook_replay", message)
        elif summary["completed"] or summary["skipped"]:
            logger.info(
                "outcome_hook_replay: completed=%d skipped=%d",
                summary["completed"],
                summary["skipped"],
            )
    except Exception as e:
        logger.warning("outcome_hook_replay job failed: %s", e)
        _record_job_failure("outcome_hook_replay", str(e))


def _run_reconciliation():
    try:
        from aletheia.execution.reconciliation import reconcile
        summary = reconcile()
        if isinstance(summary, dict) and summary.get("errors"):
            logger.warning(
                "reconciliation completed with %d reported errors",
                len(summary["errors"]),
            )
            _record_job_failure(
                "reconciliation", "reconciliation_reported_errors"
            )
    except Exception as e:
        logger.warning("reconciliation job failed: %s", e)
        _record_job_failure("reconciliation", str(e))


def _run_exchange_sync():
    """Periodic refresh of the cached real-account view (read-only)."""
    try:
        from aletheia.portfolio.exchange_sync import sync_all_exchanges
        result = _run_sync_with_timeout("exchange_sync", sync_all_exchanges)
        if result is None:
            logger.warning("exchange_sync returned no unified state")
            _record_job_failure("exchange_sync", "exchange_sync_total_failure")
            return
        if isinstance(result, dict) and result.get("errors"):
            logger.warning(
                "exchange_sync completed with %d reported account errors",
                len(result["errors"]),
            )
            _record_job_failure("exchange_sync", "exchange_sync_reported_errors")
            return
        _record_job_success("exchange_sync")
    except TimeoutError:
        logger.warning(
            "exchange_sync job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["exchange_sync"],
        )
        _record_job_failure("exchange_sync", "timed out")
    except Exception as e:
        logger.warning("exchange_sync job failed: %s", e)
        _record_job_failure("exchange_sync", str(e))


def _run_portfolio_snapshot():
    cfg = load_config()
    try:
        from aletheia.portfolio.pnl import compute_and_store_snapshot
        _live_mode_map = {"bitget": "bitget_live_limited", "okx": "okx_live_limited", "binance": "binance_live_limited"}
        _mode = _live_mode_map.get(cfg.exchange.name, "bitget_live_limited") if cfg.exchange.mode == "live" else cfg.exchange.paper_execution_mode
        _run_sync_with_timeout("portfolio_snapshot", lambda: compute_and_store_snapshot(execution_mode=_mode))
        _record_job_success("portfolio_snapshot")
    except TimeoutError:
        logger.warning(
            "portfolio_snapshot job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["portfolio_snapshot"],
        )
        _record_job_failure("portfolio_snapshot", "timed out")
    except Exception as e:
        logger.warning("portfolio_snapshot job failed: %s", e)
        _record_job_failure("portfolio_snapshot", str(e))


def _run_paper_trader():
    cfg = load_config()
    runtime = getattr(cfg, "runtime", None)
    if getattr(runtime, "paused", False):
        return
    if getattr(runtime, "kill_switch_active", False):
        logger.info("paper_trader: kill_switch active, skipping scheduler trigger")
        return
    if cfg.agent.permission_mode not in ("Paper", "Full"):
        return
    wake_sources = _consume_paper_trader_wake()
    trigger_type = (
        f"on_demand:{','.join(wake_sources)}"[:240]
        if wake_sources
        else "scheduled"
    )
    result = None
    try:
        result = _run_async_with_timeout(
            "paper_trader",
            _async_run("paper_trader", trigger_type=trigger_type),
        )
        _record_paper_trader_wake_result(result)
    except TimeoutError:
        logger.warning(
            "paper_trader job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["paper_trader"],
        )
        _record_paper_trader_wake_result(result, error="timeout")
        _record_job_failure("paper_trader", "timeout")
    except Exception as e:
        logger.warning("paper_trader job failed: %s", e)
        _record_paper_trader_wake_result(result, error=type(e).__name__)
        _record_job_failure("paper_trader", str(e))
    finally:
        # Notifications arriving while a run is in flight are not lost.  The
        # current run cannot see facts committed after its snapshot, so re-arm
        # one coalesced follow-up instead of waiting for the 3-minute heartbeat.
        if paper_trader_wake_status()["pending"]:
            with _SCHEDULER_JOB_LOCK:
                _PAPER_TRADER_WAKE_STATE["last_nudge_mono"] = 0.0
                _arm_paper_trader_job()


def _run_pi_trading_decision():
    """System Operator heartbeat — notifies the event-driven daemon or runs inline as fallback.

    When pi_daemon is enabled and running, this job simply wakes the daemon
    (which handles debounce, cooldown, and the actual LLM evaluation). The daemon
    provides sub-second response to signal events; this job is the 15-min safety net.

    When pi_daemon is disabled or not running, runs the evaluation inline (legacy path).
    """
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if cfg.agent.permission_mode not in ("Paper", "Full"):
        return

    # Daemon path: delegate to the event-driven loop
    try:
        from aletheia.agent_runtime.pi_daemon import is_daemon_running, notify_pi_daemon
        if cfg.pi_daemon.enabled and is_daemon_running():
            if notify_pi_daemon(source="heartbeat"):
                return
            _record_job_failure("pi_trading_heartbeat", "daemon_notify_failed")
    except Exception:
        pass

    # Legacy fallback: daemon disabled or not started — run inline
    try:
        from aletheia.agent_shell.commands import evaluate_trading_opportunity
        result = _run_async_with_timeout("pi_trading_decision", evaluate_trading_opportunity({}))
        action = result.get("action", "skip")
        if action in ("trade", "scale_in", "flip"):
            logger.info(
                "pi_trading_decision: %s %s %s conviction=%s proposal=%s",
                action, result.get("direction"), result.get("symbol"),
                result.get("conviction"), result.get("proposal_id"),
            )
        elif action == "watch":
            logger.info("pi_trading_decision: watch %s — %s", result.get("symbol"), result.get("reasoning", "")[:60])
        else:
            logger.debug("pi_trading_decision: %s — %s", action, result.get("reasoning", "")[:80])
        _record_job_success("pi_trading_decision")
    except TimeoutError:
        logger.warning("pi_trading_decision job timed out after %.0fs", _ASYNC_JOB_TIMEOUT_SEC.get("pi_trading_decision", 300))
        _record_job_failure("pi_trading_decision", "timeout")
    except Exception as e:
        logger.warning("pi_trading_decision job failed: %s", e)
        _record_job_failure("pi_trading_decision", str(e))


def _run_operator_loop():
    """PI Operator Loop — system-level assessment + auto-actions (4h cadence)."""
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if not cfg.operator.loop_enabled:
        return
    if cfg.agent.permission_mode not in ("Paper", "Full"):
        return
    if not _operator_assessment_due():
        logger.debug("pi_operator_loop: skipped — recent assessment is still current")
        return
    try:
        from aletheia.agent_shell.operator_loop import run_operator_loop
        summary = _run_sync_with_timeout("pi_operator_loop", lambda: run_operator_loop(trigger="scheduled"))
        if not summary:
            logger.debug("pi_operator_loop: no summary returned")
            return
        status = summary.get("status", "") if summary else ""
        if status == "skipped":
            logger.debug("pi_operator_loop: skipped — %s", summary.get("reason"))
        else:
            logger.info(
                "pi_operator_loop: %d actions decided, %d executed, %d proposed",
                summary.get("actions_decided", 0),
                summary.get("actions_executed", 0),
                summary.get("actions_proposed", 0),
            )
    except TimeoutError:
        logger.warning("pi_operator_loop timed out after %.0fs", _ASYNC_JOB_TIMEOUT_SEC.get("pi_operator_loop", 120))
        _record_job_failure("pi_operator_loop", "timeout")
    except Exception as e:
        logger.warning("pi_operator_loop job failed: %s", e)
        _record_job_failure("pi_operator_loop", str(e))


def _run_position_manager():
    # NO paused gate (intentional): position_manager only MANAGES/CLOSES existing
    # positions (stop-loss / take-profit / trailing / max-hold) — it never opens new
    # risk. `paused` stops OPENING new risk; it must never freeze protective exits,
    # exactly like kill_switch (which also never blocks closes). Re-introducing
    # `if cfg.runtime.paused: return` here is a safety regression.
    # Invariant: execution_contract §10 + spec PL-PM-07.
    try:
        from aletheia.portfolio.position_manager import evaluate_open_positions
        summary = evaluate_open_positions()
        if isinstance(summary, dict) and summary.get("errors"):
            logger.warning(
                "position_manager completed with %d reported errors",
                len(summary["errors"]),
            )
            _record_job_failure(
                "position_manager", "position_manager_reported_errors"
            )
    except Exception as e:
        logger.warning("position_manager job failed: %s", e)
        _record_job_failure("position_manager", str(e))


def _run_position_reconcile():
    """Periodic position reconciliation: adopt externals + close orphans (demo)."""
    cfg = load_config()
    # NO paused gate (intentional): reconciliation closes demo orphan positions
    # (protective) and records externally-opened positions (bookkeeping — opens no
    # new risk). Like position_manager it manages existing state and must run even
    # when paused. Invariant: execution_contract §10 (aligned with kill_switch).
    try:
        from aletheia.agent_runtime.run_logger import start_run, end_run
        from aletheia.portfolio.position_reconciler import run_reconcile

        run = start_run("position_reconciler", "scheduled", cfg.agent.permission_mode)
        try:
            summary = run_reconcile()
            outputs = []
            if summary["adopted"]:
                outputs.append(f"adopted {len(summary['adopted'])} positions")
            if summary["closed_orphans"]:
                outputs.append(f"closed {len(summary['closed_orphans'])} orphans")
            if not outputs:
                outputs.append("no action needed")
            # #144: reflect per-item errors in the run status so health's failure-ratio
            # sees a systematically-failing reconciler (matches the orchestrator convention).
            _recon_status = "completed_with_errors" if summary.get("errors") else "completed"
            end_run(run, status=_recon_status, outputs=outputs, errors=summary.get("errors") or None)
            if summary.get("errors"):
                _record_job_failure(
                    "position_reconcile", "position_reconcile_reported_errors"
                )
        except Exception as e:
            end_run(run, status="failed", errors=[str(e)])
            raise
    except Exception as e:
        logger.warning("position_reconcile job failed: %s", e)
        _record_job_failure("position_reconcile", str(e))


def _run_tpsl_fill_check():
    """Bridge triggered Bitget TPSL plans into private-fill reconciliation.

    #90 fix: the body is wrapped in `_run_sync_with_timeout` (45s cap; see
    _ASYNC_JOB_TIMEOUT_SEC['tpsl_fill_check']) so a stuck Bitget session can't
    hold the io executor slot indefinitely. The inner asyncio.run() blocks the
    calling thread; the timeout wrapper is what enforces the wall-clock bound.
    The outer try/except keeps job-wrapper contract intact (spec_agent_contracts
    test_scheduler_job_wrappers_have_try_except).
    """
    try:
        summary = _run_sync_with_timeout(
            "tpsl_fill_check", _tpsl_fill_check_body
        )
        if isinstance(summary, dict) and summary.get("failed_count"):
            _record_job_failure(
                "tpsl_fill_check", "tpsl_private_reconciliation_failed"
            )
        return summary
    except Exception as e:
        logger.warning("tpsl_fill_check outer wrapper failed: %s", e)
        _record_job_failure("tpsl_fill_check", str(e))
        return None


def _claim_observed_tpsl_close(
    position_id: str,
    *,
    execute_order_id: str,
    reason: str,
):
    """Atomically register an observed Bitget TPSL execution without placing it."""
    from datetime import datetime, timezone
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Order

    execute_order_id = str(execute_order_id or "").strip()
    if not execute_order_id:
        raise ValueError("TPSL execute order identity unavailable")
    with repo.transaction(immediate=True):
        position = repo._select_one("positions", position_id)
        if position is None or position.get("status") != "open":
            return None, False
        execution_mode = position.get("execution_mode")
        if execution_mode not in {"bitget_demo", "bitget_live_limited"}:
            raise ValueError(
                "automatic TPSL close registration requires a supported Bitget mode"
            )
        pending_id = position.get("pending_close_order_id")
        if pending_id:
            existing = repo.get_order(pending_id)
            if (
                existing is not None
                and existing.get("exchange_order_id") == execute_order_id
                and existing.get("position_id") == position_id
            ):
                return existing, False
            raise RuntimeError("TPSL execute order conflicts with pending close claim")

        close_order = Order(
            proposal_id=position.get("proposal_id"),
            client_order_id=f"close-{position_id}",
            position_id=position_id,
            close_reason=reason,
            symbol=position["symbol"],
            market_type=position["market_type"],
            direction=position["direction"],
            order_type="market",
            quantity=float(position["quantity"]),
            price=None,
            execution_mode=execution_mode,
            exchange_order_id=execute_order_id,
            status="materialization_missing",
            error_reason="private_fill_evidence_pending",
        )
        repo.insert_order(close_order)
        repo.update_position(
            position_id,
            pending_close_order_id=close_order.id,
            pending_close_status="materialization_missing",
            pending_close_requested_at=datetime.now(timezone.utc).isoformat(),
        )
        return repo.get_order(close_order.id), True


def _tpsl_fill_check_body():
    """Inner body of tpsl_fill_check — kept separate so _run_sync_with_timeout
    can wrap it. The outer entry point (`_run_tpsl_fill_check`) is what the
    scheduler dispatches; the body stays a plain sync function and returns an
    honest partial-work summary for caller-thread health accounting."""
    try:
        import asyncio
        import json as _json
        from aletheia.storage.repositories import list_all_open_positions
        from aletheia.data.symbols import to_bitget
        from aletheia.execution.exchange_materialization import (
            materialize_bitget_close,
        )
        from aletheia.exchanges.bitget.client import BitgetClient

        positions = list_all_open_positions()
        candidates = []
        for p in positions:
            tpsl_raw = p.get("exchange_tpsl_order_id")
            if not tpsl_raw:
                continue
            try:
                tpsl_ids = _json.loads(tpsl_raw) if isinstance(tpsl_raw, str) else tpsl_raw
            except (ValueError, TypeError):
                continue
            if (
                p.get("execution_mode") in {"bitget_demo", "bitget_live_limited"}
                and isinstance(tpsl_ids, dict)
                and (tpsl_ids.get("sl") or tpsl_ids.get("tp"))
            ):
                candidates.append((p, tpsl_ids))

        if not candidates:
            return {"materialized": [], "failed_count": 0, "errors": []}

        grouped = {
            "bitget_demo": [item for item in candidates if item[0]["execution_mode"] == "bitget_demo"],
            "bitget_live_limited": [
                item for item in candidates
                if item[0]["execution_mode"] == "bitget_live_limited"
            ],
        }

        async def _check_account(execution_mode, account_candidates):
            if not account_candidates:
                return {"materialized": [], "errors": []}
            account_mode = "demo" if execution_mode == "bitget_demo" else "live"
            client = BitgetClient(mode=account_mode)
            materialized = []
            errors = []
            try:
                for pos, tpsl_ids in account_candidates:
                    for plan_type, plan_order_id in (
                        ("sl", tpsl_ids.get("sl")),
                        ("tp", tpsl_ids.get("tp")),
                    ):
                        if not plan_order_id:
                            continue
                        try:
                            history = await client.get_plan_order_history(
                                order_id=plan_order_id
                            )
                            execute_ids = {
                                str(row.get("executeOrderId") or "").strip()
                                for row in history
                                if isinstance(row, dict)
                                and str(row.get("orderId") or "")
                                == str(plan_order_id)
                                and str(row.get("planStatus") or "").lower()
                                == "executed"
                                and str(row.get("executeOrderId") or "").strip()
                            }
                            if len(execute_ids) != 1:
                                continue
                            execute_order_id = next(iter(execute_ids))
                            reason = (
                                "stop_loss" if plan_type == "sl" else "take_profit"
                            )
                            close_order, _ = _claim_observed_tpsl_close(
                                pos["id"],
                                execute_order_id=execute_order_id,
                                reason=reason,
                            )
                            if close_order is None:
                                continue
                            private_fills = await client.get_order_fills(
                                order_id=execute_order_id,
                                symbol=to_bitget(pos["symbol"]),
                                market_type=pos["market_type"],
                            )
                            result = materialize_bitget_close(
                                close_order["id"], private_fills
                            )
                            if result.state == "filled":
                                materialized.append(result)
                        except Exception as exc:
                            errors.append({
                                "execution_mode": execution_mode,
                                "position_id": pos.get("id"),
                                "plan_order_id": str(plan_order_id),
                                "error_type": type(exc).__name__,
                            })
                            logger.warning(
                                "TPSL private reconciliation failed for plan %s (%s)",
                                plan_order_id,
                                type(exc).__name__,
                            )
                return {"materialized": materialized, "errors": errors}
            finally:
                try:
                    await client.close()
                except Exception as exc:
                    logger.warning(
                        "TPSL account client close failed (%s)", type(exc).__name__
                    )

        async def _check_all():
            materialized = []
            errors = []
            for execution_mode, account_candidates in grouped.items():
                try:
                    account_result = await _check_account(
                        execution_mode, account_candidates
                    )
                    materialized.extend(account_result["materialized"])
                    errors.extend(account_result["errors"])
                except Exception as exc:
                    errors.append({
                        "execution_mode": execution_mode,
                        "position_id": None,
                        "plan_order_id": None,
                        "error_type": type(exc).__name__,
                    })
                    logger.warning(
                        "TPSL account reconciliation failed for %s (%s)",
                        execution_mode,
                        type(exc).__name__,
                    )
            return {
                "materialized": materialized,
                "failed_count": len(errors),
                "errors": errors,
            }

        return asyncio.run(_check_all())
    except Exception as e:
        logger.warning("tpsl_fill_check job failed: %s", e)
        raise


def _run_equity_observer():
    """Run equity_observer during US market hours (scheduler cron already filters)."""
    cfg = load_config()
    if cfg.runtime.paused:
        return
    tickers = cfg.market_data.observed_equities or cfg.market_data.observed_stocks
    if not tickers:
        return
    try:
        _run_async_with_timeout("equity_observer", _async_run("equity_observer"))
    except TimeoutError:
        logger.warning(
            "equity_observer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["equity_observer"],
        )
        _record_job_failure("equity_observer", "timeout")
    except Exception as e:
        logger.warning("equity_observer job failed: %s", e)
        _record_job_failure("equity_observer", str(e))


def _run_prediction_observer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if not cfg.market_data.prediction_enabled:
        return
    if not cfg.market_data.observed_prediction_markets:
        return
    try:
        _run_async_with_timeout("prediction_observer", _async_run("prediction_observer"))
    except TimeoutError:
        logger.warning(
            "prediction_observer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["prediction_observer"],
        )
        _record_job_failure("prediction_observer", "timeout")
    except Exception as e:
        logger.warning("prediction_observer job failed: %s", e)
        _record_job_failure("prediction_observer", str(e))
        return  # skip divergence scan on observer failure
    # Chain: scan for crypto/prediction divergences after observer persists fresh data
    try:
        _run_async_with_timeout("divergence_scanner", _async_run("divergence_scanner"))
    except TimeoutError:
        logger.warning(
            "divergence_scanner timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["divergence_scanner"],
        )
        _record_job_failure("prediction_observer", "divergence_scanner timeout")
    except Exception as e:
        logger.warning("divergence_scanner failed: %s", e)
        _record_job_failure("prediction_observer", f"divergence_scanner: {e}")


def _run_cleanup():
    cfg = load_config()
    try:
        from aletheia.storage import repositories as repo
        from aletheia.storage.sqlite import get_db
        from aletheia.files.jsonl import write_audit
        from aletheia.execution.stale_order_cleanup import cleanup_stale_orphan_orders
        r = cfg.retention
        # ------------------------------------------------------------------
        # FK cascade ordering (Phase 3 retention) — the agent_shell hierarchy
        # forms a tree:
        #     agent_shell_sessions
        #       ├── agent_shell_messages    (FK: session_id)
        #       ├── agent_shell_events      (FK: session_id, task_id)
        #       ├── agent_shell_artifacts   (FK: session_id, task_id)
        #       ├── agent_shell_commands    (FK: session_id)
        #       ├── agent_shell_tasks       (FK: session_id)
        #       └── agent_shell_heartbeats  (FK: session_id)
        # Child rows MUST be deleted before parent rows in the same batch:
        #   1. Each child prune honors its own (shorter or equal) retention
        #      window on its own timestamp column.
        #   2. The parent prune (agent_shell_sessions) then cascades any
        #      leftover child rows younger than the child cutoff but older
        #      than the parent cutoff. Per Agent 2, the parent function
        #      cascade-deletes inside a single transaction, so this ordering
        #      is a defense-in-depth safety belt rather than a strict
        #      correctness requirement — it also keeps `counts` readable so
        #      the audit trail shows what each window took out.
        # A parallel rule applies to agent_shell_tasks (child of sessions,
        # parent of events/artifacts) — task-scoped children are pruned by
        # their own function before agent_shell_tasks fires. See
        # repositories.prune_agent_shell_sessions for the cascade impl.
        # ------------------------------------------------------------------
        min_open_exp_at = repo.min_open_experiment_exposure_at()
        counts = {
            "run_steps": repo.prune_run_steps(r.agent_run_steps_days),
            "tool_calls": repo.prune_tool_calls(r.tool_calls_days),
            "agent_runs": repo.prune_agent_runs(r.agent_runs_days),
            "market_snapshots": repo.prune_market_snapshots(r.market_snapshots_days),
            "portfolio_snapshots": repo.prune_portfolio_snapshots(r.portfolio_snapshots_days),
            "orderbook_snapshots": repo.prune_orderbook_snapshots(7),
            "perp_structure_shadow_outcomes": repo.prune_perp_structure_shadow_outcomes(30),
            "perp_structure_snapshots": repo.prune_perp_structure_snapshots(30),
            "liquidation_events": repo.prune_liquidation_events(7),
            "funding_rate_snapshots": repo.prune_funding_rate_snapshots(r.funding_rate_snapshots_days),
            "open_interest_snapshots": repo.prune_open_interest_snapshots(r.open_interest_snapshots_days),
            "macro_snapshots": repo.prune_macro_snapshots(90),
            "coingecko_top_snapshots": repo.prune_coingecko_top_snapshots(r.coingecko_top_snapshots_days),
            # #149/#150: these had a prune fn but no cleanup-job wiring (onchain) or no
            # prune at all (prediction), so they grew unbounded.
            "onchain_snapshots": repo.prune_onchain_snapshots(30),
            "prediction_probability_snapshots": repo.prune_prediction_probability_snapshots(90),
            "universe_feature_snapshots": repo.prune_universe_feature_snapshots(r.universe_feature_snapshots_days),
            "ohlcv_backfill_state": repo.prune_ohlcv_backfill_state(90),
            "universe_forward_outcomes": repo.prune_universe_forward_outcomes(
                30, min_experiment_started_at=min_open_exp_at,
            ),
            "serenity_claims": repo.prune_serenity_claims(90),
            "shadow_trades": repo.prune_shadow_trades(90),
            "filter_decision_batches": repo.prune_filter_decision_batches(r.filter_decision_batches_days),
            # Filter-family clean_window_cutoff guard: pass the oldest still-
            # open learning experiment start so rows any open experiment
            # might still cite are preserved even when their timestamp is
            # older than the plain retention window.
            "filter_decisions": repo.prune_filter_decisions(
                r.filter_decisions_days,
                min_experiment_started_at=min_open_exp_at,
            ),
            "filter_candidate_outcomes": repo.prune_filter_candidate_outcomes(
                r.filter_candidate_outcomes_days,
                min_experiment_started_at=min_open_exp_at,
            ),
            "filter_shadow_experiments": repo.prune_filter_shadow_experiments(r.filter_shadow_experiments_days),
            "operator_evaluation_events": repo.prune_operator_evaluation_events(30),
            "pi_decisions_transient": repo.prune_pi_decisions(7),
            "signal_pending_stale": repo.prune_signal_pending_stale(30),
            "system_actions": repo.prune_system_actions(7),
            # Market OHLCV per-timeframe TTL + legacy_unknown hard drop.
            "market_ohlcv": repo.prune_market_ohlcv(ttl_days={
                "1m": r.market_ohlcv_1m_days,
                "5m": r.market_ohlcv_5m_days,
                "15m": r.market_ohlcv_15m_days,
                "1h": r.market_ohlcv_1h_days,
                "4h": r.market_ohlcv_4h_days,
                "1d": r.market_ohlcv_1d_days,
            }),
            "alpha_control_observations": repo.prune_alpha_control_observations(r.alpha_control_observations_days),
            "controller_recommendations": repo.prune_controller_recommendations(r.controller_recommendations_days),
            "market_regimes": repo.prune_market_regimes(r.market_regimes_days),
            "serenity_evidence": repo.prune_serenity_evidence(r.serenity_evidence_days),
            "serenity_run_events": repo.prune_serenity_run_events(r.serenity_run_events_days),
            "runtime_sessions": repo.prune_runtime_sessions(r.runtime_sessions_days),
            "backtest_runs": repo.prune_backtest_runs(r.backtest_runs_days),
            # Universe: scanner_runs first (child rows), then snapshots which
            # cascade-deletes universe_members inside its own transaction.
            "universe_scanner_runs": repo.prune_universe_scanner_runs(r.universe_scanner_runs_days),
            "universe_snapshots": repo.prune_universe_snapshots(
                r.universe_snapshots_days,
                min_experiment_started_at=min_open_exp_at,
            ),
            # Operator-shell telemetry (DB-SIZE-1): heartbeats are the dominant
            # bloat — pure liveness, read latest-row-only, so a short window.
            # Children BEFORE parents (agent_shell_sessions) — see FK cascade
            # ordering comment above.
            "agent_shell_heartbeats": repo.prune_agent_shell_heartbeats(r.agent_shell_heartbeats_days),
            "agent_shell_messages": repo.prune_agent_shell_messages(
                days_context=r.agent_shell_messages_context_days,
                days_other=r.agent_shell_messages_other_days,
            ),
            "agent_shell_events": repo.prune_agent_shell_events(r.agent_shell_events_days),
            "agent_shell_artifacts": repo.prune_agent_shell_artifacts(
                days_strategy=r.agent_shell_artifacts_strategy_days,
                days_proposal=r.agent_shell_artifacts_proposal_days,
                days_other=r.agent_shell_artifacts_other_days,
            ),
            "agent_shell_commands": repo.prune_agent_shell_commands(r.agent_shell_commands_days),
            "agent_shell_tasks": repo.prune_agent_shell_tasks(
                days_completed=r.agent_shell_tasks_completed_days,
                days_failed=r.agent_shell_tasks_failed_days,
            ),
            "agent_shell_sessions": repo.prune_agent_shell_sessions(r.agent_shell_sessions_days),
            # Per-session JSONL transcripts on disk (not a DB table): high file
            # COUNT, low bytes, pure working-memory telemetry never read by the
            # learning/PI chain. Had no cleanup path — ~175 files/day, 2.6k in 15
            # days. 14d matches agent_shell_events (the DB form of this stream).
            "agent_shell_session_logs": _prune_session_logs(cfg),
            "agent_shell_task_notes": _prune_task_notes(cfg),
            # Filesystem retention (backups, diagnostics, pre-migration
            # backup) — dispatched to helpers in
            # aletheia.maintenance.filesystem_retention (Agent 4).
            "backups_rotated": _prune_backups(r.backups_keep_recent),
            "diagnostics": _prune_diagnostics(r.diagnostics_days),
            "pre_migration_backup": _prune_pre_migration_backup(r.pre_migration_backup_days),
            # Secondary SQLite file (~/.aletheia/tasks.db) — holds
            # agent_task_claims used for cross-process task fanout. Not part
            # of app.db so it needs its own DELETE cycle.
            "agent_task_claims": _prune_agent_task_claims_tasks_db(),
            "opportunities_expired": repo.expire_stale_opportunities(r.opportunity_ttl_hours),
            "news_items": _prune_news(cfg),
            # Cancel abandoned unresolved Orders reconciliation can never resolve
            # (no credentials / venue dropped them) so they stop failing every
            # reconciliation cycle. Bookkeeping-only: never touches an Order with a
            # Fill or open Position. Returns a summary dict, not a bare count.
            "stale_orders": cleanup_stale_orphan_orders(),
        }
        # Reclaim freed pages from the prunes above without a full VACUUM.
        # Requires PRAGMA auto_vacuum=INCREMENTAL to have been enabled at
        # DB creation / by the Phase 4 backfill; the pragma is a silent
        # no-op when auto_vacuum is NONE, so it is safe to call every cycle.
        try:
            get_db().execute("PRAGMA incremental_vacuum(1000)")
        except Exception as vac_err:  # noqa: BLE001 — best-effort reclaim
            logger.debug("incremental_vacuum skipped: %s", vac_err)
        write_audit({
            "actor": "system", "source": "scheduler",
            "event_type": "data_pruned", "entity_type": "telemetry",
            "message": f"Pruned old telemetry: {counts}",
        })
    except Exception as e:
        logger.warning("cleanup job failed: %s", e)
        _record_job_failure("cleanup", str(e))


def _prune_news(cfg) -> int:
    try:
        from aletheia.data.news_collector import prune_old_news
        return prune_old_news(cfg.news.retention_days)
    except Exception:
        return 0


def _prune_session_logs(cfg) -> int:
    """DB-JOIN-based session-log prune (Agent 4). Falls back to the legacy
    file-only pruner if the DB-join variant is not yet importable. The
    operator's persistent main session id is threaded through as an extra
    exempt stem so it is never reaped even when its DB row falls outside
    the retention window."""
    try:
        from aletheia.agent_shell.file_artifacts import prune_session_logs_db_join
        days = int(getattr(cfg.retention, "agent_shell_session_logs_days", 14))
        main_stem = str(getattr(getattr(cfg, "operator", None), "main_session_id", "") or "")
        extra = (main_stem,) if main_stem else ()
        return prune_session_logs_db_join(days, extra_exempt_stems=extra)
    except Exception:
        try:
            from aletheia.agent_shell.file_artifacts import prune_session_logs
            keep = (cfg.operator.main_session_id,)
            return prune_session_logs(14, keep)
        except Exception:
            return 0


def _prune_task_notes(cfg) -> int:
    """Task-notes filesystem prune (Agent 4) — joins agent_shell_tasks to
    unlink `.md` notes for finished tasks + orphan-sweeps stale files."""
    try:
        from aletheia.agent_shell.file_artifacts import prune_task_notes_db_join
        return prune_task_notes_db_join(
            days_completed=int(getattr(cfg.retention, "agent_shell_task_notes_completed_days", 7)),
            days_failed=int(getattr(cfg.retention, "agent_shell_task_notes_failed_days", 30)),
        )
    except Exception:
        return 0


def _prune_backups(keep_recent: int) -> int:
    try:
        from aletheia.maintenance.filesystem_retention import prune_backups
        return prune_backups(keep_recent=keep_recent)
    except Exception:
        return 0


def _prune_diagnostics(days: int) -> int:
    try:
        from aletheia.maintenance.filesystem_retention import prune_diagnostics_dir
        return prune_diagnostics_dir(days=days)
    except Exception:
        return 0


def _prune_pre_migration_backup(days: int) -> int:
    try:
        from aletheia.maintenance.filesystem_retention import prune_pre_migration_backup
        return prune_pre_migration_backup(days=days)
    except Exception:
        return 0


def _prune_agent_task_claims_tasks_db() -> int:
    """Best-effort DELETE of expired claims in the secondary tasks.db.

    That file lives at `${ALETHEIA_DATA_DIR:-~/.aletheia}/tasks.db` and is
    not managed by the app.db connection pool — the scheduler opens a
    fresh short-lived connection every cleanup cycle. Returns rowcount, or
    0 on any failure (missing file, unavailable table, locked DB).
    """
    try:
        import sqlite3
        from aletheia.config.loader import get_data_dir
        db_file = get_data_dir() / "tasks.db"
        if not db_file.exists():
            return 0
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(str(db_file), timeout=30, isolation_level=None)
        try:
            cur = conn.execute(
                "DELETE FROM agent_task_claims "
                "WHERE expires_at IS NOT NULL AND expires_at < ?",
                (now,),
            )
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception:
        return 0


def _run_news_fetch():
    cfg = load_config()
    if not cfg.news.enabled:
        return
    try:
        _run_async_with_timeout("news_fetch", _async_news_fetch())
    except TimeoutError:
        logger.warning("news_fetch timed out after %.0fs", _ASYNC_JOB_TIMEOUT_SEC["news_fetch"])
        _record_job_failure("news_fetch", "timeout")
    except Exception as e:
        logger.warning("news_fetch failed: %s", e)
        _record_job_failure("news_fetch", str(e))


async def _async_news_fetch():
    from aletheia.data.news_collector import fetch_news_feeds
    await fetch_news_feeds()


def _run_news_analyze():
    cfg = load_config()
    if not cfg.news.enabled or cfg.runtime.paused:
        return
    try:
        _run_async_with_timeout("news_analyze", _async_news_analyze())
    except TimeoutError:
        logger.warning(
            "news_analyze timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["news_analyze"],
        )
        _record_job_failure("news_analyze", "timeout")
    except Exception as e:
        logger.warning("news_analyze failed: %s", e)
        _record_job_failure("news_analyze", str(e))


async def _async_news_analyze():
    from aletheia.data.news_analyzer import analyze_pending_news

    await analyze_pending_news(batch_size=3)


def _run_persona_rollout():
    """Every 8h: advance the dynamic-sizing persona rollout one health-gated step.
    No-op unless cfg.trading.persona_auto_advance_enabled. Owner-gated items (leverage,
    roll, pyramid live) are never touched. See trading/persona_rollout.py."""
    try:
        from aletheia.trading.persona_rollout import advance_persona_rollout
        result = advance_persona_rollout()
        if result.get("action") == "advanced":
            logger.info("persona_rollout: advanced → %s", result.get("step"))
    except Exception as e:
        logger.warning("persona_rollout job failed: %s", e)
        _record_job_failure("persona_rollout", str(e))


def _run_filter_controller_observe():
    """Phase 1 shadow observe: compute + persist filter feedback recommendations.
    Read-only w.r.t. trading config — appends to controller_recommendations only.
    No-op unless cfg.controller.shadow_enabled. See analytics/filter_controller.py."""
    try:
        from aletheia.config.loader import load_config
        cfg = load_config()
        ctrl = getattr(cfg, "controller", None)
        if not ctrl or not ctrl.shadow_enabled:
            return
        from aletheia.analytics.filter_controller import run_controller_observe
        summary = run_controller_observe(min_samples=int(ctrl.min_samples_gate))
        logger.info(
            "filter_controller_observe: wrote %d recs (verdicts=%s)",
            summary.get("recommendations_written", 0), summary.get("verdict_counts"),
        )
    except Exception as e:
        logger.warning("filter_controller_observe job failed: %s", e)
        _record_job_failure("filter_controller_observe", str(e))


def _run_health_check():
    # Stale-run cleanup runs unconditionally regardless of paused/health.enabled
    # so that threads abandoned during a pause period don't accumulate forever.
    _cleanup_stale_agent_runs()
    _cleanup_stale_scanner_runs()

    cfg = load_config()
    if not cfg.health.enabled:
        return
    try:
        from aletheia.agent_runtime.health import run_health_check
        run_health_check()
    except Exception as e:
        logger.warning("health_check job failed: %s", e)
        _record_job_failure("health_check", str(e))


def _run_post_trade_reviewer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        _run_async_with_timeout("post_trade_reviewer", _async_run("post_trade_reviewer"))
    except TimeoutError:
        logger.warning(
            "post_trade_reviewer job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["post_trade_reviewer"],
        )
        _record_job_failure("post_trade_reviewer", "timeout")
    except Exception as e:
        logger.warning("post_trade_reviewer job failed: %s", e)
        _record_job_failure("post_trade_reviewer", str(e))


def _run_risk_critic():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        _run_async_with_timeout("risk_critic", _async_run("risk_critic"))
    except TimeoutError:
        logger.warning(
            "risk_critic job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["risk_critic"],
        )
        _record_job_failure("risk_critic", "timeout")
    except Exception as e:
        logger.warning("risk_critic job failed: %s", e)
        _record_job_failure("risk_critic", str(e))


def _run_insight_synthesizer():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.analytics.insight_synthesizer import synthesize_trading_memo

        loop = asyncio.new_event_loop()
        try:
            memo = loop.run_until_complete(
                asyncio.wait_for(synthesize_trading_memo(), timeout=_ASYNC_JOB_TIMEOUT_SEC["insight_synthesizer"])
            )
            if memo:
                _record_job_success("insight_synthesizer")
                logger.info("insight_synthesizer produced memo: %d trades", memo.get("sample_size", 0))
            else:
                _record_job_success("insight_synthesizer")
        finally:
            loop.close()
    except asyncio.TimeoutError:
        logger.warning("insight_synthesizer timed out after %.0fs", _ASYNC_JOB_TIMEOUT_SEC["insight_synthesizer"])
        _record_job_failure("insight_synthesizer", "timeout")
    except Exception as e:
        logger.warning("insight_synthesizer failed: %s", e)
        _record_job_failure("insight_synthesizer", str(e))

    # Piggyback: reasoning feedback aggregation (no LLM, cheap)
    try:
        from aletheia.analytics.reasoning_feedback import build_reasoning_feedback
        fb = build_reasoning_feedback()
        if fb:
            logger.info("reasoning_feedback built: %d proposals, %d executed",
                        fb.get("sample_proposals", 0), fb.get("sample_executed", 0))
    except Exception as e:
        logger.debug("reasoning_feedback piggyback failed: %s", e)


def _run_confidence_calibration():
    try:
        from aletheia.analytics.confidence_calibration import build_calibration_report
        report = build_calibration_report()
        if report:
            _record_job_success("confidence_calibration")
            logger.info("confidence_calibration: ECE=%.3f (%s), %d trades",
                        report.get("ece", 0), report.get("ece_grade", "?"), report.get("total_executed", 0))
        else:
            _record_job_success("confidence_calibration")
    except Exception as e:
        logger.warning("confidence_calibration job failed: %s", e)
        _record_job_failure("confidence_calibration", str(e))


_DAILY_LEARNING_CATCHUP_JOBS = (
    ("dreaming_daily", _run_dreaming_agent),
    ("insight_synthesizer", _run_insight_synthesizer),
    ("lesson_reviewer", _run_lesson_reviewer),
)


def _run_learning_startup_catchup():
    """Run each latest missed daily Learning window once, in dependency order."""
    try:
        from aletheia.learning.control import daily_catchup_due

        for job_id, fn in _DAILY_LEARNING_CATCHUP_JOBS:
            if daily_catchup_due(job_id):
                _run_job_guarded(job_id, fn)
    except Exception as e:
        logger.warning("learning_startup_catchup failed: %s", e)
        _record_job_failure("learning_startup_catchup", str(e))


def _run_learning_contribution_evaluator():
    try:
        from aletheia.learning.lifecycle import run_contribution_evaluator
        return _run_sync_with_timeout("learning_contribution_evaluator", run_contribution_evaluator)
    except Exception as e:
        logger.warning("learning_contribution_evaluator failed: %s", e)
        _record_job_failure("learning_contribution_evaluator", str(e))


def _run_learning_lifecycle_controller():
    try:
        from aletheia.learning.lifecycle import run_lifecycle_controller
        return _run_sync_with_timeout("learning_lifecycle_controller", run_lifecycle_controller)
    except Exception as e:
        logger.warning("learning_lifecycle_controller failed: %s", e)
        _record_job_failure("learning_lifecycle_controller", str(e))


def _run_measurement_startup_catchup():
    """Recover at most one current measurement window, independent of autonomy."""
    try:
        from aletheia.learning.control import measurement_catchup_due
        if measurement_catchup_due("learning_contribution_evaluator"):
            return _run_learning_contribution_evaluator()
        return {"status": "fresh"}
    except Exception as e:
        logger.warning("learning_measurement_startup_catchup failed: %s", e)
        _record_job_failure("learning_measurement_startup_catchup", str(e))


def _cleanup_stale_scanner_runs():
    """Mark stuck 'running' scanner runs as timed_out after 10min wall-clock.

    The 10-minute threshold is timeout(360s) + grace(15s) + buffer, ensuring
    legitimately running scans (~3.5min) are never killed by the periodic cleanup.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        was_in_tx = db.in_transaction
        result = db.execute(
            "UPDATE universe_scanner_runs SET status = 'timed_out', "
            "completed_at = datetime('now') "
            "WHERE status = 'running' AND started_at < datetime('now', '-10 minutes')",
        )
        rows = result.rowcount if result else 0
        if rows:
            logger.info("Marked %d stale scanner run(s) as timed_out", rows)
        if not was_in_tx:
            db.commit()
    except Exception as e:
        logger.debug("Failed to cleanup stale scanner runs: %s", e)


def _cleanup_stale_agent_runs():
    """Mark any stuck 'running' agent_runs as 'stale' after 20-minute wall-clock threshold.

    Called periodically from health_check so that threads abandoned by
    _run_async_with_timeout (Windows ProactorEventLoop IOCP drain) don't leave
    agent_runs rows pinned at 'running' forever. 20 min >> the longest agent
    timeout (5 min) + grace (15 s) so we only clean genuinely orphaned rows.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        was_in_tx = db.in_transaction
        result = db.execute(
            "UPDATE agent_runs SET status = 'stale', ended_at = datetime('now') "
            "WHERE status = 'running' "
            "AND started_at < datetime('now', '-20 minutes')",
        )
        rows = result.rowcount if result else 0
        if rows:
            logger.info("Marked %d stale agent run(s) as stale during health check", rows)
        if not was_in_tx:
            db.commit()
    except Exception as e:
        logger.debug("Failed to cleanup stale agent runs: %s", e)


def _run_universe_refresh():
    cfg = load_config()
    if cfg.runtime.paused:
        return
    if not cfg.universe.enabled:
        logger.info("universe_refresh skipped: universe.enabled=False in config")
        return
    try:
        _run_async_with_timeout("universe_refresh", _async_universe_refresh())
        _record_job_success("universe_refresh")
    except TimeoutError:
        logger.warning(
            "universe_refresh job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["universe_refresh"],
        )
        _cleanup_stale_scanner_runs()
        _record_job_failure("universe_refresh", "timed out")
    except Exception as e:
        logger.warning("universe_refresh job failed: %s", e)
        _cleanup_stale_scanner_runs()
        _record_job_failure("universe_refresh", str(e))


async def _async_settle_outcomes():
    from aletheia.universe.outcome_recorder import settle_due
    # settle_due() now drains the full queue within a 100s time budget (safe under
    # the 120s job timeout). It logs internally per batch; the total is returned.
    settled = await settle_due()
    logger.debug("outcome_settler: %d outcomes settled this cycle", settled)


def _run_outcome_settler():
    """Settle overdue forward-outcome rows independently of UniverseService.refresh().

    Previously settle_due() was only called inside universe_refresh, which means
    it was silently skipped when universe.enabled=False or runtime.paused=True,
    causing the learning pipeline to starve for settled outcomes. This job
    runs unconditionally every 10 minutes, including during layered pause.
    """
    try:
        _run_async_with_timeout("outcome_settler", _async_settle_outcomes())
        _record_job_success("outcome_settler")
    except TimeoutError:
        logger.warning(
            "outcome_settler timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["outcome_settler"],
        )
        _record_job_failure("outcome_settler", "timed out")
    except Exception as e:
        logger.warning("outcome_settler failed: %s", e)
        _record_job_failure("outcome_settler", str(e))


async def _async_paper_funding_settler():
    from aletheia.portfolio.paper_account import settle_unsettled_paper_closes
    result = await settle_unsettled_paper_closes()
    logger.debug(
        "paper_funding_settler: settled=%d still_pending=%d errors=%d",
        result["settled"], result["still_pending"], len(result["errors"]),
    )


def _run_paper_funding_settler():
    """Retry funding economics for paper_internal futures positions that closed
    during a collection outage (realized_pnl=NULL, funding_source='unavailable').

    Fetches historical funding rates from the exchange when a gap is detected,
    then re-computes and settles the cash ledger.  Runs unconditionally — the
    paper account health must not depend on the runtime being unpaused.
    """
    try:
        _run_async_with_timeout("paper_funding_settler", _async_paper_funding_settler())
        _record_job_success("paper_funding_settler")
    except TimeoutError:
        logger.warning(
            "paper_funding_settler timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["paper_funding_settler"],
        )
        _record_job_failure("paper_funding_settler", "timed out")
    except Exception as e:
        logger.warning("paper_funding_settler failed: %s", e)
        _record_job_failure("paper_funding_settler", str(e))


def _run_guard_promotion():
    """Promote candidate trading guards to active when evidence threshold met.

    Deterministic, no-LLM learning task. Re-homed from the deleted
    backtest_orchestrator → lifecycle_automation cycle. Gated by
    config.guard.auto_promote_enabled inside run_promotion_check().
    """
    cfg = load_config()
    if cfg.runtime.paused:
        return
    try:
        from aletheia.analytics.guard_promotion import run_promotion_check
        result = run_promotion_check()
        logger.info("guard_promotion: %s", result)
    except Exception as e:
        logger.warning("guard_promotion job failed: %s", e)
        _record_job_failure("guard_promotion", str(e))


def _run_ohlcv_nightly_backfill():
    """Nightly bulk download of OHLCV history for universe + observed symbols."""
    try:
        _run_sync_with_timeout("ohlcv_nightly_backfill", _ohlcv_backfill_body)
    except TimeoutError:
        logger.warning("ohlcv_nightly_backfill timed out after %.0fs",
                       _ASYNC_JOB_TIMEOUT_SEC["ohlcv_nightly_backfill"])
        _record_job_failure("ohlcv_nightly_backfill", "timeout")
    except Exception as e:
        logger.warning("ohlcv_nightly_backfill failed: %s", e)
        _record_job_failure("ohlcv_nightly_backfill", str(e))


def _run_active_ohlcv_backfill():
    """Repair a bounded number of active-Universe OHLCV gaps."""
    cfg = load_config()
    if (
        not cfg.historical_data.enabled
        or not cfg.historical_data.active_universe_backfill_enabled
    ):
        return
    try:
        result = _run_sync_with_timeout(
            "ohlcv_active_backfill", _active_ohlcv_backfill_body
        )
        if result.attempted and result.failed == result.attempted:
            _record_job_failure("ohlcv_active_backfill", "all attempted pairs failed")
        logger.info(
            "ohlcv_active_backfill: planned=%d attempted=%d ready=%d partial=%d "
            "failed=%d fetched=%d",
            result.planned,
            result.attempted,
            result.ready,
            result.partial,
            result.failed,
            result.bars_fetched,
        )
        return result
    except TimeoutError:
        logger.warning(
            "ohlcv_active_backfill timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["ohlcv_active_backfill"],
        )
        _record_job_failure("ohlcv_active_backfill", "timeout")
    except Exception as e:
        logger.warning("ohlcv_active_backfill failed: %s", e)
        _record_job_failure("ohlcv_active_backfill", str(e))


def _active_ohlcv_backfill_body():
    import asyncio as _asyncio
    from aletheia.data.active_ohlcv_backfill import run_active_ohlcv_backfill

    cfg = load_config()
    return _asyncio.run(run_active_ohlcv_backfill(
        min_bars=cfg.historical_data.min_bars_for_backtest,
        max_pairs=cfg.historical_data.active_universe_backfill_max_pairs_per_run,
        months=cfg.historical_data.default_months,
    ))


def _ohlcv_backfill_body():
    import asyncio as _asyncio
    from aletheia.data.history_downloader import download_symbol_history

    cfg = load_config()
    months = cfg.historical_data.default_months
    instruments = set(_get_observed_instruments(cfg))

    total_bars = 0
    for instrument in sorted(instruments, key=str):
        for tf in ("1h", "4h"):
            try:
                result = _asyncio.run(download_symbol_history(
                    instrument.symbol, tf, months=months,
                    exchange=instrument.exchange, market_type=instrument.market_type,
                ))
                total_bars += result.bars_fetched
            except Exception as e:
                logger.debug("nightly_backfill skip %s/%s: %s", instrument, tf, e)
    logger.info("ohlcv_nightly_backfill: %d symbols, %d total bars fetched",
                len(instruments), total_bars)


def _run_okx_demo_symbols():
    try:
        from aletheia.execution.okx_broker import refresh_okx_demo_symbols
        count = refresh_okx_demo_symbols()
        logger.info("okx_demo_symbols refreshed: %d symbols", count)
    except Exception as e:
        logger.warning("okx_demo_symbols refresh failed: %s", e)
        _record_job_failure("okx_demo_symbols", str(e))


async def _async_universe_refresh():
    from aletheia.universe.service import UniverseService
    from aletheia.files.jsonl import write_agent_run_log
    from aletheia.storage.models import _utcnow
    started = _utcnow()
    svc = UniverseService()
    try:
        snap_id = await svc.refresh()
        if not snap_id:
            # UniverseService returns None only after recording a failed scanner
            # run. Treat that sentinel as a failed scheduled attempt instead of
            # fabricating a completed_no_snapshot run.
            raise RuntimeError("universe_refresh_no_snapshot")
        write_agent_run_log({
            "agent_name": "universe_refresh", "trigger_type": "scheduled",
            "started_at": started, "ended_at": _utcnow(),
            "status": "completed",
            "outputs_created": [snap_id],
            "errors": [],
        })
    except Exception as e:
        write_agent_run_log({
            "agent_name": "universe_refresh", "trigger_type": "scheduled",
            "started_at": started, "ended_at": _utcnow(),
            "status": "failed", "outputs_created": [], "errors": [str(e)],
        })
        raise


async def _async_run(agent_name: str, *, trigger_type: str = "scheduled"):
    from aletheia.agent_runtime.orchestrator import run_agent
    result = await run_agent(agent_name, trigger_type=trigger_type)
    if isinstance(result, dict) and (
        result.get("status") == "completed_with_errors"
        or bool(result.get("errors"))
    ):
        # The agent already persisted its detailed errors. Raise only a stable,
        # non-provider-specific code so the scheduler failure counter cannot
        # reset on an internally degraded run.
        raise RuntimeError(f"scheduled_agent_reported_errors:{agent_name}")
    return result


# Core and context ticks are process-local scheduling counters used only for
# collector-health correlation; durable state stores the resulting timestamp.
_DC_TICK: int = 0
_DC_CONTEXT_TICK: int = 0

# Carries the current collector tick into _bounded() without changing its public
# four-argument contract (several offline specs replace that helper directly).
_DC_HEALTH_TICK: ContextVar[int | None] = ContextVar("dc_health_tick", default=None)

# These collectors return in-memory rows that are useful only after the
# scheduler persists them. Their successful call status is therefore recorded
# by the caller after storage, rather than eagerly inside _bounded().
_PERSISTENCE_GATED_COLLECTOR_HEALTH = frozenset(
    {"derivatives", "hyperliquid", "defillama"}
)


def _record_collector_health(
    label: str,
    status: str,
    *,
    duration_ms: int | None = None,
    error_code: str | None = None,
) -> None:
    """Best-effort diagnostics must never make a healthy collection fail."""
    tick = _DC_HEALTH_TICK.get()
    if tick is None:
        return
    try:
        from aletheia.data.collector_health import record_collector_status

        record_collector_status(
            label,
            status,
            tick=tick,
            duration_ms=duration_ms,
            error_code=error_code,
        )
    except Exception as exc:
        logger.debug("data_collectors: health state write failed for %s: %s", label, exc)


def _run_data_collectors():
    """Collect primary-venue orderbook, funding, and OI on a two-minute tick."""
    import os as _os
    if _os.environ.get("ALETHEIA_COLLECTOR_PROCESS"):
        return  # managed by the standalone collector subprocess spawned by _gateway_runner
    global _DC_TICK
    cfg = load_config()
    # This is an _ESSENTIAL_JOBS member under layered pause. Trading pause blocks
    # decisions and execution; it must not stop public-data accumulation.
    _DC_TICK += 1
    try:
        result = _run_async_with_timeout(
            "data_collectors", _async_data_collectors(cfg, _DC_TICK)
        )
        error_codes = (
            list(result.get("error_codes") or [])
            if isinstance(result, dict)
            else []
        )
        if error_codes:
            _record_job_failure("data_collectors", ",".join(error_codes))
        else:
            _record_job_success("data_collectors")
        return result
    except TimeoutError:
        logger.warning(
            "data_collectors job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["data_collectors"],
        )
        _record_job_failure("data_collectors", "timed out")
    except Exception as e:
        logger.warning("data_collectors job failed: %s", e)
        _record_job_failure("data_collectors", str(e))


def _run_data_collector_context():
    """Collect cross-venue and macro context on its own bounded ten-minute lane."""
    import os as _os
    if _os.environ.get("ALETHEIA_COLLECTOR_PROCESS"):
        return  # managed by the standalone collector subprocess spawned by _gateway_runner
    global _DC_CONTEXT_TICK
    cfg = load_config()
    _DC_CONTEXT_TICK += 1
    try:
        result = _run_async_with_timeout(
            "data_collector_context",
            _async_data_collector_context(cfg, _DC_CONTEXT_TICK),
        )
        error_codes = (
            list(result.get("error_codes") or [])
            if isinstance(result, dict)
            else []
        )
        if error_codes:
            _record_job_failure("data_collector_context", ",".join(error_codes))
        else:
            _record_job_success("data_collector_context")
        return result
    except TimeoutError:
        logger.warning(
            "data_collector_context job timed out after %.0fs",
            _ASYNC_JOB_TIMEOUT_SEC["data_collector_context"],
        )
        _record_job_failure("data_collector_context", "timed out")
    except Exception as e:
        logger.warning("data_collector_context job failed: %s", e)
        _record_job_failure("data_collector_context", str(e))


# Wall-clock budget for each collector lane.  Each must stay BELOW its 45-second
# job timeout so the coroutine returns before asyncio.wait_for cancels it — the
# same anti-thread-leak rule as market_observer.  _bounded() caps both each
# collector and its lane's cumulative wall-clock.
_DC_DEADLINE_S = 50.0
# Context has the same anti-thread-leak bound but a new deadline.  It never
# inherits elapsed work from the primary-venue collector lane.
_DC_CONTEXT_DEADLINE_S = 55.0


def _consume_detached_task(task: asyncio.Task) -> None:
    """Retrieve delayed cancellation outcomes so the event loop stays quiet."""
    try:
        task.result()
    except (asyncio.CancelledError, Exception):
        pass


async def _wait_without_cancel_drain(
    awaitable: Awaitable[object],
    *,
    timeout: float,
) -> object:
    """Time-box work without waiting for cancellation-resistant cleanup."""
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.add_done_callback(_consume_detached_task)
        task.cancel()
        raise

async def _bounded(coro, label: str, cap: float, deadline: float):
    """Await the coroutine under the smaller collector or lane deadline.

    Python 3.12 changed asyncio.wait_for: on timeout it cancels the inner task
    and then awaits cancellation before raising TimeoutError. CCXT coroutines
    can take tens of seconds to respond to CancelledError, causing the lane to
    stall well past the cap. The shared timeout helper shields the real task,
    cancels it without waiting, and consumes its eventual outcome.
    """
    import time as _time
    started = _time.monotonic()
    remaining = deadline - started
    if remaining <= 2.0:
        logger.warning("data_collectors: skipped %s — lane wall-clock budget exhausted",
                       label)
        coro.close()  # avoid 'coroutine was never awaited' warning
        _record_collector_health(label, "skipped_budget", duration_ms=0)
        return None

    try:
        result = await _wait_without_cancel_drain(
            coro,
            timeout=min(cap, remaining),
        )
        from aletheia.data.collector_health import classify_collector_result

        if label not in _PERSISTENCE_GATED_COLLECTOR_HEALTH:
            _record_collector_health(
                label,
                classify_collector_result(result),
                duration_ms=int((_time.monotonic() - started) * 1000),
            )
        return result
    except asyncio.TimeoutError:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        logger.warning("data_collectors: %s timed out (%.0fs cap) — skipped this tick",
                       label, min(cap, remaining))
        _record_collector_health(
            label,
            "timed_out",
            duration_ms=elapsed_ms,
            error_code="asyncio_timeout",
        )
        return None
    except Exception as e:
        elapsed_ms = int((_time.monotonic() - started) * 1000)
        logger.debug("data_collectors: %s failed: %s", label, e)
        _record_collector_health(
            label,
            "failed",
            duration_ms=elapsed_ms,
            error_code=type(e).__name__,
        )
        return None

async def _async_data_collectors(cfg, tick: int) -> dict[str, object]:
    import time as _time
    from aletheia.data.collectors.microstructure import MicrostructureCollector
    from aletheia.data.collectors.derivatives import DerivativesCollector
    from aletheia.storage import repositories as repo

    _DC_HEALTH_TICK.set(tick)
    _deadline = _time.monotonic() + _DC_DEADLINE_S
    job_error_codes: list[str] = []

    # Keep venue + market identity for primary-exchange collectors.
    instruments = _get_observed_instruments(cfg)
    derivative_symbols = _get_derivative_symbols(instruments)

    # --- Phase A: microstructure + derivatives (every tick) ---
    micro_result: dict = {}
    deriv_result: dict = {}
    raw_deriv_result = None
    deriv_duration_ms = 0
    if instruments:
        micro = MicrostructureCollector()
        micro_result = await _bounded(micro.collect(instruments), "microstructure", 18.0, _deadline) or {}
    else:
        _record_collector_health("microstructure", "not_configured")

    if derivative_symbols:
        deriv = DerivativesCollector()
        deriv_started = _time.monotonic()
        raw_deriv_result = await _bounded(
            deriv.collect(derivative_symbols), "derivatives", 25.0, _deadline
        )
        deriv_duration_ms = int((_time.monotonic() - deriv_started) * 1000)
        deriv_result = raw_deriv_result or {}
    else:
        _record_collector_health("derivatives", "not_configured")

    deriv_persist_errors: list[str] = []
    try:
        repo.insert_collector_snapshot_batch(
            orderbooks=[
                snap.model_dump() for snap in micro_result.get("snapshots", [])
            ],
            funding=[
                snap.model_dump() for snap in deriv_result.get("funding", [])
            ],
            open_interest=[
                snap.model_dump() for snap in deriv_result.get("open_interest", [])
            ],
        )
    except Exception as e:
        logger.debug("collector batch persist failed: %s", e)
        persist_code = {
            "funding": "derivatives_funding_persist_failed",
            "open_interest": "derivatives_open_interest_persist_failed",
        }.get(getattr(e, "stage", None), "collector_batch_persist_failed")
        deriv_persist_errors.append(persist_code)

    if raw_deriv_result is not None:
        if deriv_persist_errors:
            unique_errors = list(dict.fromkeys(deriv_persist_errors))
            job_error_codes.extend(unique_errors)
            _record_collector_health(
                "derivatives",
                "failed",
                duration_ms=deriv_duration_ms,
                error_code=unique_errors[0],
            )
        else:
            from aletheia.data.collector_health import classify_collector_result

            _record_collector_health(
                "derivatives",
                classify_collector_result(deriv_result),
                duration_ms=deriv_duration_ms,
            )
    if not instruments and not derivative_symbols:
        logger.debug(
            "data_collectors: no observed instruments; "
            "running symbol-independent domains only"
        )

    # Log summary
    micro_count = len(micro_result.get("snapshots", []))
    funding_count = len(deriv_result.get("funding", []))
    logger.debug(
        "data_collectors tick=%d: orderbook=%d funding=%d",
        tick, micro_count, funding_count,
    )
    unique_job_errors = list(dict.fromkeys(job_error_codes))
    return {
        "status": "partial" if unique_job_errors else "success",
        "error_codes": unique_job_errors,
    }


async def _async_data_collector_context(cfg, tick: int) -> dict[str, object]:
    """Run independent context collectors under a deadline separate from core.

    The scheduler's ``io`` lane remains single-threaded, so this never overlaps
    primary-exchange CCXT work from ``_async_data_collectors``.  Inside this one
    event loop, independent public HTTP sources can wait concurrently; SQLite
    persistence remains synchronous and therefore serial on the same thread.
    """
    import time as _time
    from aletheia.data.collectors.coingecko_top import CoinGeckoTopCollector
    from aletheia.data.collectors.defillama import DefiLlamaCollector
    from aletheia.data.collectors.hyperliquid import HyperliquidCollector
    from aletheia.data.collectors.macro import MacroCollector
    from aletheia.data.collectors.volume_scanner import VolumeScannerCollector
    from aletheia.storage import repositories as repo

    _DC_HEALTH_TICK.set(tick)
    deadline = _time.monotonic() + _DC_CONTEXT_DEADLINE_S
    instruments = _get_observed_instruments(cfg)
    symbols = list(dict.fromkeys(item.symbol for item in instruments))

    # Wave one gives the previously starved context feeds an equal start.  The
    # volume scan is the only primary-venue CCXT call in this lane; Hyperliquid
    # and DefiLlama use separate public HTTP sources.
    wave_one: dict[str, object] = {}
    tasks: list[tuple[str, object]] = []
    if instruments:
        tasks.append((
            "volume_scanner",
            _bounded(
                VolumeScannerCollector(top_n=30).collect(instruments[:200]),
                "volume_scanner", 20.0, deadline,
            ),
        ))
    else:
        _record_collector_health("volume_scanner", "not_configured")
    if getattr(cfg.market_data, "enable_hyperliquid", True) and symbols:
        tasks.append((
            "hyperliquid",
            _bounded(
                HyperliquidCollector(persist=False).collect(symbols),
                "hyperliquid", 12.0, deadline,
            ),
        ))
    elif not getattr(cfg.market_data, "enable_hyperliquid", True):
        _record_collector_health("hyperliquid", "disabled")
    else:
        _record_collector_health("hyperliquid", "not_configured")
    if getattr(cfg.market_data, "enable_defillama", True):
        tasks.append((
            "defillama",
            _bounded(
                DefiLlamaCollector(persist=False).collect(),
                "defillama", 12.0, deadline,
            ),
        ))
    else:
        _record_collector_health("defillama", "disabled")
    if tasks:
        wave_one = dict(zip(
            (label for label, _ in tasks),
            await asyncio.gather(*(coro for _, coro in tasks)),
        ))

    volume_result = wave_one.get("volume_scanner") or {}
    if volume_result.get("top_symbols"):
        _latest_volume_ranking.update({
            "symbols": volume_result["top_symbols"],
            "observed_at": volume_result["observed_at"],
        })
    for domain in ("hyperliquid", "defillama"):
        result = wave_one.get(domain) or {}
        if result.get("errors"):
            logger.debug("%s collection errors: %s", domain, result["errors"])

    # CoinGecko top rows and macro fetches are independent.  Persist only after
    # both return so the macro row can receive the same-tick movers summary.
    wave_two: dict[str, object] = {}
    tasks = []
    if getattr(cfg.market_data, "enable_coingecko_top", True):
        tasks.append((
            "coingecko_top",
            _bounded(
                CoinGeckoTopCollector().collect(
                    top_n=getattr(cfg.market_data, "coingecko_top_n", 100)
                ),
                "coingecko_top", 15.0, deadline,
            ),
        ))
    else:
        _record_collector_health("coingecko_top", "disabled")
    tasks.append((
        "macro",
        _bounded(MacroCollector().collect(symbols), "macro", 25.0, deadline),
    ))
    if getattr(cfg.market_data, "enable_coinalyze", True) and symbols:
        from aletheia.data.collectors.coinalyze import CoinalyzeCollector

        tasks.append((
            "coinalyze",
            _bounded(CoinalyzeCollector().collect(symbols), "coinalyze", 12.0, deadline),
        ))
    elif not getattr(cfg.market_data, "enable_coinalyze", True):
        _record_collector_health("coinalyze", "disabled")
    else:
        _record_collector_health("coinalyze", "not_configured")
    wave_two = dict(zip(
        (label for label, _ in tasks),
        await asyncio.gather(*(coro for _, coro in tasks)),
    ))

    cg_result = wave_two.get("coingecko_top") or {}
    macro_result = wave_two.get("macro") or {}
    macro_snapshot = macro_result.get("snapshot")
    if macro_snapshot:
        movers_summary = cg_result.get("movers_summary")
        if movers_summary:
            macro_snapshot.top_movers_summary = movers_summary
    coinalyze_result = wave_two.get("coinalyze") or {}
    if coinalyze_result.get("errors"):
        logger.debug("coinalyze collection errors: %s", coinalyze_result["errors"])

    # Build all persistence payloads before opening SQLite's single writer slot.
    hyperliquid_result = wave_one.get("hyperliquid") or {}
    defillama_result = wave_one.get("defillama") or {}
    hyperliquid_funding = [
        row.model_dump() for row in hyperliquid_result.get("funding", [])
    ]
    hyperliquid_oi = [
        row.model_dump() for row in hyperliquid_result.get("open_interest", [])
    ]
    onchain_rows = [
        row.model_dump() for row in defillama_result.get("snapshots", [])
    ]
    coinalyze_oi = [
        row.model_dump() for row in coinalyze_result.get("open_interest", [])
    ]
    coinalyze_liquidations = [
        row.model_dump() for row in coinalyze_result.get("liquidations", [])
    ]
    macro_row = macro_snapshot.model_dump() if macro_snapshot else None
    context_error_codes: list[str] = []

    def _persist_domain(domain: str, operation) -> None:
        try:
            # One outer COMMIT for the tick; savepoints retain the previous
            # best-effort isolation between independent public data sources.
            with repo.transaction(savepoint=True):
                operation()
        except Exception as exc:
            code = f"{domain}_persist_failed"
            context_error_codes.append(code)
            logger.debug("%s persist failed: %s", domain, exc)

    with repo.transaction():
        if volume_result.get("top_symbols"):
            _persist_domain(
                "volume_ranking",
                lambda: repo.save_volume_ranking_snapshot(
                    volume_result["top_symbols"], volume_result["observed_at"]
                ),
            )
        if hyperliquid_funding or hyperliquid_oi:
            _persist_domain(
                "hyperliquid",
                lambda: repo.insert_collector_snapshot_batch(
                    funding=hyperliquid_funding,
                    open_interest=hyperliquid_oi,
                ),
            )
        if onchain_rows:
            _persist_domain(
                "defillama",
                lambda: repo.insert_onchain_snapshot_batch(onchain_rows),
            )
        if cg_result.get("rows"):
            _persist_domain(
                "coingecko_top",
                lambda: repo.insert_coingecko_top_rows(cg_result["rows"]),
            )
        if macro_row:
            _persist_domain("macro", lambda: repo.insert_macro_snapshot(macro_row))
        if coinalyze_oi or coinalyze_liquidations:
            _persist_domain(
                "coinalyze",
                lambda: repo.insert_collector_snapshot_batch(
                    open_interest=coinalyze_oi,
                    liquidations=coinalyze_liquidations,
                ),
            )

    from aletheia.data.collector_health import classify_collector_result

    for domain, result in (
        ("hyperliquid", hyperliquid_result),
        ("defillama", defillama_result),
    ):
        if domain not in wave_one:
            continue
        persist_error = next(
            (code for code in context_error_codes if code.startswith(f"{domain}_")),
            None,
        )
        _record_collector_health(
            domain,
            "failed" if persist_error else classify_collector_result(result),
            error_code=persist_error,
        )

    # Sparse derivatives history is useful but must never spend the two-minute
    # live Funding/OI budget.  It runs last in the context lane and is dropped
    # honestly when that lane has no safe time left.
    if symbols and getattr(cfg.market_data, "derivatives_history_hours", 0) > 0:
        remaining = deadline - _time.monotonic()
        if remaining > 2.0:
            from aletheia.data.collectors.derivatives import DerivativesCollector

            try:
                history_result = await _wait_without_cancel_drain(
                    DerivativesCollector().collect_history(symbols),
                    timeout=min(12.0, remaining),
                )
                if history_result.get("errors"):
                    logger.debug("derivatives history backfill errors: %s", history_result["errors"])
            except asyncio.TimeoutError:
                logger.debug("derivatives history backfill timed out in context lane")
            except Exception as exc:
                logger.debug("derivatives history backfill failed: %s", exc)

    unique_errors = list(dict.fromkeys(context_error_codes))
    return {
        "status": "partial" if unique_errors else "success",
        "error_codes": unique_errors,
    }


def _get_observed_instruments(cfg):
    """Return executable active-universe instruments with full venue identity."""
    from aletheia.data.symbols import InstrumentId, MARKET_PERP, MARKET_SPOT

    # Primary: active universe members
    try:
        from aletheia.storage import repositories as repo
        members = repo.get_active_universe_members(limit=cfg.universe.max_candidates)
        if members:
            result = []
            for member in members:
                if not member.get("symbol") or not member.get("execution_eligible", True):
                    continue
                asset = repo.get_universe_asset(member.get("asset_id")) or {}
                venue = asset.get("venue") or cfg.market_data.ccxt_exchange or "binance"
                market_type = member.get("market_type") or asset.get("market_type") or MARKET_SPOT
                if market_type == "usdt_futures":
                    market_type = MARKET_PERP
                try:
                    result.append(
                        InstrumentId.from_parts(venue, member["symbol"], market_type)
                    )
                except ValueError:
                    continue
            if result:
                return result
    except Exception:
        pass

    # Fallback: configured observed instruments.
    try:
        from aletheia.trading.instruments import observed_instruments_from_config

        return observed_instruments_from_config(cfg)[:20]
    except Exception:
        return []


def _get_derivative_symbols(instruments) -> list[str]:
    """Add every open local futures liability to primary funding collection.

    This is intentionally narrower than ``_get_observed_instruments``: an open
    Position that has rotated out of the active universe needs continued funding
    schedules, but it must not re-enter microstructure scans, volume ranking, or
    any other candidate-producing surface.
    """
    from aletheia.data.symbols import to_canonical

    result = [item.symbol for item in instruments]
    try:
        from aletheia.storage import repositories as repo

        for position in repo.list_all_open_positions():
            if (
                position.get("execution_mode") != "paper_internal"
                or position.get("market_type") != "usdt_futures"
                or position.get("status") not in {None, "open"}
                or not position.get("symbol")
            ):
                continue
            try:
                result.append(to_canonical(position["symbol"]))
            except ValueError:
                continue
    except Exception:
        pass

    return list(dict.fromkeys(result))


def _get_observed_symbols(cfg) -> list[str]:
    """Compatibility view for symbol-only cross-venue consumers and tests."""
    return list(dict.fromkeys(item.symbol for item in _get_observed_instruments(cfg)))

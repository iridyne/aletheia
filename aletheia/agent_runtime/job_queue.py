"""Bounded persistent worker for manually-triggered Agent runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import asyncio
import queue
import threading
import time
from typing import Any

from aletheia.agent_runtime.run_logger import end_run, queue_run
from aletheia.config.loader import load_config
from aletheia.storage.models import AgentRun


class AgentJobQueueFull(RuntimeError):
    def __init__(self, run_id: str, capacity: int) -> None:
        self.run_id = run_id
        self.capacity = capacity
        super().__init__(f"manual agent queue is full ({capacity})")


class AgentJobManagerClosed(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentJob:
    run: AgentRun
    enqueued_monotonic: float


class AgentJobManager:
    """A single durable process worker, intentionally serial for SQLite safety."""

    def __init__(
        self,
        *,
        capacity: int = 16,
        runner: Callable[..., Awaitable[dict[str, Any]]] | None = None,
        thread_name: str = "aletheia-manual-agent-worker",
    ) -> None:
        self.capacity = max(1, int(capacity))
        self._runner = runner
        self._thread_name = thread_name
        self._queue: queue.Queue[AgentJob | None] = queue.Queue(maxsize=self.capacity)
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._accepting = True
        self._active_run_id: str | None = None
        self._completed = 0
        self._failed = 0
        self._cancelled = 0
        self._queue_wait_total_ms = 0.0
        self._queue_wait_count = 0

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        with self._start_lock:
            if self.running:
                return
            self._accepting = True
            self._thread = threading.Thread(
                target=self._worker_main,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()

    def enqueue(self, agent_name: str) -> AgentRun:
        from aletheia.agents import get_agent_fn

        # Validate before a durable queued row is created.
        get_agent_fn(agent_name)
        self.start()
        if not self._accepting:
            raise AgentJobManagerClosed("manual agent queue is shutting down")
        cfg = load_config()
        raw_provider_name = getattr(getattr(cfg, "models", None), "default_provider", None)
        provider_name = raw_provider_name if isinstance(raw_provider_name, str) else None
        run = queue_run(
            agent_name=agent_name,
            trigger_type="manual",
            permission_mode=cfg.agent.permission_mode,
            model_provider=provider_name,
        )
        job = AgentJob(run=run, enqueued_monotonic=time.monotonic())
        try:
            self._queue.put_nowait(job)
        except queue.Full as exc:
            end_run(
                run,
                status="rejected_queue_full",
                errors=[f"manual agent queue full (capacity={self.capacity})"],
            )
            raise AgentJobQueueFull(run.id, self.capacity) from exc
        return run

    def _worker_main(self) -> None:
        from aletheia.storage.sqlite import close_db, rollback_if_in_transaction

        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                break
            self._active_run_id = job.run.id
            wait_ms = (time.monotonic() - job.enqueued_monotonic) * 1000
            self._queue_wait_total_ms += wait_ms
            self._queue_wait_count += 1
            try:
                runner = self._runner
                if runner is None:
                    from aletheia.agent_runtime.orchestrator import run_agent

                    runner = run_agent
                asyncio.run(
                    runner(
                        job.run.agent_name,
                        trigger_type=job.run.trigger_type,
                        existing_run=job.run,
                    )
                )
                self._completed += 1
            except BaseException:  # noqa: BLE001 - run_agent normally records its own failure
                self._failed += 1
                try:
                    end_run(
                        job.run,
                        status="failed",
                        errors=["manual agent worker failed before lifecycle completion"],
                    )
                except Exception:
                    pass
            finally:
                rollback_if_in_transaction()
                close_db()
                self._active_run_id = None
                self._queue.task_done()

    def stop(self, *, cancel_pending: bool = True, join_timeout_sec: float = 5.0) -> None:
        self._accepting = False
        if cancel_pending:
            while True:
                try:
                    job = self._queue.get_nowait()
                except queue.Empty:
                    break
                if job is None:
                    self._queue.task_done()
                    continue
                try:
                    end_run(
                        job.run,
                        status="cancelled",
                        errors=["gateway shutdown cancelled queued manual agent run"],
                    )
                finally:
                    self._cancelled += 1
                    self._queue.task_done()
        if self.running:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                # Only possible when cancel_pending=False. The daemon worker will
                # keep draining; the next process owns no queued rows.
                return
            if self._thread is not None:
                self._thread.join(timeout=max(0.0, join_timeout_sec))
        # Do not lose the reference to an active worker after a short shutdown
        # grace.  That would let a reused manager start a second concurrent
        # worker while the first Agent is still unwinding.
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "accepting": self._accepting,
            "capacity": self.capacity,
            "queued": self._queue.qsize(),
            "active_run_id": self._active_run_id,
            "completed": self._completed,
            "failed": self._failed,
            "cancelled": self._cancelled,
            "queue_wait_avg_ms": (
                round(self._queue_wait_total_ms / self._queue_wait_count, 3)
                if self._queue_wait_count else 0.0
            ),
        }


_agent_job_manager = AgentJobManager()


def get_agent_job_manager() -> AgentJobManager:
    return _agent_job_manager

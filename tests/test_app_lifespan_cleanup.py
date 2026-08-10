"""Regression tests for deterministic FastAPI lifespan shutdown."""

import asyncio
import threading

import pytest

from aletheia.server.app import (
    _cancel_background_task,
    _lifespan_tasks,
    _run_startup_sync,
    lifecycle_task_snapshot,
)


@pytest.mark.asyncio
async def test_cancel_background_task_waits_for_cancellation():
    started = asyncio.Event()

    async def wait_forever():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(wait_forever())
    await started.wait()

    await _cancel_background_task(task, "test task")

    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancelled_startup_sync_does_not_own_default_executor():
    started = threading.Event()
    release = threading.Event()

    def blocked_sync():
        started.set()
        release.wait(timeout=2)

    task = asyncio.create_task(_run_startup_sync(blocked_sync))
    assert await asyncio.to_thread(started.wait, 1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)

    release.set()


@pytest.mark.asyncio
async def test_lifecycle_snapshot_reads_current_pi_daemon_task(monkeypatch):
    stale = asyncio.create_task(asyncio.sleep(0))
    await stale
    current = asyncio.create_task(asyncio.Event().wait())
    _lifespan_tasks["pi_daemon"] = stale
    monkeypatch.setattr(
        "aletheia.agent_runtime.pi_daemon.current_daemon_task", lambda: current
    )
    try:
        row = next(
            item for item in lifecycle_task_snapshot() if item["task_id"] == "pi_daemon"
        )
        assert row["state"] == "running"
    finally:
        current.cancel()
        with pytest.raises(asyncio.CancelledError):
            await current
        _lifespan_tasks.clear()

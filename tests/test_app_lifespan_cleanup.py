"""Regression tests for deterministic FastAPI lifespan shutdown."""

import asyncio
import threading

import pytest

from aletheia.server.app import _cancel_background_task, _run_startup_sync


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

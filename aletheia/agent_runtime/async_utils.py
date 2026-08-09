"""Utilities for running async code from sync contexts without blocking the
event loop.

The Aletheia system has many sync callers (APScheduler executor threads, sync
API routes) that need to await async coroutines (exchange queries, DB probes,
Telegram sends).  Running ``asyncio.run()`` inside a
``ThreadPoolExecutor`` works but creates a fresh thread + fresh event loop per
call, and blocks the calling thread when that thread happens to be the event
loop.

This module provides the canonical pattern:

- **No running loop** (pure sync thread): ``asyncio.run(coro)`` — safe.
- **Running loop exists** (sync thread that is NOT the event loop):
  ``asyncio.run_coroutine_threadsafe(coro, loop).result()`` — schedules on the
  event loop, blocks only the calling thread, not the loop.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Awaitable, TypeVar

logger = logging.getLogger("aletheia.async_utils")

T = TypeVar("T")


def run_coro_from_sync(
    coro: Awaitable[T],
    *,
    timeout: float | None = None,
) -> T:
    """Run an async coroutine from sync code without blocking the event loop.

    When no event loop is running in this thread, uses ``asyncio.run()``.
    When a loop is detected, schedules on it via ``run_coroutine_threadsafe``
    so the loop keeps running other tasks while this thread waits.

    Args:
        coro: The coroutine to run.
        timeout: Max seconds to wait when using ``run_coroutine_threadsafe``.
            ``None`` means wait indefinitely.  Only applies when a loop is
            detected; ``asyncio.run()`` is unbounded.

    Returns:
        The coroutine's return value.

    Raises:
        TimeoutError: If ``timeout`` is set and the coroutine does not complete.
        Exception: Any exception raised by the coroutine.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Pure sync context — no loop running in this thread.
        return asyncio.run(coro)

    # A loop is running.  Detecting whether we are ON the loop thread is not
    # directly exposed by asyncio, but a loop only reports ``is_running()`` on
    # its own thread — so ``get_event_loop().is_running()`` tells us.  On the
    # loop thread, run_coroutine_threadsafe(...).result() would wait for the
    # very loop this thread is blocking: a guaranteed self-deadlock ending in
    # TimeoutError.  In that case run the coroutine on a dedicated worker
    # thread with its own loop instead.
    loop = asyncio.get_event_loop()
    if loop.is_running():
        from concurrent.futures import ThreadPoolExecutor

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            return pool.submit(asyncio.run, coro).result(timeout=timeout)
        finally:
            pool.shutdown(wait=False)

    # Different thread than the loop: schedule onto it without blocking it.
    future = asyncio.run_coroutine_threadsafe(
        asyncio.ensure_future(coro), loop,
    )
    try:
        return future.result(timeout=timeout)
    except FuturesTimeoutError:
        future.cancel()
        raise TimeoutError(f"coroutine timed out after {timeout}s")

"""Regression tests for #253 — scheduler shielded-wait_for pattern.

Before the fix, _bounded() and the derivatives history path used bare
asyncio.wait_for(coro, ...).  Python 3.12 changed wait_for so it awaits
cancellation cleanup before raising TimeoutError — CCXT coroutines can hold
CancelledError for 10-30 s, stalling the caller's clock past the declared cap.

After the fix, _shielded_wait_for wraps the real coro in a Task + shield so
wait_for cancels the shield (instant), fires-and-forgets task.cancel(), and a
done_callback consumes any deferred exception.

NOTE: The autouse _fast_network_timeout fixture in conftest.py patches
asyncio.sleep globally, making sleeps > 2s return instantly (to eliminate
unintended network-retry delays in other tests). Test coros here use short
real sleeps (≤ 2s) so timing assertions remain meaningful.
"""

from __future__ import annotations

import asyncio
import time

import pytest


# ---------------------------------------------------------------------------
# Unit tests for _shielded_wait_for (run via asyncio.run from sync tests)
# ---------------------------------------------------------------------------


def test_shielded_wait_for_returns_result_on_success():
    """_shielded_wait_for returns the coroutine result when it finishes in time."""
    from aletheia.agent_runtime.scheduler import _shielded_wait_for

    async def _run():
        async def _fast():
            return 42
        return await _shielded_wait_for(_fast(), timeout=2.0)

    result = asyncio.run(_run())
    assert result == 42


def test_shielded_wait_for_raises_timeout_without_blocking():
    """_shielded_wait_for raises TimeoutError quickly even when the inner coro drains slowly.

    Uses short real sleeps (< 2s) to stay within the conftest autouse patch range
    while still proving that the caller-visible timeout fires before the drain ends.
    """
    from aletheia.agent_runtime.scheduler import _shielded_wait_for

    # drain_delay must be: > timeout (so drain outlasts the timeout) and < 2s (real sleep)
    drain_delay = 0.4  # seconds the inner task spends on cancellation cleanup

    async def _run():
        async def _slow_to_cancel():
            try:
                await asyncio.sleep(1.5)  # long enough; < 2s = real sleep
            except asyncio.CancelledError:
                await asyncio.sleep(drain_delay)  # drain; < 2s = real sleep
                raise

        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await _shielded_wait_for(_slow_to_cancel(), timeout=0.05)
        elapsed = time.monotonic() - start
        # Must time out quickly — the drain_delay must NOT block the caller
        assert elapsed < drain_delay * 0.8, (
            f"_shielded_wait_for blocked {elapsed:.2f}s (drain={drain_delay}s); "
            "caller should see TimeoutError before drain finishes"
        )

    asyncio.run(_run())


def test_shielded_wait_for_cancels_task_on_timeout():
    """After timeout, the underlying task is eventually cancelled (not leaked)."""
    from aletheia.agent_runtime.scheduler import _shielded_wait_for

    async def _run():
        event = asyncio.Event()

        async def _coro():
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                event.set()
                raise

        with pytest.raises(asyncio.TimeoutError):
            await _shielded_wait_for(_coro(), timeout=0.02)

        # Give the fire-and-forget cancel a moment to propagate
        await asyncio.sleep(0.05)
        assert event.is_set(), "task must be cancelled after _shielded_wait_for timeout"

    asyncio.run(_run())


def test_shielded_wait_for_done_callback_consumes_exception():
    """A deferred exception from the inner task must not surface as unhandled."""
    from aletheia.agent_runtime.scheduler import _shielded_wait_for

    async def _run():
        async def _raises_after_cancel():
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                # Raise a different exception after cancellation (simulates CCXT)
                raise RuntimeError("CCXT cleanup error")

        with pytest.raises(asyncio.TimeoutError):
            await _shielded_wait_for(_raises_after_cancel(), timeout=0.02)

        # Allow deferred callback to fire — no unhandled exception must surface
        await asyncio.sleep(0.05)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# _bounded() uses _shielded_wait_for
# ---------------------------------------------------------------------------


def test_bounded_returns_none_on_timeout_quickly():
    """_bounded must return None on timeout without blocking on cancellation drain."""
    from aletheia.agent_runtime.scheduler import _bounded

    drain_delay = 0.4  # < 2s = real sleep; > timeout cap (0.05s)

    async def _run():
        async def _slow_coro():
            try:
                await asyncio.sleep(1.5)
            except asyncio.CancelledError:
                await asyncio.sleep(drain_delay)
                raise

        deadline = asyncio.get_event_loop().time() + 30.0
        start = time.monotonic()
        result = await _bounded(_slow_coro(), label="test_collector", cap=0.05, deadline=deadline)
        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < drain_delay * 0.8, (
            f"_bounded blocked {elapsed:.2f}s (drain={drain_delay}s); expected < {drain_delay * 0.8:.2f}s"
        )

    asyncio.run(_run())


def test_bounded_returns_none_when_budget_exhausted():
    """_bounded must return None immediately when deadline has less than 2s left."""
    from aletheia.agent_runtime.scheduler import _bounded

    coro_started = False

    async def _run():
        nonlocal coro_started

        async def _would_start():
            nonlocal coro_started
            coro_started = True
            return "should not reach here"

        deadline = time.monotonic() + 1.0  # < 2s budget
        result = await _bounded(_would_start(), label="budget_test", cap=5.0, deadline=deadline)
        assert result is None
        assert not coro_started

    asyncio.run(_run())


def test_bounded_returns_result_on_success():
    """_bounded must return the coro result when it completes in time."""
    from aletheia.agent_runtime.scheduler import _bounded

    async def _run():
        async def _fast():
            return {"symbol_count": 5}

        deadline = time.monotonic() + 30.0
        result = await _bounded(_fast(), label="fast_collector", cap=5.0, deadline=deadline)
        assert result == {"symbol_count": 5}

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Implementation structure checks (verifies fix is in place)
# ---------------------------------------------------------------------------


def test_shielded_wait_for_function_exists():
    """_shielded_wait_for must be a module-level function in scheduler (#253)."""
    from aletheia.agent_runtime import scheduler
    assert hasattr(scheduler, "_shielded_wait_for"), (
        "_shielded_wait_for helper must exist in scheduler module"
    )
    assert asyncio.iscoroutinefunction(scheduler._shielded_wait_for), (
        "_shielded_wait_for must be a coroutine function"
    )


def test_bounded_source_uses_shielded_wait_for():
    """_bounded must call _shielded_wait_for, not bare asyncio.wait_for (#253)."""
    import inspect
    from aletheia.agent_runtime import scheduler
    source = inspect.getsource(scheduler._bounded)
    assert "_shielded_wait_for" in source, (
        "_bounded must delegate to _shielded_wait_for (not bare asyncio.wait_for)"
    )
    assert "asyncio.wait_for" not in source, (
        "_bounded must not call bare asyncio.wait_for after #253 fix"
    )


def test_shielded_wait_for_source_uses_shield():
    """_shielded_wait_for must use asyncio.shield to prevent 3.12 drain (#253)."""
    import inspect
    from aletheia.agent_runtime import scheduler
    source = inspect.getsource(scheduler._shielded_wait_for)
    assert "asyncio.shield" in source, (
        "_shielded_wait_for must use asyncio.shield to bypass 3.12 drain behavior"
    )
    assert "add_done_callback" in source, (
        "_shielded_wait_for must register a done_callback to consume deferred exceptions"
    )


def test_derivatives_history_source_uses_shielded_wait_for():
    """The derivatives history backfill path must use _shielded_wait_for (#253)."""
    import inspect
    from aletheia.agent_runtime import scheduler
    source = inspect.getsource(scheduler._async_data_collector_context)
    assert "_shielded_wait_for" in source, (
        "derivatives history path must use _shielded_wait_for, not bare asyncio.wait_for"
    )

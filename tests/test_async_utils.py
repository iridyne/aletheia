"""The sync→async bridge must keep one event loop per calling thread.

`asyncio.run` closed its loop at the end of every call, so a loop-bound resource
connected by one bridge call (a UTA adapter, its aiohttp session) was invalid in
the next one: `extensions/exchanges/ccxt/adapter.py` answers
`cross_loop_adapter_reuse`. Callers legitimately hold one adapter across several
calls — `aletheia.execution.coordinator.execute_order` re-anchors a drifted
identity and then submits the same order — so the bridge must reuse the loop.

Refs: issue #3507 (post-rotation execution path), tests/uta/test_ccxt_adapter.py
`test_adapter_refuses_cross_loop_reuse` (the adapter-side contract).
"""

from __future__ import annotations

import asyncio
import threading

from aletheia.async_utils import run_coro_from_sync


async def _running_loop_id() -> int:
    return id(asyncio.get_running_loop())


async def _pending_task_count() -> int:
    return len([task for task in asyncio.all_tasks() if task is not asyncio.current_task()])


async def _leak_task() -> None:
    asyncio.get_running_loop().create_task(asyncio.sleep(60))


def test_bridge_reuses_one_loop_when_the_caller_has_none() -> None:
    assert run_coro_from_sync(_running_loop_id()) == run_coro_from_sync(_running_loop_id())


def test_bridge_reuses_one_loop_per_calling_thread() -> None:
    seen: list[int] = []

    def worker() -> None:
        seen.append(run_coro_from_sync(_running_loop_id()))
        seen.append(run_coro_from_sync(_running_loop_id()))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen[0] == seen[1]
    assert seen[0] != run_coro_from_sync(_running_loop_id())


async def test_bridge_reuses_one_loop_when_a_loop_is_already_running() -> None:
    first = run_coro_from_sync(_running_loop_id())
    second = run_coro_from_sync(_running_loop_id())

    assert first == second
    assert first != id(asyncio.get_running_loop())


def test_bridge_cancels_tasks_the_previous_call_left_behind() -> None:
    run_coro_from_sync(_leak_task())

    assert run_coro_from_sync(_pending_task_count()) == 0

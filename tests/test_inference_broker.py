"""Focused regression tests for process-wide inference admission."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from aletheia.model_provider.broker import (
    brokered_stream,
    inference_priority,
    InferenceBroker,
    InferenceBrokerClosed,
    InferencePriority,
    InferenceQueueFull,
    InferenceRateLimitExceeded,
    InferenceTimeout,
    ProviderPolicy,
)
from aletheia.model_provider.base import ModelRequest, StreamChunk


def _policy(**overrides) -> ProviderPolicy:
    values = {
        "max_concurrency": 1,
        "requests_per_minute": 0,
        "tokens_per_minute": 0,
        "queue_capacity": 8,
        "reserved_high_priority_slots": 1,
        "timeout_sec": 1.0,
        "rate_window_sec": 0.05,
    }
    values.update(overrides)
    return ProviderPolicy(**values)


async def _immediate(value=None):
    await asyncio.sleep(0)
    return value


@pytest.mark.anyio
async def test_provider_semaphore_is_process_wide_across_caller_loops():
    policy = _policy(max_concurrency=2, reserved_high_priority_slots=0)
    broker = InferenceBroker(policy_loader=lambda _name: policy)
    lock = threading.Lock()
    active = 0
    peak = 0

    async def operation():
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.03)
        with lock:
            active -= 1
        return "ok"

    async def caller():
        return await broker.submit("provider-a", operation)

    try:
        results = await asyncio.gather(*(caller() for _ in range(8)))
        assert results == ["ok"] * 8
        assert peak == 2
        snapshot = await broker.snapshot()
        metrics = snapshot["providers"]["provider-a"]["metrics"]
        assert metrics["completed"] == 8
        assert metrics["peak_active"] == 2
        assert metrics["queue_wait_count"] == 8
    finally:
        await broker.aclose()


@pytest.mark.anyio
async def test_priority_queue_runs_high_priority_before_earlier_background_work():
    policy = _policy(max_concurrency=1)
    broker = InferenceBroker(policy_loader=lambda _name: policy)
    blocker_started = threading.Event()
    release_blocker = threading.Event()
    order: list[str] = []

    async def blocker():
        blocker_started.set()
        await asyncio.to_thread(release_blocker.wait)
        order.append("blocker")
        return "blocker"

    async def named(name: str):
        order.append(name)
        return name

    try:
        first = asyncio.create_task(
            broker.submit(
                "provider-a",
                blocker,
                priority=InferencePriority.TRADING,
            )
        )
        assert await asyncio.to_thread(blocker_started.wait, 1)
        background = asyncio.create_task(
            broker.submit(
                "provider-a",
                lambda: named("background"),
                priority=InferencePriority.BACKGROUND,
            )
        )
        interactive = asyncio.create_task(
            broker.submit(
                "provider-a",
                lambda: named("interactive"),
                priority=InferencePriority.INTERACTIVE,
            )
        )
        await asyncio.sleep(0.01)
        release_blocker.set()
        assert await asyncio.gather(first, interactive, background) == [
            "blocker",
            "interactive",
            "background",
        ]
        assert order == ["blocker", "interactive", "background"]
    finally:
        release_blocker.set()
        await broker.aclose()


@pytest.mark.anyio
async def test_explicit_critical_priority_overrides_ambient_background_priority():
    policy = _policy(
        max_concurrency=1,
        queue_capacity=1,
        reserved_high_priority_slots=1,
    )
    broker = InferenceBroker(policy_loader=lambda _name: policy)

    try:
        with inference_priority(InferencePriority.BACKGROUND):
            await broker.submit(
                "provider-a",
                lambda: _immediate("critical"),
                priority=InferencePriority.CRITICAL,
            )
        metrics = (await broker.snapshot())["providers"]["provider-a"]["metrics"]
        assert metrics["completed"] == 1
    finally:
        await broker.aclose()


@pytest.mark.anyio
async def test_queue_reserves_capacity_for_high_priority_and_fails_closed():
    policy = _policy(
        max_concurrency=1,
        queue_capacity=2,
        reserved_high_priority_slots=1,
    )
    broker = InferenceBroker(policy_loader=lambda _name: policy)
    started = threading.Event()
    release = threading.Event()

    async def blocker():
        started.set()
        await asyncio.to_thread(release.wait)
        return "done"

    try:
        first = asyncio.create_task(
            broker.submit(
                "provider-a",
                blocker,
                priority=InferencePriority.BACKGROUND,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)

        with pytest.raises(InferenceQueueFull):
            await broker.submit(
                "provider-a",
                lambda: asyncio.sleep(0),
                priority=InferencePriority.BACKGROUND,
            )

        high = asyncio.create_task(
            broker.submit(
                "provider-a",
                lambda: _immediate("high"),
                priority=InferencePriority.TRADING,
            )
        )
        await asyncio.sleep(0.01)
        with pytest.raises(InferenceQueueFull):
            await broker.submit(
                "provider-a",
                lambda: asyncio.sleep(0),
                priority=InferencePriority.CRITICAL,
            )
        release.set()
        assert await first == "done"
        assert await high == "high"
        metrics = (await broker.snapshot())["providers"]["provider-a"]["metrics"]
        assert metrics["rejected_queue_full"] == 2
    finally:
        release.set()
        await broker.aclose()


@pytest.mark.anyio
async def test_rpm_waits_and_tpm_rejects_impossible_request():
    rpm_policy = _policy(requests_per_minute=1, rate_window_sec=0.05)
    broker = InferenceBroker(policy_loader=lambda _name: rpm_policy)
    try:
        started = time.monotonic()
        assert await broker.submit("provider-a", lambda: _immediate(1)) == 1
        assert await broker.submit("provider-a", lambda: _immediate(2)) == 2
        elapsed = time.monotonic() - started
        assert elapsed >= 0.045
        metrics = (await broker.snapshot())["providers"]["provider-a"]["metrics"]
        assert metrics["rate_wait_count"] == 1
        assert 0 < metrics["rate_wait_total_ms"] <= round(elapsed * 1000, 3)
    finally:
        await broker.aclose()

    tpm_policy = _policy(tokens_per_minute=10)
    broker = InferenceBroker(policy_loader=lambda _name: tpm_policy)
    try:
        with pytest.raises(InferenceRateLimitExceeded):
            await broker.submit(
                "provider-b",
                lambda: _immediate("never"),
                reserved_tokens=11,
            )
    finally:
        await broker.aclose()


@pytest.mark.anyio
async def test_total_timeout_cancels_provider_operation_and_reports_wait_metrics():
    policy = _policy(timeout_sec=0.05)
    broker = InferenceBroker(policy_loader=lambda _name: policy)
    cancelled = threading.Event()

    async def never_finishes():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    try:
        with pytest.raises(InferenceTimeout):
            await broker.submit("provider-a", never_finishes)
        assert await asyncio.to_thread(cancelled.wait, 1)
        await asyncio.sleep(0.01)
        metrics = (await broker.snapshot())["providers"]["provider-a"]["metrics"]
        assert metrics["timed_out"] >= 1
        assert metrics["active"] == 0
        assert metrics["outstanding"] == 0
    finally:
        await broker.aclose()


@pytest.mark.anyio
async def test_stream_holds_broker_concurrency_until_iteration_finishes():
    policy = _policy(max_concurrency=1, reserved_high_priority_slots=0)
    broker = InferenceBroker(policy_loader=lambda _name: policy)
    active = 0
    peak = 0

    class Provider:
        async def stream(self, _request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                yield StreamChunk(content="a")
                await asyncio.sleep(0.02)
                yield StreamChunk(content="b", done=True)
            finally:
                active -= 1

    async def consume():
        return [
            chunk.content
            async for chunk in brokered_stream(
                "provider-a",
                Provider(),
                ModelRequest(messages=[{"role": "user", "content": "x"}]),
                broker=broker,
            )
        ]

    try:
        assert await asyncio.gather(consume(), consume()) == [["a", "b"], ["a", "b"]]
        assert peak == 1
    finally:
        await broker.aclose()


@pytest.mark.anyio
async def test_broker_can_restart_after_orderly_close():
    broker = InferenceBroker(policy_loader=lambda _name: _policy())

    assert await broker.submit("provider-a", lambda: _immediate("first")) == "first"
    await broker.aclose()
    assert await broker.submit("provider-a", lambda: _immediate("second")) == "second"
    await broker.aclose()


@pytest.mark.anyio
async def test_policy_reload_waits_for_admitted_work_without_rejecting_new_submit():
    current_policy = _policy(max_concurrency=1)
    broker = InferenceBroker(policy_loader=lambda _name: current_policy)
    started = threading.Event()
    release = threading.Event()

    async def blocker():
        started.set()
        await asyncio.to_thread(release.wait)
        return "old-policy"

    first = asyncio.create_task(broker.submit("provider-a", blocker))
    second = None
    third = None
    try:
        assert await asyncio.to_thread(started.wait, 1)

        current_policy = _policy(max_concurrency=2)
        await broker.invalidate_provider("provider-a")
        snapshot = await broker.snapshot()
        assert snapshot["providers"]["provider-a"]["status"] == "reloading"

        second = asyncio.create_task(
            broker.submit("provider-a", lambda: _immediate("new-policy"))
        )
        third = asyncio.create_task(
            broker.submit("provider-a", lambda: _immediate("same-generation"))
        )
        await asyncio.sleep(0.01)
        assert not second.done(), "new work must wait while the old policy drains"
        assert not third.done(), "concurrent work must wait for the same reload"

        release.set()
        assert await asyncio.gather(first, second, third) == [
            "old-policy",
            "new-policy",
            "same-generation",
        ]
        snapshot = await broker.snapshot()
        assert snapshot["providers"]["provider-a"]["status"] == "running"
        assert snapshot["providers"]["provider-a"]["policy"]["max_concurrency"] == 2
        assert snapshot["providers"]["provider-a"]["metrics"]["completed"] == 2
    finally:
        release.set()
        await asyncio.gather(
            *(task for task in (first, second, third) if task is not None),
            return_exceptions=True,
        )
        await broker.aclose()


@pytest.mark.anyio
async def test_policy_reload_wait_is_bounded_by_submission_deadline():
    current_policy = _policy(max_concurrency=1)
    broker = InferenceBroker(policy_loader=lambda _name: current_policy)
    started = threading.Event()
    release = threading.Event()

    async def blocker():
        started.set()
        await asyncio.to_thread(release.wait)
        return "old-policy"

    first = asyncio.create_task(broker.submit("provider-a", blocker))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        current_policy = _policy(max_concurrency=2)
        await broker.invalidate_provider("provider-a")

        with pytest.raises(InferenceTimeout, match="policy reload exceeded"):
            await broker.submit(
                "provider-a",
                lambda: _immediate("must-not-run"),
                timeout_sec=0.01,
            )

        snapshot = await broker.snapshot()
        assert snapshot["providers"]["provider-a"]["status"] == "reloading"
        assert snapshot["providers"]["provider-a"]["metrics"]["submitted"] == 1
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)
        await broker.aclose()


@pytest.mark.anyio
async def test_broker_shutdown_wakes_policy_reload_waiters():
    current_policy = _policy(max_concurrency=1)
    broker = InferenceBroker(policy_loader=lambda _name: current_policy)
    started = threading.Event()

    async def blocker():
        started.set()
        await asyncio.Event().wait()

    first = asyncio.create_task(broker.submit("provider-a", blocker))
    assert await asyncio.to_thread(started.wait, 1)
    current_policy = _policy(max_concurrency=2)
    await broker.invalidate_provider("provider-a")
    waiting = asyncio.create_task(
        broker.submit("provider-a", lambda: _immediate("must-not-run"))
    )
    await asyncio.sleep(0.01)
    assert not waiting.done()

    await broker.aclose()
    results = await asyncio.gather(first, waiting, return_exceptions=True)
    assert all(isinstance(result, InferenceBrokerClosed) for result in results)


@pytest.mark.anyio
async def test_broker_drains_default_executor_before_loop_close():
    shutdown_waits = []

    class TrackingExecutor(ThreadPoolExecutor):
        def shutdown(self, wait=True, *, cancel_futures=False):
            shutdown_waits.append(wait)
            return super().shutdown(wait=wait, cancel_futures=cancel_futures)

    broker = InferenceBroker(policy_loader=lambda _name: _policy())
    executor = TrackingExecutor(max_workers=1)

    async def install_executor():
        asyncio.get_running_loop().set_default_executor(executor)

    try:
        await broker.run_auxiliary(lambda: install_executor())
    finally:
        await broker.aclose()

    assert True in shutdown_waits


@pytest.mark.anyio
async def test_broker_bounds_noncooperative_executor_shutdown(monkeypatch):
    import aletheia.model_provider.broker as broker_module

    monkeypatch.setattr(broker_module, "_DEFAULT_EXECUTOR_SHUTDOWN_TIMEOUT_SEC", 0.01)
    baseline_threads = set(threading.enumerate())
    entered = threading.Event()
    release = threading.Event()
    broker = InferenceBroker(policy_loader=lambda _name: _policy())
    executor_threads = []

    def block():
        entered.set()
        release.wait()

    async def operation():
        await asyncio.to_thread(block)

    task = asyncio.create_task(broker.run_auxiliary(lambda: operation()))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()
        executor_threads = [
            thread
            for thread in threading.enumerate()
            if thread not in baseline_threads and thread.name.startswith("asyncio_")
        ]
        with pytest.warns(RuntimeWarning, match="executor did not.*joining"):
            await broker.aclose()

        assert not broker.running
        assert executor_threads and any(thread.is_alive() for thread in executor_threads)
    finally:
        release.set()
        if broker.running:
            await broker.aclose()
        shutdown_threads = [
            thread for thread in threading.enumerate() if "_do_shutdown" in thread.name
        ]
        for thread in executor_threads + shutdown_threads:
            thread.join(timeout=1)
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

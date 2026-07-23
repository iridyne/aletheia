"""Focused regression tests for process-wide inference admission."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from aletheia.model_provider.broker import (
    brokered_stream,
    inference_priority,
    InferenceBroker,
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

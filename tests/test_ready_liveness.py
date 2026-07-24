from __future__ import annotations

import asyncio
import threading
import time

import anyio.to_thread
import httpx
import pytest

from aletheia.server.app import app
from aletheia.server.responsiveness import EventLoopLagMonitor


@pytest.mark.asyncio
async def test_ready_bypasses_saturated_sync_worker_pool():
    limiter = anyio.to_thread.current_default_thread_limiter()
    worker_count = limiter.total_tokens
    release = threading.Event()
    all_started = threading.Event()
    started = 0
    started_lock = threading.Lock()

    def occupy_worker() -> None:
        nonlocal started
        with started_lock:
            started += 1
            if started == worker_count:
                all_started.set()
        release.wait(timeout=5.0)

    occupiers = [
        asyncio.create_task(anyio.to_thread.run_sync(occupy_worker))
        for _ in range(worker_count)
    ]
    try:
        async def wait_until_started() -> None:
            while not all_started.is_set():
                await asyncio.sleep(0.005)

        await asyncio.wait_for(wait_until_started(), timeout=2.0)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await asyncio.wait_for(client.get("/api/ready"), timeout=0.25)

        assert response.status_code == 200
        payload = response.json()
        assert payload["ok"] is True
        assert payload["responsiveness"]["sync_workers"] == {
            "borrowed": worker_count,
            "total": worker_count,
        }
    finally:
        release.set()
        await asyncio.gather(*occupiers)


def test_event_loop_lag_monitor_reports_recent_window(monkeypatch):
    monitor = EventLoopLagMonitor(window_sec=60.0)
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)

    monitor.record(0.012, observed_at=99.0)
    monitor.record(0.034, observed_at=100.0)

    assert monitor.snapshot() == {
        "last_ms": 34.0,
        "max_60s_ms": 34.0,
        "sample_age_ms": 0.0,
    }

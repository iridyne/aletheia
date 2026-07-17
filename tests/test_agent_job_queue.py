"""Manual Agent API and persistent bounded worker tests."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.agent_runtime.job_queue import AgentJobManager, AgentJobQueueFull
from aletheia.agent_runtime.run_logger import begin_queued_run, end_run
from aletheia.server.app import app
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import AgentRun
from aletheia.storage.sqlite import reset_db


def _setup_process_db(tmp_path, monkeypatch) -> None:
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    run_migrations()


def test_manual_agent_manager_persists_queued_then_runs(tmp_path, monkeypatch):
    _setup_process_db(tmp_path, monkeypatch)
    completed = threading.Event()

    async def fake_runner(agent_name, *, trigger_type, existing_run):
        begin_queued_run(existing_run, permission_mode="Observe", model_provider=None)
        end_run(existing_run, status="completed", outputs=["done"])
        completed.set()
        return {"status": "completed"}

    manager = AgentJobManager(capacity=2, runner=fake_runner)
    run = manager.enqueue("research_agent")

    assert run.status == "queued"
    assert completed.wait(timeout=3)
    row = repo.get_agent_run(run.id)
    assert row is not None
    assert row["status"] == "completed"
    deadline = time.monotonic() + 3
    while manager.snapshot()["completed"] < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.snapshot()["completed"] == 1
    manager.stop()
    reset_db()


def test_manual_agent_manager_rejects_when_bounded_queue_is_full(tmp_path, monkeypatch):
    _setup_process_db(tmp_path, monkeypatch)
    active = threading.Event()
    release = threading.Event()

    async def blocking_runner(agent_name, *, trigger_type, existing_run):
        begin_queued_run(existing_run, permission_mode="Observe", model_provider=None)
        active.set()
        await asyncio.to_thread(release.wait, 3)
        end_run(existing_run, status="completed")
        return {"status": "completed"}

    manager = AgentJobManager(capacity=1, runner=blocking_runner)
    manager.enqueue("research_agent")
    assert active.wait(timeout=3)
    manager.enqueue("market_observer")

    with pytest.raises(AgentJobQueueFull) as exc_info:
        manager.enqueue("dreaming_agent")

    rejected = repo.get_agent_run(exc_info.value.run_id)
    assert rejected is not None
    assert rejected["status"] == "rejected_queue_full"
    release.set()
    deadline = time.monotonic() + 3
    while manager.snapshot()["completed"] < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    manager.stop()
    reset_db()


@pytest.mark.anyio
async def test_manual_agent_route_returns_202_job_id(monkeypatch):
    run = AgentRun(
        agent_name="research_agent",
        trigger_type="manual",
        status="queued",
    )

    class FakeManager:
        def enqueue(self, agent_name):
            assert agent_name == "research_agent"
            return run

        def snapshot(self):
            return {"capacity": 16, "queued": 1, "active_run_id": None}

    monkeypatch.setattr("aletheia.server.routes.data._ensure_db", lambda: None)
    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: FakeManager(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/agent-runs/start",
            json={"agent_name": "research_agent"},
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == run.id
    assert response.json()["status"] == "queued"

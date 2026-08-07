"""Regression tests for POST /api/prediction/refresh (issue #400).

Before the fix: the route imported `get_agent` from `aletheia.agents`, which
does not exist, causing every manual Prediction refresh to raise ImportError.

After the fix: the route delegates to the durable AgentJobManager via
`get_agent_fn` (the real registry API), returning 202 Accepted + job metadata.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_db(tmp_path, monkeypatch) -> None:
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    run_migrations()


def _make_config(tmp_path, *, prediction_enabled: bool = True, categories: list[str] | None = None):
    """Write a minimal config.yaml so load_config() returns prediction settings."""
    import yaml

    cfg = {
        "market_data": {
            "prediction_enabled": prediction_enabled,
            "observed_prediction_markets": categories if categories is not None else ["crypto"],
        },
        "agent": {"permission_mode": "Observe"},
        "models": {"default_provider": "test"},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))


# ---------------------------------------------------------------------------
# Test 1 — regression: route no longer references the nonexistent get_agent API
# ---------------------------------------------------------------------------

def test_refresh_route_does_not_import_get_agent():
    """The route module must not reference `get_agent` from aletheia.agents.

    This is the regression guard: before the fix, the route used
    `from aletheia.agents import get_agent`, which raised ImportError at runtime
    because `get_agent` has never existed in the registry. Importing the module
    here would succeed even before the fix (the import is lazy), but we can
    verify statically that the broken symbol is gone.
    """
    import inspect
    import aletheia.server.routes.prediction as mod

    source = inspect.getsource(mod)
    # The broken import that caused issue #400 must not appear.
    assert "get_agent(" not in source, (
        "Route still calls get_agent() — the nonexistent function from aletheia.agents. "
        "Use get_agent_job_manager().enqueue() instead."
    )
    # The route must use the real registry path (via job_queue which calls get_agent_fn).
    assert "get_agent_job_manager" in source


# ---------------------------------------------------------------------------
# Test 2 — route returns 202 + job metadata and uses the registry API
# ---------------------------------------------------------------------------

async def test_refresh_returns_202_queued(tmp_path, monkeypatch):
    """POST /api/prediction/refresh must return 202 Accepted with job_id.

    Proves:
    - No ImportError (the bug from issue #400).
    - The endpoint uses the job_queue / registry path (FakeManager.enqueue called).
    - Returns queued status with a durable run id.
    """
    _setup_db(tmp_path, monkeypatch)
    _make_config(tmp_path)

    run = AgentRun(agent_name="prediction_observer", trigger_type="manual", status="queued")
    enqueue_called_with: list[str] = []

    class FakeManager:
        def enqueue(self, agent_name: str) -> AgentRun:
            enqueue_called_with.append(agent_name)
            return run

        def snapshot(self) -> dict:
            return {"capacity": 16, "queued": 0, "active_run_id": None}

    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: FakeManager(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/prediction/refresh")

    assert resp.status_code == 202
    body = resp.json()
    assert body["job_id"] == run.id
    assert body["run_id"] == run.id
    assert body["status"] == "queued"
    assert "queue" in body
    # Confirm the manager was called with the correct agent name.
    assert enqueue_called_with == ["prediction_observer"]


# ---------------------------------------------------------------------------
# Test 3 — durable AgentRun record created in the database
# ---------------------------------------------------------------------------

def test_refresh_creates_durable_agent_run(tmp_path, monkeypatch):
    """A successful manual trigger must produce a persisted agent_runs row.

    Uses a real AgentJobManager with a fake runner so the DB write (queue_run)
    actually happens, and verifies the row exists with trigger_type="manual".
    """
    _setup_db(tmp_path, monkeypatch)
    _make_config(tmp_path)

    completed = threading.Event()

    async def fake_runner(agent_name, *, trigger_type, existing_run):
        begin_queued_run(existing_run, permission_mode="Observe", model_provider=None)
        end_run(existing_run, status="completed", outputs=["ok"])
        completed.set()
        return {"status": "completed"}

    manager = AgentJobManager(capacity=4, runner=fake_runner)
    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: manager,
    )

    run = manager.enqueue("prediction_observer")

    assert run.status == "queued"
    assert run.trigger_type == "manual"
    assert run.agent_name == "prediction_observer"

    row = repo.get_agent_run(run.id)
    assert row is not None
    # The worker may claim the durable row before this read.  Queued, running,
    # and completed are all legal monotonic states; terminal failure states are
    # deliberately excluded.
    assert row["status"] in {"queued", "running", "completed"}
    assert row["trigger_type"] == "manual"

    assert completed.wait(timeout=5)
    deadline = time.monotonic() + 5
    while True:
        row = repo.get_agent_run(run.id)
        if row and row["status"] == "completed":
            break
        assert time.monotonic() < deadline, "timed out waiting for completed status"
        time.sleep(0.02)

    assert row["status"] == "completed"
    manager.stop()
    reset_db()


# ---------------------------------------------------------------------------
# Test 4 — duplicate/in-progress request returns typed 409 conflict
# ---------------------------------------------------------------------------

async def test_refresh_duplicate_returns_409_conflict(tmp_path, monkeypatch):
    """When the job queue is full, the route must return 409 with typed error.

    Simulates a second (duplicate) click while a run is already in progress.
    """
    _setup_db(tmp_path, monkeypatch)
    _make_config(tmp_path)

    in_progress_run_id = "run-already-queued"

    class FullManager:
        def enqueue(self, agent_name: str) -> AgentRun:
            raise AgentJobQueueFull(run_id=in_progress_run_id, capacity=1)

        def snapshot(self) -> dict:
            return {"capacity": 1, "queued": 1, "active_run_id": in_progress_run_id}

    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: FullManager(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/prediction/refresh")

    assert resp.status_code == 409
    body = resp.json()
    detail = body["detail"]
    assert detail["error"] == "prediction_refresh_in_progress"
    assert detail["job_id"] == in_progress_run_id


# ---------------------------------------------------------------------------
# Test 5 — agent failure is captured as a durable failed run
# ---------------------------------------------------------------------------

def test_refresh_agent_failure_is_captured_durably(tmp_path, monkeypatch):
    """When the prediction_observer raises, the run must be marked failed in DB.

    The AgentJobManager already handles this via its _worker_main exception
    guard. Verify the status is persisted, not dropped silently.
    """
    _setup_db(tmp_path, monkeypatch)
    _make_config(tmp_path)

    failed = threading.Event()

    async def failing_runner(agent_name, *, trigger_type, existing_run):
        begin_queued_run(existing_run, permission_mode="Observe", model_provider=None)
        failed.set()
        raise RuntimeError("polymarket fetch failed")

    manager = AgentJobManager(capacity=4, runner=failing_runner)
    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: manager,
    )

    run = manager.enqueue("prediction_observer")
    assert failed.wait(timeout=5)

    deadline = time.monotonic() + 5
    while True:
        row = repo.get_agent_run(run.id)
        if row and row["status"] == "failed":
            break
        assert time.monotonic() < deadline, "timed out waiting for failed status"
        time.sleep(0.02)

    assert row["status"] == "failed"
    manager.stop()
    reset_db()


# ---------------------------------------------------------------------------
# Test 6 — gate: disabled prediction module returns 400
# ---------------------------------------------------------------------------

async def test_refresh_disabled_returns_400(tmp_path, monkeypatch):
    """When prediction_enabled=False the route must reject with 400, no enqueue."""
    _setup_db(tmp_path, monkeypatch)
    _make_config(tmp_path, prediction_enabled=False)

    enqueue_called = False

    class GuardManager:
        def enqueue(self, agent_name: str) -> AgentRun:
            nonlocal enqueue_called
            enqueue_called = True
            raise AssertionError("should not be reached")

        def snapshot(self) -> dict:
            return {}

    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: GuardManager(),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/prediction/refresh")

    assert resp.status_code == 400
    assert not enqueue_called

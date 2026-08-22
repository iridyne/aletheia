"""Prediction observer admission must guard every manual entrypoint."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.server.app import app
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import AgentRun
from aletheia.storage.sqlite import reset_db


def _setup(tmp_path, monkeypatch, *, enabled: bool, categories: list[str] | None = None) -> None:
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.yaml").write_text(json.dumps({
        "market_data": {
            "prediction_enabled": enabled,
            "observed_prediction_markets": categories if categories is not None else ["crypto"],
        },
        "agent": {"permission_mode": "Observe"},
        "models": {"default_provider": "test"},
    }))
    reset_db()
    run_migrations()


class _FakeManager:
    def __init__(self) -> None:
        self.enqueued: list[str] = []
        self.run = AgentRun(agent_name="prediction_observer", trigger_type="manual", status="queued")

    def enqueue(self, agent_name: str, *, trigger_type: str = "manual", idempotency_key: str | None = None) -> AgentRun:
        self.enqueued.append(agent_name)
        return self.run

    def snapshot(self) -> dict:
        return {"capacity": 16, "queued": 0, "active_run_id": None}


@pytest.mark.anyio
async def test_manual_prediction_start_rejects_disabled_before_queue(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, enabled=False)
    manager = _FakeManager()
    monkeypatch.setattr("aletheia.agent_runtime.job_queue.get_agent_job_manager", lambda: manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/agent-runs/start", json={"agent_name": "prediction_observer"})

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "prediction_observer_disabled"
    assert manager.enqueued == []


@pytest.mark.anyio
async def test_manual_prediction_start_rejects_empty_categories_before_queue(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, enabled=True, categories=[])
    manager = _FakeManager()
    monkeypatch.setattr("aletheia.agent_runtime.job_queue.get_agent_job_manager", lambda: manager)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/agent-runs/start", json={"agent_name": "prediction_observer"})

    assert response.status_code == 400
    assert response.json()["detail"]["error"] == "prediction_observer_not_configured"
    assert manager.enqueued == []


@pytest.mark.anyio
async def test_catalog_reports_prediction_admission_state(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, enabled=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/agents/catalog")

    assert response.status_code == 200
    prediction = next(entry for entry in response.json()["agents"] if entry["name"] == "prediction_observer")
    assert prediction["runnable"] is False
    assert prediction["blocked_reason"] == "prediction_observer_disabled"


@pytest.mark.anyio
async def test_prediction_agent_defensively_rejects_disabled_config(monkeypatch):
    from types import SimpleNamespace

    import aletheia.agents.prediction_observer as module

    monkeypatch.setattr(module, "load_config", lambda: SimpleNamespace(
        market_data=SimpleNamespace(prediction_enabled=False, observed_prediction_markets=["crypto"]),
    ))
    monkeypatch.setattr(module, "is_tool_permitted", lambda *_args: True)

    result = await module.run_prediction_observer(permission_mode="Observe")

    assert result["errors"] == ["prediction_observer_disabled"]
    assert result["outputs"] == []

"""API/CLI regression checks retained from the original MVP acceptance suite.

The old implementation-guide checklist is archived; these tests cover current
storage, read models, review coverage, permissions and runtime interfaces.
"""

import json
import asyncio
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from typer.testing import CliRunner

from aletheia.server.app import app
from aletheia.main import app as cli_app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo

runner = CliRunner()


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("exchange:\n  paper_execution_mode: bitget_demo\n", encoding="utf-8")
    monkeypatch.setattr(loader, "CONFIG_PATH", config_path)
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(
        str(tmp_path / "app.db"),
        check_same_thread=False,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, tmp_path
    from aletheia.agent_runtime.job_queue import get_agent_job_manager
    await asyncio.to_thread(get_agent_job_manager().stop)
    reset_db()


async def _wait_for_agent_run(c, run_id: str, timeout_sec: float = 5.0) -> dict:
    """Poll the public job resource after the 202 asynchronous admission response."""

    deadline = asyncio.get_running_loop().time() + timeout_sec
    while asyncio.get_running_loop().time() < deadline:
        response = await c.get(f"/api/agent-runs/{run_id}")
        payload = response.json()
        if payload and payload.get("status") not in {"queued", "running"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"agent run {run_id} did not finish within {timeout_sec}s")


# #3 gateway status/stop/pause/resume works (CLI)
def test_ac03_gateway_status_cli(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    r = runner.invoke(cli_app, ["gateway", "status"])
    assert r.exit_code == 1
    assert "stopped" in r.output


# #4 SQLite + JSONL + Markdown storage initialized
@pytest.mark.anyio
async def test_ac04_storage_initialized(client):
    c, tmp_path = client
    await c.get("/api/status")
    assert (tmp_path / "app.db").exists()  # sqlite
    # audit jsonl after an action
    await c.post("/api/settings/permission-mode", json={"mode": "Observe"})
    assert (tmp_path / "logs" / "audit.jsonl").exists()


# #5 Dashboard pages backed by data endpoints
@pytest.mark.anyio
async def test_ac05_dashboard_data_endpoints(client):
    c, _ = client
    for path in ["/api/agent-runs", "/api/opportunities", "/api/proposals", "/api/trades",
                 "/api/positions", "/api/portfolio", "/api/settings",
                 "/api/audit/events"]:
        assert (await c.get(path)).status_code == 200



# #6 HTTP model provider layer (OpenAI/Anthropic)
@pytest.mark.anyio
async def test_ac06_model_providers(client, monkeypatch):
    from tests.helpers.model_http import mock_model_probe

    mock_model_probe(monkeypatch)
    c, _ = client
    # Providers ship empty now; the model layer supports these types on demand —
    # register them via the API (as a fresh-install operator would through Settings).
    for provider in ("openai_compatible", "anthropic_compatible"):
        created = await c.post("/api/settings/model-profiles", json={
            "profile_id": provider, "type": provider, "display_name": provider,
            "base_url": "http://model.test", "model": "m1",
        })
        assert created.status_code == 201, created.text
    data = (await c.get("/api/model")).json()
    assert set(["openai_compatible", "anthropic_compatible"]).issubset(data["providers"].keys())


# #7 Agent runtime records manual runs
@pytest.mark.anyio
async def test_ac07_agent_creates_artifacts(client, monkeypatch):
    c, _ = client
    first = await c.post("/api/agent-runs/start", json={"agent_name": "portfolio_agent"})
    second = await c.post("/api/agent-runs/start", json={"agent_name": "post_trade_reviewer"})
    assert first.status_code == second.status_code == 202
    await _wait_for_agent_run(c, first.json()["job_id"])
    await _wait_for_agent_run(c, second.json()["job_id"])
    assert len((await c.get("/api/agent-runs")).json()) >= 2


# #8 Knowledge base stores items
@pytest.mark.anyio
async def test_ac08_knowledge_base(client, monkeypatch):
    c, _ = client
    from aletheia.config.schema import ModelSelection

    monkeypatch.setattr(
        "extensions.research.dreaming_agent.get_provider_for_tier",
        lambda _tier: ModelSelection(endpoint_id="test", model_id="test-model"),
    )
    # Mock LLM to return substantive content so quality gate passes.
    async def _fake_llm(system, user, max_tokens=800, **kw):
        return ("Daily dreaming log: market observer ran 3 cycles, research agent produced 2 notes. "
                "Insight: momentum correlated with funding divergence. Focus: validate SOL breakout.", None)
    monkeypatch.setattr("extensions.research.dreaming_agent.llm_complete", _fake_llm)

    response = await c.post("/api/agent-runs/start", json={"agent_name": "dreaming_agent"})
    assert response.status_code == 202
    await _wait_for_agent_run(c, response.json()["job_id"])
    assert len((await c.get("/api/knowledge/dreaming-logs")).json()) >= 1
    assert len((await c.get("/api/knowledge?type=daily_review")).json()) >= 1





# #13 permission changes visible + audited
@pytest.mark.anyio
async def test_ac13_permission_visible_audited(client):
    c, tmp_path = client
    await c.post("/api/settings/permission-mode", json={"mode": "Full"})
    assert (await c.get("/api/status")).json()["permission_mode"] == "Full"
    events = [json.loads(l) for l in (tmp_path / "logs" / "audit.jsonl").read_text().splitlines() if l.strip()]
    assert any(e["event_type"] == "permission_changed" for e in events)


# #15 no live trade unless explicitly unlocked
@pytest.mark.anyio
async def test_ac15_live_locked_by_default(client):
    c, _ = client
    assert (await c.get("/api/status")).json()["live_state"] == "live_disabled"


# #1/#2 helpers exist (gateway start + browser) — smoke that lifespan wiring imports
def test_ac01_lifespan_imports():
    from aletheia.server.app import lifespan, app as fastapi_app
    assert lifespan is not None
    # health route registered
    paths = {r.path for r in fastapi_app.routes}
    assert "/api/health" in paths

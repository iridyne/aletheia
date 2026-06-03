"""§32 MVP acceptance criteria, exercised through the API/CLI (not internals).

Each test maps to one of the 16 acceptance criteria in IMPLEMENTATION_GUIDE.md §32.
"""

import json
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
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("aletheia.config.secrets._get_keyring", lambda: None)
    monkeypatch.setattr("aletheia.config.secrets.get_data_dir", lambda: tmp_path)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, tmp_path
    reset_db()


# #3 gateway status/stop/pause/resume works (CLI)
def test_ac03_gateway_status_cli(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    r = runner.invoke(cli_app, ["gateway", "status"])
    assert r.exit_code == 0
    assert "stopped" in r.output


# #4 SQLite + JSONL + Markdown storage initialized
@pytest.mark.anyio
async def test_ac04_storage_initialized(client):
    c, tmp_path = client
    await c.get("/api/status")
    assert (tmp_path / "test.db").exists()  # sqlite
    # audit jsonl after an action
    await c.post("/api/settings/permission-mode", json={"mode": "Research"})
    assert (tmp_path / "logs" / "audit.jsonl").exists()


# #5 Dashboard pages backed by data endpoints
@pytest.mark.anyio
async def test_ac05_dashboard_data_endpoints(client):
    c, _ = client
    for path in ["/api/agent-runs", "/api/opportunities", "/api/proposals", "/api/trades",
                 "/api/positions", "/api/strategies", "/api/portfolio", "/api/settings"]:
        assert (await c.get(path)).status_code == 200


# #6 Model provider layer (OpenAI/Anthropic/Codex)
@pytest.mark.anyio
async def test_ac06_model_providers(client):
    c, _ = client
    data = (await c.get("/api/model")).json()
    assert set(["openai_compatible", "anthropic_compatible", "codex_cli"]).issubset(data["providers"].keys())


# #7 Agent runtime creates research notes + opportunities
@pytest.mark.anyio
async def test_ac07_agent_creates_artifacts(client):
    c, _ = client
    await c.post("/api/agent-runs/start", json={"agent_name": "research_agent"})
    await c.post("/api/agent-runs/start", json={"agent_name": "market_observer"})
    assert len((await c.get("/api/agent-runs")).json()) >= 2
    assert len(repo.list_knowledge_items(item_type="research_note")) >= 1
    assert len((await c.get("/api/opportunities")).json()) >= 1


# #8 Knowledge base stores items
@pytest.mark.anyio
async def test_ac08_knowledge_base(client):
    c, _ = client
    await c.post("/api/agent-runs/start", json={"agent_name": "dreaming_agent"})
    assert len((await c.get("/api/knowledge/dreaming-logs")).json()) >= 1


# #11 paper proposal -> risk -> order -> fill -> position
@pytest.mark.anyio
async def test_ac11_paper_trade_flow(client):
    c, _ = client
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "PaperAuto"; save_config(cfg)
    from aletheia.storage.models import TradeProposal
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      quantity=0.01, price=60000, execution_mode="paper_internal")
    repo.insert_trade_proposal(p)
    resp = await c.post(f"/api/proposals/{p.id}/approve-paper")
    assert resp.json()["execution"]["status"] == "executed"
    assert len(repo.list_orders()) == 1
    assert len(repo.list_fills()) == 1
    assert len(repo.list_positions(status="open")) == 1


# #12 Risk engine blocks disallowed symbols + live by default
@pytest.mark.anyio
async def test_ac12_risk_blocks(client):
    c, _ = client
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "PaperAuto"; save_config(cfg)
    from aletheia.execution.gateway import submit_proposal
    bad = submit_proposal(symbol="DOGEUSDT", market_type="spot", direction="long",
                          quantity=1, price=1, execution_mode="paper_internal")
    assert bad["status"] == "rejected"


# #13 permission changes visible + audited
@pytest.mark.anyio
async def test_ac13_permission_visible_audited(client):
    c, tmp_path = client
    await c.post("/api/settings/permission-mode", json={"mode": "Auto"})
    assert (await c.get("/api/status")).json()["permission_mode"] == "Auto"
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

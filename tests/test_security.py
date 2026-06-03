"""Security tests (guide §28.3).

Invariants:
- Secrets never appear in logs, agent context, KB, or GET responses.
- Agent cannot invoke restricted tools.
- Live-disabled blocks live execution.
- Risk hard limits cannot be modified by the agent.
- Dashboard permission/live changes write audit events.
"""

import json
import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo


def _isolate(tmp_path, monkeypatch):
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


@pytest.fixture
async def client(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, tmp_path
    reset_db()


# --- Restricted tools ---

def test_agent_cannot_invoke_restricted_tools():
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agent_runtime.permissions import is_tool_permitted
    init_tool_registry()
    restricted = ["unlock_live", "place_live_order", "change_hard_risk_limits",
                  "modify_credentials", "modify_execution_gateway"]
    for mode in ["ReadOnly", "Research", "PaperAuto", "AllowEditing", "Auto"]:
        for tool in restricted:
            assert not is_tool_permitted(tool, mode), f"{tool} must never be permitted ({mode})"


# --- Live disabled blocks live execution ---

def test_live_disabled_blocks_live_execution(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Auto"; save_config(cfg)
    from aletheia.execution.gateway import submit_proposal
    result = submit_proposal(symbol="BTCUSDT", market_type="spot", direction="long",
                             quantity=0.001, price=60000, execution_mode="bitget_live_limited")
    assert result["status"] == "rejected"
    assert "live_disabled" in str(result.get("risk_violations", []))
    reset_db()


# --- Risk hard limits unmodifiable by agent ---

def test_agent_has_no_tool_to_change_risk_limits():
    from aletheia.agent_runtime.tool_registry import init_tool_registry, RESTRICTED_TOOLS
    init_tool_registry()
    assert "change_hard_risk_limits" in RESTRICTED_TOOLS


# --- Secrets never in GET responses ---

@pytest.mark.anyio
async def test_secret_never_in_model_get(client):
    c, _ = client
    secret = "sk-LEAKCHECK-CONFIDENTIAL-0001"
    await c.post("/api/settings/model", json={"provider": "openai_compatible", "api_key": secret})
    for path in ["/api/model", "/api/settings", "/api/status"]:
        resp = await c.get(path)
        assert secret not in resp.text, f"secret leaked in {path}"


@pytest.mark.anyio
async def test_bitget_secret_never_in_get(client):
    c, _ = client
    secret = "bg-LEAKCHECK-2222"
    await c.post("/api/settings/exchange-credentials", json={"profile": "demo", "api_key": secret})
    resp = await c.get("/api/bitget/credentials-status")
    assert secret not in resp.text


# --- Secrets not written to config.yaml or logs ---

@pytest.mark.anyio
async def test_secret_not_in_config_or_logs(client):
    c, tmp_path = client
    secret = "sk-NOTINFILES-3333"
    await c.post("/api/settings/model", json={"provider": "openai_compatible", "api_key": secret})
    cfg_file = tmp_path / "config.yaml"
    if cfg_file.exists():
        assert secret not in cfg_file.read_text(encoding="utf-8")
    audit = tmp_path / "logs" / "audit.jsonl"
    if audit.exists():
        assert secret not in audit.read_text(encoding="utf-8")


# --- Dashboard permission/live changes are audited ---

@pytest.mark.anyio
async def test_permission_and_live_changes_audited(client):
    c, tmp_path = client
    await c.post("/api/settings/permission-mode", json={"mode": "PaperAuto"})
    await c.post("/api/settings/live-unlock", json={"confirm": True})
    audit = tmp_path / "logs" / "audit.jsonl"
    assert audit.exists()
    events = [json.loads(l) for l in audit.read_text(encoding="utf-8").splitlines() if l.strip()]
    types = {e["event_type"] for e in events}
    assert "permission_changed" in types
    assert "live_unlocked" in types


@pytest.mark.anyio
async def test_live_unlock_requires_confirm(client):
    c, _ = client
    resp = await c.post("/api/settings/live-unlock", json={"confirm": False})
    assert resp.status_code == 400

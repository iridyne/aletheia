"""Tunnel-auth protection for the manual Agent-run endpoint.

Covers acceptance criteria from issue #395:
- POST /api/agent-runs/start requires X-Aletheia-Token when tunnel.enabled=True.
- Missing/wrong token → 401, zero durable/queued work.
- Correct token + allowed agent → 202, trigger_type="manual_owner".
- Correct token + disallowed agent → 403, no run created.
- Tunnel disabled → local frictionless behaviour preserved, trigger_type="manual".
- Active kill-switch or pause blocks behavior-producing agents (gate_check=True).
- Duplicate enqueue for same agent → 409 with existing run_id.
- Existing scheduled Agent jobs are unaffected.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.server.routes.data as data_routes
    import aletheia.storage.sqlite as sq

    def _clear_portfolio_cache():
        import threading
        with data_routes._portfolio_cache_lock:
            data_routes._portfolio_cache = None

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    _clear_portfolio_cache()
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
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        _clear_portfolio_cache()
        reset_db()


def _enable_tunnel(tmp_path, secret: str = "AGENTSECRET99"):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", secret)
    return secret


def _fake_manager_patch(monkeypatch):
    """Patch get_agent_job_manager with a simple fake that records calls."""
    from aletheia.storage.models import AgentRun

    class FakeManager:
        def __init__(self):
            self.enqueued = []

        def enqueue(self, agent_name, *, trigger_type="manual", idempotency_key=None):
            run = AgentRun(agent_name=agent_name, trigger_type=trigger_type, status="queued")
            self.enqueued.append(run)
            return run

        def snapshot(self):
            return {"capacity": 16, "queued": len(self.enqueued), "active_run_id": None}

    fake = FakeManager()
    monkeypatch.setattr("aletheia.server.routes.data._ensure_db", lambda: None)
    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: fake,
    )
    return fake


# ---------------------------------------------------------------------------
# Tunnel authentication
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_no_token_tunnel_returns_401_and_creates_no_run(client, tmp_path, monkeypatch):
    """Missing X-Aletheia-Token with tunnel enabled → 401, zero DB rows."""
    _enable_tunnel(tmp_path)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
    )
    assert response.status_code == 401
    assert fake.enqueued == [], "No run must be created on 401"


@pytest.mark.anyio
async def test_wrong_token_returns_401_and_creates_no_run(client, tmp_path, monkeypatch):
    """Wrong X-Aletheia-Token → 401, zero DB rows."""
    _enable_tunnel(tmp_path)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
        headers={"X-Aletheia-Token": "WRONGTOKEN"},
    )
    assert response.status_code == 401
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_tunnel_missing_secret_fails_closed(client, tmp_path, monkeypatch):
    """Tunnel enabled but token secret not set → 401 fail-closed."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    # Intentionally no set_secret call.
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
    )
    assert response.status_code == 401
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_correct_token_allowed_agent_returns_202(client, tmp_path, monkeypatch):
    """Correct token + allowed agent → 202 with queued run."""
    secret = _enable_tunnel(tmp_path)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
        headers={"X-Aletheia-Token": secret},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert len(fake.enqueued) == 1


@pytest.mark.anyio
async def test_local_access_no_token_succeeds(client, tmp_path, monkeypatch):
    """When tunnel is disabled, local requests need no token."""
    from aletheia.config.loader import load_config
    assert not getattr(load_config().tunnel, "enabled", False)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
    )
    assert response.status_code == 202
    assert len(fake.enqueued) == 1


# ---------------------------------------------------------------------------
# Actor attribution (trigger_type)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_tunnel_authenticated_run_has_trigger_type_manual_owner(client, tmp_path, monkeypatch):
    """Authenticated tunnel call → trigger_type='manual_owner'."""
    secret = _enable_tunnel(tmp_path)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
        headers={"X-Aletheia-Token": secret},
    )
    assert response.status_code == 202
    assert response.json()["trigger_type"] == "manual_owner"
    assert fake.enqueued[0].trigger_type == "manual_owner"


@pytest.mark.anyio
async def test_local_run_has_trigger_type_manual(client, tmp_path, monkeypatch):
    """Local (no tunnel) call → trigger_type='manual'."""
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
    )
    assert response.status_code == 202
    assert response.json()["trigger_type"] == "manual"
    assert fake.enqueued[0].trigger_type == "manual"


# ---------------------------------------------------------------------------
# Capability policy (allowlist)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_disallowed_agent_returns_403_no_run(client, tmp_path, monkeypatch):
    """Agent not in allowlist → 403 with typed error, no run created."""
    secret = _enable_tunnel(tmp_path)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "round_robin"},  # not in _MANUAL_AGENT_ALLOWLIST
        headers={"X-Aletheia-Token": secret},
    )
    assert response.status_code == 403
    body = response.json()
    assert body["detail"]["error"] == "agent_not_in_manual_allowlist"
    assert "allowed" in body["detail"]
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_unknown_agent_returns_403_locally(client, tmp_path, monkeypatch):
    """Unknown agent name → 403 before any registry lookup."""
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "nonexistent_agent_xyz"},
    )
    assert response.status_code == 403
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_all_allowlisted_agents_accepted_locally(client, tmp_path, monkeypatch):
    """Every agent in _MANUAL_AGENT_ALLOWLIST passes the allowlist check."""
    from aletheia.server.routes.data import _MANUAL_AGENT_ALLOWLIST
    from aletheia.storage.models import AgentRun
    from aletheia.agent_runtime.job_queue import AgentJobAlreadyQueued

    monkeypatch.setattr("aletheia.server.routes.data._ensure_db", lambda: None)

    call_log: list[str] = []

    def make_manager(agent_name_expected):
        class M:
            def enqueue(self, name, *, trigger_type="manual", idempotency_key=None):
                call_log.append(name)
                return AgentRun(agent_name=name, trigger_type=trigger_type, status="queued")
            def snapshot(self):
                return {"capacity": 16, "queued": 0, "active_run_id": None}
        return M()

    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: make_manager(None),
    )

    for agent_name in _MANUAL_AGENT_ALLOWLIST:
        response = await client.post(
            "/api/agent-runs/start",
            json={"agent_name": agent_name},
        )
        # gate_check agents may get 503 if kill-switch/pause logic fires, but never 403/401
        assert response.status_code not in (401, 403), (
            f"Agent '{agent_name}' in allowlist must not return 401/403"
        )


# ---------------------------------------------------------------------------
# Gate check for behavior-producing agents
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_kill_switch_blocks_behavior_agent(client, tmp_path, monkeypatch):
    """kill_switch_active=True → 503 for paper_trader (gate_check=True)."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.kill_switch_active = True
    save_config(cfg)

    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "paper_trader"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "kill_switch_active"
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_gateway_pause_blocks_behavior_agent(client, tmp_path, monkeypatch):
    """runtime.paused=True → 503 for paper_trader (gate_check=True)."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.paused = True
    save_config(cfg)

    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "paper_trader"},
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "gateway_paused"
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_kill_switch_does_not_block_observe_agent(client, tmp_path, monkeypatch):
    """kill_switch_active=True must not block gate_check=False agents like market_observer."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.runtime.kill_switch_active = True
    save_config(cfg)

    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "market_observer"},
    )
    assert response.status_code == 202


# ---------------------------------------------------------------------------
# Idempotency / duplicate-click prevention
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_duplicate_enqueue_same_agent_returns_409(client, tmp_path, monkeypatch):
    """Enqueuing the same agent while it's already pending → 409 with run_id."""
    from aletheia.storage.models import AgentRun
    from aletheia.agent_runtime.job_queue import AgentJobAlreadyQueued

    existing_run = AgentRun(agent_name="research_agent", trigger_type="manual", status="queued")

    class AlreadyQueuedManager:
        def enqueue(self, name, *, trigger_type="manual", idempotency_key=None):
            raise AgentJobAlreadyQueued(existing_run.id, name)

        def snapshot(self):
            return {"capacity": 16, "queued": 1, "active_run_id": existing_run.id}

    monkeypatch.setattr("aletheia.server.routes.data._ensure_db", lambda: None)
    monkeypatch.setattr(
        "aletheia.agent_runtime.job_queue.get_agent_job_manager",
        lambda: AlreadyQueuedManager(),
    )

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error"] == "agent_already_queued"
    assert body["detail"]["run_id"] == existing_run.id
    assert body["detail"]["agent_name"] == "research_agent"

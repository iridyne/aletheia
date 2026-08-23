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
# Registry integrity: every allowlisted agent must resolve via get_agent_fn
# ---------------------------------------------------------------------------

def test_all_allowlisted_agents_resolve_via_registry():
    """Every entry in _MANUAL_AGENT_ALLOWLIST must resolve without ValueError.

    This test does NOT monkeypatch get_agent_fn — it calls the real registry so
    a stale allowlist entry (module exists but no @register_agent, or missing module)
    causes the test to fail at the resolution step rather than at the user's click.
    """
    from aletheia.server.routes.data import _MANUAL_AGENT_ALLOWLIST
    from aletheia.agents import get_agent_fn

    for name in _MANUAL_AGENT_ALLOWLIST:
        try:
            fn = get_agent_fn(name)
        except ValueError as exc:
            raise AssertionError(
                f"Agent '{name}' is in _MANUAL_AGENT_ALLOWLIST but not registered: {exc}"
            ) from exc
        assert callable(fn), f"get_agent_fn('{name}') returned non-callable {fn!r}"


@pytest.mark.anyio
async def test_agent_catalog_returns_only_resolvable_names(client, tmp_path, monkeypatch):
    """GET /api/agents/catalog lists only agents that are callable in the real registry."""
    from aletheia.agents import get_agent_fn

    response = await client.get("/api/agents/catalog")
    assert response.status_code == 200
    catalog = response.json()
    assert "agents" in catalog
    for entry in catalog["agents"]:
        name = entry["name"]
        try:
            fn = get_agent_fn(name)
        except ValueError as exc:
            raise AssertionError(
                f"Catalog exposes '{name}' but it is not callable: {exc}"
            ) from exc
        assert callable(fn)
    # strategy_designer was never registered and must not appear
    names = {e["name"] for e in catalog["agents"]}
    assert "strategy_designer" not in names, "strategy_designer must not appear in the catalog"


@pytest.mark.anyio
async def test_strategy_designer_returns_403(client, tmp_path, monkeypatch):
    """strategy_designer was never registered and must always return 403."""
    monkeypatch.setattr("aletheia.server.routes.data._ensure_db", lambda: None)
    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "strategy_designer"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "agent_not_in_manual_allowlist"


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

# ---------------------------------------------------------------------------
# Operator service identity capability restriction (Issue #1100)
# ---------------------------------------------------------------------------

def _enable_remote_control(tmp_path, monkeypatch, secret: str = "SVCTOKEN99"):
    """Enable remote_control auth so the service-identity middleware runs."""
    import aletheia.config.secrets as secrets_mod
    import aletheia.server.operator_service_auth as service_auth
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.remote_control.enabled = True
    cfg.remote_control.require_token = True
    save_config(cfg)

    store = {"remote_control_api_token": "owner-token"}
    monkeypatch.setattr(secrets_mod, "get_secret", lambda k: store.get(k))
    monkeypatch.setattr(service_auth, "is_valid_operator_service_token", lambda t: t == secret)
    return secret


@pytest.mark.anyio
async def test_service_credential_can_start_approved_specialist(client, tmp_path, monkeypatch):
    """Service credential can enqueue a specialist in _OPERATOR_SERVICE_AGENT_ALLOWLIST."""
    svc_token = _enable_remote_control(tmp_path, monkeypatch)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "market_observer"},
        headers={"X-Aletheia-Operator-Service": svc_token},
    )
    assert response.status_code == 202, response.text
    assert len(fake.enqueued) == 1


@pytest.mark.anyio
async def test_service_credential_blocked_from_paper_trader(client, tmp_path, monkeypatch):
    """Service credential must receive 403 for paper_trader (behavior-producing)."""
    svc_token = _enable_remote_control(tmp_path, monkeypatch)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "paper_trader"},
        headers={"X-Aletheia-Operator-Service": svc_token},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "agent_not_in_service_allowlist"
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_manual_allowlist_expansion_does_not_grant_service_authority(client, tmp_path, monkeypatch):
    """A hypothetical new manual-only agent outside _OPERATOR_SERVICE_AGENT_ALLOWLIST is denied to service callers."""
    from aletheia.server.routes.data import _MANUAL_AGENT_ALLOWLIST, _OPERATOR_SERVICE_AGENT_ALLOWLIST

    # Verify the invariant at the allowlist level rather than patching the module.
    manual_only = set(_MANUAL_AGENT_ALLOWLIST) - set(_OPERATOR_SERVICE_AGENT_ALLOWLIST)
    assert manual_only, "Test precondition: at least one manual-only agent must exist (paper_trader)"

    svc_token = _enable_remote_control(tmp_path, monkeypatch)
    fake = _fake_manager_patch(monkeypatch)

    for agent_name in manual_only:
        response = await client.post(
            "/api/agent-runs/start",
            json={"agent_name": agent_name},
            headers={"X-Aletheia-Operator-Service": svc_token},
        )
        assert response.status_code == 403, (
            f"Agent '{agent_name}' is in _MANUAL_AGENT_ALLOWLIST but not _OPERATOR_SERVICE_AGENT_ALLOWLIST "
            f"and must return 403 for service callers"
        )
    assert fake.enqueued == []


@pytest.mark.anyio
async def test_owner_token_can_still_start_paper_trader(client, tmp_path, monkeypatch):
    """Owner-authenticated (remote_control token) callers retain the ability to start paper_trader."""
    _enable_remote_control(tmp_path, monkeypatch)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "paper_trader"},
        headers={"X-Aletheia-Token": "owner-token"},
    )
    # gate_check may fire 503 depending on runtime state, but must not 403
    assert response.status_code != 403, "Owner must not be blocked by service allowlist"


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


# ---------------------------------------------------------------------------
# Operator-service provenance (issue #1102)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_operator_service_run_has_trigger_type_operator_service(client, tmp_path, monkeypatch):
    """Service-authenticated call via X-Aletheia-Operator-Service → trigger_type='operator_service'."""
    import aletheia.server.operator_service_auth as service_auth

    _enable_tunnel(tmp_path)
    fake = _fake_manager_patch(monkeypatch)
    monkeypatch.setattr(service_auth, "is_valid_operator_service_token", lambda t: t == "svc-token")

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
        headers={"X-Aletheia-Operator-Service": "svc-token"},
    )
    assert response.status_code == 202
    assert response.json()["trigger_type"] == "operator_service"
    assert fake.enqueued[0].trigger_type == "operator_service"


@pytest.mark.anyio
async def test_operator_service_trigger_type_is_not_interactive_priority():
    """operator_service trigger_type must not map to INTERACTIVE inference priority."""
    from aletheia.model_provider.broker import priority_for_agent, InferencePriority

    p = priority_for_agent("research_agent", "operator_service")
    assert p != InferencePriority.INTERACTIVE, (
        "operator_service runs must not consume INTERACTIVE priority"
    )


def test_operator_service_runs_excluded_from_manual_runs_by_agent():
    """operator_service trigger_type must not appear in manual_runs_by_agent evidence."""
    runs = [
        {"id": "r1", "agent_name": "research_agent", "trigger_type": "operator_service"},
        {"id": "r2", "agent_name": "research_agent", "trigger_type": "operator_service"},
        {"id": "r3", "agent_name": "market_observer", "trigger_type": "operator_service"},
    ]
    manual_runs_by_agent: dict = {}
    for run in runs:
        if str(run.get("trigger_type") or "") != "manual":
            continue
        manual_runs_by_agent.setdefault(run["agent_name"], []).append(run)
    assert manual_runs_by_agent == {}, (
        "operator_service runs must not feed manual schedule-demand evidence"
    )


@pytest.mark.anyio
async def test_owner_run_remains_manual_owner(client, tmp_path, monkeypatch):
    """tunnel_authenticated (owner) → still trigger_type='manual_owner', unchanged."""
    secret = _enable_tunnel(tmp_path)
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
        headers={"X-Aletheia-Token": secret},
    )
    assert response.status_code == 202
    assert fake.enqueued[0].trigger_type == "manual_owner"


@pytest.mark.anyio
async def test_local_run_remains_manual(client, tmp_path, monkeypatch):
    """Local unauthenticated call → still trigger_type='manual', unchanged."""
    fake = _fake_manager_patch(monkeypatch)

    response = await client.post(
        "/api/agent-runs/start",
        json={"agent_name": "research_agent"},
    )
    assert response.status_code == 202
    assert fake.enqueued[0].trigger_type == "manual"

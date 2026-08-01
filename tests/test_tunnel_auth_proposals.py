"""Tunnel-auth protection for owner-facing Proposal mutation routes.

Covers the acceptance criteria from issue #394:
- POST /api/proposals/{id}/approve-paper and POST /api/proposals/{id}/reject
  require X-Aletheia-Token when tunnel.enabled=True.
- Missing/wrong token → 401, proposal unchanged, no Order/Fill/Position created.
- Correct token → existing approval/rejection semantics work.
- Tunnel disabled → local frictionless behavior preserved.
- Actor source recorded as "owner" on authenticated tunnel calls, "dashboard" locally.
- GET reads are never blocked by the proposal mutation guard.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app
from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import TradeProposal
from aletheia.storage import repositories as repo


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


def _make_pending_proposal() -> TradeProposal:
    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(p)
    return p


def _enable_tunnel(tmp_path, secret: str = "TUNNELSECRET42"):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", secret)
    return secret


# ---------------------------------------------------------------------------
# Enumerate all state-changing Proposal endpoints — the registry that must
# remain complete as the surface grows.
# ---------------------------------------------------------------------------

_PROPOSAL_MUTATION_ROUTES = [
    ("POST", "/api/proposals/{id}/approve-paper"),
    ("POST", "/api/proposals/{id}/reject"),
]

_PROPOSAL_READ_ROUTES = [
    ("GET", "/api/proposals"),
    ("GET", "/api/proposals/{id}"),
    ("GET", "/api/proposals/{id}/risk-decision"),
]


@pytest.mark.anyio
async def test_proposal_mutation_routes_require_tunnel_auth(client, tmp_path):
    """Every state-changing Proposal endpoint must return 401 with no token when tunnel is on."""
    _enable_tunnel(tmp_path)

    for method, route in _PROPOSAL_MUTATION_ROUTES:
        proposal = _make_pending_proposal()
        url = route.replace("{id}", proposal.id)
        response = await client.request(method, url)
        assert response.status_code == 401, (
            f"{method} {route} did not return 401 with no token under tunnel mode"
        )
        stored = repo.get_trade_proposal(proposal.id)
        assert stored["status"] == "pending", (
            f"{method} {route} mutated proposal status despite 401"
        )
        assert repo.list_orders() == [], (
            f"{method} {route} created an Order despite 401"
        )


@pytest.mark.anyio
async def test_proposal_mutation_wrong_token_returns_401_and_no_state_change(client, tmp_path):
    """Wrong X-Aletheia-Token → 401, proposal stays pending, no Order."""
    secret = _enable_tunnel(tmp_path)

    for method, route in _PROPOSAL_MUTATION_ROUTES:
        proposal = _make_pending_proposal()
        url = route.replace("{id}", proposal.id)
        response = await client.request(method, url, headers={"X-Aletheia-Token": "wrong"})
        assert response.status_code == 401, (
            f"{method} {route} with wrong token should return 401"
        )
        stored = repo.get_trade_proposal(proposal.id)
        assert stored["status"] == "pending"
        assert repo.list_orders() == []


@pytest.mark.anyio
async def test_proposal_reject_with_correct_token_succeeds(client, tmp_path):
    """Correct tunnel token → reject succeeds and proposal is marked rejected."""
    secret = _enable_tunnel(tmp_path)
    proposal = _make_pending_proposal()

    response = await client.post(
        f"/api/proposals/{proposal.id}/reject",
        headers={"X-Aletheia-Token": secret},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    stored = repo.get_trade_proposal(proposal.id)
    assert stored["status"] == "rejected"
    assert repo.list_orders() == []


@pytest.mark.anyio
async def test_proposal_approve_no_token_creates_no_order(client, tmp_path):
    """Unauthenticated approve-paper must not create any Order, Fill, or Position."""
    _enable_tunnel(tmp_path)
    proposal = _make_pending_proposal()

    response = await client.post(f"/api/proposals/{proposal.id}/approve-paper")
    assert response.status_code == 401

    assert repo.get_trade_proposal(proposal.id)["status"] == "pending"
    assert repo.list_orders() == []
    assert repo.list_positions(status="open") == []


@pytest.mark.anyio
async def test_proposal_mutations_work_locally_without_token(client, tmp_path):
    """When tunnel is disabled, local requests need no token."""
    # Tunnel is disabled by default; no secrets needed.
    from aletheia.config.loader import load_config
    cfg = load_config()
    assert not getattr(cfg.tunnel, "enabled", False)

    proposal = _make_pending_proposal()
    response = await client.post(f"/api/proposals/{proposal.id}/reject")
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


@pytest.mark.anyio
async def test_proposal_read_routes_not_blocked_by_tunnel_auth(client, tmp_path):
    """GET reads on /api/proposals/* must never require the tunnel token."""
    _enable_tunnel(tmp_path)
    proposal = _make_pending_proposal()

    for method, route in _PROPOSAL_READ_ROUTES:
        url = route.replace("{id}", proposal.id)
        response = await client.request(method, url)
        # 200 or 404 (risk-decision may be absent), but never 401.
        assert response.status_code != 401, (
            f"{method} {route} should not require tunnel auth"
        )


@pytest.mark.anyio
async def test_tunnel_missing_secret_fails_closed(client, tmp_path):
    """Tunnel enabled but no token configured → fail-closed (401) on mutation routes."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    # Intentionally do NOT call set_secret("tunnel_api_token", ...)

    proposal = _make_pending_proposal()
    response = await client.post(f"/api/proposals/{proposal.id}/reject")
    assert response.status_code == 401
    assert repo.get_trade_proposal(proposal.id)["status"] == "pending"


@pytest.mark.anyio
async def test_reject_actor_source_is_owner_for_tunnel_authenticated_calls(client, tmp_path):
    """Authenticated tunnel rejection records source='owner', not 'dashboard'."""
    secret = _enable_tunnel(tmp_path)
    proposal = _make_pending_proposal()

    audit_events: list[dict] = []
    import aletheia.server.routes.data as data_routes
    _real_write_audit = None

    def _capture_audit(event):
        audit_events.append(event)

    # Patch write_audit at the module level used by the route
    import aletheia.files.jsonl as jsonl_mod
    original_write_audit = jsonl_mod.write_audit

    def _capturing_write_audit(event):
        audit_events.append(dict(event))
        return original_write_audit(event)

    import aletheia.files.jsonl as jsonl_mod
    original = jsonl_mod.write_audit
    jsonl_mod.write_audit = _capturing_write_audit
    try:
        response = await client.post(
            f"/api/proposals/{proposal.id}/reject",
            headers={"X-Aletheia-Token": secret},
        )
        assert response.status_code == 200
    finally:
        jsonl_mod.write_audit = original

    rejection_events = [e for e in audit_events if e.get("event_type") == "proposal_rejected"]
    assert rejection_events, "Expected a proposal_rejected audit event"
    assert rejection_events[0]["source"] == "owner", (
        "Tunnel-authenticated rejection must record source='owner', not 'dashboard'"
    )


@pytest.mark.anyio
async def test_reject_actor_source_is_dashboard_for_local_calls(client, tmp_path):
    """Local (no tunnel) rejection records source='dashboard'."""
    audit_events: list[dict] = []

    import aletheia.files.jsonl as jsonl_mod
    original = jsonl_mod.write_audit

    def _capturing_write_audit(event):
        audit_events.append(dict(event))
        return original(event)

    proposal = _make_pending_proposal()
    jsonl_mod.write_audit = _capturing_write_audit
    try:
        response = await client.post(f"/api/proposals/{proposal.id}/reject")
        assert response.status_code == 200
    finally:
        jsonl_mod.write_audit = original

    rejection_events = [e for e in audit_events if e.get("event_type") == "proposal_rejected"]
    assert rejection_events, "Expected a proposal_rejected audit event"
    assert rejection_events[0]["source"] == "dashboard"

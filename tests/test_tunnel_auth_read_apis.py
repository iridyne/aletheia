"""Tunnel-auth default-deny protection for private read APIs.

Covers the acceptance criteria from issue #398:
- When tunnel.enabled=True, all /api/* and /_infra/* routes except _PUBLIC_PATHS
  require X-Aletheia-Token (reads are as sensitive as mutations).
- _PUBLIC_PATHS (/api/ready, /_infra/livez, /_infra/readyz) remain accessible
  anonymously — minimal liveness payloads, no private state.
- Correct token grants access to previously-blocked read routes.
- Tunnel disabled → local frictionless access preserved; no token needed.
- Missing tunnel_api_token secret with tunnel enabled → fail-closed (401).
"""

import pytest
from httpx import AsyncClient, ASGITransport

from aletheia.server.app import app, _PUBLIC_PATHS
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


def _enable_tunnel(tmp_path, secret: str = "READAPISECRET77"):
    from aletheia.config.loader import load_config, save_config
    from aletheia.config.secrets import set_secret
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    set_secret("tunnel_api_token", secret)
    return secret


# ---------------------------------------------------------------------------
# Sensitive read endpoints that must be blocked anonymously under tunnel mode
# ---------------------------------------------------------------------------

# A representative sample from each sensitive category named in issue #398.
# Not exhaustive: the default-deny policy covers the full /api/* surface.
_PRIVATE_READ_ROUTES = [
    # Trading intent and execution
    ("GET", "/api/proposals"),
    # Agent and model work
    ("GET", "/api/agent-runs"),
    # Operational diagnostics
    ("GET", "/_infra/doctor"),
    ("GET", "/_infra/status"),
    ("GET", "/api/status"),
    ("GET", "/api/health"),
    ("GET", "/api/stability"),
    ("GET", "/api/version"),
    # Research/memory (infra events)
    ("GET", "/api/infra/events"),
]

_PUBLIC_LIVENESS_ROUTES = [
    ("GET", "/api/ready"),
    ("GET", "/_infra/livez"),
    ("GET", "/_infra/readyz"),
]


@pytest.mark.anyio
async def test_private_reads_blocked_anonymously_under_tunnel(client, tmp_path):
    """Anonymous callers must receive 401 for all private read routes when tunnel is on."""
    _enable_tunnel(tmp_path)

    for method, path in _PRIVATE_READ_ROUTES:
        response = await client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} must return 401 for anonymous caller under tunnel mode (issue #398)"
        )


@pytest.mark.anyio
async def test_public_liveness_routes_accessible_anonymously_under_tunnel(client, tmp_path):
    """_PUBLIC_PATHS liveness endpoints must remain accessible without token under tunnel mode."""
    _enable_tunnel(tmp_path)

    for method, path in _PUBLIC_LIVENESS_ROUTES:
        response = await client.request(method, path)
        assert response.status_code != 401, (
            f"{method} {path} is a public liveness endpoint and must not return 401 "
            "under tunnel mode"
        )


@pytest.mark.anyio
async def test_private_reads_accessible_with_correct_token(client, tmp_path):
    """Private read routes must succeed (not 401) when the correct tunnel token is provided."""
    secret = _enable_tunnel(tmp_path)
    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000,
        execution_mode="paper_internal",
    )
    repo.insert_trade_proposal(proposal)

    for method, path in _PRIVATE_READ_ROUTES:
        response = await client.request(method, path, headers={"X-Aletheia-Token": secret})
        assert response.status_code != 401, (
            f"{method} {path} with correct token must not return 401"
        )


@pytest.mark.anyio
async def test_private_reads_blocked_with_wrong_token(client, tmp_path):
    """Wrong tunnel token → 401 for private read routes."""
    _enable_tunnel(tmp_path)

    for method, path in _PRIVATE_READ_ROUTES:
        response = await client.request(method, path, headers={"X-Aletheia-Token": "WRONGTOKEN"})
        assert response.status_code == 401, (
            f"{method} {path} with wrong token must return 401"
        )


@pytest.mark.anyio
async def test_private_reads_accessible_locally_without_tunnel(client, tmp_path):
    """When tunnel is disabled, all read routes are accessible without any token."""
    from aletheia.config.loader import load_config
    assert not getattr(load_config().tunnel, "enabled", False)

    for method, path in _PRIVATE_READ_ROUTES:
        response = await client.request(method, path)
        # No 401 — local access is frictionless regardless of endpoint sensitivity.
        assert response.status_code != 401, (
            f"{method} {path} must not return 401 when tunnel is disabled (local access)"
        )


@pytest.mark.anyio
async def test_missing_secret_fails_closed_for_reads(client, tmp_path):
    """Tunnel enabled but no token configured → fail-closed 401 for private reads."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.tunnel.enabled = True
    save_config(cfg)
    # Intentionally do NOT call set_secret("tunnel_api_token", ...)

    for method, path in _PRIVATE_READ_ROUTES:
        response = await client.request(method, path)
        assert response.status_code == 401, (
            f"{method} {path} must return 401 when tunnel is on but token is unconfigured"
        )


@pytest.mark.anyio
async def test_public_paths_constant_contains_only_liveness(client, tmp_path):
    """_PUBLIC_PATHS must contain only minimal liveness endpoints with no private state."""
    allowed = {"/api/ready", "/_infra/livez", "/_infra/readyz"}
    extra = _PUBLIC_PATHS - allowed
    assert not extra, (
        f"_PUBLIC_PATHS contains unexpected entries: {extra!r}. "
        "Public endpoints must be minimal liveness probes only (issue #398)."
    )

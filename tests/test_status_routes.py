import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient

from aletheia.server.app import app
from aletheia.storage.migrations import run_migrations
from aletheia.storage.sqlite import reset_db


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    reset_db()


@pytest.mark.anyio
async def test_ready_is_lightweight_liveness(client):
    resp = await client.get("/api/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["pid"] > 0
    assert "version" in data


@pytest.mark.anyio
async def test_infra_livez_is_lightweight_liveness(client):
    resp = await client.get("/_infra/livez")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["pid"] > 0
    assert data["uptime_sec"] >= 0
    assert "version" in data


@pytest.mark.anyio
async def test_infra_readyz_reports_static_dashboard_gap(client, tmp_path, monkeypatch):
    import aletheia.server.routes.health as health_routes

    missing_static = tmp_path / "missing-dashboard-static"
    monkeypatch.setattr(health_routes, "_dashboard_static_dir", lambda: missing_static)

    resp = await client.get("/_infra/readyz")
    assert resp.status_code == 503
    data = resp.json()
    assert data["ok"] is False
    assert data["api"] is True
    assert data["static_index"] is False
    assert data["static_dir"] == str(missing_static)


@pytest.mark.anyio
async def test_infra_status_includes_runtime_routing_context(client, monkeypatch):
    # dashboard_static is a gitignored build artifact (d728aa4); a fresh test env has no
    # built assets, so readiness would 503. This test checks the infra-status PAYLOAD
    # (host/port/runtime_mode/state), not the presence of built front-end assets, so stub
    # static-ready. The static-missing → 503 path is covered by
    # test_infra_readyz_reports_static_dashboard_gap above.
    import aletheia.server.routes.health as health_routes
    monkeypatch.setattr(health_routes, "_static_dashboard_ready", lambda: (True, "/fake/dashboard_static"))
    resp = await client.get("/_infra/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["api"] is True
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 8787
    assert data["runtime_mode"] == "current"
    assert data["runtime_state"] == "current"


@pytest.mark.anyio
async def test_status_reports_actual_scheduler_and_health(client, monkeypatch):
    import aletheia.agent_runtime.scheduler as scheduler
    import aletheia.agent_runtime.health as health
    import aletheia.agent_runtime.build_info as build_info
    import aletheia.update.git_ops as git_ops
    import aletheia.server.routes.health as health_routes

    monkeypatch.setattr(health_routes, "_cached_head_commit", lambda ttl_sec=60.0: "new")
    monkeypatch.setattr(
        scheduler,
        "status_snapshot",
        lambda: {"running": False, "state": "not_started", "job_count": 0},
    )
    monkeypatch.setattr(
        health,
        "compute_health",
        lambda: {"healthy": False, "issues": ["code_updated_pending_restart:old..new"]},
    )
    monkeypatch.setattr(build_info, "get_boot_commit", lambda: "old")
    monkeypatch.setattr(git_ops, "current_commit", lambda: "new")

    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["gateway_running"] is True
    assert data["scheduler_running"] is False
    assert data["scheduler_state"] == "not_started"
    assert data["health_status"] == "degraded"
    assert data["health_issues"] == ["code_updated_pending_restart:old..new"]
    assert data["boot_commit"] == "old"
    assert data["current_commit"] == "new"
    assert data["model_provider"]


@pytest.mark.anyio
async def test_status_reports_local_paper_without_exchange_connection(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.name = "okx"  # Retained preference must not imply a connection.
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["execution_target"] == "local_paper"
    assert data["uses_external_exchange"] is False
    assert data["paper_execution_mode"] == "paper_internal"
    assert data["exchange_alignment"] == "local_paper"
    assert data["execution_exchange"] is None


@pytest.mark.anyio
async def test_status_reports_execution_and_data_exchange_divergence(client):
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.exchange.name = "bitget"
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.market_data.ccxt_exchange = "binance"
    save_config(cfg)

    resp = await client.get("/api/status")
    data = resp.json()
    assert data["execution_exchange"] == "okx"
    assert data["market_data_exchange"] == "binance"
    assert data["exchange_alignment"] == "diverged"
    assert data["exchange_diverged"] is True

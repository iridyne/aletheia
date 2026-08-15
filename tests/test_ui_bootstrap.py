from datetime import datetime, timedelta, timezone
import sqlite3
from types import SimpleNamespace

from fastapi import Response
import pytest
from httpx import ASGITransport, AsyncClient


def test_shell_bootstrap_is_compact_local_and_no_store():
    from aletheia.server.routes.ui import shell_bootstrap

    response = Response()
    payload = shell_bootstrap(response)

    assert payload["schema_version"] == "ui-shell-v1"
    assert payload["generated_at"].endswith("Z")
    assert response.headers["cache-control"] == "no-store"
    assert "local;dur=" in response.headers["server-timing"]
    assert "proposals" not in payload
    assert "equity_history" not in payload
    assert "credentials" not in payload
    assert "exchange_diverged" in payload["exchange"]
    assert len(str(payload)) < 20_000


def test_bootstrap_failure_isolation_keeps_schema(monkeypatch):
    import aletheia.server.routes.ui as ui

    def fail_ticker():
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(ui, "_ticker_snapshot", fail_ticker)
    response = Response()
    payload = ui.shell_bootstrap(response)

    assert payload["schema_version"] == "ui-shell-v1"
    assert payload["ticker"] == {}
    assert response.headers["cache-control"] == "no-store"


def test_risk_snapshot_stays_not_evaluated_without_a_decision(monkeypatch):
    import aletheia.server.routes.ui as ui

    cfg = SimpleNamespace(
        runtime=SimpleNamespace(kill_switch_active=False, live_brake_active=False, demo_brake_active=False),
        exchange=SimpleNamespace(mode="paper"),
        live=SimpleNamespace(state="paper"),
        agent=SimpleNamespace(permission_mode="Paper"),
    )
    monkeypatch.setattr(ui.repo, "list_risk_decisions", lambda limit=1: [])
    assert ui._risk_snapshot(cfg)["status"] == "not_evaluated"
    monkeypatch.setattr(ui.repo, "list_risk_decisions", lambda limit=1: [{"id": "decision-1"}])
    assert ui._risk_snapshot(cfg)["status"] == "healthy"


def test_count_summary_normalizes_iso_run_timestamps(monkeypatch):
    import aletheia.server.routes.ui as ui

    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        "CREATE TABLE trade_proposals(status TEXT);"
        "CREATE TABLE opportunities(status TEXT);"
        "CREATE TABLE agent_runs(started_at TEXT);"
    )
    now = datetime.now(timezone.utc)
    db.executemany("INSERT INTO trade_proposals(status) VALUES (?)", [("pending",), ("approved",)])
    db.executemany("INSERT INTO opportunities(status) VALUES (?)", [("open",), ("closed",)])
    db.executemany(
        "INSERT INTO agent_runs(started_at) VALUES (?)",
        [
            ((now - timedelta(hours=23)).isoformat().replace("+00:00", "Z"),),
            ((now - timedelta(hours=25)).isoformat().replace("+00:00", "Z"),),
        ],
    )
    monkeypatch.setattr(ui, "get_db", lambda: db)
    assert ui._count_summary() == {"pending_proposals": 1, "open_opportunities": 1, "runs_24h": 1}


def test_model_setup_uses_configured_credential_channels(monkeypatch):
    import aletheia.server.routes.ui as ui

    provider = SimpleNamespace(
        type="openai_compatible",
        api_key_secret_ref="provider-key",
        oauth_token_ref=None,
        command="",
    )
    cfg = SimpleNamespace(models=SimpleNamespace(providers={"default": provider}))
    monkeypatch.setattr(ui, "get_secret", lambda ref: "configured" if ref == "provider-key" else None)
    assert ui._model_provider_usable(cfg) is True


def test_overview_bootstrap_projects_activity_and_account_only():
    from aletheia.server.routes.ui import overview_bootstrap

    response = Response()
    payload = overview_bootstrap(response)

    assert payload["schema_version"] == "ui-overview-v1"
    assert isinstance(payload["activity"], list)
    assert "summary" in payload
    assert "account" in payload
    assert response.headers["cache-control"] == "no-store"
    assert "full_proposals" not in payload


@pytest.mark.anyio
async def test_app_bootstrap_uses_no_store_and_scoped_json_compression():
    from aletheia.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/ui/shell-bootstrap", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/json")
    assert len(response.content) < 100_000
    if len(response.content) >= 1024:
        assert response.headers["content-encoding"] == "gzip"

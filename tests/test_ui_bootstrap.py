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


@pytest.mark.parametrize("reachable,age,recovered,state", [
    (True, 0, False, "reachable"),
    (False, 0, False, "unavailable"),
    (True, 100000, False, "unknown"),
    (None, 0, False, "unknown"),
    (True, 0, True, "recovering"),
])
def test_shell_connectivity_uses_cached_probe(monkeypatch, reachable, age, recovered, state):
    import time
    from aletheia.agent_runtime import connectivity
    from aletheia.server.routes.ui import shell_bootstrap

    monkeypatch.setattr(connectivity, "_last_external_probe_result", None if reachable is None else {"reachable": reachable, "ts": time.time() - age})
    monkeypatch.setattr(connectivity, "_external_last_recovery_at", time.time() if recovered else None)
    projection = shell_bootstrap(Response())["connectivity"]
    assert projection["state"] == state
    assert projection["reason"]


def test_shell_connectivity_failure_is_isolated(monkeypatch):
    from aletheia.agent_runtime import connectivity
    from aletheia.server.routes.ui import shell_bootstrap

    def fail():
        raise RuntimeError("monitor unavailable")

    monkeypatch.setattr(connectivity, "get_status", fail)
    payload = shell_bootstrap(Response())
    assert payload["connectivity"] == {"state": "unknown", "reason": "connectivity:RuntimeError"}
    assert payload["gateway"]["running"] is True


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
    monkeypatch.setattr(
        ui.repo,
        "get_entry_workbench_summary",
        lambda: {"active_entry_plans": 2, "needs_planning": 3, "expiring_soon": 1},
    )
    assert ui._count_summary() == {
        "active_entry_plans": 2,
        "needs_planning": 3,
        "expiring_soon": 1,
        "runs_24h": 1,
    }


def test_model_setup_uses_configured_credential_channels(monkeypatch):
    import aletheia.server.routes.ui as ui
    from extensions.model_endpoints import http_runtime
    from aletheia.model_provider import router

    provider = SimpleNamespace(
        type="openai_compatible",
        api_key_secret_ref="provider-key",
        oauth_token_ref=None,
        command="",
    )
    cfg = SimpleNamespace(models=SimpleNamespace(providers={"default": provider}))
    monkeypatch.setattr(http_runtime, "get_secret", lambda ref, **kwargs: "configured" if ref == "provider-key" else None)
    monkeypatch.setattr(router, "_provider_status_reader", http_runtime.http_credential_status)
    assert ui._model_credential_configured(cfg) is True


@pytest.mark.parametrize("configured,expected", [(True, False), (False, True), (None, None)])
def test_overview_setup_preserves_unknown_credential_status(monkeypatch, configured, expected):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.server.routes import ui

    monkeypatch.setattr("aletheia.analytics.execution_mode.resolve_selected_execution_mode", lambda: "bitget_demo")
    monkeypatch.setattr("aletheia.portfolio.pnl.compute_portfolio", lambda **_kwargs: {})
    monkeypatch.setattr(ui, "load_config", lambda **_kwargs: AletheiaConfig())
    monkeypatch.setattr(ui, "_model_credential_configured", lambda _cfg: configured)
    payload = ui.overview_bootstrap(Response())
    assert payload["setup_required"] is expected
    if configured is None:
        assert payload["errors"]["setup"] == "model_credential_status_unavailable"


def test_overview_bootstrap_projects_activity_and_account_only(monkeypatch):
    from aletheia.server.routes.ui import overview_bootstrap

    monkeypatch.setattr("aletheia.analytics.execution_mode.resolve_selected_execution_mode", lambda: "bitget_demo")
    response = Response()
    payload = overview_bootstrap(response)

    assert payload["schema_version"] == "ui-overview-v1"
    assert isinstance(payload["activity"], list)
    assert "summary" in payload
    assert "account" in payload
    assert response.headers["cache-control"] == "no-store"
    assert "full_proposals" not in payload


def test_overview_bootstrap_projects_unified_account_metadata(monkeypatch):
    import aletheia.analytics.execution_mode as execution_mode
    import aletheia.portfolio.pnl as pnl
    import aletheia.server.routes.ui as ui

    monkeypatch.setattr(execution_mode, "resolve_selected_execution_mode", lambda: "okx_demo")
    monkeypatch.setattr(ui, "_count_summary", lambda: {"pending_proposals": 0, "open_opportunities": 0, "runs_24h": 0})
    monkeypatch.setattr(ui, "_compact_activity", lambda: [])
    monkeypatch.setattr(ui, "_model_credential_configured", lambda _cfg: True)
    monkeypatch.setattr(ui, "load_config", lambda persist_migrations=False: SimpleNamespace(exchange=SimpleNamespace(mode="demo")))
    monkeypatch.setattr(
        pnl,
        "compute_portfolio",
        lambda execution_mode: {
            "total_value_usdt": 123.45,
            "exchange_sync_status": "ok",
            "exchange_equity_usdt": 123.45,
            "exchange_state_age_sec": 1.0,
            "uta_state": {
                "sync_status": "ok",
                "sync_complete": True,
                "connected_brokers": ["okx_demo"],
            },
        },
    )

    payload = ui.overview_bootstrap(Response())
    account = payload["account"]

    assert account["uta_state"]["connected_brokers"] == ["okx_demo"]
    assert account["uta_state"]["sync_status"] == "ok"
    assert account["exchange_equity_usdt"] == 123.45
    assert account["execution_mode"] == "okx_demo"


def test_overview_bootstrap_fails_closed_before_current_account_reads(monkeypatch):
    import aletheia.analytics.execution_mode as execution_mode
    import aletheia.portfolio.pnl as pnl
    import aletheia.server.routes.ui as ui

    monkeypatch.setattr(execution_mode, "resolve_selected_execution_mode", lambda: None)

    def unexpected_portfolio_read(*_args, **_kwargs):
        raise AssertionError("unresolved mode must not read an unscoped portfolio")

    monkeypatch.setattr(pnl, "compute_portfolio", unexpected_portfolio_read)
    response = Response()
    payload = ui.overview_bootstrap(response)

    assert payload["availability"] == "unavailable"
    assert payload["reason"] == "execution_mode_unresolved"
    assert payload["account"]["execution_mode_status"] == "unavailable"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [True, False, None])
async def test_shell_bootstrap_preserves_unknown_scheduler_state(monkeypatch, enabled):
    from aletheia.agent_runtime import scheduler
    from aletheia.server.app import app

    def status():
        if enabled is None:
            raise RuntimeError("synthetic diagnostic detail must not escape")
        return {"enabled": enabled, "running": enabled, "state": "running" if enabled else "disabled"}

    monkeypatch.setattr(scheduler, "status_snapshot", status)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/ui/shell-bootstrap")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    result = response.json()["scheduler"]
    assert result["enabled"] is enabled
    assert result["running"] is bool(enabled)
    assert result["state"] == ("unavailable:RuntimeError" if enabled is None else "running" if enabled else "disabled")
    assert "synthetic diagnostic detail" not in response.text


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

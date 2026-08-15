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

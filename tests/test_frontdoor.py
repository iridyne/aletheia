from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _static_fixture(tmp_path: Path) -> Path:
    static_dir = tmp_path / "dashboard_static"
    assets = static_dir / "assets"
    assets.mkdir(parents=True)
    (static_dir / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>ok</div></body></html>",
        encoding="utf-8",
    )
    (assets / "index-test.js").write_text("console.log('ok')", encoding="utf-8")
    return static_dir


@pytest.mark.anyio
async def test_frontdoor_serves_static_without_backend(tmp_path):
    from aletheia.gateway.frontdoor import create_app

    app = create_app(static_dir=_static_fixture(tmp_path), backend_url="http://127.0.0.1:9")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://frontdoor") as client:
        resp = await client.get("/")

    assert resp.status_code == 200
    assert "root" in resp.text


@pytest.mark.anyio
async def test_frontdoor_infra_status_stays_available_when_backend_down(tmp_path):
    from aletheia.gateway.frontdoor import create_app

    app = create_app(static_dir=_static_fixture(tmp_path), backend_url="http://127.0.0.1:9")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://frontdoor") as client:
        resp = await client.get("/_infra/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["role"] == "frontdoor"
    assert data["static_index"] is True
    assert data["backend"]["ready"] is False


@pytest.mark.anyio
async def test_frontdoor_backend_route_file_can_switch_without_restart(tmp_path):
    from aletheia.gateway.frontdoor import create_app
    from aletheia.gateway.frontdoor_route import write_frontdoor_route

    route_path = tmp_path / "frontdoor-route.json"
    write_frontdoor_route(
        backend_base_url="http://127.0.0.1:8788",
        runtime_id="runtime-a",
        path=route_path,
        reason="test-a",
    )
    app = create_app(
        static_dir=_static_fixture(tmp_path),
        backend_url="http://127.0.0.1:9",
        route_path=route_path,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://frontdoor") as client:
        first = await client.get("/_infra/livez")
        write_frontdoor_route(
            backend_base_url="http://127.0.0.1:8789",
            runtime_id="runtime-b",
            path=route_path,
            reason="test-b",
        )
        second = await client.get("/_infra/livez")

    assert first.json()["backend_base_url"] == "http://127.0.0.1:8788"
    assert second.json()["backend_base_url"] == "http://127.0.0.1:8789"
    assert second.json()["backend_route"]["runtime_id"] == "runtime-b"


@pytest.mark.anyio
async def test_frontdoor_api_returns_structured_503_when_backend_down(tmp_path):
    from aletheia.gateway.frontdoor import create_app

    app = create_app(static_dir=_static_fixture(tmp_path), backend_url="http://127.0.0.1:9")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://frontdoor") as client:
        resp = await client.get("/api/status")
        status = await client.get("/_infra/status")

    assert resp.status_code == 503
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "backend_unavailable"
    assert data["action"] == "gateway_recovering"
    assert data["retry_after_sec"] == 3
    assert resp.headers["X-Aletheia-Degraded"] == "backend_unavailable"
    detail = data["detail"]
    assert detail["reason"] == "backend_unavailable"
    assert detail["path"] == "/api/status"

    event_codes = [e["code"] for e in status.json()["events"]]
    assert "backend_unavailable" in event_codes


@pytest.mark.anyio
async def test_frontdoor_readyz_fails_only_when_static_missing(tmp_path):
    from aletheia.gateway.frontdoor import create_app

    missing_static = tmp_path / "missing"
    app = create_app(static_dir=missing_static, backend_url="http://127.0.0.1:9")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://frontdoor") as client:
        resp = await client.get("/_infra/readyz")
        root = await client.get("/")

    assert resp.status_code == 503
    assert resp.json()["static_index"] is False
    assert root.status_code == 503
    assert "dashboard assets are unavailable" in root.text.lower()

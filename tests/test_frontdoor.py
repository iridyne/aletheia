from pathlib import Path
import gzip

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


def _compressed_static_fixture(tmp_path: Path) -> Path:
    static_dir = _static_fixture(tmp_path)
    source = (static_dir / "assets" / "index-test.js").read_bytes()
    (static_dir / "assets" / "index-test.js").write_bytes(source * 200)
    source = (static_dir / "assets" / "index-test.js").read_bytes()
    (static_dir / "assets" / "index-test.js.gz").write_bytes(gzip.compress(source, mtime=0))
    (static_dir / "index.html.gz").write_bytes(gzip.compress((static_dir / "index.html").read_bytes(), mtime=0))
    return static_dir


@pytest.mark.anyio
async def test_frontdoor_negotiates_gzip_and_keeps_hashed_asset_cache_contract(tmp_path):
    from aletheia.gateway.frontdoor import create_app

    static_dir = _compressed_static_fixture(tmp_path)
    app = create_app(static_dir=static_dir, backend_url="http://127.0.0.1:9")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://frontdoor") as client:
        response = await client.get("/assets/index-test.js", headers={"Accept-Encoding": "gzip"})
        head = await client.head("/assets/index-test.js", headers={"Accept-Encoding": "gzip"})
        conditional = await client.get(
            "/assets/index-test.js",
            headers={"Accept-Encoding": "gzip", "If-None-Match": response.headers["etag"]},
        )
        missing_asset = await client.get("/assets/missing.js")
        sidecar = await client.get("/assets/index-test.js.gz")

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["cache-control"].endswith("immutable")
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["content-type"].startswith("text/javascript")
    assert len(response.content) > 256
    assert head.status_code == 200
    assert head.headers["content-encoding"] == "gzip"
    assert head.headers["etag"] == response.headers["etag"]
    assert conditional.status_code == 304
    assert missing_asset.status_code == 404
    assert sidecar.status_code == 404


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


def test_frontdoor_websocket_scope_rejected_without_assertion_error(tmp_path):
    """Unmatched WebSocket scopes must close cleanly, not raise AssertionError.

    Before the fix, StaticFiles.__call__ did `assert scope["type"] == "http"`, so
    any WebSocket path not matched by an earlier route crashed the server with an
    unhandled AssertionError (#357).
    """
    from starlette.testclient import TestClient
    from aletheia.gateway.frontdoor import create_app

    app = create_app(static_dir=_static_fixture(tmp_path), backend_url="http://127.0.0.1:9")
    client = TestClient(app)
    try:
        with client.websocket_connect("/unmatched-ws"):
            pass
    except AssertionError:
        pytest.fail("StaticFiles assertion error leaked to test: WebSocket scope hit HTTP-only mount")
    except Exception:
        pass  # WebSocketDisconnect or similar is fine — connection was rejected cleanly


@pytest.mark.anyio
async def test_frontdoor_proxies_websocket_bidirectionally(tmp_path, monkeypatch):
    import asyncio

    from aletheia.gateway import frontdoor

    captured: dict[str, object] = {}

    class FakeUpstream:
        close_code = 1000

        def __init__(self):
            self.sent: list[str | bytes] = []
            self.send_seen = asyncio.Event()
            self._messages = iter(['{"type":"update","changed":["positions"]}'])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration:
                await self.send_seen.wait()
                raise StopAsyncIteration

        async def send(self, message):
            self.sent.append(message)
            self.send_seen.set()

        async def close(self, code=1000):
            self.close_code = code

    upstream = FakeUpstream()

    def fake_connect(url, headers):
        captured["url"] = url
        captured["headers"] = headers
        return upstream

    monkeypatch.setattr(frontdoor, "_connect_backend_websocket", fake_connect)
    app = frontdoor.create_app(
        static_dir=_static_fixture(tmp_path),
        backend_url="http://127.0.0.1:8788",
    )

    outgoing: list[dict] = []
    downstream_sent = asyncio.Event()
    receive_count = 0

    async def receive():
        nonlocal receive_count
        receive_count += 1
        if receive_count == 1:
            return {"type": "websocket.connect"}
        if receive_count == 2:
            return {"type": "websocket.receive", "text": "ping"}
        await downstream_sent.wait()
        return {"type": "websocket.disconnect", "code": 1000}

    async def send(message):
        outgoing.append(message)
        if message["type"] == "websocket.send":
            downstream_sent.set()

    await app(
        {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": "/api/ws",
            "raw_path": b"/api/ws",
            "query_string": b"client=test",
            "root_path": "",
            "headers": [
                (b"host", b"frontdoor"),
                (b"authorization", b"Bearer fixture"),
                (b"sec-websocket-key", b"drop-me"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8787),
            "subprotocols": [],
            "state": {},
        },
        receive,
        send,
    )

    assert captured["url"] == "ws://127.0.0.1:8788/api/ws?client=test"
    assert upstream.sent == ["ping"]
    assert captured["headers"]["authorization"] == "Bearer fixture"
    assert not any(
        key.lower().startswith("sec-websocket-")
        for key in captured["headers"]
    )
    assert {"type": "websocket.accept", "subprotocol": None, "headers": []} in outgoing
    assert {
        "type": "websocket.send",
        "text": '{"type":"update","changed":["positions"]}',
    } in outgoing


@pytest.mark.anyio
async def test_proxy_stream_interrupted_ends_cleanly(tmp_path, monkeypatch):
    """A mid-stream backend reset must NOT crash the frontdoor response.

    Regression: proxy_api streamed upstream.aiter_raw() with no guard, so an
    httpx.ReadError mid-body propagated through StreamingResponse and tore down
    the connection (the observed intermittent frontend disconnect). The guarded
    generator now ends the body early and emits a proxy_stream_interrupted event.
    """
    import httpx
    from aletheia.gateway import frontdoor
    from aletheia.infra.events import clear_infra_events, list_infra_events

    clear_infra_events()

    app = frontdoor.create_app(
        static_dir=_static_fixture(tmp_path),
        backend_url="http://127.0.0.1:19999",  # never contacted — client is faked
    )

    async def _crashing_aiter_raw():
        yield b'{"partial":'
        raise httpx.ReadError("Connection reset by peer")

    class _FakeUpstream:
        status_code = 200
        headers = httpx.Headers([("content-type", "application/json")])

        def aiter_raw(self):
            return _crashing_aiter_raw()

        async def aclose(self):
            pass

    class _FakeClient:
        def build_request(self, method, url, **kwargs):
            return httpx.Request(method, url)

        async def send(self, request, *, stream=False):
            return _FakeUpstream()

    monkeypatch.setattr(frontdoor, "_get_proxy_client", lambda: _FakeClient())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://frontdoor"
    ) as client:
        resp = await client.get("/api/stream-test")

    # Headers were already sent → status is the upstream 200, not a 500 crash.
    assert resp.status_code == 200
    # The partial chunk was delivered before the stream was cut.
    assert resp.text == '{"partial":'
    # The interruption was recorded as a diagnostics event.
    codes = [e["code"] for e in list_infra_events()]
    assert "proxy_stream_interrupted" in codes

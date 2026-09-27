"""Stable local front-door ASGI app.

The front door owns the user-facing dashboard port and keeps serving static UI
plus infra diagnostics while the backend worker behind it restarts. It is
deliberately small: no scheduler, no trading state, no DB migrations, and no
agent runtime. `/api/*` is proxied to the supervised backend worker.
"""

from __future__ import annotations

import asyncio
# Windows: enforce SelectorEventLoop process-wide before uvicorn creates the
# front-door server loop — eliminates the IOCP AcceptEx WinError 64 crash (§23).
# See aletheia/async_utils.py for the CLI-provider incompatibility constraint.
from aletheia.async_utils import apply_windows_selector_policy

apply_windows_selector_policy()

import logging
import os
import time
import mimetypes
from pathlib import Path
from typing import AsyncIterator, Iterable

import httpx
import anyio
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from aletheia import __version__
from aletheia.gateway.frontdoor_route import read_frontdoor_route, resolve_backend_base_url
from aletheia.infra.events import emit_infra_event, list_infra_events
from aletheia.operator_service_identity import OWNER_AUTH_HEADER

logger = logging.getLogger("aletheia.gateway.frontdoor")


_boot_time = time.time()
_last_backend_ready_at: str | None = None
_PROBE_TIMEOUT = 0.8  # seconds — monkeypatched to 0.05 in tests via conftest
_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def dashboard_static_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "dashboard_static"


def backend_base_url(*, route_path: Path | None = None, fallback: str | None = None) -> str:
    return resolve_backend_base_url(path=route_path, fallback=fallback)


def _minimal_liveness_payload(*, ok: bool) -> dict:
    return {"ok": ok, "role": "frontdoor", "version": __version__}


def _remote_auth_check(request: Request) -> JSONResponse | None:
    """Return 401 if remote auth is required but caller lacks a valid token; None if allowed.

    Mirrors backend _tunnel_auth_guard: active when tunnel.enabled=True or
    remote_control.require_token=True.  Fails open on config errors so the
    front door remains reachable for local diagnostics when config is broken.
    """
    import hmac as _hmac
    from aletheia.config.loader import load_config
    from aletheia.config.secrets import get_secret
    try:
        cfg = load_config(persist_migrations=False)
    except Exception:
        return None
    tunnel_enabled = bool(getattr(getattr(cfg, "tunnel", None), "enabled", False))
    rc = getattr(cfg, "remote_control", None)
    remote_token_required = bool(getattr(rc, "enabled", False) and getattr(rc, "require_token", False))
    if not (tunnel_enabled or remote_token_required):
        return None
    expected = get_secret("remote_control_api_token") or get_secret("tunnel_api_token")
    if not expected:
        return JSONResponse(
            {"detail": "Remote Control is enabled but no API token is configured."},
            status_code=401,
        )
    if not _hmac.compare_digest(request.headers.get(OWNER_AUTH_HEADER, ""), expected):
        return JSONResponse(
            {"detail": "Unauthorized: X-Aletheia-Token required when Remote Control is active"},
            status_code=401,
        )
    return None


def _frontdoor_payload(*, static_dir: Path, backend_url: str, route: dict | None) -> dict:
    static_index = static_dir.exists() and (static_dir / "index.html").exists()
    return {
        "ok": True,
        "role": "frontdoor",
        "version": __version__,
        "pid": os.getpid(),
        "uptime_sec": round(time.time() - _boot_time, 1),
        "static_index": static_index,
        "static_dir": str(static_dir),
        "backend_base_url": backend_url,
        "backend_route": route,
    }


async def _probe_backend(backend_url: str) -> dict:
    global _last_backend_ready_at
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, trust_env=False) as client:
            resp = await client.get(f"{backend_url.rstrip('/')}/_infra/readyz")
        if resp.status_code == 200:
            from datetime import datetime, timezone
            _last_backend_ready_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "ready": resp.status_code == 200,
            "status_code": resp.status_code,
            "last_seen_at": _last_backend_ready_at,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
        emit_infra_event(
            severity="warning",
            source="frontdoor",
            code="backend_unavailable",
            entity_id=backend_url,
            message="Backend worker is unreachable from the frontdoor",
            action="gateway_recovering",
            metadata={"backend_base_url": backend_url, "error": f"{type(exc).__name__}: {exc}"},
            dedupe_sec=30,
        )
        return {
            "ready": False,
            "error": f"{type(exc).__name__}: {exc}",
            "last_seen_at": _last_backend_ready_at,
        }


async def _probe_backend_liveness(backend_url: str) -> dict:
    """Read backend liveness without consuming the ordinary proxy pool."""
    global _last_backend_ready_at
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, trust_env=False) as client:
            resp = await client.get(f"{backend_url.rstrip('/')}/_infra/livez")
        if resp.status_code == 200:
            from datetime import datetime, timezone

            _last_backend_ready_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            try:
                payload = resp.json()
            except ValueError:
                payload = {}
            if isinstance(payload, dict):
                return {**payload, "ok": True}
        return {
            "ok": False,
            "error": "backend_unavailable",
            "status_code": resp.status_code,
            "last_seen_at": _last_backend_ready_at,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
        emit_infra_event(
            severity="warning",
            source="frontdoor",
            code="backend_unavailable",
            entity_id=backend_url,
            message="Backend worker is unreachable from the frontdoor",
            action="gateway_recovering",
            metadata={"backend_base_url": backend_url, "error": type(exc).__name__},
            dedupe_sec=30,
        )
        return {
            "ok": False,
            "error": "backend_unavailable",
            "last_seen_at": _last_backend_ready_at,
        }


def _filtered_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }


_proxy_client: httpx.AsyncClient | None = None
_stream_client: httpx.AsyncClient | None = None
_PROXY_CONNECT_TIMEOUT = 5.0  # seconds — monkeypatched to 0.05 in tests via conftest
_PROXY_MAX_CONNECTIONS = 20
_STREAM_MAX_CONNECTIONS = 10
_SSE_PATHS = {"operator/events/stream", "dev/reload/events"}


def _get_proxy_client() -> httpx.AsyncClient:
    global _proxy_client
    if _proxy_client is None or _proxy_client.is_closed:
        _proxy_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=_PROXY_CONNECT_TIMEOUT, read=120.0, write=10.0, pool=10.0),
            trust_env=False,
            limits=httpx.Limits(max_connections=_PROXY_MAX_CONNECTIONS, max_keepalive_connections=10),
        )
    return _proxy_client


def _get_stream_client() -> httpx.AsyncClient:
    global _stream_client
    if _stream_client is None or _stream_client.is_closed:
        _stream_client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=_PROXY_CONNECT_TIMEOUT, read=120.0, write=10.0, pool=0.1),
            trust_env=False,
            limits=httpx.Limits(max_connections=_STREAM_MAX_CONNECTIONS, max_keepalive_connections=0),
        )
    return _stream_client


def _connect_backend_websocket(url: str, headers: dict[str, str]):
    """Open one local upstream WebSocket without consulting proxy environment."""
    from websockets.asyncio.client import connect

    return connect(
        url,
        additional_headers=headers,
        proxy=None,
        open_timeout=_PROXY_CONNECT_TIMEOUT,
        close_timeout=2.0,
    )


def _websocket_forward_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Forward application identity only; the client library owns WS handshake headers."""
    allowed = {"authorization", "cookie", OWNER_AUTH_HEADER, "user-agent"}
    return {
        key: value
        for key, value in headers
        if key.lower() in allowed
    }


class _ProxyResponse(StreamingResponse):
    """The downstream ASGI lifetime owns the upstream response, including cancellation."""

    def __init__(self, upstream, holders, request_id, **kwargs):
        super().__init__(**kwargs)
        self.upstream = upstream
        self.holders = holders
        self.request_id = request_id

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                with anyio.CancelScope(shield=True):
                    await self.upstream.aclose()
            finally:
                self.holders.pop(self.request_id, None)


async def _guarded_aiter_raw(
    upstream: httpx.Response,
    path: str,
    backend_url: str,
) -> AsyncIterator[bytes]:
    """Stream raw upstream bytes, swallowing a mid-stream read failure gracefully.

    By the time the first byte is yielded, the status line + headers have already
    been flushed to the client, so we cannot fall back to a 503 here — the only
    safe action is to end the body early and emit a diagnostics event. Without
    this, a backend reset mid-stream raises httpx.ReadError straight through
    StreamingResponse and crashes the connection (the observed intermittent
    frontend disconnect). `content-length` is stripped by _filtered_headers
    (it is in _HOP_BY_HOP_HEADERS), so a truncated body causes no length mismatch.

    The downstream response owns upstream cleanup even on cancellation.
    """
    try:
        async for chunk in upstream.aiter_raw():
            yield chunk
    except (httpx.ReadError, httpx.RemoteProtocolError, httpx.StreamError) as exc:
        emit_infra_event(
            severity="warning",
            source="frontdoor",
            code="proxy_stream_interrupted",
            entity_id=backend_url,
            message=f"API proxy stream interrupted for /api/{path}: {type(exc).__name__}",
            action="gateway_recovering",
            metadata={
                "path": f"/api/{path}",
                "backend_base_url": backend_url,
                "error": type(exc).__name__,
            },
            dedupe_sec=5,
        )
        # End the body early; client sees a truncated (but not crashed) response.
        return


def _proxy_unavailable(path: str, backend_url: str, route: dict | None, exc: Exception, *, streaming: bool = False) -> JSONResponse:
    capacity = isinstance(exc, httpx.PoolTimeout)
    reason = "frontdoor_pool_exhausted" if capacity else "backend_unavailable"
    if capacity and streaming:
        reason = "frontdoor_stream_capacity_exhausted"
    message = (
        "Frontdoor upstream connection capacity is exhausted"
        if capacity else "Aletheia backend worker is restarting or unreachable"
    )
    action = "retry_later" if capacity else "gateway_recovering"
    # Transport exceptions can contain the original URL, including query secrets.
    error = type(exc).__name__
    emit_infra_event(
        severity="critical",
        source="frontdoor",
        code=reason,
        entity_id=backend_url,
        message=message,
        action=action,
        metadata={"path": f"/api/{path}", "backend_base_url": backend_url, "error": error},
        dedupe_sec=10,
    )
    payload = {
        "ok": False,
        "error": reason,
        "reason": reason,
        "message": message,
        "action": action,
        "path": f"/api/{path}",
        "backend_base_url": backend_url,
        "backend_route": route,
        "last_seen_at": _last_backend_ready_at,
        "retry_after_sec": 3,
        "detail": {
            "reason": reason,
            "message": message,
            "path": f"/api/{path}",
            "backend_base_url": backend_url,
            "backend_route": route,
            "last_seen_at": _last_backend_ready_at,
            "retry_after_sec": 3,
            "error": error,
        },
    }
    return JSONResponse(
        status_code=503,
        content=payload,
        headers={
            "Cache-Control": "no-store",
            "Retry-After": "3",
            "X-Aletheia-Degraded": reason,
        },
    )


def create_app(
    *,
    static_dir: Path | None = None,
    backend_url: str | None = None,
    route_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="Aletheia Local Front Door", version=__version__)
    effective_static_dir = static_dir or dashboard_static_dir()
    configured_backend_url = backend_url.rstrip("/") if backend_url else None
    use_route_file = route_path is not None or configured_backend_url is None
    holders: dict[int, dict] = {}
    request_sequence = 0

    def capacity_snapshot() -> dict:
        now = time.monotonic()
        rows = [
            {**row, "age_sec": round(now - row["started_monotonic"], 3)}
            for row in holders.values()
        ]
        return {
            "max_connections": _PROXY_MAX_CONNECTIONS,
            "stream_max_connections": _STREAM_MAX_CONNECTIONS,
            "stream_admissions": sum(row["capacity_class"] == "stream" for row in rows),
            "pending_headers": sum(not row["headers_received"] for row in rows),
            "streaming": sum(row["response_class"] == "sse" for row in rows),
            "non_sse": sum(row["response_class"] == "non_sse" for row in rows),
            # Pending headers includes both pool wait and backend header wait;
            # httpx's public API does not distinguish those connection states.
            "requests": rows[:100],
            "active_requests": len(rows),
            "truncated": len(rows) > 100,
        }

    async def proxy_http(request: Request, path: str, backend: str, target: httpx.URL):
        nonlocal request_sequence
        body = await request.body()
        requested_sse = "text/event-stream" in request.headers.get("accept", "").lower()
        streaming = requested_sse or path.rstrip("/") in _SSE_PATHS
        request_sequence += 1
        request_id = request_sequence
        # Fixed diagnostic categories only: dynamic path segments, headers,
        # queries and bodies must never become telemetry.
        category = path.split("/", 1)[0]
        if category not in {"status", "ready", "ui", "market", "operator", "runtime", "events", "dev", "_infra"}:
            category = "other"
        # No await between capacity check and holder insertion: each admitted
        # stream owns exactly one slot until the existing response cleanup.
        if streaming and sum(row["capacity_class"] == "stream" for row in holders.values()) >= _STREAM_MAX_CONNECTIONS:
            return _proxy_unavailable(category, backend, _backend_route(), httpx.PoolTimeout("stream admission full"), streaming=True)
        holder = {
            "category": category,
            "started_at_unix": time.time(),
            "started_monotonic": time.monotonic(),
            "headers_received": False,
            "requested_sse": requested_sse,
            "capacity_class": "stream" if streaming else "finite",
            "response_class": "unknown",
        }
        holders[request_id] = holder
        try:
            client = _get_stream_client() if streaming else _get_proxy_client()
            upstream = await client.send(
                client.build_request(request.method, target, headers=_filtered_headers(request.headers.items()), content=body),
                stream=True,
            )
        except BaseException as exc:
            holders.pop(request_id, None)
            if not isinstance(exc, Exception):
                raise
            return _proxy_unavailable(category, backend, _backend_route(), exc, streaming=streaming)
        holder["headers_received"] = True
        holder["response_class"] = (
            "sse" if upstream.headers.get("content-type", "").split(";", 1)[0].strip().lower() == "text/event-stream"
            else "non_sse"
        )
        response_headers = _filtered_headers(upstream.headers.items())
        response_headers["X-Aletheia-Frontdoor"] = "1"
        return _ProxyResponse(
            upstream, holders, request_id,
            content=_guarded_aiter_raw(upstream, category, backend),
            status_code=upstream.status_code,
            headers=response_headers,
        )

    def _backend_url() -> str:
        if use_route_file:
            return backend_base_url(route_path=route_path, fallback=configured_backend_url)
        return configured_backend_url or backend_base_url()

    def _backend_route() -> dict | None:
        return read_frontdoor_route(route_path) if use_route_file else None

    @app.get("/_infra/livez")
    async def livez():
        return _minimal_liveness_payload(ok=True)

    @app.get("/_infra/readyz")
    async def readyz():
        static_ok = (effective_static_dir / "index.html").exists()
        backend = await _probe_backend(_backend_url())
        ok = static_ok and backend["ready"]
        payload = _minimal_liveness_payload(ok=ok)
        if not static_ok:
            payload["detail"] = "static_unavailable"
        elif not backend["ready"]:
            payload["detail"] = "backend_unavailable"
        return JSONResponse(status_code=200 if ok else 503, content=payload)

    @app.get("/_infra/status")
    async def infra_status(request: Request):
        if (denied := _remote_auth_check(request)):
            return denied
        payload = _frontdoor_payload(
            static_dir=effective_static_dir,
            backend_url=_backend_url(),
            route=_backend_route(),
        )
        payload["backend"] = await _probe_backend(_backend_url())
        payload["proxy_capacity"] = capacity_snapshot()
        payload["events"] = list_infra_events(limit=10, min_severity="warning")
        return JSONResponse(status_code=200 if payload["static_index"] else 503, content=payload)

    @app.get("/_infra/doctor")
    async def infra_doctor(request: Request):
        if (denied := _remote_auth_check(request)):
            return denied
        backend = _backend_url()
        query = request.url.query.encode("utf-8") if request.url.query else None
        target = httpx.URL(f"{backend}/_infra/doctor", query=query)
        return await proxy_http(request, "_infra/doctor", backend, target)

    @app.get("/api/ready")
    async def api_ready():
        payload = await _probe_backend_liveness(_backend_url())
        if payload.get("ok"):
            return JSONResponse(status_code=200, content=payload)
        payload.setdefault("reason", "backend_unavailable")
        payload.setdefault("message", "Aletheia backend worker is restarting or unreachable")
        payload.setdefault("action", "gateway_recovering")
        payload.setdefault("retry_after_sec", 3)
        return JSONResponse(
            status_code=503,
            content=payload,
            headers={
                "Cache-Control": "no-store",
                "Retry-After": "3",
                "X-Aletheia-Degraded": "backend_unavailable",
            },
        )

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy_api(path: str, request: Request):
        backend = _backend_url()
        query = request.url.query.encode("utf-8") if request.url.query else None
        target = httpx.URL(f"{backend}/api/{path}", query=query)
        return await proxy_http(request, path, backend, target)

    @app.websocket("/api/{path:path}")
    async def proxy_api_websocket(path: str, websocket: WebSocket):
        backend = _backend_url()
        websocket_backend = backend.replace("http://", "ws://", 1)
        query = f"?{websocket.url.query}" if websocket.url.query else ""
        target = f"{websocket_backend}/api/{path}{query}"
        headers = _websocket_forward_headers(websocket.headers.items())

        try:
            async with _connect_backend_websocket(target, headers) as upstream:
                await websocket.accept()

                async def client_to_backend() -> None:
                    while True:
                        message = await websocket.receive()
                        message_type = message.get("type")
                        if message_type == "websocket.disconnect":
                            await upstream.close(
                                code=int(message.get("code") or 1000),
                            )
                            return
                        if message.get("text") is not None:
                            await upstream.send(message["text"])
                        elif message.get("bytes") is not None:
                            await upstream.send(message["bytes"])

                async def backend_to_client() -> None:
                    async for message in upstream:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)

                directions = {
                    asyncio.create_task(client_to_backend()),
                    asyncio.create_task(backend_to_client()),
                }
                done, pending = await asyncio.wait(
                    directions,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for task in done:
                    if task.cancelled():
                        continue
                    error = task.exception()
                    if error is not None:
                        raise error
                try:
                    await websocket.close(code=int(upstream.close_code or 1000))
                except Exception:
                    logger.debug("frontdoor: websocket close after proxy session ended failed", exc_info=True)
        except Exception as exc:  # noqa: BLE001 - handshake must fail closed
            emit_infra_event(
                severity="warning",
                source="frontdoor",
                code="websocket_proxy_unavailable",
                entity_id=backend,
                message=f"WebSocket proxy failed for /api/{path}",
                action="gateway_recovering",
                metadata={
                    "path": f"/api/{path}",
                    "backend_base_url": backend,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                dedupe_sec=10,
            )
            try:
                await websocket.close(code=1013)
            except Exception:
                logger.debug("frontdoor: websocket close after proxy failure failed", exc_info=True)

    if effective_static_dir.exists() and (effective_static_dir / "index.html").exists():

        class _SPAStaticFiles(StaticFiles):
            """StaticFiles with SPA fallback: unknown paths serve index.html.

            Only intercepts 404 for non-asset paths — /assets/* 404s pass
            through so browsers detect stale chunk hashes correctly.
            """

            async def __call__(self, scope, receive, send):
                # StaticFiles is HTTP-only; its parent asserts scope["type"] == "http".
                # Reject non-HTTP scopes explicitly so WebSocket connections get a
                # clean close frame instead of an unhandled AssertionError (#357).
                if scope["type"] != "http":
                    if scope["type"] == "websocket":
                        await send({"type": "websocket.close", "code": 1001})
                    return
                await super().__call__(scope, receive, send)

            @staticmethod
            def _accepted_encodings(scope) -> list[str]:
                header = next(
                    (value.decode("latin-1") for key, value in scope.get("headers", []) if key.lower() == b"accept-encoding"),
                    "",
                )
                accepted: dict[str, float] = {}
                wildcard: float | None = None
                for item in header.split(","):
                    name, _, params = item.strip().lower().partition(";")
                    if not name:
                        continue
                    quality = 1.0
                    for param in params.split(";"):
                        key, _, raw = param.strip().partition("=")
                        if key == "q":
                            try:
                                quality = max(0.0, min(1.0, float(raw)))
                            except ValueError:
                                quality = 0.0
                    if name == "*":
                        wildcard = quality
                    else:
                        accepted[name] = quality
                candidates = {
                    "br": accepted.get("br", wildcard if wildcard is not None else 0.0),
                    "gzip": accepted.get("gzip", wildcard if wildcard is not None else 0.0),
                }
                enabled = [(quality, 1 if name == "br" else 0, name) for name, quality in candidates.items() if quality > 0]
                return [name for _quality, _br_first, name in sorted(enabled, reverse=True)]

            @classmethod
            def _accepted_encoding(cls, scope) -> str | None:
                return next(iter(cls._accepted_encodings(scope)), None)

            def _headers_for(self, response, *, url_path: str, encoding: str | None, source_path: str):
                response.headers["Cache-Control"] = (
                    "public, max-age=31536000, immutable"
                    if url_path.startswith("/assets/")
                    else "no-cache, must-revalidate"
                )
                response.headers["Vary"] = "Accept-Encoding"
                content_type = mimetypes.guess_type(source_path)[0] or "application/octet-stream"
                if source_path.endswith((".js", ".mjs")):
                    content_type = "text/javascript"
                response.headers["Content-Type"] = content_type
                if encoding:
                    response.headers["Content-Encoding"] = encoding
                elif "content-encoding" in response.headers:
                    del response.headers["Content-Encoding"]
                return response

            async def _serve(self, path: str, scope, *, url_path: str | None = None):
                url_path = url_path or scope.get("path", "")
                # Starlette passes an empty path for the root directory. Resolve
                # it to the actual document before content negotiation and MIME
                # detection; otherwise `/` is served as application/octet-stream.
                logical_path = "index.html" if path in ("", ".") else path
                encoding = None
                selected = logical_path
                # Sidecars are an implementation detail; only the logical URL is
                # resolved through content negotiation.
                for candidate_encoding in self._accepted_encodings(scope):
                    candidate = f"{logical_path}{'.br' if candidate_encoding == 'br' else '.gz'}"
                    _, candidate_stat = self.lookup_path(candidate)
                    if candidate_stat is not None:
                        selected = candidate
                        encoding = candidate_encoding
                        break
                result = await super().get_response(selected, scope)
                return self._headers_for(result, url_path=url_path, encoding=encoding, source_path=logical_path)

            async def get_response(self, path: str, scope):
                url_path = scope.get("path", "")
                if url_path.endswith((".br", ".gz", ".gzip")):
                    raise StarletteHTTPException(status_code=404)
                try:
                    return await self._serve(path, scope)
                except StarletteHTTPException as exc:
                    url_path = scope.get("path", "")
                    if exc.status_code == 404 and not url_path.startswith("/assets/") and not url_path.endswith((".br", ".gz", ".gzip")):
                        return await self._serve("index.html", scope, url_path=url_path)
                    raise

        app.mount(
            "/",
            _SPAStaticFiles(directory=str(effective_static_dir), html=True),
            name="dashboard",
        )
    else:
        async def dashboard_unavailable(_path: str = "") -> HTMLResponse:
            return HTMLResponse(
                status_code=503,
                content=f"""<!doctype html>
<html><head><meta charset=\"utf-8\" />
<meta http-equiv=\"refresh\" content=\"5\" />
<title>Aletheia dashboard unavailable</title></head>
<body style=\"background:#0a0e12;color:#e6edf3;font-family:system-ui,sans-serif;padding:32px\">
<div style=\"max-width:760px;border:1px solid #ff5d6c;border-radius:8px;padding:20px;background:#141a21\">
<h1>Aletheia dashboard assets are unavailable</h1>
<p>The local front door is alive, but the static dashboard was not found.</p>
<p>Expected assets under: <code>{effective_static_dir}</code></p>
<p>Recovery: <code>cd frontend && npm run build</code>, then <code>uv run aletheia gateway restart --no-browser</code>.</p>
</div></body></html>""",
            )

        app.add_api_route("/", dashboard_unavailable, include_in_schema=False)
        app.add_api_route("/{_path:path}", dashboard_unavailable, include_in_schema=False)

    return app


app = create_app()

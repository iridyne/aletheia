"""Stable local front-door ASGI app.

The front door owns the user-facing dashboard port and keeps serving static UI
plus infra diagnostics while the backend worker behind it restarts. It is
deliberately small: no scheduler, no trading state, no DB migrations, and no
agent runtime. `/api/*` is proxied to the supervised backend worker.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from aletheia import __version__
from aletheia.gateway.frontdoor_route import read_frontdoor_route, resolve_backend_base_url

_boot_time = time.time()
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
    try:
        async with httpx.AsyncClient(timeout=0.8, trust_env=False) as client:
            resp = await client.get(f"{backend_url.rstrip('/')}/_infra/readyz")
        return {
            "ready": resp.status_code == 200,
            "status_code": resp.status_code,
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not raise
        return {
            "ready": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _filtered_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }


async def _close_proxy_response(upstream: httpx.Response, client: httpx.AsyncClient) -> None:
    await upstream.aclose()
    await client.aclose()


def _proxy_unavailable(path: str, backend_url: str, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "reason": "backend_unavailable",
                "message": "Aletheia backend worker is restarting or unreachable",
                "path": f"/api/{path}",
                "backend_base_url": backend_url,
                "error": f"{type(exc).__name__}: {exc}",
            }
        },
        headers={"Cache-Control": "no-store"},
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

    def _backend_url() -> str:
        if use_route_file:
            return backend_base_url(route_path=route_path, fallback=configured_backend_url)
        return configured_backend_url or backend_base_url()

    def _backend_route() -> dict | None:
        return read_frontdoor_route(route_path) if use_route_file else None

    @app.get("/_infra/livez")
    async def livez():
        return _frontdoor_payload(
            static_dir=effective_static_dir,
            backend_url=_backend_url(),
            route=_backend_route(),
        )

    @app.get("/_infra/readyz")
    async def readyz():
        payload = _frontdoor_payload(
            static_dir=effective_static_dir,
            backend_url=_backend_url(),
            route=_backend_route(),
        )
        backend = await _probe_backend(_backend_url())
        payload["backend"] = backend
        status_code = 200 if payload["static_index"] else 503
        return JSONResponse(status_code=status_code, content=payload)

    @app.get("/_infra/status")
    async def infra_status():
        payload = _frontdoor_payload(
            static_dir=effective_static_dir,
            backend_url=_backend_url(),
            route=_backend_route(),
        )
        payload["backend"] = await _probe_backend(_backend_url())
        return JSONResponse(status_code=200 if payload["static_index"] else 503, content=payload)

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    async def proxy_api(path: str, request: Request):
        backend = _backend_url()
        query = request.url.query.encode("utf-8") if request.url.query else None
        target = httpx.URL(f"{backend}/api/{path}", query=query)
        body = await request.body()
        headers = _filtered_headers(request.headers.items())
        client = httpx.AsyncClient(timeout=None, trust_env=False)
        try:
            upstream_request = client.build_request(
                request.method,
                target,
                headers=headers,
                content=body,
            )
            upstream = await client.send(upstream_request, stream=True)
        except Exception as exc:  # noqa: BLE001 - must fail loud as HTTP 503
            await client.aclose()
            return _proxy_unavailable(path, backend, exc)

        response_headers = _filtered_headers(upstream.headers.items())
        response_headers["X-Aletheia-Frontdoor"] = "1"
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=response_headers,
            background=BackgroundTask(_close_proxy_response, upstream, client),
        )

    if effective_static_dir.exists() and (effective_static_dir / "index.html").exists():
        app.mount(
            "/",
            StaticFiles(directory=str(effective_static_dir), html=True),
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

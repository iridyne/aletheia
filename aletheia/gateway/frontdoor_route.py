"""Atomic front-door backend route state.

The local front door owns the user-facing port and reads this tiny JSON file to
decide which supervised backend worker should receive `/api/*` traffic. Keeping
the route outside the front-door process lets the supervisor or runtime
promotion logic switch backend workers without rebinding `:8787`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from aletheia.config.loader import get_data_dir

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def frontdoor_route_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    explicit = os.environ.get("ALETHEIA_FRONTDOOR_ROUTE_PATH", "").strip()
    if explicit:
        return Path(explicit)
    return get_data_dir() / "frontdoor-route.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validated_backend_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().rstrip("/")
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme != "http" or not parsed.netloc:
        return None
    if parsed.hostname not in _LOCAL_HOSTS:
        return None
    if parsed.path not in ("", "/"):
        return None
    return raw


def read_frontdoor_route(path: Path | None = None) -> dict | None:
    route_path = frontdoor_route_path(path)
    try:
        payload = json.loads(route_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    backend_url = _validated_backend_url(payload.get("backend_base_url"))
    if backend_url is None:
        return None
    return {**payload, "backend_base_url": backend_url}


def resolve_backend_base_url(
    *,
    path: Path | None = None,
    fallback: str | None = None,
) -> str:
    route = read_frontdoor_route(path)
    if route is not None:
        return str(route["backend_base_url"])
    if fallback:
        return fallback.rstrip("/")
    explicit = os.environ.get("ALETHEIA_BACKEND_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("ALETHEIA_BACKEND_HOST", "127.0.0.1")
    port = os.environ.get("ALETHEIA_BACKEND_PORT", "8788")
    return f"http://{host}:{port}"


def write_frontdoor_route(
    *,
    backend_base_url: str,
    runtime_id: str,
    runtime_mode: str = "current",
    runtime_state: str = "current",
    reason: str = "supervisor_start",
    backend_pid: int | None = None,
    path: Path | None = None,
) -> dict:
    backend_url = _validated_backend_url(backend_base_url)
    if backend_url is None:
        raise ValueError(f"frontdoor backend route must be a local http URL: {backend_base_url!r}")
    route_path = frontdoor_route_path(path)
    route_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "backend_base_url": backend_url,
        "runtime_id": runtime_id,
        "runtime_mode": runtime_mode,
        "runtime_state": runtime_state,
        "backend_pid": backend_pid,
        "reason": reason,
        "updated_at": _utc_now(),
    }
    tmp_path = route_path.with_suffix(f"{route_path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, route_path)
    return payload

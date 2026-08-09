"""Small in-process infra event buffer.

This is intentionally local and bounded. It is for dashboard/status visibility,
not for trading audit or durable state. Do not place secrets, prompts, or order
payloads in these events.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import threading
import time
from typing import Any

_MAX_EVENTS = 100
_MAX_LAST_EMIT = 2000  # Cap the dedupe dict so long-lived processes don't leak.
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_last_emit: dict[str, float] = {}
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def emit_infra_event(
    *,
    severity: str,
    source: str,
    code: str,
    message: str,
    action: str | None = None,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    dedupe_sec: float = 0,
) -> dict[str, Any] | None:
    """Append a sanitized runtime event, optionally de-duped by source/code/entity."""
    sev = severity if severity in {"info", "warning", "critical"} else "warning"
    key = f"{source}:{code}:{entity_id or ''}"
    now = time.time()
    with _lock:
        if dedupe_sec > 0 and now - _last_emit.get(key, 0) < dedupe_sec:
            return None
        _last_emit[key] = now
        # Bound the dedupe dict: when it grows beyond 2× the cap, evict the
        # oldest half.  This keeps long-lived processes from accumulating a
        # permanent entry per unique (source, code, entity_id) tuple.
        if len(_last_emit) > _MAX_LAST_EMIT * 2:
            sorted_items = sorted(_last_emit.items(), key=lambda kv: kv[1])
            for k, _ in sorted_items[:_MAX_LAST_EMIT]:
                _last_emit.pop(k, None)
        event = {
            "created_at": _now_iso(),
            "severity": sev,
            "source": str(source)[:80],
            "code": str(code)[:120],
            "message": str(message)[:500],
            "action": str(action)[:200] if action else None,
            "entity_id": str(entity_id)[:160] if entity_id else None,
            "metadata": metadata or {},
        }
        _events.appendleft(event)
        return dict(event)


def list_infra_events(limit: int = 20, min_severity: str | None = None) -> list[dict[str, Any]]:
    order = {"info": 0, "warning": 1, "critical": 2}
    threshold = order.get(min_severity or "info", 0)
    with _lock:
        out = [
            dict(e)
            for e in list(_events)
            if order.get(str(e.get("severity")), 1) >= threshold
        ]
    return out[: max(0, min(limit, _MAX_EVENTS))]


def clear_infra_events() -> None:
    """Test helper."""
    with _lock:
        _events.clear()
        _last_emit.clear()


"""Small in-process infra event buffer.

This is intentionally local and bounded. It is for dashboard/status visibility,
not for trading audit or durable state. Do not place secrets, prompts, or order
payloads in these events.

Two projections share one lock:

* ``_events``             bounded raw history (deque, newest first) — the raw
                          authority behind ``list_infra_events`` / the
                          ``/api/infra/events`` endpoint.
* ``_critical_unresolved`` non-evicting registry of unresolved critical events,
                          keyed by ``(source, code, entity_id)``. Critical events
                          can be evicted from the 100-slot deque by later volume,
                          silently losing a still-open incident; the registry is
                          the durable-visible "still open" projection. It is
                          cleared only by an explicit ``resolves`` emit or
                          ``clear_infra_events()``, and is reconstructible from
                          durable facts (see ``upsert_critical_unresolved``).
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
import threading
import time
from typing import Any

_MAX_EVENTS = 100
_MAX_CRITICAL_UNRESOLVED = 256
_events: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_critical_unresolved: dict[str, dict[str, Any]] = {}
_last_emit: dict[str, float] = {}
_resolution_seq: int = 0  # monotonic; increments on every successful resolves-deletion
_lock = threading.Lock()

logger = logging.getLogger("aletheia.infra.events")

_SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_key(source: str, code: str, entity_id: str | None) -> str:
    return f"{source}:{code}:{entity_id or ''}"


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
    resolves: str | None = None,
) -> dict[str, Any] | None:
    """Append a sanitized runtime event, optionally de-duped by source/code/entity.

    ``resolves`` names the ``code`` of a critical registry entry (same ``source``
    and ``entity_id``) that this event proves is over; that entry is removed and
    the monotonic resolution counter advances. Resolution happens even when the
    notification event itself is de-duped, so a suppressed notice cannot strand a
    resolved critical in the registry.
    """
    global _resolution_seq
    sev = severity if severity in _SEVERITY_ORDER else "warning"
    key = _event_key(source, code, entity_id)
    now = time.time()
    with _lock:
        deduped = dedupe_sec > 0 and now - _last_emit.get(key, 0) < dedupe_sec
        if resolves is not None:
            target_key = _event_key(source, resolves, entity_id)
            if _critical_unresolved.pop(target_key, None) is not None:
                _resolution_seq += 1
        if deduped:
            return None
        _last_emit[key] = now
        event = {
            "created_at": _now_iso(),
            "severity": sev,
            "source": str(source)[:80],
            "code": str(code)[:120],
            "message": str(message)[:500],
            "action": str(action)[:200] if action else None,
            "entity_id": str(entity_id)[:160] if entity_id else None,
            "metadata": metadata or {},
            "resolves": str(resolves)[:120] if resolves is not None else None,
        }
        if sev == "critical":
            if len(_critical_unresolved) >= _MAX_CRITICAL_UNRESOLVED and key not in _critical_unresolved:
                # Refuse to evict (which would silently lose critical events):
                # drop the newest instead. The registry is a projection that the
                # next durable-reconstruction pass restores, and the event is
                # still appended to the raw deque below.
                logger.critical(
                    "infra critical registry at capacity (%d); overflow event not retained: %s",
                    _MAX_CRITICAL_UNRESOLVED,
                    key,
                )
            else:
                _critical_unresolved[key] = dict(event)
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


def list_outstanding_infra_events(
    limit: int = 100,
    min_severity: str | None = None,
) -> list[dict[str, Any]]:
    """Return currently outstanding events.

    The deque view minus any event resolved by a newer ``resolves`` emit (exact
    ``code`` and ``entity_id`` match), unioned with the non-evicting critical
    registry so a critical event pushed out of the bounded deque by later volume
    is still reported. Each returned event carries its computed ``key``
    (``source:code:entity_id``) so callers can de-duplicate.
    """
    threshold = _SEVERITY_ORDER.get(min_severity or "info", 0)
    with _lock:
        snapshot = list(_events)
        registry = dict(_critical_unresolved)

    resolved: set[tuple[str, str, str]] = set()
    seen: set[tuple[str, str, str]] = set()
    outstanding: list[dict[str, Any]] = []

    # Raw history is newest-first; a resolution only suppresses strictly older
    # events, and older duplicates of one key collapse to the newest occurrence.
    for event in snapshot:
        source = str(event.get("source") or "")
        code = str(event.get("code") or "")
        entity = str(event.get("entity_id") or "")
        tuple_key = (source, code, entity)
        if tuple_key not in resolved and tuple_key not in seen:
            seen.add(tuple_key)
            outstanding.append({**dict(event), "key": _event_key(source, code, entity)})
        if event.get("resolves"):
            resolved.add((source, str(event["resolves"]), entity))

    for registry_key, event in registry.items():
        source = str(event.get("source") or "")
        code = str(event.get("code") or "")
        entity = str(event.get("entity_id") or "")
        tuple_key = (source, code, entity)
        if tuple_key in seen or tuple_key in resolved:
            continue
        seen.add(tuple_key)
        outstanding.append({**dict(event), "key": registry_key})

    filtered = [
        event
        for event in outstanding
        if _SEVERITY_ORDER.get(str(event.get("severity")), 1) >= threshold
    ]
    filtered.sort(
        key=lambda event: (
            _SEVERITY_ORDER.get(str(event.get("severity")), 1),
            str(event.get("created_at") or ""),
        ),
        reverse=True,
    )
    return filtered[: max(0, limit)]


def resolution_seq() -> int:
    """Monotonic counter; increments on every successful resolves-driven deletion."""
    with _lock:
        return _resolution_seq


def upsert_critical_unresolved(
    source: str,
    code: str,
    entity_id: str | None,
    message: str,
    metadata: dict | None = None,
) -> None:
    """Idempotent upsert used by durable reconstruction paths.

    Per-pass rebuilds of the registry from durable facts (e.g. audit_events plus
    ``positions.pending_close_status``) call this for every condition still
    proven open. Does NOT change ``_resolution_seq`` and does NOT append to the
    deque — only the registry projection.
    """
    key = _event_key(source, code, entity_id)
    with _lock:
        _critical_unresolved[key] = {
            "created_at": _now_iso(),
            "severity": "critical",
            "source": str(source)[:80],
            "code": str(code)[:120],
            "message": str(message)[:500],
            "action": None,
            "entity_id": str(entity_id)[:160] if entity_id else None,
            "metadata": metadata or {},
            "resolves": None,
        }


def mark_resolved_from_durable(source: str, code: str, entity_id: str | None) -> bool:
    """Remove a critical entry because a durable fact proves it resolved.

    The counterpart to ``upsert_critical_unresolved``: a per-pass reconstruction
    that rebuilds the registry from durable facts needs to retire entries the
    facts no longer support. Does not append to the deque; advances the
    resolution counter only when an entry was actually present.
    """
    global _resolution_seq
    key = _event_key(source, code, entity_id)
    with _lock:
        if _critical_unresolved.pop(key, None) is not None:
            _resolution_seq += 1
            return True
        return False


def clear_infra_events() -> None:
    """Test helper."""
    global _resolution_seq
    with _lock:
        _events.clear()
        _last_emit.clear()
        _critical_unresolved.clear()
        _resolution_seq = 0

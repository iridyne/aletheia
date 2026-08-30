"""Durable JSONL mirror outbox for funding-settlement audit facts."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.sqlite import get_db

_OUTBOX_EVENT_TYPES = (
    "exchange_position_funding_settled",
    "exchange_position_funding_history_expired",
    "paper_position_economics_corrected",
    "paper_position_economics_repaired",
    "paper_position_funding_settled",
)


def funding_audit_id(
    *,
    position_id: str,
    event_type: str,
    accounting_version: str,
    fingerprint: str,
    transition_fingerprint: str,
    occurred_at: str,
) -> str:
    """Return the stable identity shared by SQLite and the JSONL mirror."""
    canonical = json.dumps(
        [
            position_id,
            event_type,
            accounting_version,
            fingerprint,
            transition_fingerprint,
            occurred_at,
        ],
        separators=(",", ":"),
    )
    return "funding-audit-v1:" + hashlib.sha256(canonical.encode()).hexdigest()


def insert_pending_funding_audit(event: AuditEvent) -> str:
    """Insert one funding audit and enroll it atomically in the mirror outbox."""
    db = get_db()
    if not db.in_transaction:
        raise RuntimeError("funding audit must share the accounting transaction")
    inserted = repo.insert_audit_event_once(event)
    if inserted:
        db.execute(
            "UPDATE audit_events SET jsonl_mirror_status='pending', "
            "jsonl_attempt_count=0, jsonl_last_error_class=NULL, "
            "jsonl_mirrored_at=NULL WHERE id=?",
            (event.id,),
        )
        return event.id

    current = db.execute(
        "SELECT actor, source, event_type, entity_type, entity_id, "
        "before_state, after_state, message FROM audit_events WHERE id=?",
        (event.id,),
    ).fetchone()
    expected = event.model_dump()
    fields = (
        "actor",
        "source",
        "event_type",
        "entity_type",
        "entity_id",
        "before_state",
        "after_state",
        "message",
    )
    if current is None or any(current[field] != expected[field] for field in fields):
        raise ValueError("funding audit identity collision")
    return event.id


def _state(value: object) -> object:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return str(value)


def _payload(row) -> dict:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "actor": row["actor"],
        "source": row["source"],
        "event_type": row["event_type"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "before": _state(row["before_state"]),
        "after": _state(row["after_state"]),
        "message": row["message"],
    }


def funding_audit_backlog() -> dict:
    """Return a bounded health summary without reading the JSONL chain."""
    placeholders = ",".join("?" for _ in _OUTBOX_EVENT_TYPES)
    row = get_db().execute(
        f"SELECT COUNT(*) AS count, MIN(timestamp) AS oldest_at "
        f"FROM audit_events WHERE event_type IN ({placeholders}) "
        "AND jsonl_mirror_status IN ('pending','failed')",
        _OUTBOX_EVENT_TYPES,
    ).fetchone()
    return {
        "count": int(row["count"] or 0),
        "oldest_at": row["oldest_at"],
    }


def reconcile_funding_audits(
    *,
    limit: int = 100,
    position_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    """Mirror a bounded batch without replaying its accounting transition."""
    from aletheia.files.jsonl import write_audits_deduplicated

    placeholders = ",".join("?" for _ in _OUTBOX_EVENT_TYPES)
    where = [
        f"event_type IN ({placeholders})",
        "jsonl_mirror_status IN ('pending','failed')",
    ]
    params: list[object] = [*_OUTBOX_EVENT_TYPES]
    if position_id is not None:
        where.append("entity_id=?")
        params.append(position_id)
    if event_id is not None:
        where.append("id=?")
        params.append(event_id)
    params.append(min(max(int(limit), 1), 100))
    rows = get_db().execute(
        "SELECT id, timestamp, actor, source, event_type, entity_type, "
        "entity_id, before_state, after_state, message FROM audit_events WHERE "
        + " AND ".join(where)
        + " ORDER BY timestamp, id LIMIT ?",
        tuple(params),
    ).fetchall()
    events = [_payload(row) for row in rows]
    error_class: str | None = None
    try:
        mirrored_ids = write_audits_deduplicated(events, lock_timeout_sec=1.0)
    except Exception as exc:
        mirrored_ids = set()
        error_class = type(exc).__name__
    if events:
        now = datetime.now(timezone.utc).isoformat()
        with repo.transaction(immediate=True):
            for event in events:
                audit_id = str(event["id"])
                if audit_id in mirrored_ids:
                    get_db().execute(
                        "UPDATE audit_events SET jsonl_mirror_status='mirrored', "
                        "jsonl_attempt_count=COALESCE(jsonl_attempt_count,0)+1, "
                        "jsonl_last_error_class=NULL, jsonl_mirrored_at=? "
                        "WHERE id=? AND jsonl_mirror_status IN ('pending','failed')",
                        (now, audit_id),
                    )
                else:
                    get_db().execute(
                        "UPDATE audit_events SET jsonl_mirror_status='failed', "
                        "jsonl_attempt_count=COALESCE(jsonl_attempt_count,0)+1, "
                        "jsonl_last_error_class=?, jsonl_mirrored_at=NULL "
                        "WHERE id=? AND jsonl_mirror_status IN ('pending','failed')",
                        (error_class or "AuditJsonlWriteIncomplete", audit_id),
                    )
    backlog = funding_audit_backlog()
    return {
        "attempted": len(events),
        "mirrored": len(mirrored_ids),
        "failed": len(events) - len(mirrored_ids),
        "pending": backlog["count"],
        "oldest_pending_at": backlog["oldest_at"],
    }


def reconcile_position_funding_audits(position_id: str) -> str:
    """Heal one Position's pending mirrors and report the orthogonal status."""
    reconcile_funding_audits(limit=100, position_id=position_id)
    placeholders = ",".join("?" for _ in _OUTBOX_EVENT_TYPES)
    rows = get_db().execute(
        f"SELECT jsonl_mirror_status FROM audit_events "
        f"WHERE event_type IN ({placeholders}) AND entity_id=? "
        "AND jsonl_mirror_status IS NOT NULL",
        (*_OUTBOX_EVENT_TYPES, position_id),
    ).fetchall()
    if not rows:
        return "unmanaged"
    statuses = {str(row["jsonl_mirror_status"]) for row in rows}
    return "mirrored" if statuses == {"mirrored"} else "pending"

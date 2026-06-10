"""Operator control-plane persistence helpers."""

import json
import uuid
from datetime import datetime, timezone

from aletheia.storage.sqlite import get_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_load(value):
    if value in (None, ""):
        return None
    return json.loads(value)


def _commit_if_needed(db, was_in_tx: bool) -> None:
    if not was_in_tx:
        db.commit()


def _ensure_session(session_id: str, *, user_input: str = "[operator session]") -> None:
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO agent_shell_sessions (id, user_input, created_at) "
        "VALUES (?, ?, datetime('now'))",
        (session_id, user_input),
    )


def _decode_message(row) -> dict:
    item = dict(row)
    item["content"] = _json_load(item["content"])
    return item


def _decode_task(row) -> dict:
    item = dict(row)
    item["input_data"] = _json_load(item["input_data"]) or {}
    item["output_data"] = _json_load(item["output_data"])
    return item


def _decode_artifact(row) -> dict:
    item = dict(row)
    item["content"] = _json_load(item["content"])
    return item


def _decode_heartbeat(row) -> dict:
    item = dict(row)
    item["is_busy"] = bool(item["is_busy"])
    return item


def record_heartbeat(
    *,
    agent_id: str,
    status: str,
    mode: str,
    permission_mode: str,
    is_busy: bool,
    current_task_id: str | None,
    current_intent: str | None,
    queue_size: int,
    last_error: str | None,
    model_provider: str | None,
    session_id: str | None,
    last_seen_at: str | None = None,
    last_success_at: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    if session_id:
        _ensure_session(session_id)
    heartbeat_id = str(uuid.uuid4())
    seen_at = last_seen_at or _utc_now()
    success_at = last_success_at or (seen_at if status == "online" else None)
    db.execute(
        "INSERT INTO agent_shell_heartbeats ("
        "id, agent_id, status, mode, permission_mode, is_busy, current_task_id, "
        "current_intent, queue_size, last_seen_at, last_success_at, last_error, "
        "model_provider, session_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            heartbeat_id,
            agent_id,
            status,
            mode,
            permission_mode,
            int(is_busy),
            current_task_id,
            current_intent,
            queue_size,
            seen_at,
            success_at,
            last_error,
            model_provider,
            session_id,
        ),
    )
    _commit_if_needed(db, was_in_tx)
    return heartbeat_id


def get_latest_heartbeat(agent_id: str = "operator") -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM agent_shell_heartbeats "
        "WHERE agent_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (agent_id,),
    ).fetchone()
    return _decode_heartbeat(row) if row else None


def create_task(
    *,
    task_type: str,
    status: str,
    priority: str,
    created_by: str,
    assigned_to: str = "operator",
    input_data: dict | list | None = None,
    output_data: dict | list | None = None,
    error: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    task_id = str(uuid.uuid4())
    effective_started_at = started_at or (_utc_now() if status == "running" else None)
    effective_completed_at = completed_at or (
        _utc_now() if status in {"completed", "failed"} else None
    )
    db.execute(
        "INSERT INTO agent_shell_tasks ("
        "id, task_type, status, priority, created_by, assigned_to, input_data, "
        "output_data, error, started_at, completed_at, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            task_id,
            task_type,
            status,
            priority,
            created_by,
            assigned_to,
            _json_dump(input_data or {}),
            _json_dump(output_data) if output_data is not None else None,
            error,
            effective_started_at,
            effective_completed_at,
        ),
    )
    _commit_if_needed(db, was_in_tx)
    return task_id


def update_task(task_id: str, **kwargs) -> None:
    if not kwargs:
        return
    updates = dict(kwargs)
    if "input_data" in updates:
        updates["input_data"] = _json_dump(updates["input_data"] or {})
    if "output_data" in updates:
        updates["output_data"] = (
            _json_dump(updates["output_data"])
            if updates["output_data"] is not None
            else None
        )
    status = updates.get("status")
    if status == "running" and "started_at" not in updates:
        updates["started_at"] = _utc_now()
    if status in {"completed", "failed"} and "completed_at" not in updates:
        updates["completed_at"] = _utc_now()

    db = get_db()
    was_in_tx = db.in_transaction
    sets = ", ".join(f"{key} = ?" for key in updates.keys())
    db.execute(
        f"UPDATE agent_shell_tasks SET {sets} WHERE id = ?",
        [*updates.values(), task_id],
    )
    _commit_if_needed(db, was_in_tx)


def get_task(task_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM agent_shell_tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    return _decode_task(row) if row else None


def list_tasks(limit: int = 50, *, status: str | None = None) -> list[dict]:
    db = get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM agent_shell_tasks "
            "WHERE status = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM agent_shell_tasks "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_task(row) for row in rows]


def append_message(
    *,
    session_id: str,
    role: str,
    content,
    message_type: str = "message",
    related_event_id: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    _ensure_session(session_id)
    message_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO agent_shell_messages ("
        "id, session_id, role, message_type, content, related_event_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            message_id,
            session_id,
            role,
            message_type,
            _json_dump(content),
            related_event_id,
        ),
    )
    _commit_if_needed(db, was_in_tx)
    return message_id


def list_messages(session_id: str, limit: int = 200) -> list[dict]:
    db = get_db()
    rows = db.execute(
        "SELECT * FROM agent_shell_messages "
        "WHERE session_id = ? "
        "ORDER BY created_at ASC, rowid ASC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [_decode_message(row) for row in rows]


def create_artifact(
    *,
    task_id: str | None,
    session_id: str | None,
    artifact_type: str,
    title: str,
    content,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    if session_id:
        _ensure_session(session_id)
    artifact_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO agent_shell_artifacts ("
        "id, task_id, session_id, artifact_type, title, content, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            artifact_id,
            task_id,
            session_id,
            artifact_type,
            title,
            _json_dump(content),
        ),
    )
    _commit_if_needed(db, was_in_tx)
    return artifact_id


def list_artifacts(
    *,
    task_id: str | None = None,
    session_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    db = get_db()
    if task_id:
        rows = db.execute(
            "SELECT * FROM agent_shell_artifacts "
            "WHERE task_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    elif session_id:
        rows = db.execute(
            "SELECT * FROM agent_shell_artifacts "
            "WHERE session_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM agent_shell_artifacts "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_artifact(row) for row in rows]

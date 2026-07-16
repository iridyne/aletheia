"""Operator control-plane persistence helpers."""

import json
import uuid
from datetime import datetime, timezone

from aletheia.agent_shell.file_artifacts import (
    append_session_log,
    write_extension_manifest,
    write_task_note,
)
from aletheia.storage.sqlite import get_db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_load(value):
    if value in (None, ""):
        return None
    return json.loads(value)


def _current_runtime_id() -> str:
    db = get_db()
    try:
        row = db.execute(
            "SELECT runtime_id FROM runtime_registry WHERE state = 'current' "
            "ORDER BY COALESCE(promoted_at, created_at) DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if row:
            return str(row["runtime_id"])
    except Exception:
        pass
    return "runtime-local-current"


def _attach_runtime_ownership(
    *,
    entity_type: str,
    entity_id: str,
    runtime_id: str,
    ownership_kind: str,
    metadata: dict | None = None,
) -> None:
    db = get_db()
    db.execute(
        """
        INSERT INTO runtime_ownership (
            id, entity_type, entity_id, runtime_id, ownership_kind, status, metadata
        ) VALUES (?, ?, ?, ?, ?, 'active', ?)
        ON CONFLICT(entity_type, entity_id, status) WHERE status = 'active'
        DO UPDATE SET runtime_id = excluded.runtime_id,
            ownership_kind = excluded.ownership_kind,
            metadata = excluded.metadata
        """,
        (
            str(uuid.uuid4()),
            entity_type,
            entity_id,
            runtime_id,
            ownership_kind,
            _json_dump(metadata or {}),
        ),
    )


def _commit_if_needed(db, was_in_tx: bool) -> None:
    if not was_in_tx:
        db.commit()


def _ensure_session(
    session_id: str,
    *,
    user_input: str = "[operator session]",
    kind: str = "command_session",
    parent_session_id: str | None = None,
    title: str | None = None,
    runtime_id: str | None = None,
) -> None:
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO agent_shell_sessions "
        "(id, user_input, kind, parent_session_id, title, runtime_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (session_id, user_input, kind, parent_session_id, title, runtime_id or _current_runtime_id()),
    )


def create_session_record(
    *,
    session_id: str,
    user_input: str,
    kind: str = "command_session",
    parent_session_id: str | None = None,
    title: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    if parent_session_id:
        _ensure_session(parent_session_id)
    existing = db.execute(
        "SELECT id FROM agent_shell_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if existing is None:
        db.execute(
            "INSERT INTO agent_shell_sessions "
            "(id, user_input, kind, parent_session_id, title, created_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (session_id, user_input, kind, parent_session_id, title),
        )
    else:
        db.execute(
            "UPDATE agent_shell_sessions SET user_input = ?, kind = ?, parent_session_id = ?, title = ? "
            "WHERE id = ?",
            (user_input, kind, parent_session_id, title, session_id),
        )
    _commit_if_needed(db, was_in_tx)
    append_session_log(
        session_id,
        {
            "type": "session_record",
            "session_id": session_id,
            "kind": kind,
            "user_input": user_input,
            "parent_session_id": parent_session_id,
            "title": title,
            "updated_at": _utc_now(),
        },
    )
    return session_id


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


def _decode_event(row) -> dict:
    item = dict(row)
    item["payload"] = _json_load(item["payload"]) or {}
    return item


def _decode_command(row) -> dict:
    item = dict(row)
    item["parameters"] = _json_load(item["parameters"]) or {}
    item["permitted"] = bool(item["permitted"])
    return item


def _decode_extension(row) -> dict:
    item = dict(row)
    item["manifest"] = _json_load(item["manifest"]) or {}
    return item


def _decode_extension_run(row) -> dict:
    item = dict(row)
    item["input_data"] = _json_load(item["input_data"]) or {}
    item["output_data"] = _json_load(item["output_data"])
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
    runtime_id: str | None = None,
    runtime_mode: str = "current",
    runtime_state: str = "current",
    active_branch_id: str | None = None,
    active_branch_kind: str | None = None,
    active_branch_title: str | None = None,
    last_seen_at: str | None = None,
    last_success_at: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    effective_runtime_id = runtime_id or _current_runtime_id()
    if session_id:
        _ensure_session(session_id, runtime_id=effective_runtime_id)
    heartbeat_id = str(uuid.uuid4())
    seen_at = last_seen_at or _utc_now()
    success_at = last_success_at or (seen_at if status == "online" else None)
    db.execute(
        "INSERT INTO agent_shell_heartbeats ("
        "id, agent_id, runtime_id, runtime_mode, runtime_state, status, mode, permission_mode, is_busy, current_task_id, "
        "current_intent, queue_size, last_seen_at, last_success_at, last_error, "
        "model_provider, session_id, active_branch_id, active_branch_kind, active_branch_title, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            heartbeat_id,
            agent_id,
            effective_runtime_id,
            runtime_mode,
            runtime_state,
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
            active_branch_id,
            active_branch_kind,
            active_branch_title,
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
    runtime_id: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    task_id = str(uuid.uuid4())
    effective_runtime_id = runtime_id or _current_runtime_id()
    effective_started_at = started_at or (_utc_now() if status == "running" else None)
    effective_completed_at = completed_at or (
        _utc_now() if status in {"completed", "failed", "cancelled"} else None
    )
    db.execute(
        "INSERT INTO agent_shell_tasks ("
        "id, runtime_id, task_type, status, priority, created_by, assigned_to, input_data, "
        "output_data, error, started_at, completed_at, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            task_id,
            effective_runtime_id,
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
    _attach_runtime_ownership(
        entity_type="agent_shell_task",
        entity_id=task_id,
        runtime_id=effective_runtime_id,
        ownership_kind="operator_task",
        metadata={"task_type": task_type, "status": status},
    )
    _commit_if_needed(db, was_in_tx)
    task = get_task(task_id)
    if task is not None:
        write_task_note(task)
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
    if status in {"completed", "failed", "cancelled"} and "completed_at" not in updates:
        updates["completed_at"] = _utc_now()

    db = get_db()
    was_in_tx = db.in_transaction
    sets = ", ".join(f"{key} = ?" for key in updates.keys())
    db.execute(
        f"UPDATE agent_shell_tasks SET {sets} WHERE id = ?",
        [*updates.values(), task_id],
    )
    _commit_if_needed(db, was_in_tx)
    task = get_task(task_id)
    if task is not None:
        write_task_note(task)


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
    append_session_log(
        session_id,
        {
            "type": "message",
            "message_id": message_id,
            "role": role,
            "message_type": message_type,
            "content": content,
            "related_event_id": related_event_id,
            "created_at": _utc_now(),
        },
    )
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


def list_commands(
    session_id: str | None = None,
    limit: int = 200,
    *,
    query: str | None = None,
) -> list[dict]:
    db = get_db()
    select_sql = (
        "SELECT c.*, "
        "s.kind AS session_kind, "
        "s.user_input AS session_user_input, "
        "s.title AS session_title "
        "FROM agent_shell_commands c "
        "LEFT JOIN agent_shell_sessions s ON s.id = c.session_id "
    )
    if session_id and not query:
        rows = db.execute(
            select_sql
            + "WHERE c.session_id = ? "
            + "ORDER BY c.created_at ASC, c.rowid ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [_decode_command(row) for row in rows]

    params: list[object] = []
    clauses: list[str] = []

    if session_id:
        clauses.append("c.session_id = ?")
        params.append(session_id)
    if query:
        like = f"%{query.lower()}%"
        clauses.append(
            "("
            "LOWER(c.command) LIKE ? "
            "OR LOWER(COALESCE(c.result_summary, '')) LIKE ? "
            "OR LOWER(COALESCE(c.parameters, '')) LIKE ?"
            ")"
        )
        params.extend([like, like, like])

    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    params.append(limit)
    rows = db.execute(
        select_sql
        + f"{where}"
        + "ORDER BY c.created_at DESC, c.rowid DESC LIMIT ?",
        params,
    ).fetchall()
    return [_decode_command(row) for row in rows]


def list_sessions(limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """
        SELECT
            s.*,
            COALESCE(msg.message_count, 0) AS message_count,
            COALESCE(cmd.command_count, 0) AS command_count
        FROM agent_shell_sessions s
        LEFT JOIN (
            SELECT session_id, COUNT(*) AS message_count
            FROM agent_shell_messages
            GROUP BY session_id
        ) msg ON msg.session_id = s.id
        LEFT JOIN (
            SELECT session_id, COUNT(*) AS command_count
            FROM agent_shell_commands
            GROUP BY session_id
        ) cmd ON cmd.session_id = s.id
        ORDER BY s.created_at DESC, s.rowid DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_session(session_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        """
        SELECT
            s.*,
            COALESCE(msg.message_count, 0) AS message_count,
            COALESCE(cmd.command_count, 0) AS command_count
        FROM agent_shell_sessions s
        LEFT JOIN (
            SELECT session_id, COUNT(*) AS message_count
            FROM agent_shell_messages
            GROUP BY session_id
        ) msg ON msg.session_id = s.id
        LEFT JOIN (
            SELECT session_id, COUNT(*) AS command_count
            FROM agent_shell_commands
            GROUP BY session_id
        ) cmd ON cmd.session_id = s.id
        WHERE s.id = ?
        """,
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


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


def create_event(
    *,
    event_type: str,
    severity: str = "info",
    source: str = "operator",
    session_id: str | None = None,
    task_id: str | None = None,
    related_entity_type: str | None = None,
    related_entity_id: str | None = None,
    payload=None,
    runtime_id: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    effective_runtime_id = runtime_id or _current_runtime_id()
    if session_id:
        _ensure_session(session_id, runtime_id=effective_runtime_id)
    event_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO agent_shell_events ("
        "id, runtime_id, event_type, severity, source, session_id, task_id, related_entity_type, "
        "related_entity_id, payload, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            event_id,
            effective_runtime_id,
            event_type,
            severity,
            source,
            session_id,
            task_id,
            related_entity_type,
            related_entity_id,
            _json_dump(payload or {}),
        ),
    )
    _commit_if_needed(db, was_in_tx)
    if session_id:
        append_session_log(
            session_id,
            {
                "type": "event",
                "event_id": event_id,
                "event_type": event_type,
                "severity": severity,
                "source": source,
                "task_id": task_id,
                "related_entity_type": related_entity_type,
                "related_entity_id": related_entity_id,
                "payload": payload or {},
                "created_at": _utc_now(),
            },
        )
    return event_id


def list_events(
    limit: int = 100,
    *,
    session_id: str | None = None,
    task_id: str | None = None,
) -> list[dict]:
    db = get_db()
    if session_id:
        rows = db.execute(
            "SELECT * FROM agent_shell_events "
            "WHERE session_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    elif task_id:
        rows = db.execute(
            "SELECT * FROM agent_shell_events "
            "WHERE task_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM agent_shell_events "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_event(row) for row in rows]


def create_extension_record(
    *,
    session_id: str | None,
    extension_name: str,
    slash_command: str,
    description: str,
    target_command: str,
    manifest: dict | list | None = None,
    status: str = "draft",
    created_by: str = "operator",
    proposal_artifact_id: str | None = None,
    latest_run_id: str | None = None,
    enabled_at: str | None = None,
    enabled_by: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    if session_id:
        _ensure_session(session_id)
    extension_id = str(uuid.uuid4())
    db.execute(
        "INSERT INTO extension_registry ("
        "id, session_id, extension_name, slash_command, description, target_command, manifest, "
        "status, created_by, proposal_artifact_id, latest_run_id, enabled_at, enabled_by, "
        "created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
        (
            extension_id,
            session_id,
            extension_name,
            slash_command,
            description,
            target_command,
            _json_dump(manifest or {}),
            status,
            created_by,
            proposal_artifact_id,
            latest_run_id,
            enabled_at,
            enabled_by,
        ),
    )
    _commit_if_needed(db, was_in_tx)
    extension = get_extension(extension_id)
    if extension is not None:
        write_extension_manifest(extension)
    return extension_id


def update_extension(extension_id: str, **kwargs) -> None:
    if not kwargs:
        return
    updates = dict(kwargs)
    if "manifest" in updates:
        updates["manifest"] = _json_dump(updates["manifest"] or {})
    updates.setdefault("updated_at", _utc_now())

    db = get_db()
    was_in_tx = db.in_transaction
    if "session_id" in updates and updates["session_id"]:
        _ensure_session(str(updates["session_id"]))
    sets = ", ".join(f"{key} = ?" for key in updates.keys())
    db.execute(
        f"UPDATE extension_registry SET {sets} WHERE id = ?",
        [*updates.values(), extension_id],
    )
    _commit_if_needed(db, was_in_tx)
    extension = get_extension(extension_id)
    if extension is not None:
        write_extension_manifest(extension)


def get_extension(extension_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM extension_registry WHERE id = ?",
        (extension_id,),
    ).fetchone()
    return _decode_extension(row) if row else None


def get_extension_by_slash_command(slash_command: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM extension_registry WHERE slash_command = ?",
        (slash_command,),
    ).fetchone()
    return _decode_extension(row) if row else None


def list_extensions(limit: int = 100, *, status: str | None = None) -> list[dict]:
    db = get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM extension_registry "
            "WHERE status = ? "
            "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM extension_registry "
            "ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_extension(row) for row in rows]


def create_extension_run(
    *,
    extension_id: str,
    session_id: str | None,
    run_type: str,
    status: str,
    summary: str | None = None,
    input_data: dict | list | None = None,
    output_data: dict | list | None = None,
    error: str | None = None,
    scratch_path: str | None = None,
    completed_at: str | None = None,
) -> str:
    db = get_db()
    was_in_tx = db.in_transaction
    if session_id:
        _ensure_session(session_id)
    run_id = str(uuid.uuid4())
    effective_completed_at = completed_at or (_utc_now() if status in {"passed", "failed"} else None)
    db.execute(
        "INSERT INTO extension_runs ("
        "id, extension_id, session_id, run_type, status, summary, input_data, output_data, "
        "error, scratch_path, completed_at, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (
            run_id,
            extension_id,
            session_id,
            run_type,
            status,
            summary,
            _json_dump(input_data or {}),
            _json_dump(output_data) if output_data is not None else None,
            error,
            scratch_path,
            effective_completed_at,
        ),
    )
    _commit_if_needed(db, was_in_tx)
    update_extension(extension_id, latest_run_id=run_id)
    return run_id


def update_extension_run(run_id: str, **kwargs) -> None:
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
    if status in {"passed", "failed"} and "completed_at" not in updates:
        updates["completed_at"] = _utc_now()

    db = get_db()
    was_in_tx = db.in_transaction
    sets = ", ".join(f"{key} = ?" for key in updates.keys())
    db.execute(
        f"UPDATE extension_runs SET {sets} WHERE id = ?",
        [*updates.values(), run_id],
    )
    _commit_if_needed(db, was_in_tx)


def get_extension_run(run_id: str) -> dict | None:
    db = get_db()
    row = db.execute(
        "SELECT * FROM extension_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    return _decode_extension_run(row) if row else None


def list_extension_runs(
    *,
    extension_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    db = get_db()
    if extension_id:
        rows = db.execute(
            "SELECT * FROM extension_runs "
            "WHERE extension_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (extension_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM extension_runs "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_decode_extension_run(row) for row in rows]

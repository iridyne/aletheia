"""Session store — persist command sessions to SQLite."""

import time
import uuid

from aletheia.agent_shell.file_artifacts import append_session_log
from aletheia.storage.sqlite import get_db


def create_session(
    user_input: str,
    *,
    session_id: str | None = None,
    kind: str = "command_session",
    parent_session_id: str | None = None,
    title: str | None = None,
) -> tuple[str, float]:
    """Create a new session record. Returns (session_id, start_time)."""
    start = time.time()
    if session_id is not None:
        from aletheia.agent_shell.operator_store import create_session_record, get_session

        if get_session(session_id) is None:
            create_session_record(
                session_id=session_id,
                user_input=user_input,
                kind=kind,
                parent_session_id=parent_session_id,
                title=title,
            )
        append_session_log(
            session_id,
            {
                "type": "session_reused",
                "session_id": session_id,
                "kind": kind,
                "user_input": user_input,
            },
        )
        return session_id, start

    session_id = str(uuid.uuid4())
    db = get_db()
    db.execute(
        "INSERT INTO agent_shell_sessions (id, user_input, kind, created_at, parent_session_id, title, runtime_id) "
        "VALUES (?, ?, ?, datetime('now'), ?, ?, ?)",
        (session_id, user_input, kind, parent_session_id, title, "runtime-local-current"),
    )
    db.commit()
    append_session_log(
        session_id,
        {
            "type": "session_created",
            "session_id": session_id,
            "kind": kind,
            "user_input": user_input,
            "parent_session_id": parent_session_id,
            "title": title,
        },
    )
    return session_id, start


def close_session(session_id: str, *, intent: str, command: str | None,
                  response: str, permitted: bool, start_time: float) -> None:
    """Update session with results."""
    duration_ms = int((time.time() - start_time) * 1000)
    db = get_db()
    db.execute(
        "UPDATE agent_shell_sessions SET intent=?, command=?, response=?, "
        "permitted=?, duration_ms=? WHERE id=?",
        (intent, command, response[:2000], int(permitted), duration_ms, session_id),
    )
    db.commit()
    append_session_log(
        session_id,
        {
            "type": "session_closed",
            "session_id": session_id,
            "intent": intent,
            "command": command,
            "response": response[:2000],
            "permitted": permitted,
            "duration_ms": duration_ms,
        },
    )

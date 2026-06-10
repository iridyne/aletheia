"""Session store — persist command sessions to SQLite."""

import time
import uuid

from aletheia.storage.sqlite import get_db


def create_session(user_input: str) -> tuple[str, float]:
    """Create a new session record. Returns (session_id, start_time)."""
    session_id = str(uuid.uuid4())
    start = time.time()
    db = get_db()
    db.execute(
        "INSERT INTO agent_shell_sessions (id, user_input, created_at) "
        "VALUES (?, ?, datetime('now'))",
        (session_id, user_input),
    )
    db.commit()
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

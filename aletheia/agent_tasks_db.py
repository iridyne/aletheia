"""Machine-local Task Lite storage for concurrent development agents.

Task state is intentionally outside Git and outside the trading runtime database.
Git, pull requests, and CI remain the source of truth for code and verification;
this store only prevents local agents/worktrees from duplicating work and preserves
small amounts of session-recovery context.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_STATUSES = {"ready", "in_progress", "blocked", "done", "cancelled"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id          TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    intent           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'ready',
    worker_id        TEXT,
    branch           TEXT,
    scope_boundary   TEXT NOT NULL DEFAULT '',
    scope_excluded   TEXT NOT NULL DEFAULT '',
    acceptance_json  TEXT NOT NULL DEFAULT '[]',
    next_action      TEXT NOT NULL DEFAULT '',
    blockers_json    TEXT NOT NULL DEFAULT '[]',
    claimed_at       TEXT,
    expires_at       TEXT,
    updated_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_tasks_active_branch
ON agent_tasks(branch)
WHERE status = 'in_progress' AND branch IS NOT NULL;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect(root: Path) -> sqlite3.Connection:
    from aletheia.config.loader import get_data_dir
    from aletheia.runtime_paths import workflow_tasks_db

    db_path = workflow_tasks_db(root, get_data_dir())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(_SCHEMA)
    return con


def _json_list(value: Any) -> str:
    if value is None:
        value = []
    if not isinstance(value, list):
        raise ValueError("task list fields must be lists")
    return json.dumps(value, ensure_ascii=False)


def _task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["task_id"],
        "title": row["title"],
        "intent": row["intent"],
        "status": row["status"],
        "worker_id": row["worker_id"],
        "branch": row["branch"],
        "scope": {
            "boundary": row["scope_boundary"],
            "excluded": row["scope_excluded"],
        },
        "acceptance": json.loads(row["acceptance_json"] or "[]"),
        "next_action": row["next_action"],
        "blockers": json.loads(row["blockers_json"] or "[]"),
        "claimed_at": row["claimed_at"],
        "expires_at": row["expires_at"],
        "updated_at": row["updated_at"],
    }


def create_task(
    task_id: str,
    *,
    title: str,
    intent: str,
    scope_boundary: str = "",
    scope_excluded: str = "",
    acceptance: list[str] | None = None,
    next_action: str = "",
    root: Path,
) -> dict[str, Any]:
    now = _now()
    try:
        with _connect(root) as con:
            con.execute(
                """INSERT INTO agent_tasks (
                       task_id, title, intent, status, scope_boundary,
                       scope_excluded, acceptance_json, next_action,
                       blockers_json, updated_at
                   ) VALUES (?, ?, ?, 'ready', ?, ?, ?, ?, '[]', ?)""",
                (
                    task_id,
                    title,
                    intent,
                    scope_boundary,
                    scope_excluded,
                    _json_list(acceptance),
                    next_action,
                    now,
                ),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"task already exists: {task_id}") from exc
    task = get_task(task_id, root)
    assert task is not None
    return task


def list_tasks(root: Path) -> list[dict[str, Any]]:
    with _connect(root) as con:
        rows = con.execute(
            "SELECT * FROM agent_tasks ORDER BY updated_at DESC, task_id"
        ).fetchall()
    return [_task(row) for row in rows]


def get_task(task_id: str, root: Path) -> dict[str, Any] | None:
    with _connect(root) as con:
        row = con.execute(
            "SELECT * FROM agent_tasks WHERE task_id=?", (task_id,)
        ).fetchone()
    return _task(row) if row else None


def get_tasks_for_branch(branch: str, root: Path) -> list[dict[str, Any]]:
    with _connect(root) as con:
        rows = con.execute(
            "SELECT * FROM agent_tasks WHERE branch=? ORDER BY updated_at DESC",
            (branch,),
        ).fetchall()
    return [_task(row) for row in rows]


def claim_task_atomic(
    task_id: str,
    worker_id: str,
    branch: str,
    hours: int,
    root: Path,
) -> dict[str, Any] | None:
    """Atomically claim one local task for one branch/worktree.

    The partial unique index prevents two active tasks from claiming the same
    branch. The task database is shared by sibling worktrees of one clone.
    """
    now = _now()
    expires = (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")
    try:
        with _connect(root) as con:
            cursor = con.execute(
                """UPDATE agent_tasks
                   SET status='in_progress', worker_id=?, branch=?, claimed_at=?,
                       expires_at=?, updated_at=?
                   WHERE task_id=? AND status IN ('ready', 'blocked')
                   RETURNING *""",
                (worker_id, branch, now, expires, now, task_id),
            )
            row = cursor.fetchone()
    except sqlite3.IntegrityError:
        return None
    return _task(row) if row else None


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    intent: str | None = None,
    scope_boundary: str | None = None,
    scope_excluded: str | None = None,
    acceptance: list[str] | None = None,
    next_action: str | None = None,
    blockers: list[str] | None = None,
    root: Path,
) -> dict[str, Any]:
    existing = get_task(task_id, root)
    if existing is None:
        raise ValueError(f"task not found: {task_id}")
    if status is not None and status not in _STATUSES:
        raise ValueError(f"unknown task status: {status}")

    fields: list[str] = ["updated_at=?"]
    values: list[Any] = [_now()]
    for column, value in (
        ("status", status),
        ("intent", intent),
        ("scope_boundary", scope_boundary),
        ("scope_excluded", scope_excluded),
        ("next_action", next_action),
    ):
        if value is not None:
            fields.append(f"{column}=?")
            values.append(value)
    if acceptance is not None:
        fields.append("acceptance_json=?")
        values.append(_json_list(acceptance))
    if blockers is not None:
        fields.append("blockers_json=?")
        values.append(_json_list(blockers))
    if status in {"ready", "done", "cancelled"}:
        fields.extend(
            [
                "worker_id=NULL",
                "branch=NULL",
                "claimed_at=NULL",
                "expires_at=NULL",
            ]
        )
    values.append(task_id)
    with _connect(root) as con:
        con.execute(
            f"UPDATE agent_tasks SET {', '.join(fields)} WHERE task_id=?",
            values,
        )
    task = get_task(task_id, root)
    assert task is not None
    return task


def complete_task(task_id: str, root: Path) -> dict[str, Any]:
    return update_task(task_id, status="done", root=root)


def release_expired_claims(root: Path) -> list[str]:
    now = _now()
    with _connect(root) as con:
        cursor = con.execute(
            """UPDATE agent_tasks
               SET status='ready', worker_id=NULL, branch=NULL,
                   claimed_at=NULL, expires_at=NULL, updated_at=?
               WHERE status='in_progress' AND expires_at IS NOT NULL AND expires_at < ?
               RETURNING task_id""",
            (now, now),
        )
        return [row[0] for row in cursor.fetchall()]


def delete_task(task_id: str, root: Path) -> bool:
    with _connect(root) as con:
        cursor = con.execute("DELETE FROM agent_tasks WHERE task_id=?", (task_id,))
        return cursor.rowcount > 0

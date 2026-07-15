"""Machine-local SQLite claim store for agent task coordination.

Separate from data/app.db (trading runtime). Only tracks who is working on what
and when claims expire — durable intent (status, verification, handoff) stays in
git-tracked YAML.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_DB_PATH = "data/tasks.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_task_claims (
    task_id     TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'ready',
    worker_id   TEXT,
    branch      TEXT,
    claimed_at  TEXT,
    expires_at  TEXT,
    updated_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _connect(root: Path) -> sqlite3.Connection:
    db_path = root / _DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(_SCHEMA)
    return con


def sync_tasks(tasks: list[dict[str, Any]], root: Path) -> None:
    """Upsert YAML task list into SQLite.

    Rules:
    - Terminal YAML status (done/cancelled) always wins — overrides any active claim.
    - SQLite in_progress + YAML in_progress → keep SQLite claim info (it has exact timestamps).
    - YAML in_progress + SQLite ready/blocked → migrate ownership from YAML.
    - Everything else → sync status from YAML.
    """
    now = _now()
    with _connect(root) as con:
        for task in tasks:
            task_id = task.get("id")
            if not task_id:
                continue
            yaml_status = task.get("status", "ready")
            row = con.execute(
                "SELECT status FROM agent_task_claims WHERE task_id=?", (task_id,)
            ).fetchone()

            if yaml_status in ("done", "cancelled"):
                con.execute(
                    """INSERT INTO agent_task_claims (task_id, status, updated_at)
                       VALUES (?,?,?)
                       ON CONFLICT(task_id) DO UPDATE SET
                         status=excluded.status, worker_id=NULL, branch=NULL,
                         claimed_at=NULL, expires_at=NULL, updated_at=excluded.updated_at""",
                    (task_id, yaml_status, now),
                )
            elif row and row["status"] == "in_progress":
                con.execute(
                    "UPDATE agent_task_claims SET updated_at=? WHERE task_id=?",
                    (now, task_id),
                )
            elif yaml_status == "in_progress":
                ownership = task.get("ownership") or {}
                branch = ownership.get("branch") if isinstance(ownership, dict) else None
                worker = ownership.get("lease_holder") if isinstance(ownership, dict) else None
                claimed_at = ownership.get("claimed_at") if isinstance(ownership, dict) else None
                expires_at = ownership.get("expires_at") if isinstance(ownership, dict) else None
                con.execute(
                    """INSERT INTO agent_task_claims
                       (task_id, status, worker_id, branch, claimed_at, expires_at, updated_at)
                       VALUES (?,?,?,?,?,?,?)
                       ON CONFLICT(task_id) DO UPDATE SET
                         status=excluded.status, worker_id=excluded.worker_id,
                         branch=excluded.branch, claimed_at=excluded.claimed_at,
                         expires_at=excluded.expires_at, updated_at=excluded.updated_at""",
                    (task_id, "in_progress", worker, branch, claimed_at, expires_at, now),
                )
            else:
                con.execute(
                    """INSERT INTO agent_task_claims (task_id, status, updated_at)
                       VALUES (?,?,?)
                       ON CONFLICT(task_id) DO UPDATE SET
                         status=excluded.status, updated_at=excluded.updated_at""",
                    (task_id, yaml_status, now),
                )


def claim_task_atomic(
    task_id: str, worker_id: str, branch: str, hours: int, root: Path
) -> bool:
    """Attempt to atomically claim a ready/blocked task.

    Returns True on success, False when already claimed by another worker.
    Uses UPDATE...RETURNING so no TOCTOU window exists between the read and write.
    """
    now = _now()
    expires = (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")
    with _connect(root) as con:
        con.execute(
            "INSERT OR IGNORE INTO agent_task_claims (task_id, status, updated_at) VALUES (?,?,?)",
            (task_id, "ready", now),
        )
        cursor = con.execute(
            """UPDATE agent_task_claims
               SET status='in_progress', worker_id=?, branch=?, claimed_at=?,
                   expires_at=?, updated_at=?
               WHERE task_id=? AND status IN ('ready','blocked')
               RETURNING task_id""",
            (worker_id, branch, now, expires, now, task_id),
        )
        return cursor.fetchone() is not None


def get_claim(task_id: str, root: Path) -> dict[str, Any] | None:
    with _connect(root) as con:
        row = con.execute(
            "SELECT * FROM agent_task_claims WHERE task_id=?", (task_id,)
        ).fetchone()
        return dict(row) if row else None


def get_claims_for_branch(branch: str, root: Path) -> list[dict[str, Any]]:
    with _connect(root) as con:
        rows = con.execute(
            "SELECT * FROM agent_task_claims WHERE branch=?", (branch,)
        ).fetchall()
        return [dict(row) for row in rows]


def release_claim(task_id: str, root: Path) -> None:
    """Mark a claim as done in SQLite (task completed or cancelled)."""
    with _connect(root) as con:
        con.execute(
            """UPDATE agent_task_claims
               SET status='done', worker_id=NULL, branch=NULL,
                   claimed_at=NULL, expires_at=NULL, updated_at=?
               WHERE task_id=?""",
            (_now(), task_id),
        )


def release_expired_claims(root: Path) -> list[str]:
    """Reset timed-out in_progress claims back to ready.

    Returns the list of task IDs that were released.
    """
    now = _now()
    with _connect(root) as con:
        cursor = con.execute(
            """UPDATE agent_task_claims
               SET status='ready', worker_id=NULL, branch=NULL,
                   claimed_at=NULL, expires_at=NULL, updated_at=?
               WHERE status='in_progress' AND expires_at IS NOT NULL AND expires_at < ?
               RETURNING task_id""",
            (now, now),
        )
        return [row[0] for row in cursor.fetchall()]

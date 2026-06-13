"""Agent checkpoint store — enables long-running tasks to survive restarts."""

import json
from datetime import datetime, timezone

from aletheia.storage.models import PSCheckpoint, new_id
from aletheia.storage.sqlite import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointStore:
    """Manages agent run checkpoints for resumable tasks."""

    def save(self, run_id: str, step_name: str, state: dict, artifact_path: str | None = None) -> PSCheckpoint:
        """Save a checkpoint. Upserts on (run_id, step_name)."""
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        state_json = json.dumps(state, ensure_ascii=False)

        existing = db.execute(
            "SELECT checkpoint_id FROM ps_checkpoints WHERE run_id = ? AND step_name = ?",
            (run_id, step_name),
        ).fetchone()

        if existing:
            db.execute(
                "UPDATE ps_checkpoints SET state_json = ?, artifact_path = ?, created_at = ? "
                "WHERE run_id = ? AND step_name = ?",
                (state_json, artifact_path, now, run_id, step_name),
            )
            checkpoint_id = existing["checkpoint_id"]
        else:
            checkpoint_id = new_id()
            db.execute(
                "INSERT INTO ps_checkpoints (checkpoint_id, run_id, step_name, state_json, artifact_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (checkpoint_id, run_id, step_name, state_json, artifact_path, now),
            )

        if not was_in_tx:
            db.commit()

        return PSCheckpoint(
            checkpoint_id=checkpoint_id,
            run_id=run_id,
            step_name=step_name,
            state_json=state_json,
            artifact_path=artifact_path,
            created_at=now,
        )

    def exists(self, run_id: str, step_name: str) -> bool:
        """Check if a checkpoint exists for this run+step."""
        db = get_db()
        row = db.execute(
            "SELECT 1 FROM ps_checkpoints WHERE run_id = ? AND step_name = ?",
            (run_id, step_name),
        ).fetchone()
        return row is not None

    def load(self, run_id: str, step_name: str) -> dict | None:
        """Load checkpoint state. Returns None if not found."""
        db = get_db()
        row = db.execute(
            "SELECT state_json FROM ps_checkpoints WHERE run_id = ? AND step_name = ?",
            (run_id, step_name),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["state_json"])

    def list_for_run(self, run_id: str) -> list[dict]:
        """List all checkpoints for a run, ordered by creation time."""
        db = get_db()
        rows = db.execute(
            "SELECT * FROM ps_checkpoints WHERE run_id = ? ORDER BY created_at ASC",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def last_completed_step(self, run_id: str) -> str | None:
        """Return the name of the last completed step, or None."""
        db = get_db()
        row = db.execute(
            "SELECT step_name FROM ps_checkpoints WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return row["step_name"] if row else None

    def delete_for_run(self, run_id: str) -> int:
        """Delete all checkpoints for a run. Returns count deleted."""
        db = get_db()
        was_in_tx = db.in_transaction
        cur = db.execute("DELETE FROM ps_checkpoints WHERE run_id = ?", (run_id,))
        if not was_in_tx:
            db.commit()
        return cur.rowcount or 0

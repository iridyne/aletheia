"""Lesson store — persists trade lessons from agent experience."""

import json
from datetime import datetime, timezone

from aletheia.storage.event_store import get_event_store, make_event
from aletheia.storage.models import PSLesson, new_id
from aletheia.storage.sqlite import get_db


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class LessonStore:
    """CRUD for trade lessons extracted by agents."""

    def create(
        self,
        summary: str,
        source_type: str,
        source_id: str,
        category: str = "general",
        severity: str = "info",
        evidence: dict | None = None,
        recommendation: dict | None = None,
    ) -> PSLesson:
        """Create a new lesson in proposed status."""
        db = get_db()
        was_in_tx = db.in_transaction
        now = _utcnow()
        lesson_id = new_id()

        lesson = PSLesson(
            lesson_id=lesson_id,
            source_type=source_type,
            source_id=source_id,
            category=category,
            severity=severity,
            summary=summary,
            evidence_json=json.dumps(evidence or {}, ensure_ascii=False),
            recommendation_json=json.dumps(recommendation, ensure_ascii=False) if recommendation else None,
            status="proposed",
            created_at=now,
        )

        db.execute(
            "INSERT INTO ps_lessons (lesson_id, source_type, source_id, category, severity, "
            "summary, evidence_json, recommendation_json, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lesson_id, source_type, source_id, category, severity,
             summary, lesson.evidence_json, lesson.recommendation_json, "proposed", now),
        )
        if not was_in_tx:
            db.commit()

        get_event_store().append(make_event(
            "LESSON_CREATED", "lesson", lesson_id,
            payload={"summary": summary, "source_type": source_type, "source_id": source_id},
        ))

        return lesson

    def get(self, lesson_id: str) -> PSLesson | None:
        db = get_db()
        row = db.execute("SELECT * FROM ps_lessons WHERE lesson_id = ?", (lesson_id,)).fetchone()
        if row is None:
            return None
        return PSLesson(**dict(row))

    def list_all(
        self,
        status: str | None = None,
        limit: int = 100,
        *,
        oldest_first: bool = False,
    ) -> list[PSLesson]:
        db = get_db()
        direction = "ASC" if oldest_first else "DESC"
        if status:
            rows = db.execute(
                f"SELECT * FROM ps_lessons WHERE status = ? ORDER BY created_at {direction} LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = db.execute(
                f"SELECT * FROM ps_lessons ORDER BY created_at {direction} LIMIT ?", (limit,)
            ).fetchall()
        return [PSLesson(**dict(r)) for r in rows]

    def backlog_stats(self) -> tuple[int, str | None]:
        row = get_db().execute(
            "SELECT COUNT(*), MIN(created_at) FROM ps_lessons WHERE status='proposed'"
        ).fetchone()
        return int(row[0] or 0), row[1]

    def accept(self, lesson_id: str) -> None:
        """Move a lesson from proposed to accepted; stamps guard timestamps."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        activated_at = now.isoformat()
        dry_run_until = (now + timedelta(hours=24)).isoformat()
        expires_at = (now + timedelta(days=30)).isoformat()
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_lessons SET status = 'accepted',"
            " activated_at = ?, dry_run_until = ?, expires_at = ?"
            " WHERE lesson_id = ?",
            (activated_at, dry_run_until, expires_at, lesson_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "LESSON_ACCEPTED", "lesson", lesson_id,
            payload={"status": "accepted"},
        ))

    def reject(self, lesson_id: str) -> None:
        """Mark a lesson as rejected."""
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_lessons SET status = 'rejected' WHERE lesson_id = ?",
            (lesson_id,),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "LESSON_REJECTED", "lesson", lesson_id,
            payload={"status": "rejected"},
        ))

    def mark_applied(self, lesson_id: str, policy_version_id: str) -> None:
        """Stamp the policy version a lesson influenced and move it to 'applied'.

        Closes the lesson→policy chain: an accepted lesson that has been folded
        into a policy version records that version id here, so the
        content.lesson_chain doctor probe (applied_policy_version IS NOT NULL)
        reflects a real influence, not a stub.
        """
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_lessons SET applied_policy_version = ?, status = 'applied',"
            " dry_run_until = NULL "
            "WHERE lesson_id = ?",
            (policy_version_id, lesson_id),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "LESSON_APPLIED", "lesson", lesson_id,
            payload={"status": "applied", "policy_version_id": policy_version_id},
        ))

    def archive(self, lesson_id: str) -> None:
        """Mark a lesson as archived (superseded)."""
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_lessons SET status = 'archived' WHERE lesson_id = ?",
            (lesson_id,),
        )
        if not was_in_tx:
            db.commit()
        get_event_store().append(make_event(
            "LESSON_ARCHIVED", "lesson", lesson_id,
            payload={"status": "archived"},
        ))

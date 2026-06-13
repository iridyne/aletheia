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

    def list_all(self, status: str | None = None, limit: int = 100) -> list[PSLesson]:
        db = get_db()
        if status:
            rows = db.execute(
                "SELECT * FROM ps_lessons WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM ps_lessons ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [PSLesson(**dict(r)) for r in rows]

    def accept(self, lesson_id: str) -> None:
        """Move a lesson from proposed to accepted."""
        db = get_db()
        was_in_tx = db.in_transaction
        db.execute(
            "UPDATE ps_lessons SET status = 'accepted' WHERE lesson_id = ?",
            (lesson_id,),
        )
        if not was_in_tx:
            db.commit()

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

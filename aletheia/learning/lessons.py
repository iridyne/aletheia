"""Lesson store — persists trade lessons from agent experience."""

import json
import logging
from datetime import datetime, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.event_store import get_event_store, make_event
from aletheia.storage.models import PSLesson, new_id
from aletheia.storage.sqlite import get_db

logger = logging.getLogger(__name__)


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

    def _transition(
        self,
        lesson_id: str,
        *,
        expected_status: str,
        status: str,
        event_type: str,
        updates: dict[str, str] | None = None,
    ) -> bool:
        db = get_db()
        had_outer_transaction = db.in_transaction
        values = {"status": status, **(updates or {})}
        assignments = ", ".join(f"{column} = ?" for column in values)
        event = make_event(
            event_type,
            "lesson",
            lesson_id,
            payload={"status": status, **(updates or {})},
            idempotency_key=(
                f"lesson-transition:{lesson_id}:{expected_status}:{status}"
            ),
        )
        with repo.transaction(immediate=True):
            cursor = db.execute(
                f"UPDATE ps_lessons SET {assignments} "
                "WHERE lesson_id = ? AND status = ?",
                (*values.values(), lesson_id, expected_status),
            )
            if cursor.rowcount != 1:
                return False
            get_event_store().append(event)
        if not had_outer_transaction:
            try:
                get_event_store().append(event)
            except Exception:
                logger.exception("lesson event JSONL mirror scheduling failed")
        return True

    def accept(self, lesson_id: str) -> bool:
        """Move a lesson from proposed to accepted; stamps guard timestamps."""
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        activated_at = now.isoformat()
        dry_run_until = (now + timedelta(hours=24)).isoformat()
        expires_at = (now + timedelta(days=30)).isoformat()
        return self._transition(
            lesson_id,
            expected_status="proposed",
            status="accepted",
            event_type="LESSON_ACCEPTED",
            updates={
                "activated_at": activated_at,
                "dry_run_until": dry_run_until,
                "expires_at": expires_at,
            },
        )

    def reject(self, lesson_id: str, *, expected_status: str = "proposed") -> bool:
        """Mark a lesson as rejected."""
        if expected_status == "rejected":
            return False
        return self._transition(
            lesson_id,
            expected_status=expected_status,
            status="rejected",
            event_type="LESSON_REJECTED",
        )

    def mark_applied(self, lesson_id: str, policy_version_id: str) -> bool:
        """Stamp the policy version a lesson influenced and move it to 'applied'.

        Closes the lesson→policy chain: an accepted lesson that has been folded
        into a policy version records that version id here, so the
        content.lesson_chain doctor probe (applied_policy_version IS NOT NULL)
        reflects a real influence, not a stub.
        """
        return self._transition(
            lesson_id,
            expected_status="accepted",
            status="applied",
            event_type="LESSON_APPLIED",
            updates={"applied_policy_version": policy_version_id},
        )

    def archive(self, lesson_id: str, *, expected_status: str = "proposed") -> bool:
        """Mark a lesson as archived (superseded)."""
        if expected_status == "archived":
            return False
        return self._transition(
            lesson_id,
            expected_status=expected_status,
            status="archived",
            event_type="LESSON_ARCHIVED",
        )

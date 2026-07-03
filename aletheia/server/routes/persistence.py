"""Persistence subsystem API routes.

Endpoints for gateway state, reconciliation, events, backups, exports, lessons, skills, policies.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/persistence", tags=["persistence"])


# --- Gateway State ---


@router.get("/gateway/status")
async def get_gateway_status():
    """Current gateway state machine status."""
    from aletheia.gateway.state_machine import GatewayStateMachine

    sm = GatewayStateMachine()
    return {"status": sm.status.value, "freeze_reason": sm.freeze_reason}


@router.get("/gateway/sessions")
async def list_gateway_sessions(limit: int = Query(default=10, le=50)):
    """Recent gateway sessions."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    rows = db.execute(
        "SELECT * FROM ps_gateway_sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/gateway/reconciliation-reports")
async def list_reconciliation_reports(limit: int = Query(default=10, le=50)):
    """Recent reconciliation reports."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    rows = db.execute(
        "SELECT * FROM ps_reconciliation_reports ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- Events ---


@router.get("/events")
async def list_events(
    aggregate_type: str | None = None,
    aggregate_id: str | None = None,
    event_type: str | None = None,
    limit: int = Query(default=50, le=200),
):
    """Query the event log."""
    from aletheia.storage.event_store import get_event_store

    es = get_event_store()
    if aggregate_type and aggregate_id:
        events = es.list_by_aggregate(aggregate_type, aggregate_id)
    elif event_type:
        events = es.list_by_type(event_type, limit=limit)
    else:
        events = es.list_since(limit=limit)
    return [e.model_dump() for e in events]


# --- Orders / Positions ---


@router.get("/orders")
async def list_orders(status: str | None = None, mode: str = "paper", limit: int = Query(default=50, le=200)):
    """Query persisted orders."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    sql = "SELECT * FROM ps_orders WHERE mode = ?"
    params: list = [mode]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/positions")
async def list_positions(mode: str = "paper", status: str | None = None):
    """Query persisted positions."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    sql = "SELECT * FROM ps_positions WHERE mode = ?"
    params: list = [mode]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY opened_at DESC"
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# --- Backups ---


@router.get("/backups")
async def list_backups():
    """List available backups."""
    from aletheia.backup.service import BackupService

    svc = BackupService()
    return svc.list_backups()


class BackupRequest(BaseModel):
    label: str | None = None


@router.post("/backups")
async def create_backup(req: BackupRequest):
    """Create a new backup."""
    from aletheia.backup.service import BackupService

    svc = BackupService()
    path = svc.create_backup(label=req.label)
    return {"path": str(path), "status": "ok"}


@router.post("/backups/verify")
async def verify_backup_endpoint(path: str):
    """Verify a backup archive."""
    from pathlib import Path

    from aletheia.backup.verify import verify_backup

    result = verify_backup(Path(path))
    return result.to_dict()


# --- Export / Import ---


class ExportRequest(BaseModel):
    mode: str = "full-backup"
    since: str | None = None
    proposal_id: str | None = None


@router.post("/export")
async def create_export(req: ExportRequest):
    """Create an export bundle."""
    from aletheia.sync.exporter import ExportService

    svc = ExportService()
    path = svc.export_bundle(mode=req.mode, since=req.since, proposal_id=req.proposal_id)
    return {"path": str(path), "status": "ok"}


class ImportRequest(BaseModel):
    path: str
    dry_run: bool = True


@router.post("/import")
async def import_bundle(req: ImportRequest):
    """Import an export bundle. Set dry_run=false to actually merge."""
    from pathlib import Path

    from aletheia.sync.importer import ImportService

    svc = ImportService()
    archive = Path(req.path)
    if not archive.exists():
        raise HTTPException(status_code=404, detail="Archive not found")

    if req.dry_run:
        result = svc.dry_run(archive)
    else:
        result = svc.merge(archive)
    return result.to_dict()


# --- Lessons ---


@router.get("/lessons")
async def list_lessons(status: str | None = None, limit: int = Query(default=50, le=200)):
    """List lessons."""
    from aletheia.learning.lessons import LessonStore

    store = LessonStore()
    lessons = store.list_all(status=status, limit=limit)
    return [l.model_dump() for l in lessons]


@router.get("/lessons/{lesson_id}")
async def get_lesson(lesson_id: str):
    """Get a single lesson."""
    from aletheia.learning.lessons import LessonStore

    store = LessonStore()
    lesson = store.get(lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson.model_dump()


def _lesson_or_404(store, lesson_id: str):
    lesson = store.get(lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return lesson


@router.post("/lessons/{lesson_id}/accept")
async def accept_lesson(lesson_id: str):
    """Accept a proposed lesson into dry-run review."""
    from aletheia.learning.lessons import LessonStore

    store = LessonStore()
    _lesson_or_404(store, lesson_id)
    store.accept(lesson_id)
    return _lesson_or_404(store, lesson_id).model_dump()


@router.post("/lessons/{lesson_id}/reject")
async def reject_lesson(lesson_id: str):
    """Reject a lesson so it no longer enters the promotion path."""
    from aletheia.learning.lessons import LessonStore

    store = LessonStore()
    _lesson_or_404(store, lesson_id)
    store.reject(lesson_id)
    return _lesson_or_404(store, lesson_id).model_dump()


@router.post("/lessons/{lesson_id}/archive")
async def archive_lesson(lesson_id: str):
    """Archive a superseded lesson without deleting its record."""
    from aletheia.learning.lessons import LessonStore

    store = LessonStore()
    _lesson_or_404(store, lesson_id)
    store.archive(lesson_id)
    return _lesson_or_404(store, lesson_id).model_dump()


# --- Skills ---


@router.get("/skills")
async def list_skills(scope: str | None = None, status: str | None = None, limit: int = Query(default=50, le=200)):
    """List skills."""
    from aletheia.learning.skills import SkillStore

    store = SkillStore()
    skills = store.list_all(scope=scope, status=status, limit=limit)
    return [s.model_dump() for s in skills]


# --- Policies ---


@router.get("/policies")
async def list_policies():
    """List all policy types."""
    from aletheia.policies.store import PolicyStore

    store = PolicyStore()
    return {"policy_types": store.list_policy_types()}


@router.get("/policies/{policy_type}")
async def get_policy(policy_type: str):
    """Get current version of a policy type."""
    from aletheia.policies.store import PolicyStore

    store = PolicyStore()
    current = store.get_current(policy_type)
    if not current:
        raise HTTPException(status_code=404, detail="Policy not found")
    return current.model_dump()


@router.get("/policies/{policy_type}/versions")
async def list_policy_versions(policy_type: str):
    """List all versions of a policy type."""
    from aletheia.policies.store import PolicyStore

    store = PolicyStore()
    versions = store.list_versions(policy_type)
    return [v.model_dump() for v in versions]


@router.get("/policies/diff")
async def diff_policy(from_id: str = Query(...), to_id: str = Query(...)):
    """Diff two policy versions by their IDs."""
    from aletheia.policies.diff import diff_policy_versions

    try:
        return diff_policy_versions(from_id, to_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

"""Persistence subsystem API routes.

Endpoints for gateway state, reconciliation, events, backups, exports, lessons, skills, policies.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/api/persistence", tags=["persistence"])


def _archive_roots() -> list[Path]:
    """Directories the persistence import/verify endpoints will accept archives from.

    Currently `<data_dir>/backups` (where BackupService writes) and `<data_dir>/exports`
    (where ExportService writes). Kept in a helper so both endpoints stay in sync and a
    later batch that adds an additional archive root only has to touch one place.
    """
    from aletheia.config.loader import get_data_dir
    data_dir = get_data_dir()
    return [data_dir / "backups", data_dir / "exports"]


def _resolve_archive_path(caller_path: str) -> Path:
    """Constrain a caller-supplied archive path to one of the archive roots.

    #66 fix: `/backups/verify` and `/import` used to accept any local path and hand
    it straight to BackupVerifier / ImportService, so anyone able to reach the API
    could make the backend read arbitrary files (config, keys, sockets, /proc/*) on
    the host. Now we resolve the path and require it to live inside one of the
    configured archive roots.

    Raises HTTPException(400) on path-traversal / out-of-root, (404) on missing files.
    """
    if not caller_path or ".." in caller_path.split("/") or ".." in caller_path.split("\\"):
        raise HTTPException(status_code=400, detail="invalid_archive_path")
    try:
        # Reject NUL bytes and other pathological input early — Path() itself is fine
        # with unicode filenames, so a strict resolve() is the trust boundary here.
        resolved = Path(caller_path).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid_archive_path: {exc}") from exc
    roots = [r.resolve() for r in _archive_roots()]
    if not any(_path_is_within(resolved, root) for root in roots):
        raise HTTPException(
            status_code=400,
            detail="archive_path_outside_managed_roots",
        )
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="archive_not_found")
    if not resolved.is_file():
        raise HTTPException(status_code=400, detail="archive_path_not_a_file")
    return resolved


def _path_is_within(candidate: Path, root: Path) -> bool:
    """True when candidate resolves to a location inside root (both already resolved)."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


# --- Gateway State ---


@router.get("/gateway/status")
def get_gateway_status():
    """Retired PS recovery has no current runtime status or activation path."""
    return {"status": "not_available", "freeze_reason": None}


@router.get("/gateway/sessions")
def list_gateway_sessions(limit: int = Query(default=10, le=50)):
    """Recent gateway sessions."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    rows = db.execute(
        "SELECT * FROM ps_gateway_sessions ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


@router.get("/gateway/reconciliation-reports")
def list_reconciliation_reports(limit: int = Query(default=10, le=50)):
    """Recent reconciliation reports."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    rows = db.execute(
        "SELECT * FROM ps_reconciliation_reports ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- Events ---


@router.get("/events")
def list_events(
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
def list_orders(status: str | None = None, mode: str = "paper", limit: int = Query(default=50, le=200)):
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


# Light-projection scalar fields returned by default (no raw_json blob).
# Each is ~10 bytes; 50 rows ≈ 4 KB. Callers may request individual detail
# rows via GET /positions/{id} which includes the full raw_json payload.
_POSITION_LIST_FIELDS = (
    "position_id", "mode", "venue", "symbol", "side",
    "quantity", "avg_entry_price", "realized_pnl", "unrealized_pnl",
    "status", "opened_at", "closed_at", "updated_at",
)


@router.get("/positions")
def list_positions(
    mode: str = "paper",
    status: str | None = None,
    limit: int = 200,
    cursor_id: str | None = None,
):
    """Query persisted positions (light projection).

    Returns scalar fields only — no raw_json payload.  Use GET /positions/{id}
    to fetch the full detail record including reasoning evidence when the
    operator drills into a specific row.

    Pagination: pass the last returned ``position_id`` as ``cursor_id`` to
    fetch the next page (keyset pagination, stable regardless of inserts).
    """
    from aletheia.storage.sqlite import get_db

    cols = ", ".join(_POSITION_LIST_FIELDS)
    db = get_db()
    sql = f"SELECT {cols} FROM ps_positions WHERE mode = ?"  # noqa: S608
    params: list = [mode]
    if status:
        sql += " AND status = ?"
        params.append(status)
    if cursor_id:
        # Keyset: rows opened strictly before the cursor row (DESC order means
        # lower opened_at / earlier position_id comes after the cursor).
        sql += " AND (opened_at, position_id) < (SELECT opened_at, position_id FROM ps_positions WHERE position_id = ?)"
        params.append(cursor_id)
    sql += " ORDER BY opened_at DESC, position_id DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/positions/{position_id}")
def get_position(position_id: str):
    """Return a single position with full raw_json detail payload."""
    from aletheia.storage.sqlite import get_db
    import json as _json

    db = get_db()
    row = db.execute(
        "SELECT * FROM ps_positions WHERE position_id = ?", [position_id]
    ).fetchone()
    if row is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Position not found")
    data = dict(row)
    raw = data.pop("raw_json", None)
    if raw:
        try:
            parsed = _json.loads(raw)
            if isinstance(parsed, dict):
                # Merge parsed fields; explicit columns win on conflict.
                merged = {**parsed, **data}
                return merged
        except Exception:
            pass
    return data


# --- Backups ---

# These archive endpoints intentionally use synchronous handlers. FastAPI runs
# `def` endpoints in its worker threadpool, keeping filesystem walks, SQLite
# backups, checksum verification, and archive compression off the event loop.


@router.get("/backups")
def list_backups():
    """List available backups."""
    from aletheia.backup.service import BackupService

    svc = BackupService()
    return svc.list_backups()


class BackupRequest(BaseModel):
    label: str | None = None
    # A live HTTP request cannot prove a quiesced cross-store cut. It is
    # therefore explicitly an online, non-recovery snapshot.
    profile: str = "online"


@router.post("/backups")
def create_backup(req: BackupRequest):
    """Create a new backup."""
    from aletheia.backup.service import BackupService

    svc = BackupService()
    if req.profile != "online":
        raise HTTPException(
            status_code=400,
            detail="POST /backups only creates the explicit online non-recovery profile",
        )
    path = svc.create_backup(label=req.label)
    return {"path": str(path), "status": "ok", "profile": "online", "recovery_eligible": False}


@router.post("/backups/verify")
def verify_backup_endpoint(path: str):
    """Verify a backup archive.

    Only accepts paths inside the managed archive roots (data_dir/backups or
    data_dir/exports) — see #66 fix in `_resolve_archive_path`.
    """
    from aletheia.backup.verify import verify_backup

    archive = _resolve_archive_path(path)
    result = verify_backup(archive)
    return result.to_dict()


# --- Export / Import ---


class ExportRequest(BaseModel):
    mode: str = "full-backup"
    since: str | None = None
    proposal_id: str | None = None


@router.post("/export")
def create_export(req: ExportRequest):
    """Create an export bundle."""
    from extensions.sync.exporter import ExportService

    svc = ExportService()
    path = svc.export_bundle(mode=req.mode, since=req.since, proposal_id=req.proposal_id)
    status = "degraded" if svc.config_status in {"absent", "omitted"} else "ok"
    return {"path": str(path), "status": status, "config_status": svc.config_status}


class ImportRequest(BaseModel):
    path: str
    dry_run: bool = True


@router.post("/import")
def import_bundle(req: ImportRequest):
    """Import namespaced research only. Set dry_run=false to actually merge.

    Proposal and retired runtime snapshots are export/read-only, not mergeable
    trading state. Managed backup/restore is a separate explicit workflow.

    Only accepts paths inside the managed archive roots (data_dir/backups or
    data_dir/exports) — see #66 fix in `_resolve_archive_path`.
    """
    from extensions.sync.importer import ImportService

    archive = _resolve_archive_path(req.path)
    svc = ImportService()
    if req.dry_run:
        result = svc.dry_run(archive)
    else:
        result = svc.merge(archive)
    return result.to_dict()


# --- Lessons ---


@router.get("/lessons")
def list_lessons(status: str | None = None, limit: int = Query(default=50, le=200)):
    """List lessons."""
    from extensions.learning.lessons import LessonStore

    store = LessonStore()
    lessons = store.list_all(status=status, limit=limit)
    return [l.model_dump() for l in lessons]


@router.get("/lessons/{lesson_id}")
def get_lesson(lesson_id: str):
    """Get a single lesson."""
    from extensions.learning.lessons import LessonStore

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


def _lesson_transition_result(store, lesson_id: str, target_status: str):
    lesson = _lesson_or_404(store, lesson_id)
    if lesson.status != target_status:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Lesson transition conflict: expected {target_status}, "
                f"found {lesson.status}"
            ),
        )
    return lesson.model_dump()


@router.post("/lessons/{lesson_id}/accept")
def accept_lesson(lesson_id: str):
    """Accept a proposed lesson into dry-run review."""
    from extensions.learning.lessons import LessonStore

    store = LessonStore()
    _lesson_or_404(store, lesson_id)
    store.accept(lesson_id)
    return _lesson_transition_result(store, lesson_id, "accepted")


@router.post("/lessons/{lesson_id}/reject")
def reject_lesson(lesson_id: str):
    """Reject a lesson so it no longer enters the promotion path."""
    from extensions.learning.lessons import LessonStore

    store = LessonStore()
    lesson = _lesson_or_404(store, lesson_id)
    store.reject(lesson_id, expected_status=lesson.status)
    return _lesson_transition_result(store, lesson_id, "rejected")


@router.post("/lessons/{lesson_id}/archive")
def archive_lesson(lesson_id: str):
    """Archive a superseded lesson without deleting its record."""
    from extensions.learning.lessons import LessonStore

    store = LessonStore()
    lesson = _lesson_or_404(store, lesson_id)
    store.archive(lesson_id, expected_status=lesson.status)
    return _lesson_transition_result(store, lesson_id, "archived")


# --- Skills ---


@router.get("/skills")
def list_skills(scope: str | None = None, status: str | None = None, limit: int = Query(default=50, le=200)):
    """List skills."""
    from extensions.learning.skills import SkillStore

    store = SkillStore()
    skills = store.list_all(scope=scope, status=status, limit=limit)
    return [s.model_dump() for s in skills]


# --- Policies ---


@router.get("/policies")
def list_policies():
    """List all policy types."""
    from extensions.policies.store import PolicyStore

    store = PolicyStore()
    return {"policy_types": store.list_policy_types()}


@router.get("/policies/diff")
def diff_policy(from_id: str = Query(...), to_id: str = Query(...)):
    """Diff two policy versions by their IDs."""
    from extensions.policies.diff import diff_policy_versions

    try:
        return diff_policy_versions(from_id, to_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/policies/{policy_type}")
def get_policy(policy_type: str):
    """Get current version of a policy type."""
    from extensions.policies.store import PolicyStore

    store = PolicyStore()
    current = store.get_current(policy_type)
    if not current:
        raise HTTPException(status_code=404, detail="Policy not found")
    return current.model_dump()


@router.get("/policies/{policy_type}/versions")
def list_policy_versions(policy_type: str):
    """List all versions of a policy type."""
    from extensions.policies.store import PolicyStore

    store = PolicyStore()
    versions = store.list_versions(policy_type)
    return [v.model_dump() for v in versions]

"""SQLite capacity observation and bounded runtime maintenance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from statistics import median
import time


_MIB = 1024 * 1024
_CAPACITY_LOG = "database_capacity.jsonl"
_WAL_RETAINED_LIMIT_BYTES = 64 * _MIB
_WAL_AUTOCHECKPOINT_PAGES = 1_000


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    from aletheia.storage.sqlite import db_path

    return Path(db_path())


def _sidecar_size(path: Path, suffix: str) -> int:
    sidecar = path.with_name(path.name + suffix)
    try:
        return sidecar.stat().st_size
    except OSError:
        return 0


def _read_only_connection(path: Path) -> sqlite3.Connection:
    from aletheia.storage.sqlite import _open_readonly_connection

    return _open_readonly_connection(path, timeout=1.0)


def database_capacity_snapshot(
    *,
    path: Path | None = None,
    capacity_mb: int | float | None = None,
    deep: bool = False,
    top_n: int = 12,
) -> dict:
    """Return a read-only physical/logical capacity snapshot.

    ``deep`` adds current SQLite object sizes via ``dbstat``. It is deliberately
    opt-in because walking every page of a multi-GiB database is an operator
    diagnostic, not a scheduler hot path.

    The ``effective_pragmas`` field captures the PRAGMA values as seen through
    the snapshot's own read-only connection. These reflect the *read* baseline
    (``query_only=1``, ``journal_mode`` is the persistent WAL flag). They are
    **not** sampled from the writer-lane connection; do not interpret them as
    the writer's live cache or mmap state.
    """

    db_file = _database_path(path)
    if not db_file.exists():
        return {
            "captured_at": _utcnow(),
            "path": str(db_file),
            "available": False,
        }

    connection = _read_only_connection(db_file)
    try:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        auto_vacuum = int(connection.execute("PRAGMA auto_vacuum").fetchone()[0])
        effective_pragmas = {
            name: connection.execute(f"PRAGMA {name}").fetchone()[0]
            for name in (
                "journal_mode",
                "synchronous",
                "cache_size",
                "mmap_size",
                "busy_timeout",
                "journal_size_limit",
                "wal_autocheckpoint",
                "query_only",
            )
        }
        wal_bytes = _sidecar_size(db_file, "-wal")
        payload = {
            "captured_at": _utcnow(),
            "path": str(db_file),
            "available": True,
            "database_bytes": db_file.stat().st_size,
            "wal_bytes": wal_bytes,
            "wal_status": (
                "critical"
                if wal_bytes >= 4 * _WAL_RETAINED_LIMIT_BYTES
                else "warning"
                if wal_bytes > _WAL_RETAINED_LIMIT_BYTES
                else "normal"
            ),
            "wal_retained_limit_bytes": _WAL_RETAINED_LIMIT_BYTES,
            "shm_bytes": _sidecar_size(db_file, "-shm"),
            "page_size": page_size,
            "page_count": page_count,
            "freelist_count": freelist_count,
            "reclaimable_bytes": page_size * freelist_count,
            "logical_used_bytes": page_size * max(0, page_count - freelist_count),
            "auto_vacuum": auto_vacuum,
            "incremental_vacuum_enabled": auto_vacuum == 2,
            "effective_pragmas": effective_pragmas,
        }
        physical_bytes = payload["database_bytes"] + payload["wal_bytes"]
        payload["physical_bytes"] = physical_bytes
        if capacity_mb is not None and float(capacity_mb) > 0:
            capacity_bytes = int(float(capacity_mb) * _MIB)
            ratio = physical_bytes / capacity_bytes
            payload.update({
                "capacity_bytes": capacity_bytes,
                "capacity_ratio": round(ratio, 6),
                "capacity_status": (
                    "over_budget" if ratio >= 1 else "warning" if ratio >= 0.8 else "normal"
                ),
            })
        if deep:
            try:
                payload["top_objects"] = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT name, pgsize AS bytes, ncell AS cells, "
                        "unused AS unused_bytes, "
                        "ROUND((pgsize-unused)*1.0/NULLIF(pgsize,0), 6) AS packing_ratio "
                        "FROM dbstat WHERE aggregate=TRUE "
                        "ORDER BY pgsize DESC LIMIT ?",
                        (max(1, int(top_n)),),
                    )
                ]
            except sqlite3.Error as exc:
                payload["top_objects_unavailable"] = type(exc).__name__
        return payload
    finally:
        connection.close()


def record_database_capacity(
    *,
    capacity_mb: int | float,
    wal_checkpoint: dict | None = None,
) -> dict:
    """Append one bounded scheduler sample outside app.db and return it."""

    from aletheia.files.jsonl import append_jsonl

    snapshot = database_capacity_snapshot(capacity_mb=capacity_mb)
    if wal_checkpoint is not None:
        snapshot["wal_checkpoint"] = wal_checkpoint
    append_jsonl(_CAPACITY_LOG, snapshot)
    return snapshot


def passive_wal_checkpoint(
    db: sqlite3.Connection,
    *,
    path: Path | None = None,
) -> dict:
    """Run one non-blocking checkpoint and expose the exact backlog.

    PASSIVE never waits for readers or writers. A non-zero remaining frame
    count is therefore evidence that the checkpoint could not currently move
    the whole WAL back into the main database, not a reason to block runtime.
    """

    db_file = _database_path(path)
    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    before_bytes = _sidecar_size(db_file, "-wal")
    started = time.perf_counter()
    row = db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    duration_ms = (time.perf_counter() - started) * 1000.0
    if row is None or len(row) != 3:
        raise RuntimeError("SQLite returned an invalid wal_checkpoint result")
    busy, log_frames, checkpointed_frames = (int(row[0]), int(row[1]), int(row[2]))
    remaining_frames = max(0, log_frames - checkpointed_frames) if log_frames >= 0 and checkpointed_frames >= 0 else 0
    return {
        "mode": "PASSIVE",
        "busy": busy,
        "log_frames": log_frames,
        "checkpointed_frames": checkpointed_frames,
        "remaining_frames": remaining_frames,
        "remaining_bytes": remaining_frames * page_size,
        "wal_bytes_before": before_bytes,
        "wal_bytes_after": _sidecar_size(db_file, "-wal"),
        "duration_ms": round(duration_ms, 3),
        "status": (
            "not_wal"
            if log_frames < 0
            else "backlogged"
            if busy or remaining_frames >= _WAL_AUTOCHECKPOINT_PAGES
            else "partial"
            if remaining_frames
            else "complete"
        ),
        "alert_threshold_frames": _WAL_AUTOCHECKPOINT_PAGES,
    }


def optimize_query_planner(db: sqlite3.Connection) -> dict:
    """Run one bounded ``PRAGMA optimize`` and report what it did.

    SQLite >= 3.46 self-limits the ANALYZE subcommands, so this stays fast even
    on a multi-GiB database and needs no ``analysis_limit``. Usually a no-op; it
    occasionally reanalyzes a table that grew since its last statistics.

    Return value's ``analyzed`` is a lower-bound indicator: reanalysis of an
    existing table may leave ``sqlite_stat1`` row count unchanged, so
    ``analyzed=False`` does not prove ANALYZE did nothing.
    """

    def _stat1_rows() -> int:
        exists = db.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='table' AND name='sqlite_stat1'"
        ).fetchone()[0]
        if not exists:
            return 0
        return int(db.execute("SELECT count(*) FROM sqlite_stat1").fetchone()[0])

    started = time.perf_counter()
    before = _stat1_rows()
    db.execute("PRAGMA optimize")
    after = _stat1_rows()
    return {
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "stat1_rows_before": before,
        "stat1_rows_after": after,
        "analyzed": after != before,
    }


def database_capacity_history(*, limit: int = 90) -> list[dict]:
    from aletheia.files.jsonl import read_jsonl

    return read_jsonl(_CAPACITY_LOG, limit=max(1, int(limit)))


def database_capacity_trend(
    samples: list[dict],
    *,
    capacity_bytes: int | None = None,
) -> dict:
    """Return a robust physical-size slope and qualified capacity ETA.

    The median of pairwise slopes is resistant to one large WAL/checkpoint
    sample. Seven days is the minimum for calling the result established.
    """

    points: list[tuple[datetime, int]] = []
    for sample in samples:
        if not sample.get("available", True):
            continue
        try:
            captured = datetime.fromisoformat(
                str(sample["captured_at"]).replace("Z", "+00:00")
            )
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            physical = int(sample["physical_bytes"])
        except (KeyError, TypeError, ValueError):
            continue
        points.append((captured.astimezone(timezone.utc), physical))
    points = sorted(set(points))
    if len(points) < 2:
        return {
            "status": "insufficient_history",
            "sample_count": len(points),
            "minimum_samples": 3,
            "minimum_span_days": 7,
        }

    span_days = (points[-1][0] - points[0][0]).total_seconds() / 86400.0
    slopes: list[float] = []
    for index, (left_at, left_bytes) in enumerate(points):
        for right_at, right_bytes in points[index + 1 :]:
            delta_days = (right_at - left_at).total_seconds() / 86400.0
            if delta_days >= 0.5:
                slopes.append((right_bytes - left_bytes) / delta_days)
    if not slopes:
        return {
            "status": "insufficient_history",
            "sample_count": len(points),
            "span_days": round(span_days, 3),
            "minimum_samples": 3,
            "minimum_span_days": 7,
        }

    slope = float(median(slopes))
    established = len(points) >= 3 and span_days >= 7.0
    trend = {
        "status": "established" if established else "provisional",
        "sample_count": len(points),
        "span_days": round(span_days, 3),
        "slope_bytes_per_day": round(slope, 3),
        "slope_mib_per_day": round(slope / _MIB, 3),
        "minimum_samples": 3,
        "minimum_span_days": 7,
    }
    if capacity_bytes and capacity_bytes > points[-1][1] and slope > 0:
        eta_days = (int(capacity_bytes) - points[-1][1]) / slope
        trend["capacity_eta_days"] = round(eta_days, 3)
        trend["capacity_eta_at"] = (
            points[-1][0] + timedelta(days=eta_days)
        ).isoformat()
    return trend


def reclaim_incremental_freelist(
    db: sqlite3.Connection,
    *,
    max_mb: int = 16,
) -> dict:
    """Reclaim a bounded number of freelist pages without deleting rows."""

    page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
    before = int(db.execute("PRAGMA freelist_count").fetchone()[0])
    mode = int(db.execute("PRAGMA auto_vacuum").fetchone()[0])
    max_pages = max(1, int(max_mb) * _MIB // max(1, page_size))
    requested = min(before, max_pages)
    if mode != 2 or requested <= 0:
        return {
            "enabled": mode == 2,
            "requested_pages": 0,
            "reclaimed_pages": 0,
            "remaining_pages": before,
        }

    from aletheia.storage.sqlite import acquire_write_lock, release_write_lock

    acquire_write_lock()
    try:
        db.execute(f"PRAGMA incremental_vacuum({requested})")
    finally:
        release_write_lock()
    after = int(db.execute("PRAGMA freelist_count").fetchone()[0])
    return {
        "enabled": True,
        "requested_pages": requested,
        "reclaimed_pages": max(0, before - after),
        "remaining_pages": after,
    }

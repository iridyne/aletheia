"""SQLite capacity observation and explicit offline compaction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import shutil
import sqlite3
from statistics import median
import time
from typing import Callable


_MIB = 1024 * 1024
_CAPACITY_LOG = "database_capacity.jsonl"
_WAL_RETAINED_LIMIT_BYTES = 64 * _MIB
_WAL_AUTOCHECKPOINT_PAGES = 1_000
_logger = logging.getLogger("aletheia.maintenance.database_capacity")


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
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


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


def compact_database(
    *,
    apply: bool = False,
    path: Path | None = None,
    progress: Callable[[str, str], None] | None = None,
) -> dict:
    """Plan or run an atomic offline VACUUM and enable incremental reclaim.

    This never deletes rows or changes retention windows. Apply mode requires a
    stopped Gateway and enough free disk for SQLite's temporary rebuilt copy.
    """

    db_file = _database_path(path)

    def notify(stage: str, message: str) -> None:
        if progress is None:
            return
        try:
            progress(stage, message)
        except Exception:  # noqa: BLE001 - presentation must not decide DB safety.
            _logger.exception("database_compaction_progress_callback_failed stage=%s", stage)

    before = database_capacity_snapshot(path=db_file)
    result: dict = {"applied": False, "before": before}
    if before.get("available"):
        from aletheia.storage.market_ohlcv_layout import market_ohlcv_storage_profile

        plan_db = _read_only_connection(db_file)
        try:
            result["market_ohlcv_optimization"] = market_ohlcv_storage_profile(plan_db)
        finally:
            plan_db.close()
    if not apply or not before.get("available"):
        return result

    from aletheia.gateway.pidfile import is_running

    if is_running():
        raise RuntimeError("Gateway is running; stop it with `uv run aletheia gateway stop` before compaction.")
    free_bytes = shutil.disk_usage(db_file.parent).free
    required_free = 2 * int(before["database_bytes"]) + 512 * _MIB
    if free_bytes < required_free:
        raise RuntimeError(
            f"Insufficient free disk for compaction: need {required_free / _MIB:.0f} MiB, "
            f"have {free_bytes / _MIB:.0f} MiB."
        )

    # Own the same process/cross-process permit as every application writer for
    # the whole backup + rebuild + VACUUM sequence. Gateway state alone cannot
    # rule out a separately launched collector or stale CLI writer.
    from aletheia.storage.sqlite import acquire_write_lock, release_write_lock

    acquire_write_lock(timeout=30.0, path=db_file)
    from aletheia.backup.service import BackupService
    from aletheia.backup.verify import verify_backup
    from aletheia.storage.market_ohlcv_layout import (
        market_ohlcv_storage_profile,
        rebuild_market_ohlcv_without_rowid,
    )

    connection: sqlite3.Connection | None = None
    original_journal: str | None = None
    operation_error: BaseException | None = None
    restore_error: BaseException | None = None
    try:
        # Create and verify a WAL-consistent recovery point before the first
        # mutating pragma. The backup service publishes archives atomically.
        notify("backup", "creating and verifying a WAL-consistent recovery point")
        backup_path = BackupService(data_dir=db_file.parent).create_backup("pre_compact")
        verification = verify_backup(backup_path)
        if not verification.valid:
            raise RuntimeError(
                "Pre-compaction backup verification failed: " + "; ".join(verification.errors + verification.mismatches)
            )
        # The first free-space check reserved one database for the backup and
        # one for VACUUM's rebuilt copy. Recheck after the archive exists.
        remaining_free = shutil.disk_usage(db_file.parent).free
        vacuum_required = int(before["database_bytes"]) + 512 * _MIB
        if remaining_free < vacuum_required:
            raise RuntimeError(
                f"Backup created at {backup_path}, but only {remaining_free / _MIB:.0f} MiB "
                f"remains; VACUUM needs {vacuum_required / _MIB:.0f} MiB."
            )

        connection = sqlite3.connect(str(db_file), timeout=30, isolation_level=None)
        notify("preflight", "checkpointing WAL and checking database integrity")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        original_journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        checkpoint = tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
        if checkpoint[0]:
            raise RuntimeError("Pre-compaction WAL checkpoint was blocked; another database user is active.")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise RuntimeError(f"Pre-compaction integrity_check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError(f"Pre-compaction foreign_key_check returned {len(foreign_keys)} row(s).")
        # The gateway is stopped and the cross-process writer permit is held.
        # A rollback journal avoids creating a second database-sized WAL during
        # the table rebuild and VACUUM. Restore the original mode in ``finally``.
        if original_journal == "wal":
            switched = str(connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]).lower()
            if switched != "delete":
                raise RuntimeError("Could not enter offline rollback-journal mode; another database user is active.")

        notify(
            "ohlcv_layout",
            "verifying and optimizing market_ohlcv physical storage when eligible",
        )
        layout = rebuild_market_ohlcv_without_rowid(connection)
        notify("vacuum", "repacking the database and enabling incremental reclaim")
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        connection.execute("VACUUM")
        connection.execute("PRAGMA journal_size_limit=67108864")
        connection.execute("PRAGMA optimize")
        notify("verify", "checking post-compaction integrity and foreign keys")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise RuntimeError(f"Post-compaction integrity_check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError(f"Post-compaction foreign_key_check returned {len(foreign_keys)} row(s).")
        layout["after_compact"] = market_ohlcv_storage_profile(connection)
    except BaseException as exc:
        operation_error = exc
    finally:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            if original_journal == "wal":
                try:
                    restored = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
                    if restored != "wal":
                        raise RuntimeError(f"SQLite returned journal_mode={restored!r}")
                    connection.execute("PRAGMA journal_size_limit=67108864")
                except BaseException as exc:
                    restore_error = exc
            connection.close()
        release_write_lock()

    if operation_error is not None:
        if restore_error is not None:
            raise RuntimeError(
                f"Compaction failed and the original WAL mode could not be restored: {restore_error}"
            ) from operation_error
        raise operation_error
    if restore_error is not None:
        raise RuntimeError(
            f"Compaction completed but the original WAL mode could not be restored: {restore_error}"
        ) from restore_error

    result.update(
        {
            "applied": True,
            "backup_path": str(backup_path),
            "checkpoint": checkpoint,
            "market_ohlcv_optimization": layout,
            "after": database_capacity_snapshot(path=db_file),
        }
    )
    notify("complete", "offline compaction and verification completed")
    return result

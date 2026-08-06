"""SQLite capacity observation and explicit offline compaction."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import sqlite3


_MIB = 1024 * 1024
_CAPACITY_LOG = "database_capacity.jsonl"


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
        payload = {
            "captured_at": _utcnow(),
            "path": str(db_file),
            "available": True,
            "database_bytes": db_file.stat().st_size,
            "wal_bytes": _sidecar_size(db_file, "-wal"),
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
                        "SELECT name, SUM(pgsize) AS bytes, SUM(ncell) AS cells "
                        "FROM dbstat GROUP BY name ORDER BY bytes DESC LIMIT ?",
                        (max(1, int(top_n)),),
                    )
                ]
            except sqlite3.Error as exc:
                payload["top_objects_unavailable"] = type(exc).__name__
        return payload
    finally:
        connection.close()


def record_database_capacity(*, capacity_mb: int | float) -> dict:
    """Append one bounded scheduler sample outside app.db and return it."""

    from aletheia.files.jsonl import append_jsonl

    snapshot = database_capacity_snapshot(capacity_mb=capacity_mb)
    append_jsonl(_CAPACITY_LOG, snapshot)
    return snapshot


def database_capacity_history(*, limit: int = 90) -> list[dict]:
    from aletheia.files.jsonl import read_jsonl

    return read_jsonl(_CAPACITY_LOG, limit=max(1, int(limit)))


def compact_database(*, apply: bool = False, path: Path | None = None) -> dict:
    """Plan or run an atomic offline VACUUM and enable incremental reclaim.

    This never deletes rows or changes retention windows. Apply mode requires a
    stopped Gateway and enough free disk for SQLite's temporary rebuilt copy.
    """

    db_file = _database_path(path)
    before = database_capacity_snapshot(path=db_file)
    result: dict = {"applied": False, "before": before}
    if not apply or not before.get("available"):
        return result

    from aletheia.gateway.pidfile import is_running

    if is_running():
        raise RuntimeError(
            "Gateway is running; stop it with `uv run aletheia gateway stop` before compaction."
        )
    free_bytes = shutil.disk_usage(db_file.parent).free
    required_free = 2 * int(before["database_bytes"]) + 512 * _MIB
    if free_bytes < required_free:
        raise RuntimeError(
            f"Insufficient free disk for compaction: need {required_free / _MIB:.0f} MiB, "
            f"have {free_bytes / _MIB:.0f} MiB."
        )

    # Create and verify a WAL-consistent recovery point before the first
    # mutating pragma. The existing backup service excludes credentials and
    # publishes archives atomically.
    from aletheia.backup.service import BackupService
    from aletheia.backup.verify import verify_backup

    backup_path = BackupService(data_dir=db_file.parent).create_backup("pre_compact")
    verification = verify_backup(backup_path)
    if not verification.valid:
        raise RuntimeError(
            "Pre-compaction backup verification failed: "
            + "; ".join(verification.errors + verification.mismatches)
        )
    # The first free-space check reserved one database for the backup and one
    # for VACUUM's rebuilt copy. Recheck the latter after the archive exists.
    remaining_free = shutil.disk_usage(db_file.parent).free
    vacuum_required = int(before["database_bytes"]) + 512 * _MIB
    if remaining_free < vacuum_required:
        raise RuntimeError(
            f"Backup created at {backup_path}, but only {remaining_free / _MIB:.0f} MiB "
            f"remains; VACUUM needs {vacuum_required / _MIB:.0f} MiB."
        )

    connection = sqlite3.connect(str(db_file), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        checkpoint = tuple(connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise RuntimeError(f"Pre-compaction integrity_check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError(
                f"Pre-compaction foreign_key_check returned {len(foreign_keys)} row(s)."
            )
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        connection.execute("VACUUM")
        connection.execute("PRAGMA journal_size_limit=67108864")
        connection.execute("PRAGMA optimize")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise RuntimeError(f"Post-compaction integrity_check failed: {integrity}")
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise RuntimeError(
                f"Post-compaction foreign_key_check returned {len(foreign_keys)} row(s)."
            )
    finally:
        connection.close()

    result.update({
        "applied": True,
        "backup_path": str(backup_path),
        "checkpoint": checkpoint,
        "after": database_capacity_snapshot(path=db_file),
    })
    return result

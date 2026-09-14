"""Explicit offline database compaction; not a runtime maintenance job."""

from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
import sqlite3
from typing import Callable

import typer

from aletheia.maintenance.database_capacity import (
    _MIB, _database_path, _read_only_connection, database_capacity_snapshot,
)

_logger = logging.getLogger("aletheia.maintenance.database_capacity")


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
        backup_path = BackupService(data_dir=db_file.parent).create_backup(
            "pre_compact", profile="authoritative"
        )
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


def compact_database_command(
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Run offline compaction. Default is a read-only plan.",
    ),
):
    """Reclaim pages and optimize eligible physical table layouts offline."""
    import time

    started = time.perf_counter()

    def progress(stage: str, message: str) -> None:
        typer.echo(f"[{time.perf_counter() - started:6.1f}s] {stage}: {message}")

    if apply:
        typer.echo(
            "Offline compaction started. Duration depends on retained data and disk speed; "
            "keep the Gateway stopped."
        )
    try:
        result = compact_database(apply=apply, progress=progress if apply else None)
    except RuntimeError as exc:
        typer.echo(f"Compaction refused: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    if not apply:
        typer.echo(
            "Dry-run only. Stop the Gateway and repeat with "
            "`uv run aletheia data compact --apply`."
        )

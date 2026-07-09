"""Focused regression for #59 — backup snapshots app.db via SQLite backup API.

The previous code used `shutil.copy2` on a WAL-mode database. Under concurrent
writes that can:
  (a) miss committed rows still living in the -wal sidecar
  (b) copy an internally-torn page between rows

sqlite3.Connection.backup() is the officially-safe API — it holds a read lock,
follows page dependencies including the WAL, and produces a consistent
snapshot without a -wal/-shm sidecar. This regression asserts that a row
INSERTed into a WAL-mode source db is visible when the backed-up file is
opened as its own database — the behaviour a raw shutil.copy2 would fail.
"""

from __future__ import annotations

import sqlite3
import tarfile
from pathlib import Path

import pytest


def _make_wal_db_with_uncheckpointed_row(path: Path) -> None:
    """Create a WAL-mode SQLite db, INSERT a row, and DO NOT checkpoint.

    This intentionally leaves the row in the -wal sidecar so a shutil.copy2 of
    the .db file (which shipped in the pre-#59 backup) would fail to see it.
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE canary (id INTEGER PRIMARY KEY, note TEXT)")
        conn.execute("INSERT INTO canary (note) VALUES ('committed pre-backup')")
        conn.commit()
        # Leave connection open — the -wal file usually still has fresh writes
        # buffered here. Don't call `PRAGMA wal_checkpoint` — that would defeat
        # the test scenario.
    finally:
        # Keep the sidecar present: close cleanly, but leave the checkpoint to
        # SQLite's normal auto-schedule (which will not run in this synchronous
        # test window — the pages remain in the WAL until then).
        conn.close()


def test_backup_captures_wal_committed_rows(tmp_path):
    """#59: sqlite3.Connection.backup() must survive a WAL sidecar containing
    committed-but-uncheckpointed rows. The old shutil.copy2 path could drop
    them; the new snapshot API cannot."""
    # Point BackupService at a tmp workspace, seed a WAL-mode app.db.
    (tmp_path / "backups").mkdir()
    db_path = tmp_path / "app.db"
    _make_wal_db_with_uncheckpointed_row(db_path)

    # Confirm the .db + -wal shape we care about is actually present. If SQLite
    # opted to checkpoint immediately (small write, no other clients), skip —
    # this test is only meaningful when the sidecar exists.
    if not (tmp_path / "app.db-wal").exists():
        pytest.skip("No WAL sidecar present — SQLite auto-checkpointed early")

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup(label="wal-canary")

    # Extract the archive and open the copied app.db in isolation.
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(unpacked, filter="data")
    bundle_root = next(d for d in unpacked.iterdir() if d.is_dir())
    copied_db = bundle_root / "app.db"
    assert copied_db.exists()

    conn = sqlite3.connect(str(copied_db))
    try:
        rows = conn.execute("SELECT note FROM canary").fetchall()
    finally:
        conn.close()
    assert rows == [("committed pre-backup",)], (
        f"backup must include the WAL-buffered row that shutil.copy2 would miss; "
        f"got {rows}"
    )


def test_backup_still_handles_non_sqlite_app_db(tmp_path):
    """Belt-and-suspenders: if data_dir/app.db isn't a valid SQLite file for
    some reason (empty placeholder, stripped test fixture), the backup service
    falls back to shutil.copy2 rather than raising — never worse than the pre-fix
    behaviour."""
    (tmp_path / "backups").mkdir()
    # A file that ISN'T a SQLite database.
    (tmp_path / "app.db").write_bytes(b"not a sqlite file at all")

    from aletheia.backup.service import BackupService
    archive = BackupService(data_dir=tmp_path).create_backup(label="not-sqlite")

    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(unpacked, filter="data")
    bundle_root = next(d for d in unpacked.iterdir() if d.is_dir())
    copied = (bundle_root / "app.db").read_bytes()
    assert copied == b"not a sqlite file at all"

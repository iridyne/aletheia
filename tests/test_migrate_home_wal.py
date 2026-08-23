"""Tests for #1385: WAL sentinel rows survive migration.

sqlite3.Connection.backup() is WAL-safe: even if a writer has uncommitted WAL
transactions when the copy begins, the backup API produces a consistent snapshot
that includes those pages. shutil.copy2 would copy only the main db file and miss
pages still in the WAL, causing silent data loss.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from aletheia.runtime_home_migration import (
    _copy_to_staging,
    apply_migration,
)


def _make_wal_db(path: Path) -> None:
    """Create a WAL-mode database and insert a sentinel row while holding an open reader."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO sentinel(value) VALUES ('wal-committed-row')")
    conn.commit()
    conn.close()


def test_wal_sentinel_survives_migration(tmp_path: Path) -> None:
    """#1385: a row committed in WAL mode must appear in the migration destination."""
    source = tmp_path / "source"
    source.mkdir()
    db_path = source / "app.db"

    _make_wal_db(db_path)
    (source / "config.yaml").write_text("app:\n  dashboard_port: 8787\n", encoding="utf-8")

    staging = tmp_path / "staging"
    staging.mkdir()
    _copy_to_staging(source, staging)

    dest_db = staging / "app.db"
    assert dest_db.exists(), "app.db must be copied to staging"

    conn = sqlite3.connect(str(dest_db))
    try:
        rows = conn.execute("SELECT value FROM sentinel").fetchall()
    finally:
        conn.close()

    values = [r[0] for r in rows]
    assert "wal-committed-row" in values, (
        "WAL-committed sentinel row must be present in the migrated database. "
        "shutil.copy2 would miss it; sqlite3.Connection.backup() must be used."
    )


def test_wal_sentinel_survives_with_open_reader(tmp_path: Path) -> None:
    """Sentinel survives even when a reader holds the WAL open during copy."""
    source = tmp_path / "source"
    source.mkdir()
    db_path = source / "app.db"

    # Create WAL db with a row
    conn_write = sqlite3.connect(str(db_path))
    conn_write.execute("PRAGMA journal_mode=WAL")
    conn_write.execute("CREATE TABLE sentinel (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
    conn_write.execute("INSERT INTO sentinel(value) VALUES ('wal-row-with-open-reader')")
    conn_write.commit()

    # Hold a reader open (simulates concurrent read; forces WAL to stay open)
    conn_read = sqlite3.connect(str(db_path))
    conn_read.execute("BEGIN")  # open read transaction to pin WAL

    staging = tmp_path / "staging"
    staging.mkdir()
    (source / "config.yaml").write_text("app:\n  dashboard_port: 8787\n", encoding="utf-8")

    try:
        _copy_to_staging(source, staging)
    finally:
        conn_read.close()
        conn_write.close()

    dest_db = staging / "app.db"
    conn_verify = sqlite3.connect(str(dest_db))
    try:
        rows = conn_verify.execute("SELECT value FROM sentinel").fetchall()
    finally:
        conn_verify.close()

    values = [r[0] for r in rows]
    assert "wal-row-with-open-reader" in values, (
        "WAL-committed row must be present even with a concurrent reader"
    )


def test_apply_migration_db_integrity_verified(tmp_path: Path) -> None:
    """After backup-copy, integrity_check on staging db must pass."""
    source = tmp_path / "repo" / "data"
    source.mkdir(parents=True)
    _make_wal_db(source / "app.db")
    (source / "config.yaml").write_text("app:\n  dashboard_port: 8787\n", encoding="utf-8")
    (source / "logs").mkdir()
    (source / "logs" / "audit.jsonl").write_text('{"event_type":"proof"}\n', encoding="utf-8")

    target = tmp_path / "home" / ".aletheia"
    _ = apply_migration(source, target)

    dest_db = target / "app.db"
    assert dest_db.exists()
    conn = sqlite3.connect(str(dest_db))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        assert row and row[0] == "ok", f"integrity_check failed on migrated db: {row}"
        sentinel = conn.execute("SELECT value FROM sentinel").fetchall()
    finally:
        conn.close()

    assert any(r[0] == "wal-committed-row" for r in sentinel), (
        "Sentinel row from original WAL database must survive full apply_migration"
    )

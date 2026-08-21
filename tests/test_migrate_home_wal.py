"""Test #1385: WAL-safe SQLite copy in migrate-home."""

import sqlite3


def test_wal_sentinel_survives_migration(tmp_path):
    """Verify sqlite3.backup() captures committed WAL state mid-write."""
    source = tmp_path / "source"
    source.mkdir()
    dest = tmp_path / "dest"
    dest.mkdir()

    db_path = source / "app.db"

    # Create WAL database with sentinel
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE state (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO state VALUES (1, 'sentinel')")
    conn.commit()

    # Hold a reader to keep WAL from checkpointing
    reader = sqlite3.connect(str(db_path))
    reader.execute("SELECT * FROM state")  # hold read lock

    # Commit another write while reader is open
    conn.execute("INSERT INTO state VALUES (2, 'wal-write')")
    conn.commit()

    # Perform sqlite3.backup() migration (from _copy_to_staging)
    dest_db = dest / "app.db"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(dest_db))
    src.backup(dst)
    dst.close()
    src.close()

    # Close reader
    reader.close()
    conn.close()

    # Verify dest has both sentinel and wal-write
    verify_conn = sqlite3.connect(str(dest_db))
    rows = verify_conn.execute("SELECT value FROM state ORDER BY id").fetchall()
    assert len(rows) == 2
    assert rows[0][0] == "sentinel"
    assert rows[1][0] == "wal-write"
    verify_conn.close()

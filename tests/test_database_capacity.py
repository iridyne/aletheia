"""Database capacity observation and explicit compaction boundaries."""

import sqlite3

import pytest
from pydantic import ValidationError


def test_capacity_snapshot_is_read_only_and_attributes_objects(tmp_path):
    from aletheia.maintenance.database_capacity import database_capacity_snapshot

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE samples (id INTEGER PRIMARY KEY, payload BLOB)")
    db.executemany(
        "INSERT INTO samples(payload) VALUES (?)",
        [(b"x" * 4096,) for _ in range(40)],
    )
    db.commit()
    db.close()

    before_mtime = path.stat().st_mtime_ns
    snapshot = database_capacity_snapshot(
        path=path,
        capacity_mb=0.01,
        deep=True,
        top_n=5,
    )

    assert snapshot["available"] is True
    assert snapshot["database_bytes"] == path.stat().st_size
    assert snapshot["physical_bytes"] >= snapshot["database_bytes"]
    assert snapshot["logical_used_bytes"] > 0
    assert snapshot["capacity_status"] == "over_budget"
    assert any(row["name"] == "samples" for row in snapshot["top_objects"])
    assert path.stat().st_mtime_ns == before_mtime


def test_new_sqlite_database_enables_incremental_vacuum(tmp_path):
    from aletheia.storage.sqlite import _open_connection

    path = tmp_path / "new.db"
    db = _open_connection(path)
    try:
        assert db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    finally:
        db.close()


def test_capacity_budget_has_schema_bounds():
    from aletheia.config.schema import RetentionConfig

    assert RetentionConfig().app_db_capacity_mb == 2048
    with pytest.raises(ValidationError):
        RetentionConfig(app_db_capacity_mb=511)
    with pytest.raises(ValidationError):
        RetentionConfig(app_db_capacity_mb=65537)


def test_compaction_defaults_to_read_only_plan(tmp_path):
    from aletheia.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE preserved (value TEXT)")
    db.execute("INSERT INTO preserved VALUES ('evidence')")
    db.commit()
    db.close()

    result = compact_database(path=path)

    assert result["applied"] is False
    assert sqlite3.connect(path).execute("SELECT value FROM preserved").fetchone()[0] == "evidence"


def test_compaction_reclaims_pages_and_preserves_rows(tmp_path, monkeypatch):
    from aletheia.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE preserved (id INTEGER PRIMARY KEY, payload BLOB)")
    db.executemany(
        "INSERT INTO preserved(payload) VALUES (?)",
        [(b"x" * 8192,) for _ in range(100)],
    )
    db.execute("DELETE FROM preserved WHERE id > 1")
    db.commit()
    db.close()
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: False)

    result = compact_database(path=path, apply=True)

    assert result["applied"] is True
    assert result["backup_path"].endswith(".tar.gz")
    assert result["after"]["auto_vacuum"] == 2
    assert result["after"]["database_bytes"] < result["before"]["database_bytes"]
    verify = sqlite3.connect(path)
    try:
        assert verify.execute("SELECT COUNT(*) FROM preserved").fetchone()[0] == 1
        assert verify.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        verify.close()


def test_compaction_refuses_running_gateway(tmp_path, monkeypatch):
    from aletheia.maintenance.database_capacity import compact_database

    path = tmp_path / "app.db"
    sqlite3.connect(path).close()
    monkeypatch.setattr("aletheia.gateway.pidfile.is_running", lambda: True)

    with pytest.raises(RuntimeError, match="Gateway is running"):
        compact_database(path=path, apply=True)

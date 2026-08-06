from __future__ import annotations

import sqlite3
import threading


def _connection(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def test_production_migrations_persist_and_reuse_source_fingerprint(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    database = tmp_path / "app.db"
    connection = _connection(database)
    active = {"connection": connection}
    full_runs = []

    monkeypatch.setattr(migrations, "get_db", lambda: active["connection"])
    monkeypatch.setattr(migrations, "_is_pytest", lambda: False)
    monkeypatch.setattr(migrations, "_migration_source_fingerprint", lambda: "source-a")
    monkeypatch.setattr(migrations, "_verified_migration", threading.local())

    def fake_full_migration(db):
        full_runs.append("ran")
        db.executescript(migrations._MIGRATION_STATE_DDL)

    monkeypatch.setattr(migrations, "_run_all_ddl", fake_full_migration)

    assert migrations.run_migrations() is True
    assert migrations.run_migrations() is False
    assert full_runs == ["ran"]

    connection.close()
    active["connection"] = _connection(database)
    monkeypatch.setattr(migrations, "_verified_migration", threading.local())
    assert migrations.run_migrations() is False
    assert full_runs == ["ran"]
    active["connection"].close()


def test_production_migrations_rerun_when_source_changes(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    fingerprint = {"value": "source-a"}
    full_runs = []

    monkeypatch.setattr(migrations, "get_db", lambda: connection)
    monkeypatch.setattr(migrations, "_is_pytest", lambda: False)
    monkeypatch.setattr(
        migrations,
        "_migration_source_fingerprint",
        lambda: fingerprint["value"],
    )
    monkeypatch.setattr(migrations, "_verified_migration", threading.local())

    def fake_full_migration(db):
        full_runs.append(fingerprint["value"])
        db.executescript(migrations._MIGRATION_STATE_DDL)

    monkeypatch.setattr(migrations, "_run_all_ddl", fake_full_migration)

    assert migrations.run_migrations() is True
    fingerprint["value"] = "source-b"
    assert migrations.run_migrations() is True
    assert migrations.run_migrations() is False
    assert full_runs == ["source-a", "source-b"]
    connection.close()

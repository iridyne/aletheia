from __future__ import annotations

import inspect
import ast
from pathlib import Path
import sqlite3

import pytest


def _connection(path):
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _production_migration_env(monkeypatch, migrations, connection) -> list[str]:
    applied: list[str] = []
    monkeypatch.setattr(migrations, "get_db", lambda: connection)
    monkeypatch.setattr(migrations, "_is_pytest", lambda: False)
    monkeypatch.setattr(migrations, "acquire_write_lock", lambda **_kwargs: 0.0)
    monkeypatch.setattr(migrations, "release_write_lock", lambda: None)

    def apply_step(db):
        applied.append("baseline")
        db.execute("CREATE TABLE domain_state (id INTEGER PRIMARY KEY)")

    monkeypatch.setattr(
        migrations,
        "_MIGRATIONS",
        (
            migrations.MigrationStep(
                migration_id="20260806_0001_legacy_schema_v2",
                checksum="a" * 64,
                schema_version="aletheia-sqlite-v2",
                apply=apply_step,
            ),
        ),
    )
    monkeypatch.setattr(
        migrations,
        "_legacy_baseline_implementation_checksum",
        lambda: "a" * 64,
    )
    return applied


def test_production_migrations_persist_stable_ledger_and_apply_once(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    database = tmp_path / "app.db"
    connection = _connection(database)
    applied = _production_migration_env(monkeypatch, migrations, connection)

    assert migrations.apply_migrations() is True
    assert migrations.apply_migrations() is False
    assert applied == ["baseline"]

    row = connection.execute(
        "SELECT migration_id, checksum, schema_version, duration_ms "
        "FROM aletheia_schema_migrations"
    ).fetchone()
    assert dict(row) == {
        "migration_id": "20260806_0001_legacy_schema_v2",
        "checksum": "a" * 64,
        "schema_version": "aletheia-sqlite-v2",
        "duration_ms": pytest.approx(row["duration_ms"], abs=0.01),
    }
    assert row["duration_ms"] >= 0

    connection.close()
    reopened = _connection(database)
    monkeypatch.setattr(migrations, "get_db", lambda: reopened)
    assert migrations.apply_migrations() is False
    assert applied == ["baseline"]
    reopened.close()


def test_migration_checksum_drift_fails_closed_without_replaying(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    applied = _production_migration_env(monkeypatch, migrations, connection)
    assert migrations.apply_migrations() is True

    def must_not_run(_db):
        raise AssertionError("checksum drift must never replay an applied migration")

    monkeypatch.setattr(
        migrations,
        "_MIGRATIONS",
        (
            migrations.MigrationStep(
                migration_id="20260806_0001_legacy_schema_v2",
                checksum="b" * 64,
                schema_version="aletheia-sqlite-v2",
                apply=must_not_run,
            ),
        ),
    )
    monkeypatch.setattr(
        migrations,
        "_legacy_baseline_implementation_checksum",
        lambda: "b" * 64,
    )

    with pytest.raises(migrations.MigrationChecksumMismatch):
        migrations.apply_migrations()
    assert applied == ["baseline"]
    connection.close()


def test_failed_migration_is_not_recorded_and_can_retry(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)

    def fail(_db):
        raise RuntimeError("migration interrupted")

    failing = migrations.MigrationStep(
        migration_id="20260806_0001_legacy_schema_v2",
        checksum="a" * 64,
        schema_version="aletheia-sqlite-v2",
        apply=fail,
    )
    monkeypatch.setattr(migrations, "_MIGRATIONS", (failing,))
    with pytest.raises(RuntimeError, match="interrupted"):
        migrations.apply_migrations()
    assert connection.execute(
        "SELECT COUNT(*) FROM aletheia_schema_migrations"
    ).fetchone()[0] == 0

    applied: list[str] = []
    succeeding = migrations.MigrationStep(
        migration_id=failing.migration_id,
        checksum=failing.checksum,
        schema_version=failing.schema_version,
        apply=lambda db: applied.append("retried"),
    )
    monkeypatch.setattr(migrations, "_MIGRATIONS", (succeeding,))
    assert migrations.apply_migrations() is True
    assert applied == ["retried"]
    assert connection.execute(
        "SELECT COUNT(*) FROM aletheia_schema_migrations"
    ).fetchone()[0] == 1
    connection.close()


def test_read_only_verification_reports_pending_without_creating_ledger(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.migrations as migrations
    from aletheia.storage import sqlite as sqlite_store

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sqlite_store.reset_db()
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute("CREATE TABLE existing_state (id INTEGER PRIMARY KEY)")

    with pytest.raises(migrations.PendingMigrationsError) as exc_info:
        migrations.assert_migrations_current()
    assert "20260806_0001_legacy_schema_v2" in str(exc_info.value)

    with sqlite3.connect(tmp_path / "app.db") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert tables == {"existing_state"}
    sqlite_store.reset_db()


def test_legacy_migration_checksum_matches_immutable_implementation():
    import aletheia.storage.migrations as migrations

    legacy = migrations._MIGRATIONS[0]
    assert legacy.checksum == migrations._legacy_baseline_implementation_checksum()


def test_gateway_startup_has_one_explicit_migration_owner_and_read_only_children():
    from aletheia import _gateway_runner
    from aletheia.gateway import lifecycle
    from aletheia.runtime_continuity import processes
    from aletheia.server import app as server_app

    lifecycle_source = inspect.getsource(lifecycle.do_start)
    assert "_apply_gateway_migrations" in lifecycle_source

    assert "run_migrations" not in inspect.getsource(processes.current_runtime_record)
    assert "apply_migrations" not in inspect.getsource(processes.current_runtime_record)
    assert "run_migrations" not in inspect.getsource(_gateway_runner)
    assert "apply_migrations" not in inspect.getsource(_gateway_runner)

    lifespan_source = inspect.getsource(server_app.lifespan)
    assert "assert_migrations_current" in lifespan_source
    assert "run_migrations" not in lifespan_source
    assert "apply_migrations" not in lifespan_source


def test_production_code_has_no_legacy_implicit_run_migrations_callers():
    package_root = Path(__file__).resolve().parents[1] / "aletheia"
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name == "migrations.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "aletheia.storage.migrations":
                if any(alias.name == "run_migrations" for alias in node.names):
                    offenders.append(f"{path.relative_to(package_root)}:{node.lineno}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "run_migrations":
                    offenders.append(f"{path.relative_to(package_root)}:{node.lineno}")
    assert offenders == []


def test_database_ahead_of_code_fails_closed(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)
    connection.executescript(migrations._MIGRATION_LEDGER_DDL)
    connection.execute(
        "INSERT INTO aletheia_schema_migrations "
        "(migration_id, checksum, schema_version, applied_at, duration_ms) "
        "VALUES ('20990101_9999_future', ?, 'future', datetime('now'), 1)",
        ("f" * 64,),
    )
    connection.commit()

    with pytest.raises(migrations.DatabaseSchemaAheadError):
        migrations.apply_migrations()
    connection.close()


def test_migration_ledger_rejects_schema_version_drift(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)
    assert migrations.apply_migrations() is True
    connection.execute(
        "UPDATE aletheia_schema_migrations SET schema_version='corrupt'"
    )
    connection.commit()

    with pytest.raises(migrations.MigrationStateError, match="schema version mismatch"):
        migrations.apply_migrations()
    connection.close()


def test_migration_ledger_rejects_an_applied_step_after_a_gap(tmp_path, monkeypatch):
    import aletheia.storage.migrations as migrations

    connection = _connection(tmp_path / "app.db")
    _production_migration_env(monkeypatch, migrations, connection)
    first, second = (
        migrations.MigrationStep(
            migration_id=migration_id,
            checksum=checksum * 64,
            schema_version="aletheia-sqlite-v2",
            apply=lambda _db: None,
        )
        for migration_id, checksum in (
            ("20260806_0001_legacy_schema_v2", "a"),
            ("20260807_0002_example", "b"),
        )
    )
    monkeypatch.setattr(migrations, "_MIGRATIONS", (first, second))
    connection.executescript(migrations._MIGRATION_LEDGER_DDL)
    connection.execute(
        "INSERT INTO aletheia_schema_migrations "
        "(migration_id, checksum, schema_version, applied_at, duration_ms) "
        "VALUES (?, ?, ?, datetime('now'), 1)",
        (second.migration_id, second.checksum, second.schema_version),
    )
    connection.commit()

    with pytest.raises(migrations.MigrationStateError, match="contiguous prefix"):
        migrations.apply_migrations()
    connection.close()

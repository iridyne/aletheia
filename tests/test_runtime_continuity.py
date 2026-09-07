"""Retired runtime control schema preserves evidence and fails closed."""

import sqlite3

import pytest

from aletheia.backup.service import BackupService
from aletheia.backup.verify import verify_backup
from aletheia.storage import migrations


def test_runtime_retirement_requires_exact_authoritative_backup(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    with sqlite3.connect(tmp_path / "app.db") as db:
        db.execute("CREATE TABLE observation_authority_state (state_key TEXT, authority_source TEXT)")
        db.execute("INSERT INTO observation_authority_state VALUES ('experiment_authority', 'legacy')")
        db.execute("CREATE TABLE runtime_registry (runtime_id TEXT PRIMARY KEY, state TEXT, artifact_path TEXT)")
        db.execute("INSERT INTO runtime_registry VALUES ('historical', 'current', ?)", (str(loader._PROJECT_ROOT),))
        for table in migrations._RETIRED_RUNTIME_TABLES[1:]:
            db.execute(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, payload TEXT)')
            db.execute(f'INSERT INTO "{table}" VALUES (?, ?)', (table, "unknown evidence"))
        db.execute("CREATE TABLE agent_shell_tasks (id TEXT, runtime_id TEXT)")
        db.execute("INSERT INTO agent_shell_tasks VALUES ('task', 'historical')")
        db.execute("CREATE TABLE runtime_sessions (session_id TEXT, runtime_id TEXT)")
        db.execute("INSERT INTO runtime_sessions VALUES ('session', 'historical')")
        db.commit()
        with pytest.raises(migrations.MigrationStateError, match="requires_verified_backup"):
            migrations.retire_runtime_continuity_tables(db)
        backup = BackupService(tmp_path).create_backup("runtime-retirement", profile="authoritative")
        assert verify_backup(backup).recovery_eligible
        db.execute("UPDATE runtime_events SET payload='new evidence'")
        db.commit()
        with pytest.raises(migrations.MigrationStateError, match="source_changed"):
            migrations.retire_runtime_continuity_tables(db, backup_path=backup)
        assert db.execute("SELECT payload FROM runtime_events").fetchone()[0] == "new evidence"
        db.execute("UPDATE runtime_events SET payload='unknown evidence'")
        db.commit()

        db.execute("UPDATE runtime_registry SET artifact_path=?", (str(tmp_path / "old-bundle"),))
        db.commit()
        with pytest.raises(migrations.MigrationStateError, match="standard_directory"):
            migrations.retire_runtime_continuity_tables(db, backup_path=backup)
        db.execute("UPDATE runtime_registry SET artifact_path=?", (str(loader._PROJECT_ROOT),))
        db.commit()
        migrations.retire_runtime_continuity_tables(db, backup_path=backup)
        migrations.retire_runtime_continuity_tables(db)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not set(migrations._RETIRED_RUNTIME_TABLES) & tables
        assert db.execute("SELECT runtime_id FROM agent_shell_tasks").fetchone()[0] == "historical"
        assert db.execute("SELECT runtime_id FROM runtime_sessions").fetchone()[0] == "historical"
        assert verify_backup(backup).valid


def test_empty_runtime_schema_retirement_and_baseline_do_not_recreate():
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        migrations._run_all_ddl(db)
        migrations.retire_runtime_continuity_tables(db)
        migrations._run_all_ddl(db)
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not set(migrations._RETIRED_RUNTIME_TABLES) & tables
        assert migrations._legacy_baseline_implementation_checksum() == migrations._LEGACY_BASELINE_CHECKSUM

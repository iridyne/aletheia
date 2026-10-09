"""Retired runtime control schema preserves evidence and fails closed."""

import sqlite3

import pytest

from ops.backup.service import BackupService
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


def test_runtime_retirement_resolves_env_backup_and_allows_null_artifact(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    with sqlite3.connect(tmp_path / "app.db") as db:
        db.execute("CREATE TABLE runtime_registry (runtime_id TEXT PRIMARY KEY, state TEXT, artifact_path TEXT)")
        db.execute("INSERT INTO runtime_registry VALUES ('historical', 'current', NULL)")
        for table in migrations._RETIRED_RUNTIME_TABLES[1:]:
            db.execute(f'CREATE TABLE "{table}" (id TEXT PRIMARY KEY, payload TEXT)')
            db.execute(f'INSERT INTO "{table}" VALUES (?, ?)', (table, "historical data"))
        db.commit()

        backup = BackupService(tmp_path).create_backup("runtime-retirement-env", profile="online")
        assert not verify_backup(backup).recovery_eligible
        assert verify_backup(backup).valid

        monkeypatch.setenv("ALETHEIA_BACKUP_PATH", str(backup))
        # backup_path is None, must resolve from ALETHEIA_BACKUP_PATH
        migrations.retire_runtime_continuity_tables(db)

        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not set(migrations._RETIRED_RUNTIME_TABLES) & tables


def test_retire_experiment_schema_cli_refuses_unverifiable_live_gateway_pid(tmp_path, monkeypatch):
    """#3884: a live PID whose identity cannot be read is not a proven stop."""
    import contextlib
    import os

    from typer.testing import CliRunner

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.cli.runtime_cmd import runtime_app
    from aletheia.gateway import pidfile
    from ops.update import auto

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    pidfile.pid_path().write_text(str(os.getppid()), encoding="utf-8")
    monkeypatch.setattr(pidfile, "_process_cmdline", lambda pid: None)
    monkeypatch.setattr(pidfile, "find_gateway_orphans", lambda: [])
    monkeypatch.setattr(auto, "_deployment_lock", lambda: contextlib.nullcontext(True))
    reached = []

    def acquire_write_lock(*_args, **_kwargs):
        reached.append(True)
        raise RuntimeError("write lock reached")

    monkeypatch.setattr(sq, "acquire_write_lock", acquire_write_lock)
    backup = tmp_path / "backup.tar.gz"
    backup.write_bytes(b"")

    result = CliRunner().invoke(runtime_app, ["retire-experiment-schema", "--backup", str(backup)])

    assert reached == []
    assert result.exit_code == 1, result.output
    assert "Retirement rejected" in result.output

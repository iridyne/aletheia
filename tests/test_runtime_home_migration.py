from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aletheia.runtime_home_migration import (
    MIGRATION_MARKER,
    RuntimeHomeMigrationError,
    _checkpoint_and_verify_database,
    apply_migration,
    assert_runtime_home_ready,
    build_plan,
)


def _legacy_runtime(root: Path) -> Path:
    source = root / "data"
    source.mkdir(parents=True)
    with sqlite3.connect(source / "app.db") as db:
        db.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO proof(value) VALUES ('preserved')")
    (source / "config.yaml").write_text("app:\n  dashboard_port: 8787\n", encoding="utf-8")
    (source / "logs").mkdir()
    (source / "logs" / "audit.jsonl").write_text('{"event_type":"proof"}\n', encoding="utf-8")
    (source / "gateway.pid").write_text("99999999", encoding="utf-8")
    (source / "app.db-shm").write_bytes(b"transient")
    (source / "custom.lock").write_bytes(b"transient")
    (root / ".env").write_text("ALETHEIA_TEST_VALUE=preserved\n", encoding="utf-8")
    return source


def test_plan_is_read_only_and_reports_primary_state(tmp_path: Path) -> None:
    source = _legacy_runtime(tmp_path / "repo")
    target = tmp_path / "home" / ".aletheia"

    plan = build_plan(source, target)

    assert plan.source_has_primary_state is True
    assert plan.target_has_primary_state is False
    assert plan.file_count == 4
    assert not target.exists()


def test_database_validation_closes_connection_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "app.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE proof (id INTEGER PRIMARY KEY)")

    real_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    class TrackingConnection(sqlite3.Connection):
        closed = False

        def close(self) -> None:
            self.closed = True
            super().close()

    def tracking_connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)

    _checkpoint_and_verify_database(database)

    assert opened and all(connection.closed for connection in opened)


def test_apply_preserves_source_validates_db_and_skips_transients(tmp_path: Path) -> None:
    source = _legacy_runtime(tmp_path / "repo")
    target = tmp_path / "home" / ".aletheia"

    plan = apply_migration(source, target)

    assert plan.target_has_primary_state is True
    assert plan.target_has_marker is True
    assert (source / "app.db").is_file()
    assert not (target / "gateway.pid").exists()
    assert not (target / "app.db-shm").exists()
    assert not (target / "custom.lock").exists()
    assert (target / ".env").read_text(encoding="utf-8") == "ALETHEIA_TEST_VALUE=preserved\n"
    assert (source.parent / ".env").is_file()
    with sqlite3.connect(target / "app.db") as db:
        assert db.execute("SELECT value FROM proof").fetchone()[0] == "preserved"
    marker = json.loads((target / MIGRATION_MARKER).read_text(encoding="utf-8"))
    assert marker["source_preserved"] is True


def test_default_startup_refuses_legacy_state_without_marker(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    _legacy_runtime(project_root)
    target = tmp_path / "home" / ".aletheia"

    with pytest.raises(RuntimeHomeMigrationError, match="Legacy runtime state"):
        assert_runtime_home_ready(project_root=project_root, target=target, override_active=False)


def test_default_startup_refuses_marker_without_migrated_state(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    _legacy_runtime(project_root)
    target = tmp_path / "home" / ".aletheia"
    target.mkdir(parents=True)
    (target / MIGRATION_MARKER).write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeHomeMigrationError, match="Legacy runtime state"):
        assert_runtime_home_ready(project_root=project_root, target=target, override_active=False)


def test_explicit_override_bypasses_legacy_default_guard(tmp_path: Path) -> None:
    project_root = tmp_path / "repo"
    _legacy_runtime(project_root)

    assert_runtime_home_ready(
        project_root=project_root,
        target=tmp_path / "explicit",
        override_active=True,
    )


def test_apply_rejects_existing_unmarked_primary_target(tmp_path: Path) -> None:
    source = _legacy_runtime(tmp_path / "repo")
    target = tmp_path / "home" / ".aletheia"
    target.mkdir(parents=True)
    (target / "config.yaml").write_text("app: {}\n", encoding="utf-8")

    with pytest.raises(RuntimeHomeMigrationError, match="already contains primary state"):
        apply_migration(source, target)


def test_apply_rolls_back_new_target_files_on_auxiliary_collision(tmp_path: Path) -> None:
    source = _legacy_runtime(tmp_path / "repo")
    target = tmp_path / "home" / ".aletheia"
    (target / "logs").mkdir(parents=True)
    (target / "logs" / "owner-note.txt").write_text("keep\n", encoding="utf-8")

    with pytest.raises(RuntimeHomeMigrationError, match="target collision"):
        apply_migration(source, target)

    assert not (target / "app.db").exists()
    assert not (target / "config.yaml").exists()
    assert (target / "logs" / "owner-note.txt").read_text(encoding="utf-8") == "keep\n"


def test_migrate_home_cli_defaults_to_read_only_plan_then_applies(tmp_path: Path) -> None:
    from aletheia.cli.runtime_cmd import runtime_app

    source = _legacy_runtime(tmp_path / "repo")
    target = tmp_path / "home" / ".aletheia"
    runner = CliRunner()

    dry_run = runner.invoke(
        runtime_app,
        ["migrate-home", "--from", str(source), "--target", str(target)],
    )
    assert dry_run.exit_code == 0
    assert "Dry-run only" in dry_run.output
    assert not target.exists()

    applied = runner.invoke(
        runtime_app,
        ["migrate-home", "--from", str(source), "--target", str(target), "--apply"],
    )
    assert applied.exit_code == 0
    assert (target / "app.db").is_file()
    assert (source / "app.db").is_file()

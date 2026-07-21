"""Focused restore archive-snapshot integrity regressions."""

import shutil
import sqlite3
from pathlib import Path

import aletheia.backup.restore as restore_module
from aletheia.backup.restore import RestoreService
from aletheia.backup.service import BackupService


def _create_backup(tmp_path: Path, name: str, payload: str) -> Path:
    data_dir = tmp_path / name
    data_dir.mkdir()
    db = sqlite3.connect(data_dir / "app.db")
    try:
        db.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        db.execute("INSERT INTO marker (value) VALUES (?)", (payload,))
        db.commit()
    finally:
        db.close()
    (data_dir / "config.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    return BackupService(data_dir=data_dir).create_backup(label=name)


def _read_marker(db_path: Path) -> str:
    db = sqlite3.connect(db_path)
    try:
        return str(db.execute("SELECT value FROM marker").fetchone()[0])
    finally:
        db.close()


def test_restore_consumes_the_same_private_snapshot_that_was_verified(
    tmp_path: Path, monkeypatch
):
    """#285: replacing the source after verify cannot replace restored bytes."""
    trusted_archive = _create_backup(tmp_path, "trusted", "trusted database")
    replacement_archive = _create_backup(tmp_path, "replacement", "replacement database")
    target = tmp_path / "restore_target"
    observed_verify_paths: list[Path] = []
    real_verify = restore_module.verify_backup

    def verify_then_replace_source(snapshot_path: Path):
        observed_verify_paths.append(Path(snapshot_path))
        result = real_verify(snapshot_path)
        shutil.copy2(replacement_archive, trusted_archive)
        return result

    monkeypatch.setattr(restore_module, "verify_backup", verify_then_replace_source)

    result = RestoreService().restore(trusted_archive, target)

    assert result.success is True
    assert _read_marker(target / "app.db") == "trusted database"
    assert len(observed_verify_paths) == 1
    assert observed_verify_paths[0] != trusted_archive
    assert observed_verify_paths[0].name == "source-backup.tar.gz"


def test_restore_snapshot_copy_failure_leaves_target_untouched(tmp_path: Path):
    """#285: failure before snapshot verification cannot start target writes."""
    target = tmp_path / "restore_target"

    result = RestoreService().restore(tmp_path / "missing.tar.gz", target)

    assert result.success is False
    assert any("failed to snapshot" in error.lower() for error in result.errors)
    assert not target.exists()


def test_restore_copy_failure_removes_all_new_target_files(tmp_path: Path, monkeypatch):
    """#289: failure after the first install copy leaves no partial new target."""
    archive = _create_backup(tmp_path, "new-target", "new database")
    target = tmp_path / "restore_target"
    original_copy2 = restore_module.shutil.copy2

    def fail_config_install(source, destination, *args, **kwargs):
        destination = Path(destination)
        if destination == target / "config.yaml":
            raise OSError("injected target copy failure")
        return original_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(restore_module.shutil, "copy2", fail_config_install)

    result = RestoreService().restore(archive, target)

    assert result.success is False
    assert any("target rolled back" in error.lower() for error in result.errors)
    assert not target.exists()


def test_restore_copy_failure_restores_overwritten_files(tmp_path: Path, monkeypatch):
    """#289: an existing target is restored byte-for-byte after install failure."""
    archive = _create_backup(tmp_path, "existing-target", "new database")
    target = tmp_path / "restore_target"
    target.mkdir()
    old_database = b"original database bytes"
    (target / "app.db").write_bytes(old_database)
    (target / "keep.txt").write_text("unchanged\n", encoding="utf-8")
    original_copy2 = restore_module.shutil.copy2

    def fail_config_install(source, destination, *args, **kwargs):
        destination = Path(destination)
        if destination == target / "config.yaml":
            raise OSError("injected target copy failure")
        return original_copy2(source, destination, *args, **kwargs)

    monkeypatch.setattr(restore_module.shutil, "copy2", fail_config_install)

    result = RestoreService().restore(archive, target)

    assert result.success is False
    assert (target / "app.db").read_bytes() == old_database
    assert (target / "keep.txt").read_text(encoding="utf-8") == "unchanged\n"
    assert not (target / "config.yaml").exists()

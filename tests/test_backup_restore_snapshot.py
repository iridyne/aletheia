"""Focused restore archive-snapshot integrity regressions."""

import shutil
from pathlib import Path

import aletheia.backup.restore as restore_module
from aletheia.backup.restore import RestoreService
from aletheia.backup.service import BackupService


def _create_backup(tmp_path: Path, name: str, payload: bytes) -> Path:
    data_dir = tmp_path / name
    data_dir.mkdir()
    (data_dir / "app.db").write_bytes(payload)
    return BackupService(data_dir=data_dir).create_backup(label=name)


def test_restore_consumes_the_same_private_snapshot_that_was_verified(
    tmp_path: Path, monkeypatch
):
    """#285: replacing the source after verify cannot replace restored bytes."""
    trusted_archive = _create_backup(tmp_path, "trusted", b"trusted database")
    replacement_archive = _create_backup(tmp_path, "replacement", b"replacement database")
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
    assert (target / "app.db").read_bytes() == b"trusted database"
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

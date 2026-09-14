"""Regression tests for backup security fixes #1434 #1618."""
from __future__ import annotations
import inspect
import os
import tempfile
from pathlib import Path
import pytest


# ─────────────────────────────────────────────────────────────────────────────
# #1434 — Symlinks blocked in export/backup source trees
# ─────────────────────────────────────────────────────────────────────────────

def test_no_symlink_ignore_returns_symlinks_in_names(tmp_path):
    """_no_symlink_ignore must return symlink names for copytree to skip."""
    from aletheia.sync.exporter import _no_symlink_ignore
    # Create a real file and a symlink
    real = tmp_path / "real.txt"
    real.write_text("data")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    skipped = _no_symlink_ignore(str(tmp_path), ["real.txt", "link.txt"])
    assert "link.txt" in skipped, "symlink must be in skip list"
    assert "real.txt" not in skipped, "regular file must not be skipped"


def test_export_copytree_uses_symlink_protection():
    """export service copytree calls use _no_symlink_ignore."""
    import aletheia.sync.exporter as mod
    src = inspect.getsource(mod.ExportService._copy_artifacts)
    assert "_no_symlink_ignore" in src or "is_symlink" in src, (
        "_copy_artifacts must reject symlinks in backup source (#1434)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# #1618 — Symlinks at restore destination rejected
# ─────────────────────────────────────────────────────────────────────────────

def test_restore_security_error_class_exists():
    """RestoreSecurityError must be defined in restore module."""
    from aletheia.backup.restore import RestoreSecurityError
    assert issubclass(RestoreSecurityError, RuntimeError)


def test_restore_rejects_symlink_at_destination(tmp_path):
    """A verified archive cannot overwrite a file outside the restore target."""
    from aletheia.backup.restore import RestoreService
    from aletheia.backup.service import BackupService
    import sqlite3

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    with sqlite3.connect(bundle / "app.db") as db:
        db.execute("CREATE TABLE preserved (value TEXT)")
    archive = BackupService(bundle).create_backup()

    # Create target dir with a symlink at the destination path
    target = tmp_path / "target"
    target.mkdir()
    real_file = tmp_path / "real_elsewhere.txt"
    real_file.write_text("existing")
    link = target / "app.db"
    link.symlink_to(real_file)

    result = RestoreService().restore(archive, target)
    assert not result.success
    assert result.restored_files == 0
    assert any("symlink" in error for error in result.errors)
    assert link.is_symlink()
    assert real_file.read_text() == "existing"

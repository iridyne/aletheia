"""Core backup rotation keeps the former service algorithm without Ops."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from aletheia.maintenance.filesystem_retention import prune_backups


def test_rotation_preserves_pattern_mtime_clamp_and_removed_count(tmp_path, monkeypatch):
    backups = tmp_path / "backups"
    backups.mkdir()
    for index in range(3):
        path = backups / f"backup_{index}.tar.gz"
        path.touch()
        os.utime(path, (index + 1, index + 1))
    (backups / "unrelated.tar.gz").touch()
    (backups / "backup_directory.tar.gz").mkdir()
    assert prune_backups(1, tmp_path) == 2
    assert (backups / "backup_2.tar.gz").is_file()
    assert prune_backups(1, tmp_path) == 0
    assert prune_backups(-1, tmp_path) == 1
    assert (backups / "unrelated.tar.gz").is_file()
    assert (backups / "backup_directory.tar.gz").is_dir()


def test_mkdir_failure_precedes_keep_conversion(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise PermissionError("bounded mkdir failure")
    monkeypatch.setattr(Path, "mkdir", denied)
    with pytest.raises(PermissionError, match="bounded mkdir failure"):
        prune_backups("invalid keep", tmp_path)


def test_rotation_unlink_failure_keeps_archive_and_original_log_identity(tmp_path, monkeypatch, caplog):
    backups = tmp_path / "backups"
    backups.mkdir()
    path = backups / "backup_evidence.tar.gz"
    path.touch()
    def denied(*args, **kwargs):
        raise OSError("bounded unlink failure")
    monkeypatch.setattr(Path, "unlink", denied)
    with caplog.at_level("DEBUG", logger="aletheia.backup.service"):
        assert prune_backups(0, tmp_path) == 0
    assert path.is_file()
    assert any(r.name == "aletheia.backup.service" and "retain_recent skipped" in r.message for r in caplog.records)


def test_core_verification_capacity_and_retention_import_without_ops(tmp_path):
    script = '''
import sys
class MissingOps:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "ops" or fullname.startswith("ops."):
            raise ModuleNotFoundError(name=fullname)
sys.meta_path.insert(0, MissingOps())
from aletheia.backup.verify import verify_backup
from aletheia.maintenance.database_capacity import database_capacity_snapshot, reclaim_incremental_freelist
from aletheia.maintenance.filesystem_retention import prune_backups
from pathlib import Path
assert prune_backups(data_dir=Path(sys.argv[1])) == 0
assert (Path(sys.argv[1]) / "backups").is_dir()
assert "ops" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr

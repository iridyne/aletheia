"""Cross-store backup identity and recovery-profile regressions (#1597)."""

from __future__ import annotations

import sqlite3
import tarfile
import tempfile
from pathlib import Path

import yaml
import pytest


def _seed_workspace(root: Path) -> None:
    connection = sqlite3.connect(str(root / "app.db"))
    connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
    connection.execute("INSERT INTO sentinel VALUES ('before')")
    connection.commit()
    connection.close()
    logs = root / "logs"
    logs.mkdir()
    (logs / "audit.jsonl").write_text('{"id":"before"}\n', encoding="utf-8")


def _manifest(archive: Path) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(root, filter="data")
        bundle = next(path for path in root.iterdir() if path.is_dir())
        return yaml.safe_load((bundle / "manifest.yaml").read_text(encoding="utf-8"))


def test_online_backup_is_explicitly_non_recovery(tmp_path):
    _seed_workspace(tmp_path)

    from aletheia.backup.service import BackupService
    from aletheia.backup.verify import verify_backup

    archive = BackupService(data_dir=tmp_path).create_backup(label="online")
    manifest = _manifest(archive)
    result = verify_backup(archive)

    assert result.valid
    assert manifest["profile"] == "online"
    assert manifest["recovery_eligible"] is False
    assert result.recovery_eligible is False
    assert manifest["sqlite_identity"]
    assert manifest["audit_head_hash"]
    assert manifest["audit_inventory"]


def test_authoritative_backup_records_lab_and_audit_identity(tmp_path):
    _seed_workspace(tmp_path)
    lab = tmp_path / "observation-lab"
    lab.mkdir()
    connection = sqlite3.connect(str(lab / "lab.db"))
    connection.execute("CREATE TABLE evidence (value TEXT)")
    connection.execute("INSERT INTO evidence VALUES ('lab')")
    connection.commit()
    connection.close()
    archive_root = lab / "archives"
    archive_root.mkdir()
    (archive_root / "generation.json").write_text('{"generation":1}\n', encoding="utf-8")

    from aletheia.backup.service import BackupService
    from aletheia.backup.verify import verify_backup

    archive = BackupService(data_dir=tmp_path).create_backup(
        label="authoritative", profile="authoritative"
    )
    manifest = _manifest(archive)
    result = verify_backup(archive)

    assert result.valid, result.errors + result.mismatches
    assert result.recovery_eligible is True
    assert manifest["profile"] == "authoritative"
    assert manifest["lab_sqlite_identity"]
    assert manifest["checkpoint_identity"]

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(root, filter="data")
        bundle = next(path for path in root.iterdir() if path.is_dir())
        (bundle / "logs" / "audit.jsonl").write_text('{"id":"tampered"}\n', encoding="utf-8")
        tampered = tmp_path / "tampered.tar.gz"
        with tarfile.open(tampered, "w:gz") as tar:
            tar.add(bundle, arcname=bundle.name)
    checked = verify_backup(tampered)
    assert not checked.valid
    assert any("audit bytes changed" in error for error in checked.errors)


def test_lab_authoritative_backup_requires_verified_joint_checkpoint(tmp_path):
    _seed_workspace(tmp_path)
    app = sqlite3.connect(str(tmp_path / "app.db"))
    app.execute(
        "CREATE TABLE observation_authority_state ("
        "state_key TEXT PRIMARY KEY, authority_source TEXT NOT NULL, authority_epoch TEXT NOT NULL, "
        "actor TEXT NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    app.execute(
        "INSERT INTO observation_authority_state VALUES "
        "('experiment_authority', 'lab', 'lab:test', 'test', 'test', '2026-01-01T00:00:00+00:00')"
    )
    app.commit()
    app.close()
    lab = tmp_path / "observation-lab"
    lab.mkdir()
    connection = sqlite3.connect(str(lab / "lab.db"))
    connection.execute(
        "CREATE TABLE joint_checkpoints (joint_fingerprint TEXT, recorded_at TEXT, checkpoint_id TEXT)"
    )
    connection.commit()
    connection.close()

    from aletheia.backup.service import BackupService

    with pytest.raises(RuntimeError, match="joint_checkpoint"):
        BackupService(data_dir=tmp_path).create_backup(
            label="lab-without-proof", profile="authoritative"
        )


def test_restore_requires_authoritative_profile(tmp_path):
    _seed_workspace(tmp_path)

    from aletheia.backup.restore import RestoreService
    from aletheia.backup.service import BackupService

    archive = BackupService(data_dir=tmp_path).create_backup(label="online")
    result = RestoreService().restore(
        archive,
        tmp_path / "restore-target",
        require_recovery_checkpoint=True,
    )

    assert not result.success
    assert any("authoritative recovery checkpoint" in error for error in result.errors)

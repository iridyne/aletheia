"""Cross-store backup identity and recovery-profile regressions (#1597)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tarfile
import tempfile
from pathlib import Path

import yaml
import pytest


def _seed_outbox_cursor_bundle(root: Path, *, cursor_sequence: int = 1) -> Path:
    """Build the smallest Lab-authoritative outbox/cursor cut for verification."""
    bundle = root / "bundle"
    (bundle / "observation-lab").mkdir(parents=True)
    app = sqlite3.connect(str(bundle / "app.db"))
    app.execute(
        "CREATE TABLE observation_delivery_outbox ("
        "sequence INTEGER PRIMARY KEY AUTOINCREMENT, receipt_id TEXT, manifest_hash TEXT, acknowledged_at TEXT)"
    )
    app.execute(
        "INSERT INTO observation_delivery_outbox VALUES (1, 'r1', 'm1', '2026-01-01T00:00:00+00:00')"
    )
    app.commit()
    app.close()

    outbox_fingerprint = hashlib.sha256(
        json.dumps(
            [],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    cursor_payload = {
        "source_name": "main",
        "contiguous_sequence": cursor_sequence,
        "last_receipt_id": "r1",
        "retry_sequences": [],
        "receipt_chain_fingerprint": "a" * 64,
    }
    cursor_json = json.dumps(
        cursor_payload, sort_keys=True, separators=(",", ":"), default=str
    )
    cursor_fingerprint = hashlib.sha256(cursor_json.encode("utf-8")).hexdigest()

    lab = sqlite3.connect(str(bundle / "observation-lab" / "lab.db"))
    lab.executescript(
        "CREATE TABLE joint_checkpoints ("
        "main_outbox_high_watermark INTEGER, main_outbox_pending_count INTEGER, "
        "main_outbox_fingerprint TEXT, recorded_at TEXT, checkpoint_id TEXT);"
        "CREATE TABLE observation_import_cursors ("
        "source_name TEXT PRIMARY KEY, contiguous_sequence INTEGER, "
        "retry_sequences_json TEXT, source_checkpoint_json TEXT, source_checkpoint_fingerprint TEXT);"
        "CREATE TABLE observation_exposures (source_receipt_id TEXT);"
        "CREATE TABLE observation_lifecycle_events (source_receipt_id TEXT);"
        "CREATE TABLE lab_audit_outbox (payload_json TEXT);"
        "CREATE TABLE observation_archives (archive_path TEXT, archive_sha256 TEXT, replay_fingerprint TEXT);"
    )
    lab.execute("INSERT INTO observation_exposures VALUES ('r1')")
    lab.execute(
        "INSERT INTO observation_import_cursors VALUES (?, ?, ?, ?, ?)",
        ("main", cursor_sequence, "[]", cursor_json, cursor_fingerprint),
    )
    lab.execute(
        "INSERT INTO joint_checkpoints VALUES (?, ?, ?, ?, ?)",
        (1, 0, outbox_fingerprint, "2026-01-01T00:00:00+00:00", "cp1"),
    )
    lab.commit()
    lab.close()
    return bundle


def _seed_workspace(root: Path) -> None:
    connection = sqlite3.connect(str(root / "app.db"))
    connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
    connection.execute("INSERT INTO sentinel VALUES ('before')")
    connection.commit()
    connection.close()
    logs = root / "logs"
    logs.mkdir()
    (logs / "audit.jsonl").write_text('{"id":"before"}\n', encoding="utf-8")


def test_authoritative_outbox_cursor_cut_accepts_matching_import_proof(tmp_path):
    from aletheia.backup.verify import VerifyResult, _verify_outbox_cursor_cut

    bundle = _seed_outbox_cursor_bundle(tmp_path)
    result = VerifyResult()
    _verify_outbox_cursor_cut(bundle, result)

    assert result.valid, result.errors


def test_authoritative_outbox_cursor_cut_accepts_proven_retention_pruning(tmp_path):
    from aletheia.backup.verify import VerifyResult, _verify_outbox_cursor_cut

    bundle = _seed_outbox_cursor_bundle(tmp_path)
    app = sqlite3.connect(str(bundle / "app.db"))
    app.execute("DELETE FROM observation_delivery_outbox WHERE sequence=1")
    app.commit()
    app.close()

    result = VerifyResult()
    _verify_outbox_cursor_cut(bundle, result)

    assert result.valid, result.errors


def test_authoritative_outbox_cursor_cut_accepts_legacy_fingerprint(tmp_path):
    from aletheia.backup.verify import VerifyResult, _verify_outbox_cursor_cut

    bundle = _seed_outbox_cursor_bundle(tmp_path)
    legacy_fingerprint = hashlib.sha256(
        json.dumps(
            [[1, "r1", "m1", "2026-01-01T00:00:00+00:00"]],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    lab = sqlite3.connect(str(bundle / "observation-lab" / "lab.db"))
    lab.execute(
        "UPDATE joint_checkpoints SET main_outbox_fingerprint=?",
        (legacy_fingerprint,),
    )
    lab.commit()
    lab.close()

    result = VerifyResult()
    _verify_outbox_cursor_cut(bundle, result)

    assert result.valid, result.errors


def test_authoritative_outbox_cursor_cut_rejects_missing_sequence_watermark(tmp_path):
    from aletheia.backup.verify import VerifyResult, _verify_outbox_cursor_cut

    bundle = _seed_outbox_cursor_bundle(tmp_path)
    app = sqlite3.connect(str(bundle / "app.db"))
    app.executescript(
        "ALTER TABLE observation_delivery_outbox RENAME TO old_outbox;"
        "CREATE TABLE observation_delivery_outbox ("
        "sequence INTEGER PRIMARY KEY, receipt_id TEXT, manifest_hash TEXT, "
        "acknowledged_at TEXT);"
        "INSERT INTO observation_delivery_outbox SELECT * FROM old_outbox;"
        "DROP TABLE old_outbox;"
    )
    app.commit()
    app.close()

    result = VerifyResult()
    _verify_outbox_cursor_cut(bundle, result)

    assert not result.valid
    assert any("outbox cut is unreadable" in error for error in result.errors)


def test_authoritative_outbox_cursor_cut_rejects_acknowledged_row_ahead_of_cursor(tmp_path):
    from aletheia.backup.verify import VerifyResult, _verify_outbox_cursor_cut

    bundle = _seed_outbox_cursor_bundle(tmp_path)
    app = sqlite3.connect(str(bundle / "app.db"))
    app.execute(
        "INSERT INTO observation_delivery_outbox VALUES (2, 'r2', 'm2', '2026-01-01T00:00:00+00:00')"
    )
    app.commit()
    app.close()

    result = VerifyResult()
    _verify_outbox_cursor_cut(bundle, result)

    assert not result.valid
    assert any("newer than Lab cursor" in error for error in result.errors)


def test_authoritative_outbox_cursor_cut_rejects_pending_deletion(tmp_path):
    from aletheia.backup.verify import VerifyResult, _verify_outbox_cursor_cut

    bundle = _seed_outbox_cursor_bundle(tmp_path)
    app = sqlite3.connect(str(bundle / "app.db"))
    app.execute(
        "INSERT INTO observation_delivery_outbox "
        "(sequence, receipt_id, manifest_hash, acknowledged_at) "
        "VALUES (2, 'pending-r2', 'm2', NULL)"
    )
    app.commit()
    app.close()
    pending_fingerprint = hashlib.sha256(
        json.dumps(
            [[2, "pending-r2", "m2"]],
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    lab = sqlite3.connect(str(bundle / "observation-lab" / "lab.db"))
    lab.execute(
        "UPDATE joint_checkpoints SET main_outbox_high_watermark=2, "
        "main_outbox_pending_count=1, main_outbox_fingerprint=?",
        (pending_fingerprint,),
    )
    lab.commit()
    lab.close()
    app = sqlite3.connect(str(bundle / "app.db"))
    app.execute("DELETE FROM observation_delivery_outbox WHERE sequence=2")
    app.commit()
    app.close()

    result = VerifyResult()
    _verify_outbox_cursor_cut(bundle, result)

    assert not result.valid
    assert any("does not match joint checkpoint" in error for error in result.errors)


def test_authoritative_outbox_cursor_cut_rejects_retry_at_cursor(tmp_path):
    from aletheia.backup.verify import VerifyResult, _verify_outbox_cursor_cut

    bundle = _seed_outbox_cursor_bundle(tmp_path)
    lab = sqlite3.connect(str(bundle / "observation-lab" / "lab.db"))
    checkpoint = {
        "source_name": "main",
        "contiguous_sequence": 1,
        "last_receipt_id": "r1",
        "retry_sequences": [1],
        "receipt_chain_fingerprint": "a" * 64,
    }
    checkpoint_json = json.dumps(
        checkpoint, sort_keys=True, separators=(",", ":"), default=str
    )
    lab.execute(
        "UPDATE observation_import_cursors SET retry_sequences_json='[1]', "
        "source_checkpoint_json=?, source_checkpoint_fingerprint=? "
        "WHERE source_name='main'",
        (
            checkpoint_json,
            hashlib.sha256(checkpoint_json.encode("utf-8")).hexdigest(),
        ),
    )
    lab.commit()
    lab.close()

    result = VerifyResult()
    _verify_outbox_cursor_cut(bundle, result)

    assert not result.valid
    assert any("retry receipt is not ahead of cursor" in error for error in result.errors)


def _manifest(archive: Path) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(root, filter="data")
        bundle = next(path for path in root.iterdir() if path.is_dir())
        return yaml.safe_load((bundle / "manifest.yaml").read_text(encoding="utf-8"))


def test_online_backup_is_explicitly_non_recovery(tmp_path):
    _seed_workspace(tmp_path)

    from ops.backup.service import BackupService
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

    from ops.backup.service import BackupService
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

    from ops.backup.service import BackupService

    with pytest.raises(RuntimeError, match="joint_checkpoint"):
        BackupService(data_dir=tmp_path).create_backup(
            label="lab-without-proof", profile="authoritative"
        )


def test_restore_requires_authoritative_profile(tmp_path):
    _seed_workspace(tmp_path)

    from ops.backup.restore import RestoreService
    from ops.backup.service import BackupService

    archive = BackupService(data_dir=tmp_path).create_backup(label="online")
    result = RestoreService().restore(
        archive,
        tmp_path / "restore-target",
        require_recovery_checkpoint=True,
    )

    assert not result.success
    assert any("authoritative recovery checkpoint" in error for error in result.errors)


def test_authoritative_backup_refuses_a_data_root_without_app_db(tmp_path):
    """#1444: a recovery checkpoint that holds no database cannot be published."""
    _seed_workspace(tmp_path)
    (tmp_path / "app.db").unlink()

    from ops.backup.service import BackupService

    with pytest.raises(RuntimeError, match="authoritative_backup_requires_app_db"):
        BackupService(data_dir=tmp_path).create_backup(label="no-db", profile="authoritative")
    assert not list((tmp_path / "backups").glob("*.tar.gz"))
    # The online profile stays an explicit non-recovery snapshot.
    BackupService(data_dir=tmp_path).create_backup(label="no-db-online")


def test_update_rollback_point_check_rejects_online_and_accepts_authoritative(tmp_path):
    _seed_workspace(tmp_path)

    from ops.backup.service import BackupService
    from ops.update.auto import _recovery_checkpoint_problem

    service = BackupService(data_dir=tmp_path)
    online = service.create_backup(label="online")
    assert "not a recovery checkpoint" in (_recovery_checkpoint_problem(online) or "")
    assert _recovery_checkpoint_problem(service.create_backup(label="auth", profile="authoritative")) is None

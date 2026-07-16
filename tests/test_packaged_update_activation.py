from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia.update import packaged, packaged_commands


def _configure_request(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request: dict):
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.setenv("ALETHEIA_PACKAGED", "1")
    monkeypatch.setenv("ALETHEIA_HOST_ROOT", str(host))
    (host / "update-request.json").write_text(json.dumps(request), encoding="utf-8")
    return host


def test_normal_activation_runs_migrations_without_restoring_backup(monkeypatch, tmp_path):
    _configure_request(
        monkeypatch,
        tmp_path,
        {
            "schema_version": 1,
            "operation_id": "update-normal",
            "action": "activate",
            "candidate_release": "new",
            "previous_release": "old",
            "backup_path": str(tmp_path / "backup.tar.gz"),
        },
    )
    monkeypatch.setattr(
        packaged,
        "restore_update_backup",
        lambda *_args, **_kwargs: pytest.fail("normal activation must not restore the backup"),
    )
    monkeypatch.setattr(
        packaged,
        "validate_current_data",
        lambda: {"ok": True, "sqlite": "ok", "audit_records": 7},
    )

    result = packaged_commands.prepare_activation_data()
    assert result == {
        "ok": True,
        "action": "activate",
        "validation": {"ok": True, "sqlite": "ok", "audit_records": 7},
    }


def test_manual_rollback_restores_exact_checkpoint_before_validation(monkeypatch, tmp_path):
    backup = tmp_path / "pre-update.tar.gz"
    backup.write_bytes(b"backup")
    _configure_request(
        monkeypatch,
        tmp_path,
        {
            "schema_version": 1,
            "operation_id": "rollback-normal",
            "action": "rollback",
            "candidate_release": "old",
            "previous_release": "new",
            "backup_path": str(backup),
        },
    )
    calls: list[Path] = []

    def restore(path: Path):
        calls.append(path)
        return {"success": True, "files_restored": 12}

    monkeypatch.setattr(packaged, "restore_update_backup", restore)
    monkeypatch.setattr(
        packaged,
        "validate_current_data",
        lambda: pytest.fail("restore_update_backup already validates restored data"),
    )

    result = packaged_commands.prepare_activation_data()
    assert calls == [backup]
    assert result == {
        "ok": True,
        "action": "rollback",
        "restored": {"success": True, "files_restored": 12},
    }


def test_rollback_data_preparation_rejects_missing_checkpoint(monkeypatch, tmp_path):
    missing = tmp_path / "missing.tar.gz"
    _configure_request(
        monkeypatch,
        tmp_path,
        {
            "schema_version": 1,
            "operation_id": "rollback-missing",
            "action": "rollback",
            "candidate_release": "old",
            "previous_release": "new",
            "backup_path": str(missing),
        },
    )

    with pytest.raises(packaged.PackagedUpdateError) as caught:
        packaged_commands.prepare_activation_data()
    assert caught.value.reason == "rollback_checkpoint_unavailable"

"""Focused backup checksum-manifest integrity regressions."""

import tarfile
from pathlib import Path

import pytest

from aletheia.backup.restore import RestoreService
from aletheia.backup.service import BackupService
from aletheia.backup.verify import verify_backup
from aletheia.storage.hashing import sha256_file


def _build_backup_archive(
    tmp_path: Path, *, checksum_lines: list[str], include_app_db: bool = True
) -> Path:
    staging = tmp_path / "backup_staging"
    staging.mkdir()
    if include_app_db:
        (staging / "app.db").write_bytes(b"database payload")
    (staging / "manifest.yaml").write_text("backup_id: test\n", encoding="utf-8")
    (staging / "checksums.txt").write_text(
        "\n".join(checksum_lines) + ("\n" if checksum_lines else ""),
        encoding="utf-8",
    )
    archive = tmp_path / "backup_test.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(staging, arcname="backup_test")
    return archive


@pytest.mark.parametrize(
    ("checksum_lines", "error_fragment"),
    [
        (["not-a-checksum  app.db"], "malformed sha-256"),
        (["0" * 64 + " app.db"], "malformed checksum entry"),
        (["0" * 64 + "  ../app.db"], "unsafe checksum path"),
    ],
)
def test_backup_verifier_rejects_malformed_or_unsafe_entries(
    tmp_path, checksum_lines, error_fragment
):
    """#279: invalid manifest syntax is an integrity failure, never a skipped line."""
    archive = _build_backup_archive(tmp_path, checksum_lines=checksum_lines)

    result = verify_backup(archive)

    assert result.valid is False
    assert any(error_fragment in error.lower() for error in result.errors)


def test_backup_verifier_rejects_duplicate_checksum_paths(tmp_path):
    """#279: one payload path has exactly one authoritative checksum."""
    payload = tmp_path / "payload"
    payload.write_bytes(b"database payload")
    checksum = sha256_file(payload)
    archive = _build_backup_archive(
        tmp_path, checksum_lines=[f"{checksum}  app.db", f"{checksum}  app.db"]
    )

    result = verify_backup(archive)

    assert result.valid is False
    assert any("duplicate checksum path" in error.lower() for error in result.errors)


def test_unlisted_database_is_invalid_and_restore_writes_nothing(tmp_path):
    """#279: restore cannot consume an app.db omitted from the checksum manifest."""
    archive = _build_backup_archive(tmp_path, checksum_lines=[])
    target = tmp_path / "restore_target"

    verify = verify_backup(archive)
    restored = RestoreService().restore(archive, target)

    assert verify.valid is False
    assert "Unlisted: app.db" in verify.mismatches
    assert restored.success is False
    assert not (target / "app.db").exists()


def test_backup_service_output_still_verifies_and_restores(tmp_path):
    """#279: strict completeness preserves the canonical backup format."""
    data_dir = tmp_path / "runtime"
    data_dir.mkdir()
    (data_dir / "app.db").write_bytes(b"not sqlite, copied by fallback")
    (data_dir / "config.yaml").write_text("mode: paper\n", encoding="utf-8")

    archive = BackupService(data_dir=data_dir).create_backup(label="valid")
    verify = verify_backup(archive)
    target = tmp_path / "valid_restore"
    restored = RestoreService().restore(archive, target)

    assert verify.valid is True
    assert verify.checked_count == verify.file_count == 2
    assert restored.success is True
    assert (target / "app.db").read_bytes() == b"not sqlite, copied by fallback"
    assert (target / "config.yaml").read_text(encoding="utf-8") == "mode: paper\n"

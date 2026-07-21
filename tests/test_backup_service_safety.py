"""Focused BackupService path and naming safety regressions."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import aletheia.backup.service as backup_service_module
from aletheia.backup.service import BackupService
from aletheia.backup.verify import verify_backup


@pytest.mark.parametrize(
    "label",
    [
        "",
        ".",
        "..",
        "../escape",
        "../../../../escape",
        "nested/name",
        r"nested\name",
        "C:escape",
        "bad label",
        "line\nbreak",
        "a" * 65,
        "trailing.",
    ],
)
def test_backup_rejects_unsafe_labels_before_creating_paths(tmp_path: Path, label: str):
    """#281: a caller label is never a path fragment."""
    data_dir = tmp_path / "runtime"
    service = BackupService(data_dir=data_dir)

    with pytest.raises(ValueError, match="Backup label"):
        service.create_backup(label=label)

    assert list((data_dir / "backups").iterdir()) == []
    assert list(tmp_path.rglob("*.tar.gz")) == []


@pytest.mark.parametrize("label", ["nightly-01", "paper_v2", "snapshot.7", "A"])
def test_backup_accepts_safe_single_component_labels(tmp_path: Path, label: str):
    """#281: ordinary operator labels remain supported and contained."""
    data_dir = tmp_path / "runtime"
    archive = BackupService(data_dir=data_dir).create_backup(label=label)

    assert archive.parent.resolve() == (data_dir / "backups").resolve()
    assert archive.name.startswith(f"backup_{label}_")
    assert verify_backup(archive).valid is True


def test_unlabelled_backup_remains_supported_and_contained(tmp_path: Path):
    data_dir = tmp_path / "runtime"
    archive = BackupService(data_dir=data_dir).create_backup()

    assert archive.parent.resolve() == (data_dir / "backups").resolve()
    assert archive.name.startswith("backup_")
    assert verify_backup(archive).valid is True


def test_same_timestamp_and_label_get_unique_staging_and_archive_paths(
    tmp_path: Path, monkeypatch
):
    """#283: timestamp resolution cannot merge two backup invocations."""

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)
            return value if tz is not None else value.replace(tzinfo=None)

    data_dir = tmp_path / "runtime"
    backup_dir = data_dir / "backups"
    cleaned_staging_paths: list[Path] = []
    original_rmtree = backup_service_module.shutil.rmtree

    def record_staging_cleanup(path, *args, **kwargs):
        candidate = Path(path)
        if candidate.parent == backup_dir:
            cleaned_staging_paths.append(candidate)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(backup_service_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(backup_service_module.shutil, "rmtree", record_staging_cleanup)

    service = BackupService(data_dir=data_dir)
    first = service.create_backup(label="same")
    second = service.create_backup(label="same")

    assert first != second
    assert first.exists() and second.exists()
    assert verify_backup(first).valid is True
    assert verify_backup(second).valid is True
    assert len(cleaned_staging_paths) == 2
    assert cleaned_staging_paths[0] != cleaned_staging_paths[1]


def test_corrupt_database_aborts_backup_without_publishing_archive(tmp_path: Path):
    """#287: checksums cannot turn rejected SQLite bytes into a valid recovery point."""
    data_dir = tmp_path / "runtime"
    data_dir.mkdir()
    (data_dir / "app.db").write_bytes(b"not a sqlite database")
    service = BackupService(data_dir=data_dir)

    with pytest.raises(sqlite3.DatabaseError):
        service.create_backup(label="corrupt")

    assert list((data_dir / "backups").glob("*.tar.gz")) == []
    assert list((data_dir / "backups").glob("*_staging")) == []

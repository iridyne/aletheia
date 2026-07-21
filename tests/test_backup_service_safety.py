"""Focused BackupService path and naming safety regressions."""

from pathlib import Path

import pytest

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

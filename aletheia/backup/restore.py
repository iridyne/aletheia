"""Backup restore — recovers workspace from a backup archive."""

import shutil
import tarfile
import tempfile
from pathlib import Path

import yaml

from aletheia.backup.verify import verify_backup


class RestoreResult:
    """Result of a restore operation."""

    def __init__(self):
        self.success: bool = False
        self.restored_files: int = 0
        self.target_dir: str = ""
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "restored_files": self.restored_files,
            "target_dir": self.target_dir,
            "errors": self.errors,
        }


class RestoreService:
    """Restores workspace from a backup archive."""

    @staticmethod
    def _verified_snapshot(
        archive_path: Path, workspace: Path, result: RestoreResult
    ) -> Path | None:
        """Copy once, then verify the immutable per-call archive snapshot."""
        snapshot_path = workspace / "source-backup.tar.gz"
        try:
            shutil.copy2(archive_path, snapshot_path)
        except OSError as exc:
            result.errors.append(f"Failed to snapshot backup archive: {exc}")
            return None

        verify = verify_backup(snapshot_path)
        if not verify.valid:
            result.errors = [
                f"Verification failed: {error}"
                for error in verify.errors + verify.mismatches
            ]
            return None
        return snapshot_path

    def dry_run(self, archive_path: Path, target_dir: Path) -> RestoreResult:
        """Preview restore without making changes."""
        result = RestoreResult()
        result.target_dir = str(target_dir)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            snapshot_path = self._verified_snapshot(archive_path, tmp_path, result)
            if snapshot_path is None:
                return result

            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir()
            with tarfile.open(snapshot_path, "r:gz") as tar:
                tar.extractall(extract_dir, filter="data")
            subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
            if subdirs:
                bundle_dir = subdirs[0]
                for f in bundle_dir.rglob("*"):
                    if f.is_file() and f.name not in ("checksums.txt", "manifest.yaml"):
                        result.restored_files += 1

        result.success = True
        return result

    def restore(self, archive_path: Path, target_dir: Path) -> RestoreResult:
        """Restore backup to target directory."""
        result = RestoreResult()
        result.target_dir = str(target_dir)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            snapshot_path = self._verified_snapshot(archive_path, tmp_path, result)
            if snapshot_path is None:
                return result

            extract_dir = tmp_path / "extracted"
            extract_dir.mkdir()
            with tarfile.open(snapshot_path, "r:gz") as tar:
                tar.extractall(extract_dir, filter="data")

            subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
            if not subdirs:
                result.errors.append("Empty archive")
                return result
            bundle_dir = subdirs[0]

            # Restore to target
            target_dir.mkdir(parents=True, exist_ok=True)
            count = 0
            for item in bundle_dir.rglob("*"):
                if item.is_file() and item.name not in ("checksums.txt", "manifest.yaml"):
                    rel = item.relative_to(bundle_dir)
                    dst = target_dir / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dst)
                    count += 1

            result.restored_files = count
            result.success = True

        return result

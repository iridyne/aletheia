"""Backup restore — recovers workspace from a backup archive."""

import shutil
import tarfile
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

    def dry_run(self, archive_path: Path, target_dir: Path) -> RestoreResult:
        """Preview restore without making changes."""
        result = RestoreResult()
        result.target_dir = str(target_dir)

        # Verify first
        verify = verify_backup(archive_path)
        if not verify.valid:
            result.errors = [f"Verification failed: {e}" for e in verify.errors + verify.mismatches]
            return result

        # Count what would be restored
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(tmp_path, filter="data")
            subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
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

        # Verify first
        verify = verify_backup(archive_path)
        if not verify.valid:
            result.errors = [f"Verification failed: {e}" for e in verify.errors + verify.mismatches]
            return result

        # Extract
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(tmp_path, filter="data")

            subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
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

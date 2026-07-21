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

    @staticmethod
    def _install_bundle(
        bundle_dir: Path,
        target_dir: Path,
        workspace: Path,
        result: RestoreResult,
    ) -> bool:
        """Install verified files with process-failure rollback for the target."""
        source_files = [
            item
            for item in bundle_dir.rglob("*")
            if item.is_file() and item.name not in ("checksums.txt", "manifest.yaml")
        ]
        target_existed = target_dir.exists()
        if target_dir.is_symlink() or (target_existed and not target_dir.is_dir()):
            result.errors.append("Restore target must be a real directory")
            return False

        existing_dirs = {target_dir} if target_existed else set()
        if target_existed:
            existing_dirs.update(path for path in target_dir.rglob("*") if path.is_dir())

        rollback_dir = workspace / "rollback"
        rollback_dir.mkdir()
        rollback_files: dict[Path, Path] = {}
        try:
            for source in source_files:
                rel = source.relative_to(bundle_dir)
                destination = target_dir / rel
                if destination.is_symlink():
                    raise OSError(f"restore destination is a symlink: {rel}")
                if destination.exists():
                    if not destination.is_file():
                        raise OSError(f"restore destination is not a file: {rel}")
                    rollback_copy = rollback_dir / rel
                    rollback_copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, rollback_copy)
                    rollback_files[destination] = rollback_copy
        except OSError as exc:
            result.errors.append(f"Failed to prepare restore rollback: {exc}")
            return False

        new_files: list[Path] = []
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            for source in source_files:
                rel = source.relative_to(bundle_dir)
                destination = target_dir / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination not in rollback_files:
                    new_files.append(destination)
                shutil.copy2(source, destination)
        except OSError as exc:
            rollback_errors: list[str] = []
            for destination in reversed(new_files):
                try:
                    if destination.exists() or destination.is_symlink():
                        destination.unlink()
                except OSError as rollback_exc:
                    rollback_errors.append(f"remove {destination}: {rollback_exc}")
            for destination, rollback_copy in rollback_files.items():
                try:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(rollback_copy, destination)
                except OSError as rollback_exc:
                    rollback_errors.append(f"restore {destination}: {rollback_exc}")

            if target_dir.exists():
                new_dirs = [
                    path
                    for path in target_dir.rglob("*")
                    if path.is_dir() and path not in existing_dirs
                ]
                for directory in sorted(new_dirs, key=lambda path: len(path.parts), reverse=True):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                if not target_existed:
                    try:
                        target_dir.rmdir()
                    except OSError:
                        pass

            result.errors.append(f"Restore copy failed; target rolled back: {exc}")
            if rollback_errors:
                result.errors.append("Restore rollback incomplete: " + "; ".join(rollback_errors))
            return False

        result.restored_files = len(source_files)
        return True

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

            if not self._install_bundle(bundle_dir, target_dir, tmp_path, result):
                return result
            result.success = True

        return result

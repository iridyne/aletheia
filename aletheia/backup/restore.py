"""Backup restore — recovers workspace from a backup archive."""

import os
import shutil
import tarfile
import tempfile
from pathlib import Path

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
    def _copy_to_snapshot(archive_path: Path) -> Path:
        """Copy archive to an isolated temp file (#285).

        Verify and extract operate on this private snapshot rather than the
        mutable source path, so replacing the archive between the verify and
        extract calls cannot affect which bytes are consumed.
        """
        fd, snap = tempfile.mkstemp(suffix=".tar.gz.snap")
        os.close(fd)
        snap_path = Path(snap)
        shutil.copy2(archive_path, snap_path)
        return snap_path

    def dry_run(self, archive_path: Path, target_dir: Path) -> RestoreResult:
        """Preview restore without making changes."""
        result = RestoreResult()
        result.target_dir = str(target_dir)

        snap = self._copy_to_snapshot(archive_path)
        try:
            verify = verify_backup(snap)
            if not verify.valid:
                result.errors = [
                    f"Verification failed: {e}" for e in verify.errors + verify.mismatches
                ]
                return result

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                with tarfile.open(snap, "r:gz") as tar:
                    tar.extractall(tmp_path, filter="data")
                subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
                if subdirs:
                    bundle_dir = subdirs[0]
                    for f in bundle_dir.rglob("*"):
                        if f.is_file() and f.name not in ("checksums.txt", "manifest.yaml"):
                            result.restored_files += 1

            result.success = True
            return result
        finally:
            snap.unlink(missing_ok=True)

    def restore(self, archive_path: Path, target_dir: Path) -> RestoreResult:
        """Restore backup to target directory.

        #285: verifies and extracts the same private snapshot of the archive.
        #289: tracks overwrites and new files; rolls back on any copy failure.
        """
        result = RestoreResult()
        result.target_dir = str(target_dir)

        snap = self._copy_to_snapshot(archive_path)
        try:
            verify = verify_backup(snap)
            if not verify.valid:
                result.errors = [
                    f"Verification failed: {e}" for e in verify.errors + verify.mismatches
                ]
                return result

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                with tarfile.open(snap, "r:gz") as tar:
                    tar.extractall(tmp_path, filter="data")

                subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
                if not subdirs:
                    result.errors.append("Empty archive")
                    return result
                bundle_dir = subdirs[0]

                files_to_restore = sorted(
                    item for item in bundle_dir.rglob("*")
                    if item.is_file() and item.name not in ("checksums.txt", "manifest.yaml")
                )

                # #289: set up rollback tracking before any mutation
                target_was_absent = not target_dir.exists()
                target_dir.mkdir(parents=True, exist_ok=True)

                rollback_dir = tmp_path / "_rollback"
                rollback_dir.mkdir()
                overwrites: list[tuple[Path, Path]] = []  # (dst, backup_copy)
                new_files: list[Path] = []

                try:
                    for item in files_to_restore:
                        rel = item.relative_to(bundle_dir)
                        dst = target_dir / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)

                        if dst.exists():
                            bk = rollback_dir / rel
                            bk.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(dst, bk)
                            overwrites.append((dst, bk))
                        else:
                            new_files.append(dst)

                        shutil.copy2(item, dst)
                        result.restored_files += 1

                    result.success = True
                    return result

                except Exception as exc:
                    # Restore overwritten files to their pre-restore bytes
                    for dst, bk in overwrites:
                        try:
                            shutil.copy2(bk, dst)
                        except Exception:
                            pass
                    # Remove files that didn't exist before this call
                    for p in new_files:
                        try:
                            p.unlink(missing_ok=True)
                        except Exception:
                            pass
                    # If target_dir was created by this call, remove it entirely
                    if target_was_absent:
                        shutil.rmtree(target_dir, ignore_errors=True)

                    result.errors.append(f"Restore failed and was rolled back: {exc}")
                    result.restored_files = 0
                    return result
        finally:
            snap.unlink(missing_ok=True)

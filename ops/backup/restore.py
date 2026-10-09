"""Backup restore — recovers workspace from a backup archive."""

import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from aletheia.backup.verify import verify_backup
from aletheia.runtime_paths import large_temp_dir

# The rollback point: these SQLite snapshots replace the target's copies.
# Every other root in the backup manifest (ops/backup/service.py) — events/,
# artifacts/, logs/ other than the audit family, observation-lab/archives/ —
# restores append-only: a missing file is written, an existing one is kept,
# nothing is deleted (owner decision 2026-10-09, #3765 / #1441).
_ROLLBACK_POINT = frozenset({Path("app.db"), Path("observation-lab/lab.db")})


class RestoreSecurityError(RuntimeError):
    """Raised when a restore operation would write outside the target tree via symlink."""


def _is_audit_generation(path: Path) -> bool:
    if path.name == "audit.jsonl":
        return True
    prefix = "audit.jsonl."
    return path.name.startswith(prefix) and path.name[len(prefix):].isdigit()


def _append_only(rel: Path) -> bool:
    if rel in _ROLLBACK_POINT:
        return False
    return not (rel.parent == Path("logs") and _is_audit_generation(rel))


class RestoreResult:
    """Result of a restore operation."""

    def __init__(self):
        self.success: bool = False
        self.restored_files: int = 0
        self.target_dir: str = ""
        self.errors: list[str] = []
        # Archived paths left untouched because the target already had them (#1441).
        self.preserved_files: list[str] = []
        # #1621: a live config.yaml is never overwritten, so redacted fields are
        # never rehydrated into it. "preserved_live" | "restored_redacted"
        # (target had none) | "absent_in_checkpoint".
        self.config_disposition: str = "absent_in_checkpoint"
        # Rollback actions that failed after a failed restore: the target is mixed.
        self.rollback_failures: list[dict[str, str]] = []

    @property
    def rollback_complete(self) -> bool:
        return not self.rollback_failures

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "restored_files": self.restored_files,
            "target_dir": self.target_dir,
            "errors": self.errors,
            "preserved_files": self.preserved_files,
            "config_disposition": self.config_disposition,
            "rollback_complete": self.rollback_complete,
            "rollback_failures": self.rollback_failures,
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
        fd, snap = tempfile.mkstemp(suffix=".tar.gz.snap", dir=large_temp_dir())
        os.close(fd)
        snap_path = Path(snap)
        shutil.copy2(archive_path, snap_path)
        return snap_path

    def dry_run(
        self,
        archive_path: Path,
        target_dir: Path,
        require_recovery_checkpoint: bool = False,
    ) -> RestoreResult:
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
            if require_recovery_checkpoint and not verify.recovery_eligible:
                result.errors.append(
                    "Restore requires an authoritative recovery checkpoint; "
                    f"archive profile is {verify.profile or 'unknown'}"
                )
                return result

            with tempfile.TemporaryDirectory(dir=large_temp_dir()) as tmp:
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

    def restore(
        self,
        archive_path: Path,
        target_dir: Path,
        require_recovery_checkpoint: bool = False,
    ) -> RestoreResult:
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
            if require_recovery_checkpoint and not verify.recovery_eligible:
                result.errors.append(
                    "Restore requires an authoritative recovery checkpoint; "
                    f"archive profile is {verify.profile or 'unknown'}"
                )
                return result

            with tempfile.TemporaryDirectory(dir=large_temp_dir()) as tmp:
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
                removed_files: list[tuple[Path, Path]] = []

                try:
                    archived_audit_paths = {
                        item.relative_to(bundle_dir)
                        for item in files_to_restore
                        if item.parent.relative_to(bundle_dir) == Path("logs")
                        and _is_audit_generation(item)
                    }
                    # #1598: a restored standalone .db snapshot must not meet a
                    # -wal/-shm left by the runtime it replaces; set them aside
                    # (rolled back with the rest on failure).
                    stale_sidecars = [
                        target_dir / item.relative_to(bundle_dir).with_name(item.name + suffix)
                        for item in files_to_restore
                        if item.relative_to(bundle_dir) in _ROLLBACK_POINT
                        for suffix in ("-wal", "-shm")
                    ]
                    for stale in stale_sidecars:
                        if not (stale.exists() or stale.is_symlink()):
                            continue
                        rel = stale.relative_to(target_dir)
                        if stale.is_symlink():
                            raise RestoreSecurityError(
                                f"restore target is a symlink and would redirect the write: {rel}"
                            )
                        bk = rollback_dir / "_removed" / rel
                        bk.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(stale, bk)
                        removed_files.append((stale, bk))
                        stale.unlink()

                    target_logs = target_dir / "logs"
                    if target_logs.is_symlink():
                        raise RestoreSecurityError(
                            "restore target logs directory is a symlink"
                        )
                    if target_logs.exists():
                        for stale in target_logs.glob("audit.jsonl*"):
                            rel = stale.relative_to(target_dir)
                            if not _is_audit_generation(stale) or rel in archived_audit_paths:
                                continue
                            if stale.is_symlink():
                                raise RestoreSecurityError(
                                    f"restore target is a symlink and would redirect the write: {rel}"
                                )
                            bk = rollback_dir / "_removed" / rel
                            bk.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(stale, bk)
                            removed_files.append((stale, bk))
                            stale.unlink()

                    for item in files_to_restore:
                        rel = item.relative_to(bundle_dir)
                        dst = target_dir / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)

                        # #1618: reject symlinks at destination — they redirect restore
                        # copies outside the intended target tree.
                        if dst.is_symlink():
                            raise RestoreSecurityError(
                                f"restore target is a symlink and would redirect the write: {rel}"
                            )

                        if rel == Path("config.yaml"):
                            result.config_disposition = (
                                "preserved_live" if dst.exists() else "restored_redacted"
                            )
                        if dst.exists() and _append_only(rel):
                            result.preserved_files.append(rel.as_posix())
                            continue
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
                    def undo(path: Path, action: str, step) -> None:
                        try:
                            step()
                        except Exception as undo_exc:
                            result.rollback_failures.append(
                                {"path": str(path), "action": action, "error": repr(undo_exc)}
                            )

                    # Restore overwritten files to their pre-restore bytes
                    for dst, bk in overwrites:
                        undo(dst, "restore_overwritten", lambda dst=dst, bk=bk: shutil.copy2(bk, dst))
                    # Remove files that didn't exist before this call
                    for p in new_files:
                        undo(p, "remove_new", lambda p=p: p.unlink(missing_ok=True))
                    # Restore removed audit generations and WAL sidecars
                    for dst, bk in removed_files:
                        def put_back(dst=dst, bk=bk):
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(bk, dst)
                        undo(dst, "restore_removed", put_back)
                    # If target_dir was created by this call, remove it entirely
                    if target_was_absent:
                        undo(target_dir, "remove_created_target", lambda: shutil.rmtree(target_dir))

                    if result.rollback_complete:
                        result.errors.append(f"Restore failed and was rolled back: {exc}")
                    else:
                        paths = ", ".join(f["path"] for f in result.rollback_failures)
                        result.errors.append(
                            f"Restore failed and rollback INCOMPLETE ({paths}): {exc}"
                        )
                    result.restored_files = 0
                    return result
        finally:
            snap.unlink(missing_ok=True)

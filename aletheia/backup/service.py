"""Backup service — creates and manages workspace backups."""

import logging
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aletheia.config.loader import get_data_dir
from aletheia.storage.hashing import sha256_file

logger = logging.getLogger("aletheia.backup.service")

# Default number of backup archives to keep on disk. Anything older (by mtime)
# is removed after each new backup write and can also be reaped standalone by
# the scheduler.
DEFAULT_KEEP_RECENT = 7


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class BackupManifest:
    """Metadata for a backup bundle."""

    def __init__(self, backup_id: str, created_at: str, data_dir: str, file_count: int, total_bytes: int):
        self.backup_id = backup_id
        self.created_at = created_at
        self.data_dir = data_dir
        self.file_count = file_count
        self.total_bytes = total_bytes

    def to_dict(self) -> dict:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "data_dir": self.data_dir,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
        }


# Paths that must never be included in a backup
_EXCLUDE_PATTERNS = {".env", ".key", ".pem", ".secret", "secrets/", "__pycache__", ".pyc"}


def _should_exclude(path: Path) -> bool:
    name_lower = str(path).lower()
    for pat in _EXCLUDE_PATTERNS:
        if pat in name_lower:
            return True
    return False


class BackupService:
    """Creates full or incremental workspace backups."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or get_data_dir()
        self._backup_dir = self._data_dir / "backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _snapshot_sqlite_db(src: Path, dst: Path) -> None:
        """Copy a SQLite database using Connection.backup() so WAL-mode commits
        that haven't been checkpointed yet still make it into the archive.

        `sqlite3.Connection.backup(target)` is the officially-safe way to snapshot
        a live database — it holds a read lock, walks pages including the WAL,
        and produces a consistent .db without a -wal/-shm sidecar. Falling back
        to shutil.copy2 keeps the old best-effort behaviour when the file isn't
        actually SQLite (e.g. empty placeholder or a stripped test fixture)."""
        import sqlite3

        try:
            src_conn = sqlite3.connect(str(src))
            try:
                dst_conn = sqlite3.connect(str(dst))
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()
        except sqlite3.DatabaseError:
            # Not a SQLite file (or corrupt beyond backup()) — best-effort raw copy.
            shutil.copy2(src, dst)

    def create_backup(self, label: str | None = None) -> Path:
        """Create a full backup of the workspace. Returns archive path."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_id = f"backup_{timestamp}"
        if label:
            backup_id = f"backup_{label}_{timestamp}"

        staging = self._backup_dir / f"_{backup_id}_staging"
        staging.mkdir(parents=True, exist_ok=True)

        try:
            file_count = 0
            total_bytes = 0

            # Copy app.db.
            # #59 fix: SQLite runs in WAL journal mode; a bare shutil.copy2 of the
            # .db file races the writer — committed rows still in the -wal sidecar
            # get missed, and the copied .db can even be internally inconsistent
            # (page torn between the copy and a mid-flight WAL checkpoint). Use
            # sqlite3.Connection.backup() which is the API designed for exactly
            # this: a live consistent snapshot even under concurrent writes, no
            # checkpoint hazard, no partial WAL. Fall back to the raw copy if the
            # source isn't a readable SQLite file (empty init or non-sqlite path)
            # so the backup still packages what's on disk rather than exploding.
            db_path = self._data_dir / "app.db"
            if db_path.exists():
                dst = staging / "app.db"
                self._snapshot_sqlite_db(db_path, dst)
                file_count += 1
                total_bytes += dst.stat().st_size

            # Copy config
            config_path = self._data_dir / "config.yaml"
            if config_path.exists():
                shutil.copy2(config_path, staging / "config.yaml")
                file_count += 1
                total_bytes += config_path.stat().st_size

            # Copy events dir
            events_dir = self._data_dir / "events"
            if events_dir.exists():
                dst_events = staging / "events"
                shutil.copytree(events_dir, dst_events, dirs_exist_ok=True)
                for f in dst_events.rglob("*"):
                    if f.is_file():
                        file_count += 1
                        total_bytes += f.stat().st_size

            # #58 fix: copy runtime-home logs/ (contains audit.jsonl, the hash-chained
            # append-only audit log). Backup previously only captured app.db +
            # config + events + artifacts — restoring from that omitted the entire
            # audit chain, so a fresh restore couldn't verify the audit-integrity
            # invariant (files/jsonl.verify_audit_chain). Recovering audit trust
            # requires the file itself. Secrets are excluded via _should_exclude.
            logs_dir = self._data_dir / "logs"
            if logs_dir.exists():
                dst_logs = staging / "logs"
                dst_logs.mkdir(exist_ok=True)
                for item in logs_dir.rglob("*"):
                    if item.is_file() and not _should_exclude(item):
                        rel = item.relative_to(logs_dir)
                        dst = dst_logs / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dst)
                        file_count += 1
                        total_bytes += item.stat().st_size

            # Copy artifacts (excluding secrets)
            artifacts_dir = self._data_dir / "artifacts"
            if artifacts_dir.exists():
                dst_artifacts = staging / "artifacts"
                dst_artifacts.mkdir(exist_ok=True)
                for item in artifacts_dir.rglob("*"):
                    if item.is_file() and not _should_exclude(item):
                        rel = item.relative_to(artifacts_dir)
                        dst = dst_artifacts / rel
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dst)
                        file_count += 1
                        total_bytes += item.stat().st_size

            # Write checksums
            checksums = []
            for f in sorted(staging.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(staging)
                    h = sha256_file(f)
                    checksums.append(f"{h}  {rel}")
            (staging / "checksums.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")

            # Write manifest
            manifest = BackupManifest(
                backup_id=backup_id,
                created_at=_utcnow(),
                data_dir=str(self._data_dir),
                file_count=file_count,
                total_bytes=total_bytes,
            )
            (staging / "manifest.yaml").write_text(
                yaml.dump(manifest.to_dict(), default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )

            # Create tar.gz
            archive_path = self._backup_dir / f"{backup_id}.tar.gz"
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(staging, arcname=backup_id)

            # Rotate older archives — every write path funnels through here,
            # so keeping the count bounded per-write means the standalone
            # scheduler sweep only has to catch the edge cases (crashed
            # writes, manual copies).
            try:
                self.retain_recent(DEFAULT_KEEP_RECENT)
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("backup retain_recent after create failed: %s", exc)

            return archive_path

        finally:
            if staging.exists():
                shutil.rmtree(staging)

    def retain_recent(self, keep_recent: int = DEFAULT_KEEP_RECENT) -> int:
        """Delete backup archives older than the newest ``keep_recent`` files.

        Backups are ordered by mtime descending; anything past index
        ``keep_recent`` is unlinked. Idempotent — repeated calls with the same
        argument are no-ops once the directory is at or below the target size.

        Returns the number of archives removed.
        """
        keep_recent = max(0, int(keep_recent))
        if not self._backup_dir.is_dir():
            return 0
        archives = [p for p in self._backup_dir.glob("backup_*.tar.gz") if p.is_file()]
        archives.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        stale = archives[keep_recent:]
        removed = 0
        for path in stale:
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.debug("backup retain_recent skipped %s: %s", path.name, exc)
        return removed

    def list_backups(self) -> list[dict]:
        """List available backups."""
        results = []
        for f in sorted(self._backup_dir.glob("backup_*.tar.gz")):
            results.append({
                "path": str(f),
                "name": f.stem,
                "size_bytes": f.stat().st_size,
                "created": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
        return results


def retain_recent(keep_recent: int = DEFAULT_KEEP_RECENT, data_dir: Path | None = None) -> int:
    """Standalone entry point for scheduler / one-shot callers.

    Instantiates a :class:`BackupService` against ``data_dir`` (defaults to
    :func:`get_data_dir`) and rotates its backup directory. Returns the number
    of archives removed.
    """
    return BackupService(data_dir=data_dir).retain_recent(keep_recent)

"""Backup service — creates and manages workspace backups."""

import logging
import re
import shutil
import tarfile
import uuid
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

# Label must start with a letter/digit, then contain only letters, digits,
# dots, underscores, and hyphens — no path separators, spaces, or control
# chars. Max 63 chars so the full backup_id stays well under filesystem limits.
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


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
        """Copy a SQLite database using Connection.backup() for a WAL-safe snapshot.

        sqlite3.Connection.backup() holds a read lock, walks pages including the
        WAL, and produces a consistent .db without a -wal/-shm sidecar. A
        DatabaseError means the source is corrupt or not a SQLite file — the
        error propagates so backup creation fails rather than archiving corrupt
        bytes (#287)."""
        import sqlite3

        src_conn = sqlite3.connect(str(src))
        try:
            dst_conn = sqlite3.connect(str(dst))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()

    def create_backup(self, label: str | None = None) -> Path:
        """Create a full backup of the workspace. Returns archive path.

        #281: label is validated against _SAFE_LABEL_RE before use.
        #283: backup_id includes a UUID suffix for per-invocation uniqueness.
        #287: SQLite snapshot failure propagates — no raw-copy fallback.
        #291: archive is written to a partial file and renamed atomically.
        """
        # #281: validate label before constructing any paths
        if label is not None:
            if not _SAFE_LABEL_RE.match(label):
                raise ValueError(
                    f"Backup label {label!r} is invalid. Labels must start with a letter "
                    "or digit and contain only letters, digits, dots, underscores, and "
                    "hyphens (max 63 characters)."
                )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        uuid_suffix = uuid.uuid4().hex[:8]  # #283: per-invocation uniqueness
        if label:
            backup_id = f"backup_{label}_{timestamp}_{uuid_suffix}"
        else:
            backup_id = f"backup_{timestamp}_{uuid_suffix}"

        # #281: containment invariant — staging and archive must stay inside _backup_dir
        staging = self._backup_dir / f"_{backup_id}_staging"
        archive_path = self._backup_dir / f"{backup_id}.tar.gz"
        partial_path = self._backup_dir / f"_{backup_id}.tar.gz.part"  # #291
        _bd = self._backup_dir.resolve()
        for p, name in (
            (staging, "staging"),
            (archive_path, "archive"),
            (partial_path, "partial"),
        ):
            if not p.resolve().is_relative_to(_bd):
                raise ValueError(
                    f"Backup {name} path escaped backup directory: {p}"
                )

        staging.mkdir(parents=True, exist_ok=True)

        try:
            file_count = 0
            total_bytes = 0

            # Copy app.db via WAL-safe SQLite snapshot (#59, #287).
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

            # #58 fix: copy runtime-home logs/ (contains audit.jsonl).
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

            # #291: write to partial then rename atomically so list_backups / retention
            # never see a mid-construction archive.
            try:
                with tarfile.open(partial_path, "w:gz") as tar:
                    tar.add(staging, arcname=backup_id)
                partial_path.rename(archive_path)
            except Exception:
                if partial_path.exists():
                    partial_path.unlink(missing_ok=True)
                raise

            # Rotate older archives
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

"""Backup service — creates and manages workspace backups."""

import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from aletheia.config.loader import get_data_dir
from aletheia.storage.hashing import sha256_file


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

            # Copy app.db
            db_path = self._data_dir / "app.db"
            if db_path.exists():
                dst = staging / "app.db"
                shutil.copy2(db_path, dst)
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

            # #58 fix: copy data/logs/ (contains audit.jsonl, the hash-chained
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

            return archive_path

        finally:
            if staging.exists():
                shutil.rmtree(staging)

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

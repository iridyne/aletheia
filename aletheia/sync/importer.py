"""Import service — merges exported bundles into the local workspace."""

import json
import shutil
import tarfile
from pathlib import Path

from aletheia.config.loader import get_data_dir
from aletheia.storage.hashing import sha256_file
from aletheia.storage.sqlite import get_db


class ImportConflict:
    """Represents a conflict during import."""

    def __init__(self, object_type: str, object_id: str, local_hash: str, remote_hash: str, message: str):
        self.object_type = object_type
        self.object_id = object_id
        self.local_hash = local_hash
        self.remote_hash = remote_hash
        self.message = message

    def to_dict(self) -> dict:
        return {
            "object_type": self.object_type,
            "object_id": self.object_id,
            "local_hash": self.local_hash,
            "remote_hash": self.remote_hash,
            "message": self.message,
        }


class ImportResult:
    """Result of a dry-run or merge import."""

    def __init__(self):
        self.new_rows: dict[str, int] = {}
        self.skipped_duplicates: int = 0
        self.conflicts: list[ImportConflict] = []
        self.errors: list[str] = []

    def to_dict(self) -> dict:
        return {
            "new_rows": self.new_rows,
            "skipped_duplicates": self.skipped_duplicates,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "errors": self.errors,
        }


class ImportService:
    """Imports export bundles into the local workspace."""

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or get_data_dir()

    def dry_run(self, archive_path: Path) -> ImportResult:
        """Preview what an import would do without making changes."""
        return self._process(archive_path, commit=False)

    def merge(self, archive_path: Path) -> ImportResult:
        """Import and merge the bundle into the local workspace."""
        return self._process(archive_path, commit=True)

    def _process(self, archive_path: Path, commit: bool) -> ImportResult:
        result = ImportResult()

        # Extract to temp dir
        extract_dir = self._data_dir / "tmp" / "import_staging"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)

        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(extract_dir, filter="data")

            # Find the bundle root (first subdir)
            subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
            if not subdirs:
                result.errors.append("Empty archive")
                return result
            bundle_dir = subdirs[0]

            # Verify checksums
            checksums_file = bundle_dir / "checksums.txt"
            if checksums_file.exists():
                self._verify_checksums(bundle_dir, checksums_file, result)

            # Import DB tables
            db_dir = bundle_dir / "db"
            if db_dir.exists():
                self._import_db_tables(db_dir, result, commit)

            # Import artifacts
            artifacts_dir = bundle_dir / "artifacts"
            if artifacts_dir.exists() and commit:
                self._import_artifacts(artifacts_dir)

        finally:
            if extract_dir.exists():
                shutil.rmtree(extract_dir)

        return result

    def _verify_checksums(self, bundle_dir: Path, checksums_file: Path, result: ImportResult) -> None:
        """Verify all files against checksums.txt."""
        for line in checksums_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                continue
            expected_hash, rel_path = parts
            file_path = bundle_dir / rel_path
            if file_path.exists():
                actual = sha256_file(file_path)
                if actual != expected_hash:
                    result.errors.append(f"Checksum mismatch: {rel_path}")

    def _import_db_tables(self, db_dir: Path, result: ImportResult, commit: bool) -> None:
        """Import JSONL table data with deduplication."""
        db = get_db()

        for jsonl_file in sorted(db_dir.glob("*.jsonl")):
            table_name = jsonl_file.stem
            new_count = 0

            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)

                    # Check for duplicate by primary key (assume 'id' column)
                    pk_col = self._find_pk(table_name, row)
                    if pk_col and pk_col in row:
                        existing = db.execute(
                            f"SELECT 1 FROM {table_name} WHERE {pk_col} = ?",
                            (row[pk_col],),
                        ).fetchone()
                        if existing:
                            result.skipped_duplicates += 1
                            continue

                    if commit:
                        cols = ", ".join(row.keys())
                        placeholders = ", ".join(["?"] * len(row))
                        try:
                            db.execute(
                                f"INSERT OR IGNORE INTO {table_name} ({cols}) VALUES ({placeholders})",
                                list(row.values()),
                            )
                        except Exception as e:
                            result.errors.append(f"Insert into {table_name} failed: {e}")
                            continue

                    new_count += 1

            if new_count > 0:
                result.new_rows[table_name] = new_count

        if commit:
            db.commit()

    def _import_artifacts(self, artifacts_dir: Path) -> None:
        """Copy artifacts into local store."""
        local_artifacts = self._data_dir / "artifacts"
        local_artifacts.mkdir(parents=True, exist_ok=True)
        for category in artifacts_dir.iterdir():
            if category.is_dir():
                dst = local_artifacts / category.name
                shutil.copytree(category, dst, dirs_exist_ok=True)

    def _find_pk(self, table_name: str, row: dict) -> str | None:
        """Guess the primary key column name for a table."""
        # Convention: ps_* tables use specific pk names
        pk_map = {
            "ps_event_log": "event_id",
            "ps_gateway_sessions": "gateway_session_id",
            "ps_reconciliation_reports": "reconciliation_report_id",
            "ps_execution_intents": "execution_intent_id",
            "ps_order_intents": "intent_id",
            "ps_orders": "order_id",
            "ps_fills": "fill_id",
            "ps_positions": "position_id",
            "ps_checkpoints": "checkpoint_id",
            "ps_lessons": "lesson_id",
            "ps_skills": "skill_id",
            "ps_policy_versions": "policy_version_id",
        }
        if table_name in pk_map:
            return pk_map[table_name]
        if "id" in row:
            return "id"
        return None

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

            # checksums.txt is mandatory — an import without it cannot be trusted.
            checksums_file = bundle_dir / "checksums.txt"
            if not checksums_file.exists():
                result.errors.append("checksums.txt missing — bundle integrity cannot be verified")
                return result

            self._verify_checksums(bundle_dir, checksums_file, result)

            # Fail-closed: any verification error aborts before touching state.
            if result.errors:
                return result

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
        """Strict manifest verification (#271).

        - Rejects malformed lines, non-hex hashes, and unsafe paths.
        - Rejects duplicate paths within the manifest.
        - Errors if any listed file is missing or its SHA-256 does not match.
        - Errors if any file under db/ or artifacts/ is not in the manifest
          (manifest.yaml is exempt — the exporter writes it after checksums).
        """
        import re

        _HEX_RE = re.compile(r"^[0-9a-f]{64}$")
        declared: dict[str, str] = {}  # rel_path → expected_hash

        for lineno, line in enumerate(checksums_file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                result.errors.append(f"checksums.txt line {lineno}: malformed (expected '<hash>  <path>')")
                continue
            expected_hash, rel_path = parts
            if not _HEX_RE.match(expected_hash):
                result.errors.append(f"checksums.txt line {lineno}: non-hex SHA-256 hash for {rel_path!r}")
                continue
            # Reject path traversal and absolute paths
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                result.errors.append(f"checksums.txt line {lineno}: unsafe path {rel_path!r}")
                continue
            if rel_path in declared:
                result.errors.append(f"checksums.txt line {lineno}: duplicate path {rel_path!r}")
                continue
            declared[rel_path] = expected_hash

        # Validate each declared file
        for rel_path, expected_hash in declared.items():
            file_path = bundle_dir / rel_path
            if not file_path.exists():
                result.errors.append(f"Checksum manifest lists missing file: {rel_path}")
                continue
            actual = sha256_file(file_path)
            if actual != expected_hash:
                result.errors.append(f"Checksum mismatch: {rel_path}")

        # Reject payload files not declared in the manifest (unlisted files).
        # manifest.yaml is exempt — the exporter writes it after checksums.txt.
        _EXEMPT = {"manifest.yaml"}
        for subdir_name in ("db", "artifacts"):
            subdir = bundle_dir / subdir_name
            if not subdir.exists():
                continue
            for file_path in subdir.rglob("*"):
                if not file_path.is_file():
                    continue
                try:
                    rel = file_path.relative_to(bundle_dir).as_posix()
                except ValueError:
                    rel = str(file_path)
                if rel not in declared and rel not in _EXEMPT:
                    result.errors.append(f"Unlisted payload file not in checksum manifest: {rel}")

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

                    # Check for duplicate by primary key (assume 'id' column).
                    # #64 fix: an existing PK with DIFFERENT column values used to
                    # be counted as `skipped_duplicates` — a diverged row silently
                    # ignored so the operator never learned the bundle was already
                    # out of sync. The `ImportConflict` class existed but was
                    # never instantiated. Now we distinguish:
                    #   PK exists, payload identical  → `skipped_duplicates`
                    #   PK exists, payload different  → `conflicts` entry
                    #   PK missing                    → inserted (as before)
                    pk_col = self._find_pk(table_name, row)
                    if pk_col and pk_col in row:
                        existing = db.execute(
                            f"SELECT * FROM {table_name} WHERE {pk_col} = ?",
                            (row[pk_col],),
                        ).fetchone()
                        if existing:
                            local = dict(existing)
                            diff_cols = [
                                col for col, val in row.items()
                                if col in local and local[col] != val
                            ]
                            if diff_cols:
                                result.conflicts.append(ImportConflict(
                                    object_type=table_name,
                                    object_id=str(row[pk_col]),
                                    local_hash=self._row_fingerprint(local),
                                    remote_hash=self._row_fingerprint(row),
                                    message=(
                                        f"{table_name} row {row[pk_col]!r} diverges "
                                        f"from local on columns: {', '.join(sorted(diff_cols))}"
                                    ),
                                ))
                            else:
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

    @staticmethod
    def _row_fingerprint(row: dict) -> str:
        """Stable SHA-256 fingerprint of a row's JSON canonical form for the
        conflict local_hash / remote_hash fields. Keys sorted so identical rows
        with different insertion order produce identical hashes."""
        import hashlib
        canonical = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

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

"""Import service — merges exported bundles into the local workspace."""

import json
import re
import shutil
import sqlite3
import tarfile
import tempfile
from pathlib import Path

from aletheia.config.loader import get_data_dir
from aletheia.storage.hashing import sha256_file
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_IMPORTABLE_TABLES = frozenset(
    {
        "ps_execution_intents",
        "ps_fills",
        "ps_gateway_sessions",
        "ps_lessons",
        "ps_order_intents",
        "ps_orders",
        "ps_positions",
        "ps_skills",
        "serenity_claims",
        "serenity_evidence",
        "serenity_opportunities",
        "serenity_themes",
        "trade_proposals",
    }
)


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
        staging_root = self._data_dir / "tmp"
        staging_root.mkdir(parents=True, exist_ok=True)
        extract_dir = Path(tempfile.mkdtemp(prefix="import-", dir=staging_root))

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
            if not checksums_file.is_file():
                result.errors.append("Missing checksums.txt; import aborted")
                return result

            self._verify_checksums(bundle_dir, checksums_file, result)
            if result.errors:
                return result

            # Import DB tables
            db_dir = bundle_dir / "db"
            if db_dir.exists():
                db_payloads = self._validate_db_payloads(db_dir, result)
                if result.errors:
                    return result
                self._import_db_tables(db_payloads, result, commit)

            # Import artifacts
            artifacts_dir = bundle_dir / "artifacts"
            if artifacts_dir.exists() and commit:
                self._import_artifacts(artifacts_dir)

        finally:
            if extract_dir.exists():
                shutil.rmtree(extract_dir)

        return result

    def _verify_checksums(self, bundle_dir: Path, checksums_file: Path, result: ImportResult) -> None:
        """Verify all files against checksums.txt.

        #63 fix: a file listed in the manifest but MISSING on disk used to be
        silently skipped (the `if file_path.exists()` branch just fell through
        with no `result.errors` append). Now the missing case is an explicit
        error — a bundle can't be safely imported when the manifest lists files
        that were never delivered.
        """
        declared_paths: set[str] = set()
        bundle_root = bundle_dir.resolve()

        for line_number, line in enumerate(
            checksums_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                result.errors.append(
                    f"Malformed checksum entry on line {line_number}: "
                    "expected '<sha256>  <path>'"
                )
                continue

            expected_hash, raw_path = parts
            normalized_path = raw_path.replace("\\", "/")
            path_parts = normalized_path.split("/")
            if not _SHA256_RE.fullmatch(expected_hash):
                result.errors.append(f"Malformed SHA-256 on checksum line {line_number}")
                continue
            if (
                normalized_path.startswith("/")
                or not normalized_path
                or any(part in {"", ".", ".."} for part in path_parts)
                or ":" in path_parts[0]
            ):
                result.errors.append(
                    f"Unsafe checksum path on line {line_number}: {raw_path}"
                )
                continue

            canonical_path = "/".join(path_parts)
            if canonical_path in declared_paths:
                result.errors.append(f"Duplicate checksum path: {canonical_path}")
                continue
            declared_paths.add(canonical_path)

            file_path = bundle_dir.joinpath(*path_parts)
            try:
                file_path.resolve().relative_to(bundle_root)
            except ValueError:
                result.errors.append(
                    f"Unsafe checksum path on line {line_number}: {raw_path}"
                )
                continue

            if not file_path.is_file() or file_path.is_symlink():
                result.errors.append(
                    f"Checksum manifest lists missing file: {canonical_path}"
                )
                continue
            actual = sha256_file(file_path)
            if actual.lower() != expected_hash.lower():
                result.errors.append(f"Checksum mismatch: {canonical_path}")

        actual_paths = {
            path.relative_to(bundle_dir).as_posix()
            for path in bundle_dir.rglob("*")
            if path.is_file()
            and path.relative_to(bundle_dir).as_posix()
            not in {"checksums.txt", "manifest.yaml"}
        }
        for unlisted_path in sorted(actual_paths - declared_paths):
            result.errors.append(
                f"Payload file is not listed in checksums.txt: {unlisted_path}"
            )

    def _validate_db_payloads(
        self, db_dir: Path, result: ImportResult
    ) -> dict[str, list[dict]]:
        """Load and authorize every JSONL row before the first database write."""
        db = get_db()
        payloads: dict[str, list[dict]] = {}

        for jsonl_file in sorted(db_dir.glob("*.jsonl")):
            table_name = jsonl_file.stem
            if table_name not in _IMPORTABLE_TABLES:
                result.errors.append(f"Unsupported import table: {table_name}")
                continue

            quoted_table = self._quote_identifier(table_name)
            table_columns = {
                str(row["name"])
                for row in db.execute(f"PRAGMA table_info({quoted_table})").fetchall()
            }
            if not table_columns:
                result.errors.append(f"Import table is unavailable locally: {table_name}")
                continue

            rows: list[dict] = []
            with open(jsonl_file, "r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        result.errors.append(
                            f"Invalid JSON in {table_name} line {line_number}: {exc.msg}"
                        )
                        continue
                    if not isinstance(row, dict) or not row:
                        result.errors.append(
                            f"Import row must be a non-empty JSON object: "
                            f"{table_name} line {line_number}"
                        )
                        continue

                    unknown_columns = sorted(set(row) - table_columns)
                    if unknown_columns:
                        result.errors.append(
                            f"Unsupported columns in {table_name} line {line_number}: "
                            f"{', '.join(unknown_columns)}"
                        )
                        continue
                    rows.append(row)

            payloads[table_name] = rows

        return payloads

    def _import_db_tables(
        self,
        rows_by_table: dict[str, list[dict]],
        result: ImportResult,
        commit: bool,
    ) -> None:
        """Import preloaded rows atomically, or inspect them for a dry-run."""
        try:
            if commit:
                with transaction():
                    self._merge_db_rows(rows_by_table, result, commit=True)
            else:
                self._merge_db_rows(rows_by_table, result, commit=False)
        except sqlite3.Error as exc:
            result.new_rows.clear()
            result.errors.append(f"Database import failed; no rows committed: {exc}")

    def _merge_db_rows(
        self,
        rows_by_table: dict[str, list[dict]],
        result: ImportResult,
        *,
        commit: bool,
    ) -> None:
        db = get_db()

        for table_name in sorted(rows_by_table):
            quoted_table = self._quote_identifier(table_name)
            new_count = 0

            for row in rows_by_table[table_name]:
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
                    quoted_pk = self._quote_identifier(pk_col)
                    existing = db.execute(
                        f"SELECT * FROM {quoted_table} WHERE {quoted_pk} = ?",
                        (row[pk_col],),
                    ).fetchone()
                    if existing:
                        local = dict(existing)
                        diff_cols = [
                            col
                            for col, val in row.items()
                            if col in local and local[col] != val
                        ]
                        if diff_cols:
                            result.conflicts.append(
                                ImportConflict(
                                    object_type=table_name,
                                    object_id=str(row[pk_col]),
                                    local_hash=self._row_fingerprint(local),
                                    remote_hash=self._row_fingerprint(row),
                                    message=(
                                        f"{table_name} row {row[pk_col]!r} diverges "
                                        "from local on columns: "
                                        f"{', '.join(sorted(diff_cols))}"
                                    ),
                                )
                            )
                        else:
                            result.skipped_duplicates += 1
                        continue

                if commit:
                    cols = ", ".join(self._quote_identifier(col) for col in row)
                    placeholders = ", ".join(["?"] * len(row))
                    db.execute(
                        f"INSERT INTO {quoted_table} ({cols}) VALUES ({placeholders})",
                        list(row.values()),
                    )

                new_count += 1

            if new_count > 0:
                result.new_rows[table_name] = new_count

    def _import_artifacts(self, artifacts_dir: Path) -> None:
        """Copy artifacts into local store."""
        local_artifacts = self._data_dir / "artifacts"
        local_artifacts.mkdir(parents=True, exist_ok=True)
        for category in artifacts_dir.iterdir():
            if category.is_dir():
                dst = local_artifacts / category.name
                shutil.copytree(category, dst, dirs_exist_ok=True)

    @staticmethod
    def _quote_identifier(identifier: str) -> str:
        """Quote a validated SQLite identifier defensively."""
        return '"' + identifier.replace('"', '""') + '"'

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

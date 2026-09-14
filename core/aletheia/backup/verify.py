"""Backup verification — checks integrity of backup archives."""

import hashlib
import json
import re
import sqlite3
import tarfile
import tempfile
from pathlib import Path

import yaml

from aletheia.storage.hashing import sha256_file

_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
# Files exempt from the "unlisted payload" check — the exporter writes manifest.yaml
# after checksums.txt, so it cannot include itself in the manifest.
_EXEMPT_NAMES = frozenset({"checksums.txt", "manifest.yaml"})


def _canonical_json(value: object) -> str:
    """Use the same stable JSON shape as the joint-checkpoint writer."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _open_sqlite_readonly(path: Path) -> sqlite3.Connection:
    """Inspect a bundled SQLite snapshot without creating WAL sidecars."""
    # The backup contains a single sqlite3.Connection.backup() image, not its
    # source WAL sidecar.  Immutable mode therefore reads the complete image
    # without creating ``-wal``/``-shm`` files that would look like unlisted
    # payload during the later checksum scan.
    return sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True,
    )


class VerifyResult:
    """Result of backup verification."""

    def __init__(self):
        self.valid: bool = True
        self.file_count: int = 0
        self.checked_count: int = 0
        self.mismatches: list[str] = []
        self.errors: list[str] = []
        self.profile: str | None = None
        self.recovery_eligible: bool = False
        self.checkpoint_identity: str | None = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "file_count": self.file_count,
            "checked_count": self.checked_count,
            "mismatches": self.mismatches,
            "errors": self.errors,
            "profile": self.profile,
            "recovery_eligible": self.recovery_eligible,
            "checkpoint_identity": self.checkpoint_identity,
        }


def verify_backup(archive_path: Path) -> VerifyResult:
    """Verify a backup archive's integrity by checking all checksums.

    Strict manifest verification (#279):
    - Rejects malformed checksum lines, non-hex hashes, and unsafe paths.
    - Rejects duplicate path declarations within the manifest.
    - Errors if any listed file is missing or its SHA-256 does not match.
    - Errors if any payload file under the bundle is absent from the manifest
      (checksums.txt and manifest.yaml are exempt).
    """
    result = VerifyResult()

    if not archive_path.exists():
        result.valid = False
        result.errors.append(f"Archive not found: {archive_path}")
        return result

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                tar.extractall(tmp_path, filter="data")
        except Exception as e:
            result.valid = False
            result.errors.append(f"Failed to extract: {e}")
            return result

        # Find bundle root
        subdirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        if not subdirs:
            result.valid = False
            result.errors.append("Empty archive")
            return result
        bundle_dir = subdirs[0]

        manifest_file = bundle_dir / "manifest.yaml"
        manifest: dict = {}
        if manifest_file.exists():
            try:
                loaded = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    manifest = loaded
                else:
                    result.valid = False
                    result.errors.append("manifest.yaml must contain a mapping")
            except Exception as exc:
                result.valid = False
                result.errors.append(f"manifest.yaml could not be parsed: {exc}")

        # Archives created before the profile contract remain verifiable, but
        # they are never eligible as authoritative recovery checkpoints.
        profile = manifest.get("profile", "legacy")
        result.profile = profile if isinstance(profile, str) else None
        result.recovery_eligible = profile == "authoritative"
        checkpoint_identity = manifest.get("checkpoint_identity")
        result.checkpoint_identity = (
            checkpoint_identity if isinstance(checkpoint_identity, str) else None
        )
        if profile not in {"legacy", "online", "authoritative"}:
            result.valid = False
            result.errors.append(f"Unknown backup profile: {profile!r}")

        if profile == "authoritative":
            _verify_checkpoint_manifest(bundle_dir, manifest, result)

        # Read checksums
        checksums_file = bundle_dir / "checksums.txt"
        if not checksums_file.exists():
            result.valid = False
            result.errors.append("Missing checksums.txt")
            return result

        # Count payload files (excludes exempt names)
        for f in bundle_dir.rglob("*"):
            if f.is_file() and f.name not in _EXEMPT_NAMES:
                result.file_count += 1

        # ── Strict manifest parsing (#279) ────────────────────────────────────
        declared: dict[str, str] = {}  # rel_path → expected_hash
        for lineno, raw_line in enumerate(
            checksums_file.read_text(encoding="utf-8").splitlines(), 1
        ):
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split("  ", 1)
            if len(parts) != 2:
                result.errors.append(
                    f"checksums.txt line {lineno}: malformed (expected '<hash>  <path>')"
                )
                result.valid = False
                continue
            expected_hash, rel_path = parts
            if not _HEX_RE.match(expected_hash):
                result.errors.append(
                    f"checksums.txt line {lineno}: non-hex SHA-256 hash for {rel_path!r}"
                )
                result.valid = False
                continue
            if rel_path.startswith("/") or ".." in rel_path.split("/"):
                result.errors.append(
                    f"checksums.txt line {lineno}: unsafe path {rel_path!r}"
                )
                result.valid = False
                continue
            if rel_path in declared:
                result.errors.append(
                    f"checksums.txt line {lineno}: duplicate path {rel_path!r}"
                )
                result.valid = False
                continue
            declared[rel_path] = expected_hash

        # ── Verify each declared file ─────────────────────────────────────────
        for rel_path, expected_hash in declared.items():
            file_path = bundle_dir / rel_path
            if not file_path.exists():
                result.mismatches.append(f"Missing: {rel_path}")
                result.valid = False
                continue
            actual = sha256_file(file_path)
            result.checked_count += 1
            if actual != expected_hash:
                result.mismatches.append(f"Mismatch: {rel_path}")
                result.valid = False

        # ── Reject payload files not declared in the manifest (#279) ─────────
        for f in bundle_dir.rglob("*"):
            if not f.is_file():
                continue
            if f.name in _EXEMPT_NAMES:
                continue
            try:
                rel = f.relative_to(bundle_dir).as_posix()
            except ValueError:
                rel = str(f)
            if rel not in declared:
                result.errors.append(
                    f"Unlisted payload file not in checksum manifest: {rel}"
                )
                result.valid = False

    return result


def _verify_checkpoint_manifest(
    bundle_dir: Path, manifest: dict, result: VerifyResult,
) -> None:
    """Validate the cross-store identity recorded for an authoritative backup."""
    sqlite_identity = manifest.get("sqlite_identity")
    lab_sqlite_identity = manifest.get("lab_sqlite_identity")
    audit_head_hash = manifest.get("audit_head_hash")
    inventory = manifest.get("audit_inventory")
    checkpoint_identity = manifest.get("checkpoint_identity")
    lab_checkpoint_identity = manifest.get("lab_checkpoint_identity")
    authority_source_raw = manifest.get("experiment_authority_source")
    # Backward-compatible authoritative backups predate the durable cutover
    # row; they are legacy-authority checkpoints, never Lab-authority ones.
    authority_source = authority_source_raw or "legacy"

    if manifest.get("recovery_eligible") is not True:
        result.valid = False
        result.errors.append("authoritative checkpoint must advertise recovery_eligible=true")

    if authority_source_raw is not None and authority_source not in {"legacy", "lab"}:
        result.valid = False
        result.errors.append("authoritative checkpoint is missing a valid experiment authority source")

    if not isinstance(sqlite_identity, str) or not re.fullmatch(r"[0-9a-f]{64}", sqlite_identity):
        result.valid = False
        result.errors.append("authoritative checkpoint is missing a valid sqlite_identity")
    app_db = bundle_dir / "app.db"
    if isinstance(sqlite_identity, str) and app_db.exists():
        if hashlib.sha256(app_db.read_bytes()).hexdigest() != sqlite_identity:
            result.valid = False
            result.errors.append("authoritative checkpoint sqlite_identity does not match app.db")
    elif isinstance(sqlite_identity, str):
        result.valid = False
        result.errors.append("authoritative checkpoint declares sqlite_identity without app.db")

    if lab_sqlite_identity is not None:
        lab_db = bundle_dir / "observation-lab" / "lab.db"
        if not isinstance(lab_sqlite_identity, str) or not re.fullmatch(
            r"[0-9a-f]{64}", lab_sqlite_identity
        ):
            result.valid = False
            result.errors.append("authoritative checkpoint lab_sqlite_identity is invalid")
        elif not lab_db.exists() or hashlib.sha256(lab_db.read_bytes()).hexdigest() != lab_sqlite_identity:
            result.valid = False
            result.errors.append("authoritative checkpoint lab_sqlite_identity does not match lab.db")
    elif authority_source == "lab":
        result.valid = False
        result.errors.append("Lab-authoritative checkpoint is missing lab_sqlite_identity")

    if authority_source == "lab":
        lab_db = bundle_dir / "observation-lab" / "lab.db"
        if not isinstance(lab_checkpoint_identity, str) or not _HEX_RE.match(lab_checkpoint_identity):
            result.valid = False
            result.errors.append("Lab-authoritative checkpoint is missing lab_checkpoint_identity")
        else:
            try:
                with _open_sqlite_readonly(lab_db) as connection:
                    row = connection.execute(
                        "SELECT joint_fingerprint FROM joint_checkpoints "
                        "ORDER BY recorded_at DESC, checkpoint_id DESC LIMIT 1"
                    ).fetchone()
                if row is None or row[0] != lab_checkpoint_identity:
                    result.valid = False
                    result.errors.append("Lab-authoritative checkpoint identity does not match lab checkpoint")
            except (OSError, sqlite3.Error):
                result.valid = False
                result.errors.append("Lab-authoritative checkpoint lab checkpoint is unreadable")

        # A Lab checkpoint is only recoverable when every compact archive it
        # references is present in the bundle and still carries its replay
        # identity.  The database may contain absolute paths from the source
        # host; map only the managed ``observation-lab/...`` suffix into the
        # extracted bundle and reject anything outside that root.
        try:
            with _open_sqlite_readonly(lab_db) as connection:
                archive_rows = connection.execute(
                    "SELECT archive_id, archive_path, archive_sha256, replay_fingerprint "
                    "FROM observation_archives ORDER BY archive_id"
                ).fetchall()
        except (OSError, sqlite3.Error):
            archive_rows = None
            result.valid = False
            result.errors.append("Lab-authoritative checkpoint archive index is unreadable")
        if archive_rows is not None:
            import gzip

            for archive_id, archive_path, archive_sha256, replay_fingerprint in archive_rows:
                raw_path = Path(str(archive_path))
                try:
                    parts = raw_path.parts
                    marker = parts.index("observation-lab")
                    relative = Path(*parts[marker:])
                    if relative.parts[:2] != ("observation-lab", "archives"):
                        raise ValueError("archive_path_outside_managed_root")
                    bundle_archive = bundle_dir / relative
                    bundle_archives_root = (bundle_dir / "observation-lab" / "archives").resolve()
                    bundle_archive.resolve().relative_to(bundle_archives_root)
                except (ValueError, IndexError):
                    result.valid = False
                    result.errors.append(
                        f"Lab-authoritative archive path is outside managed root: {archive_id}"
                    )
                    continue
                if not bundle_archive.is_file():
                    result.valid = False
                    result.errors.append(
                        f"Lab-authoritative archive is missing from bundle: {archive_id}"
                    )
                    continue
                data = bundle_archive.read_bytes()
                if not isinstance(archive_sha256, str) or hashlib.sha256(data).hexdigest() != archive_sha256:
                    result.valid = False
                    result.errors.append(
                        f"Lab-authoritative archive bytes do not match index: {archive_id}"
                    )
                    continue
                try:
                    payload = json.loads(gzip.decompress(data))
                    replay = payload.get("replay_fingerprint") if isinstance(payload, dict) else None
                    candidate = dict(payload) if isinstance(payload, dict) else {}
                    candidate.pop("replay_fingerprint", None)
                    expected_replay = hashlib.sha256(
                        json.dumps(
                            candidate,
                            sort_keys=True,
                            separators=(",", ":"),
                            allow_nan=False,
                            default=str,
                        ).encode("utf-8")
                    ).hexdigest()
                except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                    replay = None
                    expected_replay = None
                if (
                    not isinstance(replay, str)
                    or replay != replay_fingerprint
                    or replay != expected_replay
                ):
                    result.valid = False
                    result.errors.append(
                        f"Lab-authoritative archive replay identity mismatch: {archive_id}"
                    )

        _verify_outbox_cursor_cut(bundle_dir, result)

        # Non-empty legacy Experiment state is allowed in an authoritative
        # bundle only when the durable Main cutover proof is present.  Empty
        # fresh schemas need no proof row.
        legacy_tables = (
            "learning_experiments", "learning_exposures",
            "learning_contribution_verdicts", "learning_inference_decisions",
            "learning_lifecycle_events", "filter_shadow_experiments",
            "filter_shadow_evaluations", "strategy_experiments",
        )
        try:
            with _open_sqlite_readonly(app_db) as connection:
                legacy_count = 0
                for table in legacy_tables:
                    exists = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,),
                    ).fetchone()
                    if exists is not None:
                        legacy_count += int(
                            connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0
                        )
                if legacy_count:
                    proof = connection.execute(
                        "SELECT migration_plan_fingerprint, retention_claim_fingerprint, "
                        "checkpoint_fingerprint, runtime_commit, rollback_backup_identity "
                        "FROM observation_authority_cutover_proofs "
                        "ORDER BY recorded_at DESC, proof_id DESC LIMIT 1"
                    ).fetchone()
                    if proof is None or any(not str(value or "").strip() for value in proof):
                        result.valid = False
                        result.errors.append(
                            "Lab-authoritative checkpoint is missing cutover proof for legacy state"
                        )
                    elif str(proof[2]) != str(lab_checkpoint_identity):
                        result.valid = False
                        result.errors.append(
                            "Lab-authoritative checkpoint cutover proof does not match Lab checkpoint"
                        )
        except (OSError, sqlite3.Error):
            result.valid = False
            result.errors.append("Lab-authoritative checkpoint cutover proof is unreadable")

    if not isinstance(audit_head_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", audit_head_hash):
        result.valid = False
        result.errors.append("authoritative checkpoint is missing a valid audit_head_hash")
        audit_head_hash = "0" * 64
    if not isinstance(inventory, list):
        result.valid = False
        result.errors.append("authoritative checkpoint audit_inventory must be a list")
        inventory = []

    actual_inventory: list[dict[str, object]] = []
    seen_paths: set[str] = set()
    last_line = ""
    for item in inventory:
        if not isinstance(item, dict):
            result.valid = False
            result.errors.append("authoritative checkpoint audit_inventory contains a non-object")
            continue
        path_value = item.get("path")
        expected_hash = item.get("sha256")
        expected_size = item.get("size_bytes")
        if not isinstance(path_value, str) or not re.fullmatch(
            r"logs/audit\.jsonl(?:\.\d+)?", path_value
        ):
            result.valid = False
            result.errors.append(f"authoritative checkpoint audit path is invalid: {path_value!r}")
            continue
        if path_value in seen_paths:
            result.valid = False
            result.errors.append(f"authoritative checkpoint audit path is duplicated: {path_value}")
            continue
        seen_paths.add(path_value)
        path = bundle_dir / path_value
        try:
            path.resolve().relative_to(bundle_dir.resolve())
        except ValueError:
            result.valid = False
            result.errors.append(f"authoritative checkpoint audit path escapes bundle: {path_value}")
            continue
        if not path.is_file() or not isinstance(expected_hash, str) or not isinstance(expected_size, int):
            result.valid = False
            result.errors.append(f"authoritative checkpoint audit inventory mismatch: {path_value}")
            continue
        data = path.read_bytes()
        actual_inventory.append({
            "path": path_value,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        })
        if actual_inventory[-1]["sha256"] != expected_hash or len(data) != expected_size:
            result.valid = False
            result.errors.append(f"authoritative checkpoint audit bytes changed: {path_value}")
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                last_line = line.strip()

    actual_head = hashlib.sha256(last_line.encode("utf-8")).hexdigest() if last_line else "0" * 64
    if actual_head != audit_head_hash:
        result.valid = False
        result.errors.append("authoritative checkpoint audit_head_hash does not match audit inventory")

    payload = {
        "profile": "authoritative",
        "sqlite_identity": sqlite_identity,
        "lab_sqlite_identity": lab_sqlite_identity,
        "lab_checkpoint_identity": lab_checkpoint_identity,
        "experiment_authority_source": authority_source_raw,
        "audit_head_hash": audit_head_hash,
        "audit_inventory": inventory,
    }
    actual_identity = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not isinstance(checkpoint_identity, str) or checkpoint_identity != actual_identity:
        result.valid = False
        result.errors.append("authoritative checkpoint identity does not match manifest contents")


def _verify_outbox_cursor_cut(bundle_dir: Path, result: VerifyResult) -> None:
    """Prove the bundled Main receipt cut agrees with the Lab import cursor.

    The joint checkpoint already carries the Main outbox digest, but checking
    only the Lab database identity cannot detect a Lab-only rollback or a
    cursor that refers to receipts omitted from the restored app snapshot.
    Numeric sequence gaps remain allowed: they are only a problem when the
    durable cursor/retry checkpoint or an acknowledged receipt proves that a
    specific source row should be present.
    """
    app_db = bundle_dir / "app.db"
    lab_db = bundle_dir / "observation-lab" / "lab.db"
    if not app_db.is_file() or not lab_db.is_file():
        result.valid = False
        result.errors.append("Lab-authoritative outbox/cursor cut is unavailable")
        return

    try:
        with _open_sqlite_readonly(app_db) as app:
            app.row_factory = sqlite3.Row
            outbox_schema = app.execute(
                "PRAGMA table_info(observation_delivery_outbox)"
            ).fetchall()
            outbox_columns = {str(row[1]) for row in outbox_schema}
            required_outbox = {
                "sequence", "receipt_id", "manifest_hash", "acknowledged_at",
            }
            if not required_outbox.issubset(outbox_columns):
                raise sqlite3.Error("outbox_schema_unavailable")
            from aletheia.observation_lab.checkpoint import _OUTBOX_IDENTITY_COLUMNS

            if not set(_OUTBOX_IDENTITY_COLUMNS).issubset(outbox_columns):
                raise sqlite3.Error("outbox_identity_schema_unavailable")
            immutable_columns = list(_OUTBOX_IDENTITY_COLUMNS)
            select_columns = [*immutable_columns, "acknowledged_at"]
            if "lab_application_status" in outbox_columns:
                select_columns.append("lab_application_status")
            outbox_rows = app.execute(
                "SELECT " + ", ".join(select_columns)
                + " FROM observation_delivery_outbox ORDER BY sequence"
            ).fetchall()
            outbox_state = [
                tuple(row[column] for column in immutable_columns)
                for row in outbox_rows
                if row["acknowledged_at"] is None
            ]
            outbox_by_sequence = {
                int(row["sequence"]): row for row in outbox_rows
            }
            sequence_row = app.execute(
                "SELECT seq FROM sqlite_sequence "
                "WHERE name='observation_delivery_outbox'"
            ).fetchone()
            if sequence_row is None and outbox_rows:
                raise sqlite3.Error("outbox_sequence_watermark_unavailable")
            outbox_watermark = int(sequence_row[0]) if sequence_row is not None else 0
            outbox_pending = sum(
                1 for row in outbox_rows if row["acknowledged_at"] is None
            )
            outbox_fingerprint = _sha256_json(outbox_state)
            legacy_outbox_fingerprint = _sha256_json([
                (
                    row["sequence"], row["receipt_id"], row["manifest_hash"],
                    row["acknowledged_at"],
                )
                for row in outbox_rows
            ])
            legacy_outbox_values = (
                int(outbox_rows[-1]["sequence"]) if outbox_rows else 0,
                outbox_pending,
                legacy_outbox_fingerprint,
            )
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        result.valid = False
        result.errors.append(
            f"Lab-authoritative outbox cut is unreadable: {type(exc).__name__}"
        )
        return

    try:
        with _open_sqlite_readonly(lab_db) as lab:
            lab.row_factory = sqlite3.Row
            checkpoint_row = lab.execute(
                "SELECT main_outbox_high_watermark, main_outbox_pending_count, "
                "main_outbox_fingerprint FROM joint_checkpoints "
                "ORDER BY recorded_at DESC, checkpoint_id DESC LIMIT 1"
            ).fetchone()
            cursor = lab.execute(
                "SELECT contiguous_sequence, retry_sequences_json, "
                "source_checkpoint_json, source_checkpoint_fingerprint "
                "FROM observation_import_cursors WHERE source_name='main'"
            ).fetchone()
            exposure_rows = lab.execute(
                "SELECT source_receipt_id FROM observation_exposures "
                "WHERE source_receipt_id IS NOT NULL"
            ).fetchall()
            lifecycle_rows = lab.execute(
                "SELECT source_receipt_id FROM observation_lifecycle_events "
                "WHERE source_receipt_id IS NOT NULL"
            ).fetchall()
            audit_rows = lab.execute(
                "SELECT payload_json FROM lab_audit_outbox"
            ).fetchall()
            archive_rows = lab.execute(
                "SELECT archive_path, archive_sha256, replay_fingerprint "
                "FROM observation_archives"
            ).fetchall()
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        result.valid = False
        result.errors.append(
            f"Lab-authoritative import cursor is unreadable: {type(exc).__name__}"
        )
        return

    if checkpoint_row is None:
        result.valid = False
        result.errors.append("Lab-authoritative joint checkpoint is missing outbox cut")
    else:
        checkpoint_values = (
            checkpoint_row["main_outbox_high_watermark"],
            checkpoint_row["main_outbox_pending_count"],
            checkpoint_row["main_outbox_fingerprint"],
        )
        current_outbox_values = (
            outbox_watermark, outbox_pending, outbox_fingerprint,
        )
        if checkpoint_values not in {current_outbox_values, legacy_outbox_values}:
            result.valid = False
            result.errors.append(
                "Lab-authoritative outbox cut does not match joint checkpoint"
            )

    if cursor is None:
        # A fresh Lab with no imported Main receipts is a valid empty cut. A
        # non-empty outbox still needs the cursor so the restore cannot claim
        # that receipts were already imported.
        if outbox_rows:
            result.valid = False
            result.errors.append("Lab-authoritative import cursor is missing")
        return

    try:
        cursor_sequence = int(cursor["contiguous_sequence"])
        retry_sequences = json.loads(cursor["retry_sequences_json"] or "[]")
        checkpoint = json.loads(cursor["source_checkpoint_json"] or "")
        cursor_fingerprint = str(cursor["source_checkpoint_fingerprint"] or "")
        valid_cursor = (
            cursor_sequence >= 0
            and isinstance(retry_sequences, list)
            and all(isinstance(item, int) and item >= 0 for item in retry_sequences)
            and isinstance(checkpoint, dict)
            and checkpoint.get("source_name") == "main"
            and int(checkpoint.get("contiguous_sequence", -1)) == cursor_sequence
            and len(cursor_fingerprint) == 64
            and cursor_fingerprint == _sha256_json(checkpoint)
            and isinstance(checkpoint.get("receipt_chain_fingerprint"), str)
            and bool(re.fullmatch(r"[0-9a-f]{64}", checkpoint["receipt_chain_fingerprint"]))
            and checkpoint.get("retry_sequences") == retry_sequences
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        valid_cursor = False
        retry_sequences = []
        checkpoint = {}
        cursor_sequence = -1
    if not valid_cursor:
        result.valid = False
        result.errors.append("Lab-authoritative import cursor checkpoint is invalid")
        return

    if len(set(retry_sequences)) != len(retry_sequences):
        result.valid = False
        result.errors.append("Lab-authoritative import cursor retries are duplicated")
    for sequence in retry_sequences:
        if sequence <= cursor_sequence:
            result.valid = False
            result.errors.append(
                f"Lab-authoritative retry receipt is not ahead of cursor: {sequence}"
            )
        elif sequence not in outbox_by_sequence:
            result.valid = False
            result.errors.append(
                f"Lab-authoritative retry receipt is missing from app cut: {sequence}"
            )
        elif outbox_by_sequence[sequence]["acknowledged_at"] is not None:
            result.valid = False
            result.errors.append(
                f"Lab-authoritative retry receipt is already acknowledged: {sequence}"
            )

    covered_receipts = {
        str(row[0]) for row in exposure_rows if row[0] is not None
    }
    covered_receipts.update(
        str(row[0]) for row in lifecycle_rows if row[0] is not None
    )
    for row in audit_rows:
        try:
            payload = json.loads(row[0] or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            source_receipt_id = payload.get("source_receipt_id")
            if source_receipt_id:
                covered_receipts.add(str(source_receipt_id))

    # Archive coverage is needed after hot exposure/lifecycle rows have been
    # compacted.  Reuse the same managed-root/path rules as the archive check
    # above and ignore malformed archives here; that check already records the
    # precise archive error and the missing receipt must remain unproven.
    import gzip

    for archive_path, archive_sha256, replay_fingerprint in archive_rows:
        try:
            raw_path = Path(str(archive_path))
            parts = raw_path.parts
            marker = parts.index("observation-lab")
            relative = Path(*parts[marker:])
            if relative.parts[:2] != ("observation-lab", "archives"):
                continue
            archive = bundle_dir / relative
            archive_root = (bundle_dir / "observation-lab" / "archives").resolve()
            archive.resolve().relative_to(archive_root)
            data = archive.read_bytes()
            if hashlib.sha256(data).hexdigest() != str(archive_sha256):
                continue
            payload = json.loads(gzip.decompress(data))
            candidate = dict(payload)
            replay = candidate.pop("replay_fingerprint", None)
            if replay != str(replay_fingerprint) or replay != _sha256_json(candidate):
                continue
            archive_rows_payload = payload.get("rows") or {}
            for key in ("exposures", "lifecycle_events"):
                for item in archive_rows_payload.get(key) or ():
                    if isinstance(item, dict) and item.get("source_receipt_id"):
                        covered_receipts.add(str(item["source_receipt_id"]))
        except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            continue

    for row in outbox_rows:
        sequence = int(row["sequence"])
        if sequence <= cursor_sequence and str(row["receipt_id"]) not in covered_receipts:
            result.valid = False
            result.errors.append(
                f"Lab cursor claims receipt missing from Lab evidence: {row['receipt_id']}"
            )
        if sequence > cursor_sequence and (
            row["acknowledged_at"] is not None
            or (
                "lab_application_status" in row.keys()
                and row["lab_application_status"] in {"applied", "rejected", "conflicted"}
            )
        ):
            result.valid = False
            result.errors.append(
                f"Main outbox receipt is newer than Lab cursor: {row['receipt_id']}"
            )

    last_receipt_id = checkpoint.get("last_receipt_id")
    if cursor_sequence > 0 and (
        not isinstance(last_receipt_id, str)
        or not last_receipt_id
        or last_receipt_id not in covered_receipts
    ):
        result.valid = False
        result.errors.append("Lab cursor last receipt is missing from Lab evidence")

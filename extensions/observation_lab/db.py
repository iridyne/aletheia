"""Physical isolation for Observation Lab state (Issue #1072).

``~/.aletheia/observation-lab/lab.db`` is the Lab's own SQLite file. Two
consequences:

- Production migrations cannot silently rewrite a frozen unit — a schema
  change in ``app.db`` affects fresh Production reads, not the Lab's
  immutable snapshots.
- Reads FROM Production go through ``aletheia.storage.sqlite.get_db`` (or
  ``open_readonly_snapshot`` for physical RO paths); writes TO the Lab go
  through ``get_lab_db``. The two are never confused.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from aletheia.config.loader import get_data_dir
from extensions.observation_lab.canonical_schema import CANONICAL_SCHEMA_SQL

logger = logging.getLogger("aletheia.observation_lab.db")


_local = threading.local()
_readonly_local = threading.local()
_writer_lock_local = threading.local()
_WRITE_LOCK = threading.RLock()
LAB_SCHEMA_MIGRATION_ID = "20260902_0001_observation_ownership_v2"


@contextmanager
def lab_writer_lock():
    """Serialize Lab writers across threads and cooperating processes."""
    depth = int(getattr(_writer_lock_local, "depth", 0))
    if depth:
        _writer_lock_local.depth = depth + 1
        try:
            yield
        finally:
            _writer_lock_local.depth = depth
        return
    target = lab_db_path()
    lock_fd = os.open(str(target.with_name("lab.db.lock")), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with _WRITE_LOCK:
            try:
                import fcntl

                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            except ImportError:
                pass
            _writer_lock_local.depth = 1
            try:
                yield
            finally:
                _writer_lock_local.depth = 0
                try:
                    import fcntl

                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except ImportError:
                    pass
    finally:
        os.close(lock_fd)


def _schema_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


_EXECUTION_MANIFEST_COLUMNS = (
    "manifest_id, observation_id, generation, spec_hash, manifest_hash, "
    "treatment_adapter_id, treatment_adapter_version, assignment_policy, "
    "assignment_salt, assignment_unit_key_template, allocation_pct, "
    "control_payload_json, treatment_payload_json, eligible_population_json, "
    "behavior_contract_hash, interference_key, composability_contract, "
    "effective_from, effective_until, deterministic_baseline_json, "
    "unavailable_fallback, owner_admission_ref, manifest_contract_version, "
    "effect_mode, assignment_namespace_key, assignment_hash_version, "
    "behavior_policy_epoch_ref, created_at"
)
_EXECUTION_MANIFEST_LEGACY_COLUMNS = (
    "manifest_id, observation_id, generation, spec_hash, manifest_hash, "
    "treatment_adapter_id, treatment_adapter_version, assignment_policy, "
    "assignment_salt, assignment_unit_key_template, allocation_pct, "
    "control_payload_json, treatment_payload_json, eligible_population_json, "
    "behavior_contract_hash, interference_key, composability_contract, "
    "effective_from, effective_until, deterministic_baseline_json, "
    "unavailable_fallback, owner_admission_ref, created_at"
)
_EXECUTION_MANIFEST_DEFAULTS = {
    "manifest_contract_version": "execution-manifest/v1",
    "effect_mode": "observe_only",
    "assignment_namespace_key": None,
    "assignment_hash_version": "sha256-prefix16/0xffffffffffffffff",
    "behavior_policy_epoch_ref": None,
}


_EXECUTION_MANIFEST_TABLE_SQL = (
    "CREATE TABLE execution_manifests ("
    "manifest_id TEXT PRIMARY KEY,"
    "observation_id TEXT NOT NULL,"
    "generation INTEGER NOT NULL,"
    "spec_hash TEXT NOT NULL,"
    "manifest_hash TEXT NOT NULL UNIQUE,"
    "treatment_adapter_id TEXT NOT NULL,"
    "treatment_adapter_version TEXT NOT NULL,"
    "assignment_policy TEXT NOT NULL,"
    "assignment_salt TEXT NOT NULL,"
    "assignment_unit_key_template TEXT NOT NULL,"
    "allocation_pct REAL NOT NULL,"
    "control_payload_json TEXT NOT NULL,"
    "treatment_payload_json TEXT NOT NULL,"
    "eligible_population_json TEXT NOT NULL,"
    "behavior_contract_hash TEXT NOT NULL,"
    "interference_key TEXT NOT NULL,"
    "composability_contract TEXT NOT NULL,"
    "effective_from TEXT NOT NULL CHECK (julianday(effective_from) IS NOT NULL),"
    "effective_until TEXT CHECK (effective_until IS NULL OR "
    "julianday(effective_until) IS NOT NULL),"
    "deterministic_baseline_json TEXT NOT NULL,"
    "unavailable_fallback TEXT NOT NULL,"
    "owner_admission_ref TEXT,"
    "manifest_contract_version TEXT NOT NULL DEFAULT 'execution-manifest/v1',"
    "effect_mode TEXT NOT NULL DEFAULT 'observe_only' CHECK (effect_mode IN ('observe_only','paper_treatment')),"
    "assignment_namespace_key TEXT,"
    "assignment_hash_version TEXT NOT NULL DEFAULT 'sha256-prefix16/0xffffffffffffffff',"
    "behavior_policy_epoch_ref TEXT,"
    "created_at TEXT NOT NULL,"
    "UNIQUE (observation_id, generation)"
    ");"
)


_LAB_SCHEMA_SQL = (
    "CREATE TABLE IF NOT EXISTS lab_schema_migrations ("
    "migration_id TEXT PRIMARY KEY,"
    "checksum TEXT NOT NULL,"
    "applied_at TEXT NOT NULL"
    ");"
    "CREATE TABLE IF NOT EXISTS filter_shadow_frozen_units ("
    "id TEXT PRIMARY KEY,"
    "experiment_id TEXT NOT NULL,"
    "outcome_candidate_key TEXT NOT NULL,"
    "decision TEXT NOT NULL CHECK (decision IN ('passed','rejected')),"
    "input_snapshot TEXT NOT NULL,"
    "parameter_snapshot TEXT NOT NULL,"
    "first_evaluated_at TEXT NOT NULL,"
    "source_runtime_commit TEXT,"
    "first_seen_at TEXT NOT NULL,"
    "UNIQUE (experiment_id, outcome_candidate_key)"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_frozen_units_experiment "
    "ON filter_shadow_frozen_units(experiment_id, first_evaluated_at);"
    # UFC shadow-equivalence observation for Issue #1072.
    # One row per equivalence pass per (observation_id, cohort_key). Append-only:
    # a mismatch never overwrites a prior match; a subsequent match records a
    # new row so we retain the full history for causal audit.
    "CREATE TABLE IF NOT EXISTS ufc_shadow_equivalence ("
    "id TEXT PRIMARY KEY,"
    "observation_id TEXT NOT NULL,"       # ufc:<exp_id>:<policy_epoch>:<fixed_look_gen>
    "experiment_id TEXT NOT NULL,"
    "cohort_key TEXT NOT NULL,"           # hypothesis_id + policy_epoch
    "checked_at TEXT NOT NULL,"
    "matched INTEGER NOT NULL CHECK (matched IN (0, 1)),"
    "mismatch_field TEXT,"                # NULL when matched=1
    "learning_verdict TEXT,"              # observed learning verdict string
    "recomputed_verdict TEXT,"
    "learning_treatment_n INTEGER,"
    "recomputed_treatment_n INTEGER,"
    "learning_control_n INTEGER,"
    "recomputed_control_n INTEGER,"
    "notes TEXT"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_ufc_shadow_equivalence_obs "
    "ON ufc_shadow_equivalence(observation_id, checked_at DESC);"
    # ExecutionManifest for Issue #1072's manifest contract.
    # Immutable per (observation_id, generation, manifest_hash). Publishing
    # a semantically different manifest for the same observation MUST create
    # a new generation — this table never UPDATEs, only INSERTs.
    + _EXECUTION_MANIFEST_TABLE_SQL.replace(
        "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
    )
    + "CREATE INDEX IF NOT EXISTS idx_execution_manifests_active "
    "ON execution_manifests(effective_from, effective_until);"
    "CREATE INDEX IF NOT EXISTS idx_execution_manifests_interference "
    "ON execution_manifests(interference_key, effective_from);"
    # Joint checkpoint for Issue #1072's storage/runtime continuity evidence.
    # Every scheduled Lab pass can append a fingerprint over the app.db
    # migration head plus the active manifest-set hashes. A mismatch remains
    # detectable; its authority/response is governed by the current checkpoint
    # contract and executable tests rather than by an implementation-stage note.
    "CREATE TABLE IF NOT EXISTS joint_checkpoints ("
    "checkpoint_id TEXT PRIMARY KEY,"
    "recorded_at TEXT NOT NULL,"
    "app_migration_head TEXT NOT NULL,"
    "app_migration_count INTEGER NOT NULL,"
    "active_manifest_hashes TEXT NOT NULL,"     # canonical json array
    "active_manifest_count INTEGER NOT NULL,"
    "lab_schema_head TEXT,"
    "lab_schema_count INTEGER,"
    "lab_state_fingerprint TEXT,"
    "checkpoint_ledger_fingerprint TEXT,"
    "main_outbox_high_watermark INTEGER,"
    "main_outbox_pending_count INTEGER,"
    "main_outbox_fingerprint TEXT,"
    "joint_fingerprint TEXT NOT NULL,"          # sha256 over the whole shape
    "notes TEXT"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_joint_checkpoints_recent "
    "ON joint_checkpoints(recorded_at DESC);"
    "CREATE TRIGGER IF NOT EXISTS trg_joint_checkpoints_no_update "
    "BEFORE UPDATE ON joint_checkpoints BEGIN "
    "SELECT RAISE(ABORT, 'joint_checkpoints is append-only'); END;"
    "CREATE TRIGGER IF NOT EXISTS trg_joint_checkpoints_no_delete "
    "BEFORE DELETE ON joint_checkpoints BEGIN "
    "SELECT RAISE(ABORT, 'joint_checkpoints is append-only'); END;"
    # Typed exposure fact for Issue #1072.
    # Append-only. Every eligible prospective unit — even one whose manifest
    # resolution declined (fail-closed conflict, not-effective window,
    # malformed input) — records a row so "unavailable / exclusion reason"
    # is inspectable at audit time. Lab side only; production
    # ``learning_exposures`` remains the canonical write path for paths that
    # have not been explicitly rewired to a compatible manifest consumer.
    "CREATE TABLE IF NOT EXISTS lab_exposures ("
    "exposure_id TEXT PRIMARY KEY,"
    "observation_id TEXT NOT NULL,"
    "manifest_id TEXT,"                        # NULL when no manifest matched
    "manifest_hash TEXT,"
    "assignment_unit_key TEXT NOT NULL,"
    "candidate_key TEXT NOT NULL,"
    "assignment TEXT NOT NULL CHECK ("
    "  assignment IN ('treatment','control','unassigned')"
    "),"
    "applied INTEGER NOT NULL CHECK (applied IN (0, 1)),"
    "behavior_changed INTEGER NOT NULL CHECK (behavior_changed IN (0, 1)),"
    "payload_json TEXT NOT NULL,"
    "reason TEXT NOT NULL,"
    "active_manifest_set_json TEXT NOT NULL,"  # identity set the resolver saw
    "conflict_result TEXT NOT NULL,"           # single_manifest / no_manifest / disjoint_ok / factorial_ok / undeclared_conflict:*
    "exposed_at TEXT NOT NULL,"
    "source_runtime_commit TEXT"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_lab_exposures_obs "
    "ON lab_exposures(observation_id, exposed_at DESC);"
    "CREATE INDEX IF NOT EXISTS idx_lab_exposures_unit "
    "ON lab_exposures(assignment_unit_key, exposed_at DESC);"
    "CREATE INDEX IF NOT EXISTS idx_lab_exposures_conflict "
    "ON lab_exposures(conflict_result, exposed_at DESC);"
    "CREATE TRIGGER IF NOT EXISTS trg_lab_exposures_no_update "
    "BEFORE UPDATE ON lab_exposures BEGIN "
    "SELECT RAISE(ABORT, 'lab_exposures is append-only'); END;"
    "CREATE TRIGGER IF NOT EXISTS trg_lab_exposures_no_delete "
    "BEFORE DELETE ON lab_exposures BEGIN "
    "SELECT RAISE(ABORT, 'lab_exposures is append-only'); END;"
    "CREATE TABLE IF NOT EXISTS observation_windows ("
    "observation_id TEXT NOT NULL,"
    "kind TEXT NOT NULL,"
    "handler_version TEXT NOT NULL,"
    "generation INTEGER NOT NULL CHECK (generation > 0),"
    "slot_key TEXT NOT NULL,"
    "authority_mode TEXT NOT NULL CHECK (authority_mode IN ('observe_only','external_authority')),"
    "status TEXT NOT NULL CHECK (status IN ('active','finalized','invalidated','aborted')),"
    "spec_json TEXT NOT NULL,"
    "spec_hash TEXT NOT NULL,"
    "producer_commit TEXT,"
    "started_at TEXT NOT NULL,"
    "collect_until TEXT NOT NULL,"
    "finalize_not_before TEXT NOT NULL,"
    "last_advanced_at TEXT,"
    "last_summary_json TEXT,"
    "last_error TEXT,"
    "terminal_at TEXT,"
    "terminal_reason TEXT,"
    "artifact_path TEXT,"
    "artifact_sha256 TEXT,"
    "PRIMARY KEY (observation_id, generation)"
    ");"
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_observation_windows_active_slot "
    "ON observation_windows(slot_key) WHERE status='active';"
    "CREATE INDEX IF NOT EXISTS idx_observation_windows_fair "
    "ON observation_windows(status, last_advanced_at, started_at, observation_id, generation);"
    "CREATE TRIGGER IF NOT EXISTS trg_observation_windows_identity_immutable "
    "BEFORE UPDATE ON observation_windows WHEN "
    "OLD.observation_id IS NOT NEW.observation_id OR OLD.generation IS NOT NEW.generation OR "
    "OLD.kind IS NOT NEW.kind OR OLD.handler_version IS NOT NEW.handler_version OR "
    "OLD.slot_key IS NOT NEW.slot_key OR OLD.authority_mode IS NOT NEW.authority_mode OR "
    "OLD.spec_json IS NOT NEW.spec_json OR OLD.spec_hash IS NOT NEW.spec_hash OR "
    "OLD.producer_commit IS NOT NEW.producer_commit OR OLD.started_at IS NOT NEW.started_at OR "
    "OLD.collect_until IS NOT NEW.collect_until OR "
    "OLD.finalize_not_before IS NOT NEW.finalize_not_before BEGIN "
    "SELECT RAISE(ABORT, 'observation_windows identity is immutable'); END;"
    "CREATE TABLE IF NOT EXISTS observation_units ("
    "unit_id TEXT PRIMARY KEY,"
    "observation_id TEXT NOT NULL,"
    "generation INTEGER NOT NULL,"
    "spec_hash TEXT NOT NULL,"
    "unit_key TEXT NOT NULL,"
    "source_kind TEXT NOT NULL,"
    "source_ref TEXT NOT NULL,"
    "source_hash TEXT NOT NULL,"
    "known_at TEXT NOT NULL,"
    "captured_at TEXT NOT NULL,"
    "producer_commit TEXT,"
    "producer_runtime_session_id TEXT,"
    "admission TEXT NOT NULL CHECK (admission IN ('admitted','unavailable','excluded')),"
    "reason TEXT,"
    "UNIQUE (observation_id, generation, unit_key),"
    "FOREIGN KEY (observation_id, generation) REFERENCES observation_windows(observation_id, generation)"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_observation_units_window "
    "ON observation_units(observation_id, generation, admission, known_at);"
    "CREATE TRIGGER IF NOT EXISTS trg_observation_units_no_update "
    "BEFORE UPDATE ON observation_units BEGIN "
    "SELECT RAISE(ABORT, 'observation_units is append-only'); END;"
    "CREATE TRIGGER IF NOT EXISTS trg_observation_units_no_delete "
    "BEFORE DELETE ON observation_units "
    "WHEN NOT EXISTS (SELECT 1 FROM observation_archives a "
    "WHERE a.observation_id=OLD.observation_id AND a.generation=OLD.generation) BEGIN "
    "SELECT RAISE(ABORT, 'observation_units is append-only'); END;"
    "CREATE TRIGGER IF NOT EXISTS trg_execution_manifests_no_update "
    "BEFORE UPDATE ON execution_manifests BEGIN "
    "SELECT RAISE(ABORT, 'execution_manifests is append-only'); END;"
    "CREATE TRIGGER IF NOT EXISTS trg_execution_manifests_no_delete "
    "BEFORE DELETE ON execution_manifests BEGIN "
    "SELECT RAISE(ABORT, 'execution_manifests is append-only'); END;"
) + CANONICAL_SCHEMA_SQL


def lab_db_path() -> Path:
    """Resolve the Lab DB location under the current data root."""
    root = get_data_dir() / "observation-lab"
    root.mkdir(parents=True, exist_ok=True)
    return root / "lab.db"


def get_lab_db() -> sqlite3.Connection:
    """Return a thread-local, path-aware connection to ``lab.db``.

    Path change (tests swap ``DATA_DIR``) closes the stale connection and
    reopens against the new path. Schema is materialized on every open — the
    ``IF NOT EXISTS`` guard keeps it idempotent.
    """
    readonly_connection = getattr(_readonly_local, "connection", None)
    if readonly_connection is not None:
        return readonly_connection
    if getattr(_readonly_local, "block_missing", False):
        raise FileNotFoundError(get_data_dir() / "observation-lab" / "lab.db")

    target = lab_db_path()
    cached_path = getattr(_local, "path", None)
    cached_conn = getattr(_local, "conn", None)
    if cached_conn is not None and cached_path == target:
        return cached_conn
    if cached_conn is not None:
        try:
            cached_conn.close()
        except sqlite3.Error:
            logger.warning("observation_lab: closing stale cached lab.db connection failed; opening a fresh connection anyway", exc_info=True)
    with lab_writer_lock():
        conn = sqlite3.connect(
            str(target),
            isolation_level=None,  # autocommit — matches app.db policy
            check_same_thread=False,
            timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_LAB_SCHEMA_SQL)
        _ensure_lab_schema_ledger(conn)
    _local.conn = conn
    _local.path = target
    return conn


def _ensure_lab_schema_ledger(conn: sqlite3.Connection) -> None:
    """Record the materialized Lab schema without rewriting migration history."""
    _migrate_execution_manifest_constraints(conn)
    manifest_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(execution_manifests)").fetchall()
    }
    for name, ddl in (
        ("manifest_contract_version", "TEXT NOT NULL DEFAULT 'execution-manifest/v1'"),
        ("effect_mode", "TEXT NOT NULL DEFAULT 'observe_only'"),
        ("assignment_namespace_key", "TEXT"),
        ("assignment_hash_version", "TEXT NOT NULL DEFAULT 'sha256-prefix16/0xffffffffffffffff'"),
        ("behavior_policy_epoch_ref", "TEXT"),
    ):
        if name not in manifest_columns:
            conn.execute(f"ALTER TABLE execution_manifests ADD COLUMN {name} {ddl}")
    retention_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(observation_retention_claims)").fetchall()
    }
    for name, ddl in (
        ("release_proof_fingerprint", "TEXT"),
        ("release_reason", "TEXT"),
    ):
        if name not in retention_columns:
            conn.execute(f"ALTER TABLE observation_retention_claims ADD COLUMN {name} {ddl}")
    exposure_columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(observation_exposures)").fetchall()
    }
    if "authority_epoch" not in exposure_columns:
        conn.execute("ALTER TABLE observation_exposures ADD COLUMN authority_epoch TEXT")
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(joint_checkpoints)").fetchall()
    }
    for name, ddl in (
        ("lab_schema_head", "TEXT"),
        ("lab_schema_count", "INTEGER"),
        ("lab_state_fingerprint", "TEXT"),
        ("checkpoint_ledger_fingerprint", "TEXT"),
        ("main_outbox_high_watermark", "INTEGER"),
        ("main_outbox_pending_count", "INTEGER"),
        ("main_outbox_fingerprint", "TEXT"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE joint_checkpoints ADD COLUMN {name} {ddl}")

    _ensure_archive_eviction_triggers(conn)

    checksum = hashlib.sha256(_LAB_SCHEMA_SQL.encode("utf-8")).hexdigest()
    migration = conn.execute(
        "SELECT checksum FROM lab_schema_migrations WHERE migration_id=?",
        (LAB_SCHEMA_MIGRATION_ID,),
    ).fetchone()
    if migration is not None and migration["checksum"] != checksum:
        raise RuntimeError("lab_schema_checksum_mismatch")
    if migration is None:
        conn.execute(
            "INSERT INTO lab_schema_migrations (migration_id, checksum, applied_at) "
            "VALUES (?, ?, ?)",
            (LAB_SCHEMA_MIGRATION_ID, checksum, _schema_now()),
        )


def _ensure_archive_eviction_triggers(conn: sqlite3.Connection) -> None:
    """Allow hot-row eviction only after a verified archive index exists."""
    for table in ("observation_units", "observation_exposures", "observation_evaluations", "observation_inference_plans"):
        trigger = f"trg_{table}_no_delete"
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute(
            f"CREATE TRIGGER {trigger} BEFORE DELETE ON {table} "
            "WHEN NOT EXISTS (SELECT 1 FROM observation_archives a "
            "WHERE a.observation_id=OLD.observation_id AND a.generation=OLD.generation) "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;"
        )


def _migrate_execution_manifest_constraints(conn: sqlite3.Connection) -> None:
    """Upgrade pre-ledger manifests before recording the new schema head.

    ``CREATE TABLE IF NOT EXISTS`` does not alter an existing SQLite table. The
    first Lab schema therefore needs one explicit, data-preserving rebuild so
    the timestamp checks that the manifest resolver relies on are physically
    present before their checksum is advertised as applied.
    """
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='execution_manifests'"
    ).fetchone()
    if table is None or "julianday(effective_from)" in str(table[0]).lower():
        return

    table_columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(execution_manifests)").fetchall()
    }
    source_columns = (
        _EXECUTION_MANIFEST_COLUMNS
        if set(_EXECUTION_MANIFEST_COLUMNS.split(", ")) <= table_columns
        else _EXECUTION_MANIFEST_LEGACY_COLUMNS
    )
    source_names = source_columns.split(", ")
    rows = conn.execute(f"SELECT {source_columns} FROM execution_manifests").fetchall()
    for row in rows:
        values = dict(zip(source_names, tuple(row)))
        try:
            start = datetime.fromisoformat(str(values["effective_from"]).replace("Z", "+00:00"))
            end_value = values["effective_until"]
            end = (
                datetime.fromisoformat(str(end_value).replace("Z", "+00:00"))
                if end_value is not None else None
            )
            if start.tzinfo is None or start.utcoffset() != timezone.utc.utcoffset(start):
                raise ValueError("effective_from must be aware UTC")
            if end is not None and (
                end.tzinfo is None
                or end.utcoffset() != timezone.utc.utcoffset(end)
                or end <= start
            ):
                raise ValueError("effective_until must be later aware UTC")
        except (TypeError, ValueError) as exc:
            raise sqlite3.IntegrityError(
                "cannot migrate malformed execution_manifest interval"
            ) from exc

    index_names = (
        "idx_execution_manifests_active",
        "idx_execution_manifests_interference",
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        for index_name in index_names:
            conn.execute(f"DROP INDEX IF EXISTS {index_name}")
        conn.execute("ALTER TABLE execution_manifests RENAME TO execution_manifests_legacy")
        conn.execute(_EXECUTION_MANIFEST_TABLE_SQL)
        placeholders = ", ".join("?" for _ in _EXECUTION_MANIFEST_COLUMNS.split(", "))
        conn.executemany(
            f"INSERT INTO execution_manifests ({_EXECUTION_MANIFEST_COLUMNS}) VALUES ({placeholders})",
            [
                tuple(
                    values.get(name, _EXECUTION_MANIFEST_DEFAULTS.get(name))
                    for name in _EXECUTION_MANIFEST_COLUMNS.split(", ")
                )
                for values in (
                    dict(zip(source_names, tuple(row)))
                    for row in rows
                )
            ],
        )
        conn.execute("DROP TABLE execution_manifests_legacy")
        conn.execute(
            "CREATE INDEX idx_execution_manifests_active "
            "ON execution_manifests(effective_from, effective_until)"
        )
        conn.execute(
            "CREATE INDEX idx_execution_manifests_interference "
            "ON execution_manifests(interference_key, effective_from)"
        )
        conn.execute(
            "CREATE TRIGGER trg_execution_manifests_no_update "
            "BEFORE UPDATE ON execution_manifests BEGIN "
            "SELECT RAISE(ABORT, 'execution_manifests is append-only'); END;"
        )
        conn.execute(
            "CREATE TRIGGER trg_execution_manifests_no_delete "
            "BEFORE DELETE ON execution_manifests BEGIN "
            "SELECT RAISE(ABORT, 'execution_manifests is append-only'); END;"
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


@contextmanager
def readonly_lab_db(*, allow_missing: bool = False):
    """Open ``lab.db`` physically read-only without creating Lab state."""
    previous_connection = getattr(_readonly_local, "connection", None)
    previous_block = getattr(_readonly_local, "block_missing", False)
    if previous_connection is not None:
        yield previous_connection
        return

    path = get_data_dir() / "observation-lab" / "lab.db"
    if not path.is_file():
        if allow_missing:
            _readonly_local.connection = None
            _readonly_local.block_missing = True
            try:
                yield None
            finally:
                _readonly_local.connection = previous_connection
                _readonly_local.block_missing = previous_block
            return
        raise FileNotFoundError(path)

    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=1.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    _readonly_local.connection = connection
    _readonly_local.block_missing = False
    try:
        yield connection
    finally:
        _readonly_local.connection = previous_connection
        _readonly_local.block_missing = previous_block
        connection.close()


@contextmanager
def lab_transaction(*, immediate: bool = False):
    """Serialize Lab writes and preserve an existing caller transaction."""
    db = get_lab_db()
    with lab_writer_lock():
        was_in_tx = db.in_transaction
        if not was_in_tx:
            db.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield db
            if not was_in_tx:
                db.commit()
        except Exception:
            if not was_in_tx:
                db.rollback()
            raise


def close_lab_db() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            logger.warning("observation_lab: lab.db connection close failed during teardown", exc_info=True)
        _local.conn = None
        _local.path = None


def reset_lab_db() -> None:
    """Test hook: drop cached connection so a fresh ``DATA_DIR`` is picked up."""
    close_lab_db()

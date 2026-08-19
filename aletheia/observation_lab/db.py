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

import sqlite3
import threading
from pathlib import Path

from aletheia.config.loader import get_data_dir

_local = threading.local()

_LAB_SCHEMA_SQL = (
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
    # UFC shadow-equivalence adoption (Issue #1072 Packet 1).
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
    # ExecutionManifest (Issue #1072 Cluster 3 / §"Minimal contracts").
    # Immutable per (observation_id, generation, manifest_hash). Publishing
    # a semantically different manifest for the same observation MUST create
    # a new generation — this table never UPDATEs, only INSERTs.
    "CREATE TABLE IF NOT EXISTS execution_manifests ("
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
    "composability_contract TEXT NOT NULL,"       # 'exclusive' / 'disjoint_population' / 'factorial'
    "effective_from TEXT NOT NULL,"
    "effective_until TEXT,"
    "deterministic_baseline_json TEXT NOT NULL,"
    "unavailable_fallback TEXT NOT NULL,"          # e.g. 'baseline' / 'exclude'
    "owner_admission_ref TEXT,"
    "created_at TEXT NOT NULL,"
    "UNIQUE (observation_id, generation)"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_execution_manifests_active "
    "ON execution_manifests(effective_from, effective_until);"
    "CREATE INDEX IF NOT EXISTS idx_execution_manifests_interference "
    "ON execution_manifests(interference_key, effective_from);"
    # Joint checkpoint (Issue #1072 Cluster 5 / §"Storage and runtime boundary").
    # Every scheduled Lab pass computes and appends a fingerprint over the
    # app.db migration head + lab.db manifest-set hashes. A restore that
    # leaves those two out of step is detectable at startup: the freshly
    # computed fingerprint disagrees with the most recent stored one, and
    # the affected Observations fail closed as invalidated (per new #1072
    # §"Storage and runtime boundary" verbatim).
    "CREATE TABLE IF NOT EXISTS joint_checkpoints ("
    "checkpoint_id TEXT PRIMARY KEY,"
    "recorded_at TEXT NOT NULL,"
    "app_migration_head TEXT NOT NULL,"
    "app_migration_count INTEGER NOT NULL,"
    "active_manifest_hashes TEXT NOT NULL,"     # canonical json array
    "active_manifest_count INTEGER NOT NULL,"
    "joint_fingerprint TEXT NOT NULL,"          # sha256 over the whole shape
    "notes TEXT"
    ");"
    "CREATE INDEX IF NOT EXISTS idx_joint_checkpoints_recent "
    "ON joint_checkpoints(recorded_at DESC);"
    # Typed exposure fact (Issue #1072 Cluster 4 / §"Exposure fact").
    # Append-only. Every eligible prospective unit — even one whose manifest
    # resolution declined (fail-closed conflict, not-effective window,
    # malformed input) — records a row so "unavailable / exclusion reason"
    # is inspectable at audit time. Lab side only; production
    # ``learning_exposures`` remains the canonical write path for legacy
    # UFC until Packet 2 rewires Main Runtime to consume manifests.
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
)


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
    target = lab_db_path()
    cached_path = getattr(_local, "path", None)
    cached_conn = getattr(_local, "conn", None)
    if cached_conn is not None and cached_path == target:
        return cached_conn
    if cached_conn is not None:
        try:
            cached_conn.close()
        except sqlite3.Error:
            pass
    conn = sqlite3.connect(
        str(target),
        isolation_level=None,  # autocommit — matches app.db policy
        check_same_thread=False,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.executescript(_LAB_SCHEMA_SQL)
    _local.conn = conn
    _local.path = target
    return conn


def close_lab_db() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _local.conn = None
        _local.path = None


def reset_lab_db() -> None:
    """Test hook: drop cached connection so a fresh ``DATA_DIR`` is picked up."""
    close_lab_db()

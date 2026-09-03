"""Lab V2 durable schema primitives.

The schema is deliberately boring: one physical Lab database, append-only
evidence tables, and small mutable cursors/health projections.  Main-owned
production facts stay in ``app.db``; this file only describes Lab state.
"""

from __future__ import annotations


CANONICAL_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS observation_generation_contracts (
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation > 0),
    stage TEXT NOT NULL,
    parent_generation INTEGER,
    assignment_policy TEXT NOT NULL,
    assignment_namespace_key TEXT NOT NULL,
    assignment_salt TEXT NOT NULL,
    assignment_unit_key_template TEXT NOT NULL,
    assignment_hash_version TEXT NOT NULL DEFAULT 'sha256-prefix16/2^64',
    allocation_pct REAL NOT NULL CHECK (allocation_pct >= 0 AND allocation_pct <= 1),
    gates_json TEXT NOT NULL DEFAULT '{}',
    harm_budget_json TEXT NOT NULL DEFAULT '{}',
    target_horizon TEXT NOT NULL,
    outcome_contract_version TEXT NOT NULL,
    metric_version TEXT NOT NULL,
    behavior_contract_hash TEXT NOT NULL,
    behavior_policy_epoch_ref TEXT,
    behavior_policy_epoch_hash TEXT,
    owner_admission_ref TEXT,
    started_at TEXT NOT NULL,
    effective_until TEXT,
    contract_hash TEXT NOT NULL UNIQUE,
    terminal_status TEXT,
    terminal_at TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (observation_id, generation),
    FOREIGN KEY (observation_id, generation)
        REFERENCES observation_windows(observation_id, generation)
);
CREATE INDEX IF NOT EXISTS idx_observation_generation_contract_stage
    ON observation_generation_contracts(stage, started_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_observation_generation_contracts_no_update
    BEFORE UPDATE ON observation_generation_contracts BEGIN
    SELECT RAISE(ABORT, 'observation_generation_contracts is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_generation_contracts_no_delete
    BEFORE DELETE ON observation_generation_contracts BEGIN
    SELECT RAISE(ABORT, 'observation_generation_contracts is append-only'); END;

CREATE TABLE IF NOT EXISTS observation_nominations (
    nomination_id TEXT PRIMARY KEY,
    source_nomination_id TEXT NOT NULL UNIQUE,
    source_hash TEXT NOT NULL,
    observation_id TEXT,
    generation INTEGER,
    disposition TEXT NOT NULL CHECK (disposition IN ('admitted','rejected','pending')),
    snapshot_json TEXT NOT NULL,
    rejection_reason TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observation_nominations_source
    ON observation_nominations(source_nomination_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_observation_nominations_no_update
    BEFORE UPDATE ON observation_nominations BEGIN
    SELECT RAISE(ABORT, 'observation_nominations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_nominations_no_delete
    BEFORE DELETE ON observation_nominations BEGIN
    SELECT RAISE(ABORT, 'observation_nominations is append-only'); END;

CREATE TABLE IF NOT EXISTS observation_exposures (
    exposure_id TEXT PRIMARY KEY,
    source_receipt_id TEXT NOT NULL UNIQUE,
    receipt_sequence INTEGER,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    manifest_id TEXT,
    manifest_hash TEXT,
    assignment_unit_key TEXT NOT NULL,
    candidate_key TEXT NOT NULL,
    assignment TEXT NOT NULL CHECK (assignment IN ('treatment','control','unassigned')),
    applied INTEGER NOT NULL CHECK (applied IN (0,1)),
    behavior_changed INTEGER NOT NULL CHECK (behavior_changed IN (0,1)),
    baseline_payload_hash TEXT NOT NULL,
    applied_payload_hash TEXT NOT NULL,
    baseline_payload_json TEXT NOT NULL DEFAULT '{}',
    applied_payload_json TEXT NOT NULL DEFAULT '{}',
    outcome_candidate_key TEXT,
    outcome_source TEXT,
    resolution_reason TEXT NOT NULL,
    behavior_contract_hash TEXT,
    behavior_policy_epoch_ref TEXT,
    active_manifest_snapshot_json TEXT NOT NULL DEFAULT '[]',
    manifest_set_fingerprint TEXT,
    revocation_snapshot_fingerprint TEXT,
    authority_source TEXT NOT NULL DEFAULT 'lab',
    authority_epoch TEXT,
    runtime_session_id TEXT,
    producer_commit TEXT,
    exposed_at TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observation_exposures_generation
    ON observation_exposures(observation_id, generation, exposed_at DESC);
CREATE INDEX IF NOT EXISTS idx_observation_exposures_sequence
    ON observation_exposures(receipt_sequence);
CREATE TRIGGER IF NOT EXISTS trg_observation_exposures_no_update
    BEFORE UPDATE ON observation_exposures BEGIN
    SELECT RAISE(ABORT, 'observation_exposures is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_exposures_no_delete
    BEFORE DELETE ON observation_exposures
    WHEN NOT EXISTS (SELECT 1 FROM observation_archives a
                     WHERE a.observation_id=OLD.observation_id
                       AND a.generation=OLD.generation)
    BEGIN
    SELECT RAISE(ABORT, 'observation_exposures is append-only'); END;

CREATE TABLE IF NOT EXISTS observation_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    evaluation_kind TEXT NOT NULL CHECK (evaluation_kind IN ('monitoring','confirmatory')),
    source_cutoff TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    exposure_fingerprint TEXT NOT NULL,
    outcome_fingerprint TEXT NOT NULL,
    result_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    disposition TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (observation_id, generation, evaluation_kind, input_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_observation_evaluations_generation
    ON observation_evaluations(observation_id, generation, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_observation_evaluations_no_update
    BEFORE UPDATE ON observation_evaluations BEGIN
    SELECT RAISE(ABORT, 'observation_evaluations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_evaluations_no_delete
    BEFORE DELETE ON observation_evaluations
    WHEN NOT EXISTS (SELECT 1 FROM observation_archives a
                     WHERE a.observation_id=OLD.observation_id
                       AND a.generation=OLD.generation)
    BEGIN
    SELECT RAISE(ABORT, 'observation_evaluations is append-only'); END;

CREATE TABLE IF NOT EXISTS observation_results (
    result_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    evaluation_id TEXT,
    source_cutoff TEXT NOT NULL,
    verdict TEXT NOT NULL,
    disposition TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_fingerprint TEXT NOT NULL UNIQUE,
    archive_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (observation_id, generation, evaluation_id)
);
CREATE INDEX IF NOT EXISTS idx_observation_results_generation
    ON observation_results(observation_id, generation, created_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_observation_results_no_update
    BEFORE UPDATE ON observation_results BEGIN
    SELECT RAISE(ABORT, 'observation_results is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_results_no_delete
    BEFORE DELETE ON observation_results BEGIN
    SELECT RAISE(ABORT, 'observation_results history is retained'); END;

CREATE TABLE IF NOT EXISTS observation_inference_plans (
    plan_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    inference_contract_version TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('collecting','frozen','consumed','invalidated')),
    cutoff_json TEXT,
    cutoff_fingerprint TEXT,
    cutoff_frozen_at TEXT,
    consumed_evaluation_id TEXT,
    consumed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (observation_id, generation)
);
CREATE TRIGGER IF NOT EXISTS trg_observation_inference_plans_identity_immutable
    BEFORE UPDATE ON observation_inference_plans
    WHEN OLD.plan_id IS NOT NEW.plan_id
      OR OLD.observation_id IS NOT NEW.observation_id
      OR OLD.generation IS NOT NEW.generation
      OR OLD.inference_contract_version IS NOT NEW.inference_contract_version
      OR OLD.plan_json IS NOT NEW.plan_json
      OR OLD.plan_hash IS NOT NEW.plan_hash
      OR (OLD.cutoff_fingerprint IS NOT NULL
          AND (NEW.cutoff_fingerprint IS NOT OLD.cutoff_fingerprint
               OR NEW.cutoff_json IS NOT OLD.cutoff_json
               OR NEW.cutoff_frozen_at IS NOT OLD.cutoff_frozen_at))
    BEGIN SELECT RAISE(ABORT, 'observation_inference_plans identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_inference_plans_no_delete
    BEFORE DELETE ON observation_inference_plans
    WHEN NOT EXISTS (SELECT 1 FROM observation_archives a
                     WHERE a.observation_id=OLD.observation_id
                       AND a.generation=OLD.generation)
    BEGIN
    SELECT RAISE(ABORT, 'observation_inference_plans history is retained'); END;

CREATE TABLE IF NOT EXISTS observation_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    source_receipt_id TEXT,
    audit_identity TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observation_lifecycle_events_generation
    ON observation_lifecycle_events(observation_id, generation, created_at, event_id);
CREATE TRIGGER IF NOT EXISTS trg_observation_lifecycle_events_no_update
    BEFORE UPDATE ON observation_lifecycle_events BEGIN
    SELECT RAISE(ABORT, 'observation_lifecycle_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_lifecycle_events_no_delete
    BEFORE DELETE ON observation_lifecycle_events BEGIN
    SELECT RAISE(ABORT, 'observation_lifecycle_events history is retained'); END;

CREATE TABLE IF NOT EXISTS manifest_revocations (
    revocation_id TEXT PRIMARY KEY,
    manifest_id TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    revoked_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    lifecycle_event_id TEXT,
    source_action_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (manifest_id, manifest_hash)
);
CREATE INDEX IF NOT EXISTS idx_manifest_revocations_lookup
    ON manifest_revocations(manifest_hash, revoked_at DESC);
CREATE TRIGGER IF NOT EXISTS trg_manifest_revocations_no_update
    BEFORE UPDATE ON manifest_revocations BEGIN
    SELECT RAISE(ABORT, 'manifest_revocations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_manifest_revocations_no_delete
    BEFORE DELETE ON manifest_revocations BEGIN
    SELECT RAISE(ABORT, 'manifest_revocations history is retained'); END;

CREATE TABLE IF NOT EXISTS observation_import_cursors (
    source_name TEXT PRIMARY KEY,
    contiguous_sequence INTEGER NOT NULL DEFAULT 0 CHECK (contiguous_sequence >= 0),
    retry_sequences_json TEXT NOT NULL DEFAULT '[]',
    source_checkpoint_json TEXT,
    source_checkpoint_fingerprint TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observation_job_health (
    job_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_started_at TEXT,
    last_succeeded_at TEXT,
    last_failed_at TEXT,
    input_watermark INTEGER,
    backlog_count INTEGER,
    oldest_backlog_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observation_retention_claims (
    claim_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    claim_domain TEXT NOT NULL,
    derived_from_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active','released')),
    created_at TEXT NOT NULL,
    released_at TEXT,
    release_proof_fingerprint TEXT,
    release_reason TEXT,
    UNIQUE (observation_id, generation, source_kind, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_observation_retention_claims_active
    ON observation_retention_claims(status, claim_domain, observation_id, generation);
CREATE TRIGGER IF NOT EXISTS trg_observation_retention_claims_identity_immutable
    BEFORE UPDATE ON observation_retention_claims
    WHEN OLD.claim_id IS NOT NEW.claim_id
      OR OLD.observation_id IS NOT NEW.observation_id
      OR OLD.generation IS NOT NEW.generation
      OR OLD.source_kind IS NOT NEW.source_kind
      OR OLD.source_ref IS NOT NEW.source_ref
      OR OLD.source_hash IS NOT NEW.source_hash
      OR OLD.claim_domain IS NOT NEW.claim_domain
      OR OLD.derived_from_fingerprint IS NOT NEW.derived_from_fingerprint
    BEGIN SELECT RAISE(ABORT, 'observation_retention_claim identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_retention_claims_no_delete
    BEFORE DELETE ON observation_retention_claims BEGIN
    SELECT RAISE(ABORT, 'observation_retention_claim history is retained'); END;

CREATE TABLE IF NOT EXISTS observation_archives (
    archive_id TEXT PRIMARY KEY,
    observation_id TEXT NOT NULL,
    generation INTEGER NOT NULL,
    terminal_status TEXT NOT NULL,
    root_identity TEXT NOT NULL UNIQUE,
    archive_schema TEXT NOT NULL,
    archive_path TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL,
    replay_fingerprint TEXT NOT NULL,
    terminal_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (observation_id, generation)
);
CREATE TRIGGER IF NOT EXISTS trg_observation_archives_no_update
    BEFORE UPDATE ON observation_archives BEGIN
    SELECT RAISE(ABORT, 'observation_archives is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_observation_archives_no_delete
    BEFORE DELETE ON observation_archives BEGIN
    SELECT RAISE(ABORT, 'observation_archives history is retained'); END;

CREATE TABLE IF NOT EXISTS lab_audit_outbox (
    audit_id TEXT PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    observation_id TEXT,
    generation INTEGER,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','mirrored','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    mirrored_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lab_audit_outbox_pending
    ON lab_audit_outbox(status, created_at, audit_id);
"""


__all__ = ["CANONICAL_SCHEMA_SQL"]

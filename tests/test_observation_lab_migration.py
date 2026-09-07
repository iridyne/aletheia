"""Focused proof for deterministic legacy Learning ledger import."""

from __future__ import annotations

import pytest

from aletheia.learning.ledger import insert_hypothesis
from aletheia.learning.models import LearningExperiment, LearningExposure, LearningHypothesis


def _seed_legacy_row(table, record):
    """Load historical SQLite evidence without reviving retired runtime writers."""
    from aletheia.storage.sqlite import get_db

    values = record.model_dump(mode="json")
    columns = ", ".join(values)
    placeholders = ", ".join("?" for _ in values)
    get_db().execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
        tuple(values.values()),
    )


def test_plan_is_read_only_and_apply_is_idempotent():
    from aletheia.config.loader import get_data_dir
    from aletheia.observation_lab.migration import (
        apply_legacy_learning_migration,
        plan_legacy_learning_migration,
    )
    from aletheia.observation_lab.db import get_lab_db

    hypothesis = LearningHypothesis(
        hypothesis_id="legacy-hypothesis",
        fingerprint="legacy-fingerprint",
        cohort_id="legacy-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="legacy",
        scope_json='{"scope":"all"}',
        action_json='{"value":2}',
        consumer="test",
        target_horizon="24h",
    )
    experiment = LearningExperiment(
        experiment_id="legacy-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        stage="shadow",
        assignment_salt="legacy-salt",
        started_at="2026-01-01T00:00:00+00:00",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", experiment)
    _seed_legacy_row("learning_exposures", LearningExposure(
        exposure_id="legacy-exposure",
        dedup_key="legacy-exposure-dedup",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment="control",
        consumer="test",
        consumer_stage="score",
        path_id=hypothesis.path_id,
        overlap_group=hypothesis.overlap_group,
        assignment_unit_key="unit-1",
        candidate_key="candidate-1",
        before_json='{"value":1}',
        after_json='{"value":1}',
        concurrent_policy_epoch="legacy-epoch",
        exposed_at="2026-01-01T00:00:00+00:00",
    ))

    lab_path = get_data_dir() / "observation-lab" / "lab.db"
    assert not lab_path.exists()
    plan = plan_legacy_learning_migration()
    assert plan["available"] is True
    assert plan["legacy_tables"]["learning_experiments"]["row_count"] == 1
    assert not lab_path.exists()

    applied = apply_legacy_learning_migration()
    assert applied["imported"]["windows"] == 1
    assert applied["imported"]["exposures"] == 1
    assert get_lab_db().execute(
        "SELECT authority_mode, status FROM observation_windows WHERE observation_id=?",
        ("legacy-learning:legacy-experiment",),
    ).fetchone()["authority_mode"] == "observe_only"
    exposure = get_lab_db().execute(
        "SELECT source_receipt_id, resolution_reason, authority_source FROM observation_exposures"
    ).fetchone()
    assert tuple(exposure) == ("legacy:legacy-exposure", "legacy_import", "legacy")
    second = apply_legacy_learning_migration()
    assert second["imported"]["windows"] == 1
    assert get_lab_db().execute(
        "SELECT COUNT(*) FROM observation_exposures"
    ).fetchone()[0] == 1


def test_migration_refuses_to_replace_an_imported_legacy_snapshot():
    from aletheia.observation_lab.migration import apply_legacy_learning_migration
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    hypothesis = LearningHypothesis(
        hypothesis_id="changed-source-hypothesis",
        fingerprint="changed-source-fingerprint",
        cohort_id="changed-source-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="legacy",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", LearningExperiment(
        experiment_id="changed-source-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="source-v1",
        started_at="2026-01-01T00:00:00+00:00",
    ))

    first = apply_legacy_learning_migration()
    assert first["idempotent"] is True
    old_fingerprint = get_lab_db().execute(
        "SELECT source_checkpoint_fingerprint FROM observation_import_cursors "
        "WHERE source_name='legacy_learning'"
    ).fetchone()[0]
    get_db().execute(
        "UPDATE learning_experiments SET assignment_salt='source-v2' "
        "WHERE experiment_id='changed-source-experiment'"
    )

    with pytest.raises(RuntimeError, match="legacy_import_source_changed"):
        apply_legacy_learning_migration()
    assert get_lab_db().execute(
        "SELECT source_checkpoint_fingerprint FROM observation_import_cursors "
        "WHERE source_name='legacy_learning'"
    ).fetchone()[0] == old_fingerprint
    from aletheia.observation_lab.canonical import get_authority_source, set_authority_source

    with pytest.raises(RuntimeError, match="authority_cutover_legacy_import_checkpoint_missing"):
        set_authority_source("lab", actor="owner", reason="changed source")
    assert get_authority_source().source == "legacy"


def test_filter_shadow_history_is_imported_and_closed_rows_are_archived(tmp_path):
    from aletheia.observation_lab.canonical import verify_generation_archive
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.migration import apply_legacy_learning_migration
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "INSERT INTO filter_shadow_experiments "
        "(id, experiment_key, created_at, started_at, filter_name, filter_version, stage, scanner_family, "
        "direction, market_type, horizon, control_parameter, treatment_parameter, status, min_samples, "
        "min_coverage, min_edge_delta_pct, source_replay, production_mutation_allowed, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "filter-legacy", "filter-key", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
            "volume", "v1", "shadow", "family", "long", "spot", "24h", "1", "2",
            "completed_no_support", 10, 0.5, 0.0, "{}", 0, "2026-01-02T00:00:00+00:00",
        ),
    )
    get_db().execute(
        "INSERT INTO filter_shadow_evaluations "
        "(id, experiment_id, created_at, evidence_through_at, disposition, note) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "filter-eval", "filter-legacy", "2026-01-02T00:00:00+00:00",
            "2026-01-02T00:00:00+00:00", "completed_no_support", "none",
        ),
    )
    result = apply_legacy_learning_migration(archive_root=str(tmp_path))
    assert result["imported"]["windows"] == 1
    assert result["imported"]["evaluations"] == 1
    archive = get_lab_db().execute(
        "SELECT archive_id FROM observation_archives WHERE observation_id=?",
        ("legacy-filter-shadow:filter-legacy",),
    ).fetchone()
    assert archive is not None
    assert verify_generation_archive(archive[0])["available"] is True


def test_legacy_retirement_rejects_corrupt_generation_archive(tmp_path):
    from aletheia.observation_lab.migration import apply_legacy_learning_migration
    from aletheia.storage.migrations import MigrationStateError, retire_legacy_experiment_tables
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import get_db

    hypothesis = LearningHypothesis(
        hypothesis_id="archive-proof-hypothesis",
        fingerprint="archive-proof-fingerprint",
        cohort_id="archive-proof-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="legacy",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", LearningExperiment(
        experiment_id="archive-proof-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        status="completed_no_support",
        assignment_salt="archive-proof-salt",
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-02T00:00:00+00:00",
    ))

    apply_legacy_learning_migration(archive_root=str(tmp_path))
    archive_path = get_lab_db().execute(
        "SELECT archive_path FROM observation_archives "
        "WHERE observation_id='legacy-learning:archive-proof-experiment'"
    ).fetchone()[0]
    with open(archive_path, "ab") as archive:
        archive.write(b"corrupt")

    with pytest.raises(MigrationStateError, match="archive_unverified"):
        retire_legacy_experiment_tables(get_db(), require_lab_authority=False)
    assert get_db().execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='learning_experiments'"
    ).fetchone() is not None


def test_explicit_retirement_requires_import_proof_and_drops_legacy_tables():
    from aletheia.observation_lab.canonical import get_authority_source
    from aletheia.observation_lab.migration import apply_legacy_learning_migration
    from aletheia.storage.sqlite import get_db

    hypothesis = LearningHypothesis(
        hypothesis_id="retire-hypothesis",
        fingerprint="retire-fingerprint",
        cohort_id="retire-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="legacy",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", LearningExperiment(
        experiment_id="retire-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="retire-salt",
        started_at="2026-01-01T00:00:00+00:00",
    ))

    result = apply_legacy_learning_migration(
        retire_legacy_schema=True,
        rollback_backup_identity="backup:retire-test",
    )

    assert result["legacy_schema_retirement"]["available"] is True
    assert get_authority_source().source == "lab"
    db = get_db()
    for table in ("learning_experiments", "learning_exposures", "filter_shadow_experiments"):
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is None


def test_legacy_exposure_import_preserves_explicit_manifest_effect_identity():
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.migration import apply_legacy_learning_migration

    hypothesis = LearningHypothesis(
        hypothesis_id="manifest-hypothesis",
        fingerprint="manifest-fingerprint",
        cohort_id="manifest-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="legacy",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    experiment = LearningExperiment(
        experiment_id="manifest-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="manifest-salt",
        started_at="2026-01-01T00:00:00+00:00",
    )
    _seed_legacy_row("learning_experiments", experiment)
    _seed_legacy_row("learning_exposures", LearningExposure(
        exposure_id="manifest-exposure",
        dedup_key="manifest-exposure-dedup",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment="treatment",
        consumer="test",
        consumer_stage="score",
        path_id=hypothesis.path_id,
        overlap_group=hypothesis.overlap_group,
        assignment_unit_key="unit-1",
        candidate_key="candidate-1",
        before_json='{"value":1}',
        after_json='{"value":2}',
        manifest_json=(
            '{"authority_applied":true,"behavior_changed":true,'
            '"manifest_id":"legacy-manifest","manifest_hash":"legacy-hash",'
            '"active_manifest_snapshot":["legacy-hash"],'
            '"manifest_set_fingerprint":"manifest-set",'
            '"revocation_snapshot_fingerprint":"revocation-set",'
            '"behavior_policy_epoch_ref":"policy-1"}'
        ),
        outcome_contract_version="v1",
        concurrent_policy_epoch="legacy-epoch",
        exposed_at="2026-01-01T00:00:00+00:00",
    ))

    apply_legacy_learning_migration()
    row = get_lab_db().execute(
        "SELECT manifest_id, manifest_hash, applied, behavior_changed, "
        "active_manifest_snapshot_json, manifest_set_fingerprint, "
        "revocation_snapshot_fingerprint, behavior_policy_epoch_ref "
        "FROM observation_exposures WHERE source_receipt_id='legacy:manifest-exposure'"
    ).fetchone()
    assert tuple(row) == (
        "legacy-manifest", "legacy-hash", 1, 1, '["legacy-hash"]',
        "manifest-set", "revocation-set", "policy-1",
    )


def test_retired_strategy_and_refrozen_history_cannot_reopen_authority():
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.observation_lab.migration import apply_legacy_learning_migration
    from aletheia.storage.sqlite import get_db

    hypothesis = LearningHypothesis(
        hypothesis_id="refrozen-hypothesis",
        fingerprint="refrozen-fingerprint",
        cohort_id="refrozen-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="legacy",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", LearningExperiment(
        experiment_id="refrozen-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        stage="paper_10",
        assignment_salt="legacy-salt",
        status="refrozen",
        started_at="2026-01-01T00:00:00+00:00",
    ))
    get_db().execute(
        "INSERT INTO strategy_records (id, name) VALUES ('retired-strategy', 'retired')"
    )
    get_db().execute(
        "INSERT INTO strategy_experiments (id, strategy_id, experiment_type) "
        "VALUES ('retired-experiment', 'retired-strategy', 'legacy')"
    )

    result = apply_legacy_learning_migration()
    assert result["unprovable_rows"] >= 1
    window = get_lab_db().execute(
        "SELECT status, terminal_at FROM observation_windows "
        "WHERE observation_id='legacy-learning:refrozen-experiment'"
    ).fetchone()
    assert tuple(window) == ("invalidated", window[1])
    assert window[1] is not None
    nomination = get_lab_db().execute(
        "SELECT disposition, rejection_reason, snapshot_json FROM observation_nominations "
        "WHERE source_nomination_id='legacy:strategy:retired-experiment'"
    ).fetchone()
    assert nomination["disposition"] == "rejected"
    assert nomination["rejection_reason"] == "retired_strategy_engine"
    assert "retired-experiment" in nomination["snapshot_json"]


def test_lab_cutover_refuses_unimported_legacy_rows():
    from aletheia.learning.ledger import insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis
    from aletheia.observation_lab.canonical import set_authority_source

    hypothesis = LearningHypothesis(
        hypothesis_id="cutover-block-hypothesis",
        fingerprint="cutover-block-fingerprint",
        cohort_id="cutover-block-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="test",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", LearningExperiment(
        experiment_id="cutover-block-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        stage="shadow",
        assignment_salt="cutover-block-salt",
        started_at="2026-01-01T00:00:00+00:00",
    ))

    with pytest.raises(RuntimeError, match="authority_cutover_legacy_import_checkpoint_missing"):
        set_authority_source("lab", actor="test", reason="must import first")


def test_explicit_lab_retirement_drops_legacy_runtime_and_restores_foreign_keys():
    from aletheia.observation_lab.canonical import set_authority_source
    from aletheia.storage.migrations import (
        _LEGACY_EXPERIMENT_DROP_ORDER,
        retire_legacy_experiment_tables,
    )
    from aletheia.storage.sqlite import get_db

    set_authority_source("lab", actor="test", reason="empty schema cutover")
    db = get_db()
    db.execute("PRAGMA foreign_keys=ON")
    result = retire_legacy_experiment_tables()
    assert result["available"] is True
    assert result["dropped"]
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    for table in _LEGACY_EXPERIMENT_DROP_ORDER:
        assert db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is None


def test_nonempty_lab_cutover_requires_and_records_complete_proof():
    from aletheia.observation_lab.canonical import (
        build_authority_cutover_proof,
        set_authority_source,
    )
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
    )
    from aletheia.observation_lab.migration import apply_legacy_learning_migration
    from aletheia.storage.sqlite import get_db

    hypothesis = LearningHypothesis(
        hypothesis_id="proof-hypothesis",
        fingerprint="proof-fingerprint",
        cohort_id="proof-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="test",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", LearningExperiment(
        experiment_id="proof-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        stage="shadow",
        assignment_salt="proof-salt",
        started_at="2026-01-01T00:00:00+00:00",
    ))
    apply_legacy_learning_migration()

    with pytest.raises(RuntimeError, match="authority_cutover_proof_required"):
        set_authority_source("lab", actor="owner", reason="proof required")

    save_joint_checkpoint(compute_joint_checkpoint(notes="cutover-proof-test"))
    proof = build_authority_cutover_proof(rollback_backup_identity="backup-test")
    state = set_authority_source(
        "lab", actor="owner", reason="complete proof", cutover_proof=proof,
    )
    assert state.source == "lab"
    row = get_db().execute(
        "SELECT authority_source, migration_plan_fingerprint, retention_claim_fingerprint, "
        "checkpoint_fingerprint, runtime_commit, rollback_backup_identity "
        "FROM observation_authority_cutover_proofs"
    ).fetchone()
    assert tuple(row) == (
        "lab",
        proof["migration_plan_fingerprint"],
        proof["retention_claim_fingerprint"],
        proof["checkpoint_fingerprint"],
        proof["runtime_commit"],
        "backup-test",
    )


def test_authority_cli_refuses_legacy_even_with_the_recorded_cutover_proof():
    from typer.testing import CliRunner

    from aletheia.cli.observe_cmd import observe_app
    from aletheia.observation_lab.canonical import (
        build_authority_cutover_proof,
        get_authority_source,
        set_authority_source,
    )
    from aletheia.observation_lab.checkpoint import (
        compute_joint_checkpoint,
        save_joint_checkpoint,
    )
    from aletheia.observation_lab.migration import apply_legacy_learning_migration

    hypothesis = LearningHypothesis(
        hypothesis_id="cli-rollback-hypothesis",
        fingerprint="cli-rollback-fingerprint",
        cohort_id="cli-rollback-cohort",
        path_id="ranker_case_score",
        overlap_group="ranker",
        source_type="test",
        scope_json="{}",
        action_json="{}",
        consumer="test",
        target_horizon="24h",
    )
    insert_hypothesis(hypothesis)
    _seed_legacy_row("learning_experiments", LearningExperiment(
        experiment_id="cli-rollback-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        stage="shadow",
        assignment_salt="cli-rollback-salt",
        started_at="2026-01-01T00:00:00+00:00",
    ))
    apply_legacy_learning_migration()
    save_joint_checkpoint(compute_joint_checkpoint(notes="cli-rollback"))
    proof = build_authority_cutover_proof(rollback_backup_identity="backup-cli")
    set_authority_source(
        "lab", actor="owner", reason="cli rollback setup", cutover_proof=proof,
    )

    result = CliRunner().invoke(
        observe_app,
        [
            "set-experiment-authority", "legacy", "--reason", "validated rollback",
            "--runtime-commit", proof["runtime_commit"],
            "--rollback-backup-identity", "backup-cli", "--json",
        ],
    )

    assert result.exit_code == 1
    assert "authority_source_must_be_lab" in result.stderr
    assert get_authority_source().source == "lab"

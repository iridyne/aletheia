"""Focused proof for deterministic legacy Learning ledger import."""

from __future__ import annotations

from aletheia.learning.ledger import insert_experiment, insert_exposure, insert_hypothesis
from aletheia.learning.models import LearningExperiment, LearningExposure, LearningHypothesis


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
    insert_experiment(experiment)
    insert_exposure(LearningExposure(
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
    insert_experiment(LearningExperiment(
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

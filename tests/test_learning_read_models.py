"""Focused tests for the read-only lco-v1 owner projection."""

from __future__ import annotations

import json
from pathlib import Path


def _setup_env(tmp_path: Path, monkeypatch) -> None:
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import reset_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._config_cache = None
    reset_db()
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _seed_current_experiment(monkeypatch) -> tuple[str, str]:
    from aletheia.agent_runtime import build_info
    from aletheia.learning.experiments import _policy_epoch
    from aletheia.learning.ledger import (
        insert_experiment,
        insert_hypothesis,
        insert_record,
    )
    from aletheia.learning.models import (
        LearningContributionVerdict,
        LearningExperiment,
        LearningHypothesis,
        LearningJobHealth,
    )
    from aletheia.learning.verdicts import METRIC_VERSION

    monkeypatch.setattr(build_info, "_boot_commit", "commit-current")
    hypothesis = LearningHypothesis(
        fingerprint="lco-fingerprint",
        cohort_id="lco-cohort",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="filter_candidate_outcomes:clean_net:v1",
        scope_json='{"family":"momentum"}',
        action_json='{"type":"opportunity_multiplier"}',
        consumer="universe_allocator",
        target_horizon="1h",
        status="shadow",
        effect_threshold=0.2,
    )
    experiment = LearningExperiment(
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt="lco-salt",
        status="active",
        started_at="2026-07-24T00:00:00+00:00",
        min_samples_per_arm=10,
        min_market_days=1,
    )
    assert insert_hypothesis(hypothesis)
    assert insert_experiment(experiment)
    epoch = _policy_epoch(hypothesis.path_id)
    verdict = LearningContributionVerdict(
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage="shadow",
        window_start="2026-07-24T00:00:00+00:00",
        window_end="2026-07-24T01:00:00+00:00",
        unique_treatment_n=4,
        unique_control_n=5,
        baseline=-0.1,
        observed=0.2,
        delta=0.3,
        confidence_low=0.01,
        confidence_high=0.5,
        harm_metrics_json=json.dumps({
            "raw_exposure_n": 18,
            "unique_assignment_unit_n": 9,
            "cluster_member_n": 27,
            "allocation_selected_member_n": 7,
            "mirrored_universe_row_n": 7,
            "outcome_source_member_counts": {
                "filter_candidate_outcomes": 27,
            },
            "allocation_selection_source_member_counts": {
                "filter_candidate_outcomes": 27,
            },
            "exclusion_reasons": {"unsettled": 2},
            "invalid_reasons": {},
            "evidence_conflict_fingerprints": [],
            "breached": False,
            "breach_reasons": [],
        }),
        verdict="insufficient_data",
        reason="no_actual_intervention",
        policy_epoch=epoch,
        target_horizon="1h",
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        code_commit="commit-current",
    )
    assert insert_record(
        "learning_contribution_verdicts",
        verdict,
        ignore_duplicates=True,
    )
    health = LearningJobHealth(
        job_id="learning_contribution_evaluator",
        schedule_key="interval:60m",
        status="healthy",
        last_succeeded_at="2026-07-24T01:00:00+00:00",
        last_failed_at="2026-07-23T23:00:00+00:00",
        last_failure_reason=(
            "sqlite OperationalError at /private/local/path: database unavailable"
        ),
        input_watermark="old-window",
    )
    assert insert_record("learning_job_health", health, ignore_duplicates=True)
    return hypothesis.hypothesis_id, experiment.experiment_id


def test_lco_projection_separates_measurement_integrity_and_authority(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    hypothesis_id, experiment_id = _seed_current_experiment(monkeypatch)

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    result = build_learning_contribution_projection()
    assert result["schema_version"] == "lco-v1"
    assert result["availability"] == "available"
    item = result["items"][0]
    assert item["hypothesis_id"] == hypothesis_id
    assert item["experiment_id"] == experiment_id
    assert item["current_identity"]["matches_running_cohort"] is True
    assert item["measurement"]["state"] == "insufficient_data"
    assert item["samples"] == {
        "raw_exposures": 18,
        "treatment_assignment_units": 4,
        "control_assignment_units": 5,
        "required_per_arm": 10,
        "complete_assignment_units": 9,
        "cluster_members": 27,
        "selected_members": 7,
    }
    assert item["integrity"]["outcome_source_member_counts"] == {
        "filter_candidate_outcomes": 27,
    }
    assert item["lifecycle"]["recommended_action"] == "admit_trial"
    assert item["lifecycle"]["mutation_available"] is False
    assert item["lifecycle"]["authority_enabled"] is False


def test_lco_projection_keeps_stale_verdict_historical(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "UPDATE learning_contribution_verdicts SET code_commit='old-commit' "
        "WHERE experiment_id=?",
        (experiment_id,),
    )
    get_db().commit()

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    item = build_learning_contribution_projection()["items"][0]
    assert item["measurement"]["state"] == "not_measured"
    assert item["measurement"]["reason"] == "code_commit_mismatch"
    assert item["latest_historical_measurement"]["state"] == "insufficient_data"
    assert item["latest_historical_measurement"]["current"] is False


def test_lco_projection_sanitizes_failure_and_is_side_effect_free(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    tables = (
        "learning_hypotheses",
        "learning_experiments",
        "learning_contribution_verdicts",
        "learning_job_health",
    )
    before = {
        table: get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    result = build_learning_contribution_projection()
    after = {
        table: get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    assert after == before
    evaluator = result["operations"]["evaluator"]
    assert evaluator["last_failure_code"] == "database_unavailable"
    assert "last_error" not in evaluator
    assert "last_failure_reason" not in evaluator
    assert "/private/local/path" not in json.dumps(result)
    universe = next(
        path
        for path in result["paths"]
        if path["path_id"] == "universe_family_calibration"
    )
    assert universe["consumer_wired"] is True
    assert universe["settings_supported"] is True
    assert universe["measurement_supported"] is True


def test_unavailable_lco_envelope_does_not_invent_empty_success():
    from aletheia.learning.read_models import (
        unavailable_learning_contribution_projection,
    )

    result = unavailable_learning_contribution_projection(
        "projection_read_failed"
    )
    assert result["availability"] == "unavailable"
    assert result["unavailable_reason"] == "projection_read_failed"
    assert result["data_freshness"]["state"] == "unavailable"
    assert result["operations"] is None

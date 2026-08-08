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
    setattr(sq, "_db_conn", conn)
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
    from aletheia.learning.behavior_contracts import current_behavior_contract_hash

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
    behavior_contract_hash = current_behavior_contract_hash(
        path_id=hypothesis.path_id,
        policy_epoch=epoch,
        target_horizon=hypothesis.target_horizon,
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
    )
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
            "evidence_conflict_fingerprints": ["conflict-fingerprint"],
            "evidence_conflicts": [{
                "reason": "conflicting_mirrored_outcome",
                "outcome_candidate_key": "candidate:1h",
                "canonical": {
                    "source": "filter_candidate_outcomes",
                    "row_id": 41,
                },
                "mirrors": [{
                    "source": "universe_forward_outcomes",
                    "row_id": 82,
                }],
                "differing_fields": {
                    "net_return_pct": {"canonical": 1.0, "mirrors": [-1.0]},
                },
            }],
            "evidence_conflict_total_n": 1,
            "evidence_conflict_provenance_truncated": False,
            "harm_state": "indeterminate",
            "harm_indeterminate_reason": "insufficient_samples",
            "assigned_treatment_n": 4,
            "authority_applied_treatment_n": 0,
            "actual_intervention_treatment_n": 0,
            "baseline_treatment_n": 4,
            "actual_intervention_state": "none",
            "actual_intervention_reason": "no_actual_intervention",
            "treatment_authority_rejection_reasons": {
                "experiment_stage_not_paper": 4,
            },
            "treatment_non_intervention_reasons": {
                "authority:experiment_stage_not_paper": 4,
            },
            "breached": False,
            "breach_reasons": [],
            "market_day_coverage": {
                "basis": "eligible_assignment_unit_exposure_utc_day_either_arm",
                "status": "gap",
                "required_consecutive_days": 7,
                "max_allowed_missing_days": 0,
                "outage_waiver": "unsupported_fail_closed",
                "first_observed_day": "2026-07-17",
                "last_observed_day": "2026-07-24",
                "current_window_start": "2026-07-24",
                "current_window_end": "2026-07-24",
                "current_consecutive_market_days": 1,
                "longest_consecutive_market_days": 1,
                "observed_treatment_day_n": 2,
                "observed_control_day_n": 2,
                "qualified_treatment_n": 1,
                "qualified_control_n": 1,
                "invalid_exposure_time_n": 0,
                "missing_treatment_day_n": 6,
                "missing_control_day_n": 6,
                "missing_market_day_n": 6,
                "missing_treatment_days": ["2026-07-18"],
                "missing_control_days": ["2026-07-18"],
                "missing_market_days": ["2026-07-18"],
                "missing_days_truncated": True,
            },
            "source_code_commits": ["commit-current"],
            "source_code_commit_unknown_n": 0,
            "code_commit_provenance_complete": True,
        }),
        verdict="insufficient_data",
        reason="no_actual_intervention",
        policy_epoch=epoch,
        target_horizon="1h",
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        code_commit="commit-current",
        behavior_contract_hash=behavior_contract_hash,
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
    assert item["measurement"]["coverage"]["status"] == "gap"
    assert item["measurement"]["coverage"]["missing_market_day_n"] == 6
    assert item["measurement"]["coverage"]["missing_market_days"] == [
        "2026-07-18"
    ]
    assert item["samples"] == {
        "raw_exposures": 18,
        "treatment_assignment_units": 4,
        "assigned_treatment_units": 4,
        "authority_applied_treatment_units": 0,
        "actual_intervention_treatment_units": 0,
        "baseline_treatment_units": 4,
        "control_assignment_units": 5,
        "required_per_arm": 10,
        "complete_assignment_units": 9,
        "cluster_members": 27,
        "selected_members": 7,
    }
    assert item["integrity"]["outcome_source_member_counts"] == {
        "filter_candidate_outcomes": 27,
    }
    assert item["integrity"]["harm_state"] == "indeterminate"
    assert item["integrity"]["harm_indeterminate_reason"] == "insufficient_samples"
    assert item["integrity"]["actual_intervention_state"] == "none"
    assert item["integrity"]["actual_intervention_reason"] == (
        "no_actual_intervention"
    )
    assert item["integrity"]["treatment_authority_rejection_reasons"] == {
        "experiment_stage_not_paper": 4,
    }
    assert item["integrity"]["treatment_non_intervention_reasons"] == {
        "authority:experiment_stage_not_paper": 4,
    }
    assert item["lifecycle"]["recommended_action"] == "admit_trial"
    assert item["lifecycle"]["mutation_available"] is False
    assert item["lifecycle"]["authority_enabled"] is False


def test_lco_projection_reports_resolved_treatment_authority(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from aletheia.learning.authority import AuthorityDecision
    import aletheia.learning.authority as authority

    monkeypatch.setattr(
        authority,
        "resolve_learning_authority",
        lambda *_args, **_kwargs: AuthorityDecision(
            True,
            "current_stage_measurement_healthy",
            "paper_10",
            0.1,
            "activation-verdict",
            "current-verdict",
        ),
    )
    from aletheia.learning.read_models import build_learning_contribution_projection

    lifecycle = build_learning_contribution_projection()["items"][0]["lifecycle"]

    assert lifecycle["authority_enabled"] is True
    assert lifecycle["authority_block_reason"] is None


def test_lco_projection_holds_positive_with_insufficient_actual_interventions(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    db = get_db()
    metrics = json.loads(db.execute(
        "SELECT harm_metrics_json FROM learning_contribution_verdicts "
        "WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()[0])
    metrics.update({
        "harm_state": "clear",
        "harm_indeterminate_reason": None,
        "assigned_treatment_n": 10,
        "authority_applied_treatment_n": 1,
        "actual_intervention_treatment_n": 1,
        "baseline_treatment_n": 9,
        "actual_intervention_state": "partial",
        "treatment_authority_rejection_reasons": {
            "test_authority_rejection": 9,
        },
        "treatment_non_intervention_reasons": {
            "authority:test_authority_rejection": 9,
        },
    })
    db.execute(
        "UPDATE learning_experiments SET stage='paper_10' WHERE experiment_id=?",
        (experiment_id,),
    )
    db.execute(
        "UPDATE learning_contribution_verdicts SET stage='paper_10', verdict='positive', "
        "reason='apparent positive', unique_treatment_n=10, harm_metrics_json=? "
        "WHERE experiment_id=?",
        (json.dumps(metrics), experiment_id),
    )
    db.commit()

    from aletheia.learning.read_models import build_learning_contribution_projection

    lifecycle = build_learning_contribution_projection()["items"][0]["lifecycle"]

    assert lifecycle["recommended_action"] == "hold"
    assert lifecycle["recommendation_reason"] == (
        "insufficient_actual_intervention_samples"
    )


def test_lco_projection_marks_authority_read_failure_partial(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    import aletheia.learning.authority as authority

    monkeypatch.setattr(
        authority,
        "resolve_learning_authority",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private")),
    )
    from aletheia.learning.read_models import build_learning_contribution_projection

    result = build_learning_contribution_projection()

    assert result["availability"] == "partial"
    assert result["path_states"][0]["availability"] == "partial"
    assert result["path_states"][0]["unavailable_reason"] == (
        "authority_projection_failed"
    )
    assert result["items"][0]["availability"] == "partial"
    assert result["items"][0]["lifecycle"]["authority_block_reason"] == (
        "authority_projection_failed"
    )


def test_lco_projection_keeps_measurement_when_operations_read_fails(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    import aletheia.learning.control as control

    monkeypatch.setattr(
        control,
        "get_learning_job_health",
        lambda _job_id: (_ for _ in ()).throw(RuntimeError("private operations failure")),
    )
    from aletheia.learning.read_models import build_learning_contribution_projection

    result = build_learning_contribution_projection()

    assert result["availability"] == "partial"
    assert result["unavailable_reason"] == "operations_projection_failed"
    assert result["operations"]["availability"] == "partial"
    assert result["operations"]["query_errors"] == {
        "evaluator": "read_failed",
        "lifecycle_audit": "read_failed",
    }
    assert result["operations"]["evaluator"] is None
    assert result["operations"]["lifecycle_audit"] is None
    assert result["operations"]["config"]["paper_influence_enabled"] is True
    assert result["data_freshness"]["state"] == "unavailable"
    assert result["items"]
    assert "private operations failure" not in str(result)


def test_lco_projection_preserves_evaluator_when_audit_health_read_fails(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    import aletheia.learning.control as control

    real_get_health = control.get_learning_job_health

    def get_health(job_id):
        if job_id == "learning_lifecycle_audit_reconciler":
            raise RuntimeError("private audit failure")
        return real_get_health(job_id)

    monkeypatch.setattr(control, "get_learning_job_health", get_health)
    from aletheia.learning.read_models import build_learning_contribution_projection

    result = build_learning_contribution_projection()

    assert result["availability"] == "partial"
    assert result["operations"]["evaluator"] is not None
    assert result["operations"]["lifecycle_audit"] is None
    assert result["operations"]["query_errors"] == {
        "lifecycle_audit": "read_failed",
    }
    assert result["data_freshness"]["state"] != "unavailable"


def test_lco_projection_preserves_evaluator_when_catchup_read_fails(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    import aletheia.learning.control as control

    monkeypatch.setattr(
        control,
        "measurement_catchup_due",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private catchup failure")
        ),
    )
    from aletheia.learning.read_models import build_learning_contribution_projection

    result = build_learning_contribution_projection()

    assert result["availability"] == "partial"
    assert result["operations"]["evaluator"] is not None
    assert result["operations"]["evaluator"]["catchup_due"] is None
    assert result["operations"]["query_errors"] == {
        "evaluator_catchup": "read_failed",
    }
    assert result["data_freshness"]["state"] != "unavailable"
    assert "private catchup failure" not in str(result)


def test_lco_freshness_uses_configured_evaluation_cadence(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.learning.evaluation_interval_minutes = 180
    save_config(cfg)
    from aletheia.learning.read_models import build_learning_contribution_projection

    result = build_learning_contribution_projection()

    assert result["data_freshness"]["stale_after_sec"] == 21_600
    assert result["path_states"][0]["data_freshness"]["stale_after_sec"] == 21_600
    assert result["items"][0]["data_freshness"]["stale_after_sec"] == 21_600


def test_lco_projection_keeps_one_current_item_per_measurable_path(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis
    from aletheia.learning.read_models import build_learning_contribution_projection

    operator = LearningHypothesis(
        fingerprint="lco-operator-fingerprint",
        cohort_id="lco-operator-cohort",
        path_id="operator_learning_bundle",
        overlap_group="operator",
        source_type="operator_learning_bundle:v1",
        scope_json="{}",
        action_json="{}",
        consumer="system_operator",
        target_horizon="4h",
        status="shadow",
    )
    assert insert_hypothesis(operator)
    assert insert_experiment(LearningExperiment(
        experiment_id="operator-current-experiment",
        hypothesis_id=operator.hypothesis_id,
        assignment_salt="operator-salt",
        status="active",
        created_at="2026-07-01T00:00:00+00:00",
    ))
    # Spare query capacity must not let unsupported historical experiments leak
    # back into the measured owner projection.
    for index in range(1):
        universe = LearningHypothesis(
            fingerprint=f"newer-universe-fingerprint-{index}",
            cohort_id=f"newer-universe-cohort-{index}",
            path_id="universe_family_calibration",
            overlap_group="universe",
            source_type="filter_candidate_outcomes:clean_net:v1",
            scope_json="{}",
            action_json="{}",
            consumer="universe_allocator",
            target_horizon="1h",
            status="shadow",
        )
        assert insert_hypothesis(universe)
        assert insert_experiment(LearningExperiment(
            experiment_id=f"newer-universe-experiment-{index}",
            hypothesis_id=universe.hypothesis_id,
            assignment_salt=f"newer-universe-{index}",
            status="active",
            created_at=f"2026-07-25T00:{index:02d}:00+00:00",
        ))

    result = build_learning_contribution_projection()
    assert {item["path_id"] for item in result["items"]} == {
        "universe_family_calibration",
    }
    assert {
        state["path_id"]: state["state"] for state in result["path_states"]
    } == {"universe_family_calibration": "active"}
    latest_by_path = {
        item["path_id"]: item
        for item in reversed(result["items"])
    }
    states_by_path = {
        state["path_id"]: state for state in result["path_states"]
    }
    latest_universe_id = latest_by_path["universe_family_calibration"][
        "experiment_id"
    ]
    for path_id in states_by_path:
        assert states_by_path[path_id]["data_freshness"] == (
            latest_by_path[path_id]["data_freshness"]
        )

    import aletheia.learning.read_models as read_models
    real_project = read_models._project_experiment

    def fail_latest_universe(row, **kwargs):
        if row["experiment_id"] == latest_universe_id:
            raise ValueError("test latest projection failure")
        return real_project(row, **kwargs)

    monkeypatch.setattr(read_models, "_project_experiment", fail_latest_universe)
    latest_failed = build_learning_contribution_projection()
    latest_states = {
        state["path_id"]: state for state in latest_failed["path_states"]
    }
    assert latest_states["universe_family_calibration"]["availability"] == (
        "unavailable"
    )


def test_lco_projection_reports_protected_admission_readiness(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    db = get_db()
    metrics = json.loads(
        db.execute(
            "SELECT harm_metrics_json FROM learning_contribution_verdicts "
            "WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()[0]
    )
    metrics["harm_state"] = "clear"
    metrics["harm_indeterminate_reason"] = None
    db.execute(
        "UPDATE learning_contribution_verdicts SET harm_metrics_json=? "
        "WHERE experiment_id=?",
        (json.dumps(metrics), experiment_id),
    )
    db.commit()

    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "trial_admission_gate_reason",
        lambda _path_id, **_kwargs: None,
    )
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    lifecycle_view = build_learning_contribution_projection()["items"][0]["lifecycle"]
    assert lifecycle_view["recommended_action"] == "admit_trial"
    assert lifecycle_view["authority_enabled"] is False
    assert lifecycle_view["mutation_available"] is True
    assert lifecycle_view["mutation_unavailable_reason"] is None


def test_lco_projection_never_offers_admission_with_indeterminate_harm(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "trial_admission_gate_reason",
        lambda _path_id, **_kwargs: None,
    )
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    lifecycle_view = build_learning_contribution_projection()["items"][0]["lifecycle"]

    assert lifecycle_view["recommended_action"] == "admit_trial"
    assert lifecycle_view["mutation_available"] is False
    assert lifecycle_view["mutation_unavailable_reason"] == "harm_gate_indeterminate"


def test_lco_projection_never_offers_admission_for_malformed_measurement_metadata(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    db = get_db()
    db.execute(
        "UPDATE learning_contribution_verdicts SET harm_metrics_json=? "
        "WHERE experiment_id=?",
        ("{malformed", experiment_id),
    )
    db.commit()

    import aletheia.learning.lifecycle as lifecycle

    monkeypatch.setattr(
        lifecycle,
        "trial_admission_gate_reason",
        lambda _path_id, **_kwargs: None,
    )
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    lifecycle_view = build_learning_contribution_projection()["items"][0]["lifecycle"]

    assert lifecycle_view["recommended_action"] == "admit_trial"
    assert lifecycle_view["mutation_available"] is False
    assert lifecycle_view["mutation_unavailable_reason"] == (
        "measurement_metadata_invalid"
    )


def test_lco_projection_keeps_stale_behavior_verdict_historical(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "UPDATE learning_contribution_verdicts SET behavior_contract_hash='old-behavior' "
        "WHERE experiment_id=?",
        (experiment_id,),
    )
    get_db().commit()

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    item = build_learning_contribution_projection()["items"][0]
    assert item["measurement"]["state"] == "not_measured"
    assert item["measurement"]["reason"] == "behavior_contract_mismatch"
    assert item["latest_historical_measurement"]["state"] == "insufficient_data"
    assert item["latest_historical_measurement"]["current"] is False


def test_lco_projection_keeps_matching_behavior_current_across_commits(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    get_db().execute(
        "UPDATE learning_contribution_verdicts SET code_commit='old-commit' "
        "WHERE experiment_id=?",
        (experiment_id,),
    )
    get_db().commit()

    from aletheia.learning.read_models import build_learning_contribution_projection

    item = build_learning_contribution_projection()["items"][0]

    assert item["measurement"]["state"] == "insufficient_data"
    assert item["current_identity"]["matches_running_cohort"] is True
    assert item["latest_historical_measurement"]["code_commit"] == "old-commit"


def test_lco_projection_selects_current_verdict_before_newer_historical_row(
    tmp_path, monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningContributionVerdict
    from aletheia.storage.sqlite import get_db

    current = dict(get_db().execute(
        "SELECT * FROM learning_contribution_verdicts WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone())
    historical = LearningContributionVerdict(**{
        **current,
        "verdict_id": "newer-historical-verdict",
        "behavior_contract_hash": "historical-behavior",
        "code_commit": "historical-build",
        "verdict": "negative",
        "reason": "historical negative",
        "created_at": "2099-07-24T02:00:00+00:00",
    })
    assert insert_record("learning_contribution_verdicts", historical)

    from aletheia.learning.read_models import build_learning_contribution_projection

    item = build_learning_contribution_projection()["items"][0]

    assert item["measurement"]["state"] == "insufficient_data"
    assert item["measurement"]["reason"] == "no_actual_intervention"
    assert item["current_identity"]["matches_running_cohort"] is True
    assert item["latest_historical_measurement"]["verdict_id"] == historical.verdict_id
    assert item["latest_historical_measurement"]["current"] is False
    assert item["latest_historical_measurement"]["mismatch_reason"] == (
        "behavior_contract_mismatch"
    )


def test_lco_projection_never_publishes_a_stale_verdicts_numbers_as_current(
    tmp_path,
    monkeypatch,
):
    """A superseded verdict's aggregates must not sit beside its zeroed arm counts.

    ``measurement.*`` was gated on identity but ``samples.*`` and ``integrity.*``
    were not, so a single item could report ``state=not_measured`` with ``arms
    0/0`` and, from that very same superseded row, ``harm_breached=true`` and
    ``raw_exposures=41``.  Nobody reading that can tell whether a live harm signal
    exists.  Gate all three blocks together and keep the facts in the block that
    is labelled not-current.
    """
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db

    db = get_db()
    metrics = json.loads(
        db.execute(
            "SELECT harm_metrics_json FROM learning_contribution_verdicts "
            "WHERE experiment_id=?",
            (experiment_id,),
        ).fetchone()[0]
    )
    metrics["breached"] = True
    metrics["breach_reasons"] = ["mae_p95_worsening"]
    db.execute(
        "UPDATE learning_contribution_verdicts SET behavior_contract_hash='old-behavior', "
        "harm_metrics_json=? WHERE experiment_id=?",
        (json.dumps(metrics), experiment_id),
    )
    db.commit()

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    item = build_learning_contribution_projection()["items"][0]
    assert item["current_identity"]["mismatch_reason"] == "behavior_contract_mismatch"
    assert item["measurement"]["state"] == "not_measured"

    samples = item["samples"]
    integrity = item["integrity"]
    assert samples["treatment_assignment_units"] == 0
    assert samples["control_assignment_units"] == 0
    assert samples["raw_exposures"] == 0
    assert samples["complete_assignment_units"] == 0
    assert samples["cluster_members"] == 0
    assert samples["selected_members"] == 0
    assert integrity["harm_breached"] is False
    assert integrity["breach_reasons"] == []
    assert integrity["matching_mirror_rows"] == 0
    assert integrity["outcome_source_member_counts"] == {}
    # The gate's own requirement is an experiment fact, not a verdict fact.
    assert samples["required_per_arm"] == 10

    # Nothing is hidden: the block labelled not-current carries every fact.
    historical = item["latest_historical_measurement"]
    assert historical["current"] is False
    assert historical["harm_breached"] is True
    assert historical["breach_reasons"] == ["mae_p95_worsening"]
    assert historical["raw_exposures"] == 18
    assert historical["complete_assignment_units"] == 9
    assert historical["treatment_assignment_units"] == 4
    assert historical["control_assignment_units"] == 5
    assert historical["window_end"] == "2026-07-24T01:00:00+00:00"


def test_lco_projection_reads_the_newest_evaluation_not_the_latest_window(
    tmp_path,
    monkeypatch,
):
    """The 1970 sentinel window must not make a superseded verdict look current.

    The projection keeps its own copy of the latest-verdict selector, so it needs
    the same evaluation-order guarantee as get_latest_measurement_verdict: a cohort
    that empties out stamps the sentinel window, which sorts after every real
    window and silently re-elected the previous row.
    """
    _setup_env(tmp_path, monkeypatch)
    _, experiment_id = _seed_current_experiment(monkeypatch)

    from aletheia.learning.experiments import _policy_epoch
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.learning.models import LearningContributionVerdict
    from aletheia.learning.verdicts import METRIC_VERSION

    from aletheia.storage.sqlite import get_db

    seeded_id = _seeded_verdict_id(experiment_id)
    db = get_db()
    # The seed leaves created_at at "now"; pin it so evaluation order is explicit
    # and the sentinel window is the only thing that could reorder these two.
    db.execute(
        "UPDATE learning_contribution_verdicts SET created_at=? WHERE verdict_id=?",
        ("2026-07-24T02:00:00+00:00", seeded_id),
    )
    db.commit()
    superseded = get_record("learning_contribution_verdicts", seeded_id)
    assert superseded is not None
    emptied = LearningContributionVerdict(
        experiment_id=experiment_id,
        hypothesis_id=superseded["hypothesis_id"],
        stage="shadow",
        window_start="1970-01-01T00:00:00+00:00",
        window_end="1970-01-01T00:00:00+00:00",
        unique_treatment_n=0,
        unique_control_n=0,
        harm_metrics_json=json.dumps({"breached": False, "breach_reasons": []}),
        verdict="insufficient_data",
        reason="insufficient unique prospective treatment/control samples",
        created_at="2026-07-24T09:00:00+00:00",
        policy_epoch=_policy_epoch("universe_family_calibration"),
        target_horizon="1h",
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        code_commit="commit-current",
        behavior_contract_hash=superseded["behavior_contract_hash"],
    )
    assert insert_record(
        "learning_contribution_verdicts", emptied, ignore_duplicates=True
    )

    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    item = build_learning_contribution_projection()["items"][0]
    assert item["latest_historical_measurement"]["verdict_id"] == emptied.verdict_id
    assert item["samples"]["treatment_assignment_units"] == 0
    assert item["samples"]["control_assignment_units"] == 0


def _seeded_verdict_id(experiment_id: str) -> str:
    from aletheia.storage.sqlite import get_db

    return str(
        get_db()
        .execute(
            "SELECT verdict_id FROM learning_contribution_verdicts "
            "WHERE experiment_id=?",
            (experiment_id,),
        )
        .fetchone()[0]
    )


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
        "learning_exposures",
        "learning_contribution_verdicts",
        "learning_lifecycle_events",
        "learning_job_health",
        "learning_policy_epochs",
    )
    before = {
        table: [
            dict(row)
            for row in get_db().execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
        ]
        for table in tables
    }
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    result = build_learning_contribution_projection()
    after = {
        table: [
            dict(row)
            for row in get_db().execute(
                f"SELECT * FROM {table} ORDER BY rowid"
            ).fetchall()
        ]
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


def test_lco_projection_exposes_lifecycle_audit_degradation(tmp_path, monkeypatch):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)

    from aletheia.learning.control import record_lifecycle_audit_result
    from aletheia.learning.read_models import build_learning_contribution_projection

    record_lifecycle_audit_result(pending=1, failed=1)
    audit = build_learning_contribution_projection()["operations"]["lifecycle_audit"]

    assert audit["current_status"] == "degraded"
    assert audit["last_failure_code"] == "audit_append_failed"
    assert audit["backlog_count"] == 1
    assert "last_error" not in audit
    assert "last_failure_reason" not in audit


def test_lco_projection_reports_an_overdue_evaluator_as_overdue(
    tmp_path,
    monkeypatch,
):
    """A job that simply stopped running must not keep reporting ``healthy``.

    Regression (#444): ``status`` is written only on success or failure, so an
    evaluator that stops firing altogether holds its last ``healthy`` value
    forever -- indistinguishable from one that is actually running.
    """
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from aletheia.storage.sqlite import get_db
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    db = get_db()
    db.execute(
        "UPDATE learning_job_health SET next_due_at=? WHERE job_id=?",
        ("2026-07-24T02:00:00+00:00", "learning_contribution_evaluator"),
    )
    db.commit()

    evaluator = build_learning_contribution_projection()["operations"]["evaluator"]

    assert evaluator["overdue"] is True
    assert evaluator["overdue_seconds"] > 0
    assert evaluator["current_status"] == "overdue"
    # The stored column is untouched: the signal is derived at read time only.
    assert db.execute(
        "SELECT status FROM learning_job_health WHERE job_id=?",
        ("learning_contribution_evaluator",),
    ).fetchone()[0] == "healthy"


def test_lco_projection_does_not_mark_a_job_overdue_before_it_is_due(
    tmp_path,
    monkeypatch,
):
    _setup_env(tmp_path, monkeypatch)
    _seed_current_experiment(monkeypatch)
    from datetime import datetime, timedelta, timezone

    from aletheia.storage.sqlite import get_db
    from aletheia.learning.read_models import (
        build_learning_contribution_projection,
    )

    due = datetime.now(timezone.utc) + timedelta(minutes=30)
    db = get_db()
    db.execute(
        "UPDATE learning_job_health SET next_due_at=? WHERE job_id=?",
        (due.isoformat(), "learning_contribution_evaluator"),
    )
    db.commit()

    evaluator = build_learning_contribution_projection()["operations"]["evaluator"]

    assert evaluator["overdue"] is False
    assert evaluator["overdue_seconds"] is None
    assert evaluator["current_status"] == "healthy"


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
    assert result["data_freshness"]["stale_after_sec"] is None
    assert result["operations"] is None


def test_lco_projection_excludes_unsupported_paths_when_window_is_full(
    tmp_path,
    monkeypatch,
):
    """Unsupported experiments never consume the bounded measured projection."""
    _setup_env(tmp_path, monkeypatch)
    _, ufc_experiment_id = _seed_current_experiment(monkeypatch)

    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    # 21 newer open experiments with a different path_id fill the window.
    newer_ts = "2099-01-01T00:00:00+00:00"
    for i in range(21):
        h = LearningHypothesis(
            fingerprint=f"filler-fp-{i}",
            cohort_id=f"filler-cohort-{i}",
            path_id="filler_path",
            overlap_group="filler",
            source_type="filter_candidate_outcomes:clean_net:v1",
            scope_json="{}",
            action_json="{}",
            consumer="universe_allocator",
            target_horizon="1h",
            status="shadow",
            effect_threshold=0.2,
            created_at=newer_ts,
            updated_at=newer_ts,
        )
        e = LearningExperiment(
            hypothesis_id=h.hypothesis_id,
            assignment_salt=f"filler-salt-{i}",
            status="active",
            started_at=newer_ts,
            min_samples_per_arm=10,
            min_market_days=1,
            created_at=newer_ts,
        )
        assert insert_hypothesis(h)
        assert insert_experiment(e)

    import aletheia.config.loader as loader_mod
    from aletheia.config.schema import AletheiaConfig, LearningConfig

    # Part A: canonical defaults include the sole measurement-supported path.
    monkeypatch.setattr(
        loader_mod, "load_config", lambda **_kwargs: AletheiaConfig(),
    )

    from aletheia.learning.read_models import build_learning_contribution_projection

    result_default = build_learning_contribution_projection(limit=20)
    returned_ids_default = {item["experiment_id"] for item in result_default["items"]}
    assert ufc_experiment_id in returned_ids_default, (
        "A measured experiment must stay visible with the default influence allowlist"
    )
    assert len(result_default["items"]) == 1
    assert all(item["path_id"] != "filler_path" for item in result_default["items"])

    # Part B: an explicit authority configuration does not widen measurement eligibility.
    influence_cfg = AletheiaConfig(
        learning=LearningConfig(paper_influence_paths=["universe_family_calibration"])
    )
    monkeypatch.setattr(
        loader_mod, "load_config", lambda **_kwargs: influence_cfg,
    )

    result_influence = build_learning_contribution_projection(limit=20)
    returned_ids_influence = {item["experiment_id"] for item in result_influence["items"]}
    assert ufc_experiment_id in returned_ids_influence, (
        "With paper_influence_paths=[universe_family_calibration], "
        "UFC must appear within the limit-20 window"
    )
    assert len(result_influence["items"]) == 1

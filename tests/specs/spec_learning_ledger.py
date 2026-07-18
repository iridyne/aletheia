"""Executable Phase 1 Learning ledger claims."""

from __future__ import annotations

import json

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "LE-S01", "learning_evolution", "§4 durable ledger", "L2a",
    "Hypothesis, experiment, exposure, verdict, lifecycle-event, job-health, and policy-epoch stores exist with stable ids.",
    status="implemented",
))
def test_learning_ledger_tables_and_models_exist():
    from aletheia.learning.models import (
        LearningContributionVerdict, LearningExperiment, LearningExposure,
        LearningHypothesis, LearningJobHealth, LearningLifecycleEvent, LearningPolicyEpoch,
    )
    from aletheia.storage.sqlite import get_db

    expected = {
        "learning_hypotheses", "learning_experiments", "learning_exposures",
        "learning_contribution_verdicts", "learning_lifecycle_events", "learning_job_health", "learning_policy_epochs",
    }
    tables = {row[0] for row in get_db().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert expected <= tables
    for model, field in (
        (LearningHypothesis, "hypothesis_id"), (LearningExperiment, "experiment_id"),
        (LearningExposure, "exposure_id"), (LearningContributionVerdict, "verdict_id"),
        (LearningLifecycleEvent, "event_id"), (LearningJobHealth, "job_id"), (LearningPolicyEpoch, "epoch_id"),
    ):
        assert field in model.model_fields
    assert "assignment_unit_key" in LearningExposure.model_fields


def test_a1_additive_columns_are_present_after_migration():
    from aletheia.storage.sqlite import get_db

    db = get_db()
    columns = {
        table: {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        for table in (
            "learning_exposures",
            "learning_job_health",
            "filter_candidate_outcomes",
        )
    }

    assert "assignment_unit_key" in columns["learning_exposures"]
    assert {"last_failed_at", "last_failure_reason"} <= columns["learning_job_health"]
    assert "allocation_selected" in columns["filter_candidate_outcomes"]


@spec_claim(SpecClaim(
    "LE-B01", "learning_evolution", "§5 canonical fingerprint", "L2b",
    "Ordering, prose, timestamps, and artifact ids do not change normalized hypothesis identity.",
    status="implemented",
))
def test_hypothesis_fingerprint_is_stable_and_excludes_prose():
    from aletheia.learning.hypotheses import hypothesis_fingerprint

    common = dict(path_id="ranker_factor_filter", target_horizon="4H", source_cohort="Trade_Case:v2")
    first = hypothesis_fingerprint(
        scope={"symbol": "BTC/USDT", "direction": "LONG", "summary": "first prose"},
        action={"type": "block", "values": ["b", "a"]}, **common,
    )
    second = hypothesis_fingerprint(
        scope={"direction": "long", "symbol": "btc/usdt", "summary": "different", "created_at": "later"},
        action={"values": ["a", "b"], "type": "BLOCK"}, **common,
    )
    assert first == second


@spec_claim(SpecClaim(
    "LE-B23", "learning_evolution", "§6 idempotent exposure storage", "L2b",
    "Retrying the same exposure dedup key stores one immutable influence record.",
    status="implemented",
))
def test_hypothesis_and_exposure_inserts_are_idempotent():
    from aletheia.learning.hypotheses import hypothesis_fingerprint
    from aletheia.learning.ledger import insert_experiment, insert_exposure, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningExposure, LearningHypothesis
    from aletheia.storage.sqlite import get_db

    fingerprint = hypothesis_fingerprint(
        path_id="ranker_case_score", scope={"scanner": "momentum"},
        action={"delta": -5}, target_horizon="4h", source_cohort="case:v1",
    )
    hypothesis = LearningHypothesis(
        fingerprint=fingerprint, cohort_id="cohort-1", path_id="ranker_case_score",
        overlap_group="ranker", source_type="trade_case", scope_json='{"scanner":"momentum"}',
        action_json='{"delta":-5}', consumer="ranker", target_horizon="4h",
    )
    assert insert_hypothesis(hypothesis)
    duplicate = hypothesis.model_copy(update={"hypothesis_id": "different-id", "source_artifact_ids_json": '["other"]'})
    assert not insert_hypothesis(duplicate)

    experiment = LearningExperiment(hypothesis_id=hypothesis.hypothesis_id, assignment_salt="salt")
    assert insert_experiment(experiment)
    exposure = LearningExposure(
        dedup_key="exp:candidate-1", experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id, assignment="treatment", consumer="ranker",
        consumer_stage="score", path_id="ranker_case_score", overlap_group="ranker",
        candidate_key="candidate-1", concurrent_policy_epoch="epoch-1",
    )
    assert insert_exposure(exposure)
    assert not insert_exposure(exposure.model_copy(update={"exposure_id": "retry-id"}))
    db = get_db()
    assert db.execute("SELECT COUNT(*) FROM learning_hypotheses").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM learning_exposures").fetchone()[0] == 1


def _registered_experiment(token: str):
    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    hypothesis = LearningHypothesis(
        hypothesis_id=f"hypothesis-{token}",
        fingerprint=f"fingerprint-{token}",
        cohort_id="cohort",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="filter_candidate_outcomes:clean_net:v1",
        scope_json="{}",
        action_json="{}",
        consumer="universe_allocator",
        target_horizon="4h",
    )
    experiment = LearningExperiment(
        experiment_id=f"experiment-{token}",
        hypothesis_id=hypothesis.hypothesis_id,
        assignment_salt=f"salt-{token}",
        status="registered",
        started_at=None,
    )
    assert insert_hypothesis(hypothesis)
    assert insert_experiment(experiment)
    return hypothesis, experiment


def _exposure(hypothesis, experiment, *, token: str, exposed_at: str):
    from aletheia.learning.models import LearningExposure

    return LearningExposure(
        exposure_id=f"exposure-{token}",
        dedup_key=f"dedup-{token}",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment="control",
        consumer="universe_allocator",
        consumer_stage="score",
        path_id="universe_family_calibration",
        overlap_group="universe",
        assignment_unit_key=f"cluster-{token}",
        candidate_key=f"candidate-{token}",
        outcome_candidate_key=f"candidate-{token}:4h",
        outcome_source="fixed_horizon_candidate",
        concurrent_policy_epoch="epoch-test",
        exposed_at=exposed_at,
    )


def test_first_exposure_activates_registered_experiment_at_earliest_exposure():
    from aletheia.learning.ledger import get_record, insert_exposure

    hypothesis, experiment = _registered_experiment("activation")
    later = _exposure(
        hypothesis,
        experiment,
        token="activation-later",
        exposed_at="2026-07-18T12:00:00+00:00",
    )
    earlier = _exposure(
        hypothesis,
        experiment,
        token="activation-earlier",
        exposed_at="2026-07-18T11:00:00+00:00",
    )

    assert insert_exposure(later)
    first_state = get_record("learning_experiments", experiment.experiment_id)
    assert first_state["status"] == "active"
    assert first_state["started_at"] == later.exposed_at

    assert insert_exposure(earlier)
    final_state = get_record("learning_experiments", experiment.experiment_id)
    assert final_state["status"] == "active"
    assert final_state["started_at"] == earlier.exposed_at


def test_exposure_retry_repairs_partially_registered_experiment():
    from aletheia.learning.ledger import get_record, insert_exposure, insert_record

    hypothesis, experiment = _registered_experiment("repair")
    exposure = _exposure(
        hypothesis,
        experiment,
        token="repair",
        exposed_at="2026-07-18T10:00:00+00:00",
    )
    assert insert_record("learning_exposures", exposure)
    assert get_record("learning_experiments", experiment.experiment_id)["status"] == "registered"

    assert not insert_exposure(exposure)
    repaired = get_record("learning_experiments", experiment.experiment_id)
    assert repaired["status"] == "active"
    assert repaired["started_at"] == exposure.exposed_at


def test_migration_repair_activates_legacy_registered_experiment_with_exposure():
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.storage.migrations import _repair_learning_experiment_activation
    from aletheia.storage.sqlite import get_db

    hypothesis, experiment = _registered_experiment("migration-repair")
    exposure = _exposure(
        hypothesis,
        experiment,
        token="migration-repair",
        exposed_at="2026-07-18T08:00:00+00:00",
    )
    assert insert_record("learning_exposures", exposure)

    _repair_learning_experiment_activation(get_db())

    repaired = get_record("learning_experiments", experiment.experiment_id)
    assert repaired["status"] == "active"
    assert repaired["started_at"] == exposure.exposed_at


def test_previous_schema_upgrades_additively_and_idempotently():
    from aletheia.learning.ledger import get_record, insert_record
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db

    hypothesis, experiment = _registered_experiment("upgrade")
    exposure = _exposure(
        hypothesis,
        experiment,
        token="upgrade",
        exposed_at="2026-07-18T07:00:00+00:00",
    )
    assert insert_record("learning_exposures", exposure)
    db = get_db()
    db.execute(
        "UPDATE learning_experiments SET status='registered', started_at=NULL "
        "WHERE experiment_id=?",
        (experiment.experiment_id,),
    )
    db.execute("DROP INDEX idx_learning_exposures_assignment_unit")
    db.execute("ALTER TABLE learning_exposures DROP COLUMN assignment_unit_key")
    db.execute("ALTER TABLE learning_job_health DROP COLUMN last_failed_at")
    db.execute("ALTER TABLE learning_job_health DROP COLUMN last_failure_reason")
    db.execute("ALTER TABLE filter_candidate_outcomes DROP COLUMN allocation_selected")
    db.commit()

    run_migrations()
    run_migrations()

    columns = {
        table: {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
        for table in (
            "learning_exposures",
            "learning_job_health",
            "filter_candidate_outcomes",
        )
    }
    indexes = {
        row[1] for row in db.execute("PRAGMA index_list(learning_exposures)").fetchall()
    }
    repaired = get_record("learning_experiments", experiment.experiment_id)

    assert "assignment_unit_key" in columns["learning_exposures"]
    assert {"last_failed_at", "last_failure_reason"} <= columns["learning_job_health"]
    assert "allocation_selected" in columns["filter_candidate_outcomes"]
    assert "idx_learning_exposures_assignment_unit" in indexes
    assert repaired["status"] == "active"
    assert repaired["started_at"] == exposure.exposed_at
    assert db.execute(
        "SELECT COUNT(*) FROM learning_exposures WHERE exposure_id=?",
        (exposure.exposure_id,),
    ).fetchone()[0] == 1


def test_failed_first_exposure_does_not_partially_activate_experiment():
    from aletheia.learning.ledger import get_record, insert_exposure
    from aletheia.storage.sqlite import get_db

    hypothesis, experiment = _registered_experiment("atomic")
    exposure = _exposure(
        hypothesis,
        experiment,
        token="atomic",
        exposed_at="2026-07-18T09:00:00+00:00",
    )
    db = get_db()
    db.execute(
        "CREATE TRIGGER reject_test_exposure BEFORE INSERT ON learning_exposures "
        "BEGIN SELECT RAISE(ABORT, 'simulated exposure failure'); END"
    )

    with pytest.raises(Exception, match="simulated exposure failure"):
        insert_exposure(exposure)

    persisted = get_record("learning_experiments", experiment.experiment_id)
    assert persisted["status"] == "registered"
    assert persisted["started_at"] is None
    assert db.execute(
        "SELECT COUNT(*) FROM learning_exposures WHERE experiment_id=?",
        (experiment.experiment_id,),
    ).fetchone()[0] == 0

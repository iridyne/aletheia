"""Executable Phase 1 Learning ledger claims."""

from __future__ import annotations

from tests.test_learning_verdict_runtime import _historical_record


from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "LE-B01", "learning_evolution", "§5 canonical fingerprint", "L2b",
    "Ordering and prose do not change normalized hypothesis identity; behavior versions do.",
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
    changed_contract = hypothesis_fingerprint(
        scope={"symbol": "BTC/USDT", "direction": "LONG", "summary": "first prose"},
        action={"type": "block", "values": ["b", "a"]},
        behavior_contract_version="ranker-factor-filter/v2",
        **common,
    )
    assert changed_contract != first


def _registered_experiment(token: str):
    from aletheia.learning.ledger import insert_hypothesis
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
    assert _historical_record("learning_experiments", experiment)
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


def test_migration_repair_activates_legacy_registered_experiment_with_exposure():
    from aletheia.learning.ledger import get_record
    from aletheia.storage.migrations import _repair_learning_experiment_activation
    from aletheia.storage.sqlite import get_db

    hypothesis, experiment = _registered_experiment("migration-repair")
    exposure = _exposure(
        hypothesis,
        experiment,
        token="migration-repair",
        exposed_at="2026-07-18T08:00:00+00:00",
    )
    assert _historical_record("learning_exposures", exposure)

    _repair_learning_experiment_activation(get_db())

    repaired = get_record("learning_experiments", experiment.experiment_id)
    assert repaired["status"] == "active"
    assert repaired["started_at"] == exposure.exposed_at


def test_previous_schema_upgrades_additively_and_idempotently():
    from aletheia.learning.ledger import get_record
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.sqlite import get_db

    hypothesis, experiment = _registered_experiment("upgrade")
    exposure = _exposure(
        hypothesis,
        experiment,
        token="upgrade",
        exposed_at="2026-07-18T07:00:00+00:00",
    )
    assert _historical_record("learning_exposures", exposure)
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


def test_hypothesis_inserts_are_idempotent():
    from aletheia.learning.hypotheses import hypothesis_fingerprint
    from aletheia.learning.ledger import insert_hypothesis
    from aletheia.learning.models import LearningHypothesis
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

    assert get_db().execute("SELECT COUNT(*) FROM learning_hypotheses").fetchone()[0] == 1

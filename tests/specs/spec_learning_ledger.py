"""Executable Phase 1 Learning ledger claims."""

from __future__ import annotations

import json

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "LE-S01", "learning_evolution", "§4 durable ledger", "L2a",
    "Hypothesis, experiment, exposure, verdict, job-health, and policy-epoch stores exist with stable ids.",
    status="implemented",
))
def test_learning_ledger_tables_and_models_exist():
    from aletheia.learning.models import (
        LearningContributionVerdict, LearningExperiment, LearningExposure,
        LearningHypothesis, LearningJobHealth, LearningPolicyEpoch,
    )
    from aletheia.storage.sqlite import get_db

    expected = {
        "learning_hypotheses", "learning_experiments", "learning_exposures",
        "learning_contribution_verdicts", "learning_job_health", "learning_policy_epochs",
    }
    tables = {row[0] for row in get_db().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert expected <= tables
    for model, field in (
        (LearningHypothesis, "hypothesis_id"), (LearningExperiment, "experiment_id"),
        (LearningExposure, "exposure_id"), (LearningContributionVerdict, "verdict_id"),
        (LearningJobHealth, "job_id"), (LearningPolicyEpoch, "epoch_id"),
    ):
        assert field in model.model_fields


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

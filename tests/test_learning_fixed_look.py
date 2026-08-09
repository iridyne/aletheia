"""Fixed-look Learning admission contracts for paper authority."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3

import pytest


_BASE = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)


def _iso(*, days: int = 0, minutes: int = 0) -> str:
    return (_BASE + timedelta(days=days, minutes=minutes)).isoformat()


def _paper_experiment(*, experiment_id: str = "paper-fixed-look"):
    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    hypothesis = LearningHypothesis(
        hypothesis_id=f"hypothesis-{experiment_id}",
        fingerprint=f"fingerprint-{experiment_id}",
        cohort_id="universe-clean-net-v1",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="universe_forward_outcomes:clean_net:v2",
        scope_json='{"family":"momentum","direction":"long","market_type":"perp"}',
        action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
        consumer="universe_allocator",
        target_horizon="4h",
        effect_threshold=0.10,
    )
    assert insert_hypothesis(hypothesis)
    experiment = LearningExperiment(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage="paper_10",
        assignment_salt=f"salt-{experiment_id}",
        allocation_pct=0.10,
        min_samples_per_arm=1,
        min_market_days=1,
        status="active",
        started_at=_iso(),
    )
    assert insert_experiment(experiment)
    return hypothesis, experiment


def _exposure(
    hypothesis,
    experiment,
    *,
    key: str,
    assignment: str,
    exposed_at: str,
    behavior_changed: bool,
    behavior_hash: str,
):
    from aletheia.learning.ledger import insert_exposure
    from aletheia.learning.models import LearningExposure

    applied = assignment == "treatment"
    exposure = LearningExposure(
        exposure_id=f"exposure-{experiment.experiment_id}-{key}",
        dedup_key=f"dedup-{experiment.experiment_id}-{key}",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment=assignment,
        consumer="universe_allocator",
        consumer_stage="opportunity_score",
        path_id=hypothesis.path_id,
        overlap_group=hypothesis.overlap_group,
        assignment_unit_key=key,
        candidate_key=key,
        outcome_candidate_key=key,
        before_json='{"value":1.0}',
        after_json=json.dumps({"value": 1.1 if behavior_changed else 1.0}),
        manifest_json=json.dumps({
            "authority_applied": applied,
            "authority_reason": "paper_trial" if applied else "control_assignment",
            "behavior_changed": behavior_changed,
            "behavior_change_reason": (
                "effective_treatment_delta"
                if behavior_changed
                else "control_assignment"
            ),
        }),
        outcome_contract_version="v1",
        experiment_stage=experiment.stage,
        behavior_contract_hash=behavior_hash,
        concurrent_policy_epoch="epoch-fixed-look",
        exposed_at=exposed_at,
    )
    assert insert_exposure(exposure)


def _positive_verdict(hypothesis, experiment, decision, *, verdict_id: str):
    from aletheia.learning.models import LearningContributionVerdict
    from aletheia.learning.verdicts import METRIC_VERSION

    return LearningContributionVerdict(
        verdict_id=verdict_id,
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=experiment.stage,
        window_start=_iso(),
        window_end=_iso(minutes=1),
        unique_treatment_n=1,
        unique_control_n=1,
        baseline=0.0,
        observed=0.5,
        delta=0.5,
        confidence_low=0.1,
        confidence_high=0.9,
        harm_metrics_json=json.dumps({"breached": False, "harm_state": "clear"}),
        verdict="positive",
        reason="fixed look passed",
        policy_epoch="epoch-fixed-look",
        target_horizon="4h",
        metric_version=METRIC_VERSION,
        behavior_contract_hash="behavior-fixed-look",
        inference_contract_version=decision.inference_contract_version,
        inference_role="confirmatory",
        authority_eligible=True,
        inference_plan_hash=decision.plan_hash,
        inference_cutoff_fingerprint=decision.cutoff_fingerprint,
    )


def test_cutoff_is_frozen_from_exposure_facts_and_cannot_expand():
    from aletheia.learning.fixed_look import (
        freeze_fixed_look_cutoff,
        get_fixed_look_decision,
        register_fixed_look_decision,
    )

    hypothesis, experiment = _paper_experiment()
    behavior_hash = "behavior-fixed-look"
    decision = register_fixed_look_decision(
        experiment,
        hypothesis,
        policy_epoch="epoch-fixed-look",
        behavior_contract_hash=behavior_hash,
    )
    assert decision.status == "collecting"

    _exposure(
        hypothesis,
        experiment,
        key="treatment-1",
        assignment="treatment",
        exposed_at=_iso(),
        behavior_changed=True,
        behavior_hash=behavior_hash,
    )
    _exposure(
        hypothesis,
        experiment,
        key="control-1",
        assignment="control",
        exposed_at=_iso(minutes=1),
        behavior_changed=False,
        behavior_hash=behavior_hash,
    )

    frozen = freeze_fixed_look_cutoff(experiment.experiment_id)
    assert frozen.status == "cutoff_frozen"
    assert frozen.cutoff_fingerprint
    cutoff = json.loads(frozen.cutoff_json or "{}")
    assert cutoff["assignment_unit_keys"] == ["treatment-1", "control-1"]
    original_fingerprint = frozen.cutoff_fingerprint

    _exposure(
        hypothesis,
        experiment,
        key="treatment-late",
        assignment="treatment",
        exposed_at=_iso(minutes=2),
        behavior_changed=True,
        behavior_hash=behavior_hash,
    )
    assert freeze_fixed_look_cutoff(experiment.experiment_id).cutoff_fingerprint == original_fingerprint
    assert get_fixed_look_decision(experiment.experiment_id).cutoff_fingerprint == original_fingerprint


def test_confirmatory_verdict_is_consumed_once_and_bound_to_frozen_cutoff():
    from aletheia.learning.fixed_look import (
        consume_fixed_look_verdict,
        freeze_fixed_look_cutoff,
        get_fixed_look_decision,
        register_fixed_look_decision,
    )

    hypothesis, experiment = _paper_experiment(experiment_id="paper-consume-once")
    behavior_hash = "behavior-fixed-look"
    register_fixed_look_decision(
        experiment,
        hypothesis,
        policy_epoch="epoch-fixed-look",
        behavior_contract_hash=behavior_hash,
    )
    for key, assignment, changed, minute in (
        ("treatment-1", "treatment", True, 0),
        ("control-1", "control", False, 1),
    ):
        _exposure(
            hypothesis,
            experiment,
            key=key,
            assignment=assignment,
            exposed_at=_iso(minutes=minute),
            behavior_changed=changed,
            behavior_hash=behavior_hash,
        )
    decision = freeze_fixed_look_cutoff(experiment.experiment_id)
    verdict = _positive_verdict(
        hypothesis,
        experiment,
        decision,
        verdict_id="confirmatory-once",
    )

    assert consume_fixed_look_verdict(verdict) is True
    assert consume_fixed_look_verdict(verdict) is False
    persisted = get_fixed_look_decision(experiment.experiment_id)
    assert persisted.status == "consumed"
    assert persisted.consumed_verdict_id == verdict.verdict_id

    from aletheia.learning.fixed_look import inference_authority_reason

    assert inference_authority_reason(
        verdict.model_copy(update={"policy_epoch": "other-epoch"}).model_dump()
    ) == "inference_plan_identity_mismatch"

    from aletheia.storage.sqlite import get_db

    db = get_db()
    with pytest.raises(sqlite3.IntegrityError, match="plan is immutable"):
        db.execute(
            "UPDATE learning_inference_decisions SET plan_hash=? WHERE experiment_id=?",
            ("0" * 64, experiment.experiment_id),
        )
    with pytest.raises(sqlite3.IntegrityError, match="cutoff is immutable"):
        db.execute(
            "UPDATE learning_inference_decisions SET cutoff_json='{}' "
            "WHERE experiment_id=?",
            (experiment.experiment_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="decisions are retained"):
        db.execute(
            "DELETE FROM learning_inference_decisions WHERE experiment_id=?",
            (experiment.experiment_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="verdict is retained"):
        db.execute(
            "DELETE FROM learning_contribution_verdicts WHERE verdict_id=?",
            (verdict.verdict_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="verdict is immutable"):
        db.execute(
            "UPDATE learning_contribution_verdicts SET verdict='neutral' "
            "WHERE verdict_id=?",
            (verdict.verdict_id,),
        )

    conflicting = verdict.model_copy(update={"verdict_id": "confirmatory-conflict"})
    assert consume_fixed_look_verdict(conflicting) is False
    assert get_fixed_look_decision(experiment.experiment_id).consumed_verdict_id == verdict.verdict_id


def test_monitoring_positive_is_never_authority_eligible():
    from aletheia.learning.fixed_look import inference_authority_reason
    from aletheia.learning.models import LearningContributionVerdict

    hypothesis, experiment = _paper_experiment(experiment_id="paper-monitoring")
    monitoring = LearningContributionVerdict(
        verdict_id="monitoring-positive",
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=experiment.stage,
        window_start=_iso(),
        window_end=_iso(minutes=1),
        unique_treatment_n=50,
        unique_control_n=50,
        verdict="positive",
        reason="descriptive only",
    )

    assert inference_authority_reason(monitoring.model_dump()) == "inference_role_not_confirmatory"


def test_paper_collection_requires_exact_registered_plan():
    from aletheia.learning.fixed_look import (
        inference_collection_reason,
        register_fixed_look_decision,
    )

    hypothesis, experiment = _paper_experiment(experiment_id="paper-plan-authority")
    assert inference_collection_reason(
        experiment,
        policy_epoch="epoch-fixed-look",
        behavior_contract_hash="behavior-fixed-look",
        target_horizon=hypothesis.target_horizon,
    ) == "inference_contract_unavailable"

    register_fixed_look_decision(
        experiment,
        hypothesis,
        policy_epoch="epoch-fixed-look",
        behavior_contract_hash="behavior-fixed-look",
    )
    assert inference_collection_reason(
        experiment,
        policy_epoch="epoch-fixed-look",
        behavior_contract_hash="behavior-fixed-look",
        target_horizon=hypothesis.target_horizon,
    ) is None
    assert inference_collection_reason(
        experiment,
        policy_epoch="other-epoch",
        behavior_contract_hash="behavior-fixed-look",
        target_horizon=hypothesis.target_horizon,
    ) == "inference_plan_identity_mismatch"


def test_fixed_look_registration_is_prospective_and_single_lineage():
    from aletheia.learning.fixed_look import (
        InferenceContractError,
        register_fixed_look_decision,
    )

    first_hypothesis, first = _paper_experiment(experiment_id="paper-first-lineage")
    register_fixed_look_decision(
        first,
        first_hypothesis,
        policy_epoch="epoch-fixed-look",
        behavior_contract_hash="behavior-fixed-look",
    )

    competing_hypothesis, competing = _paper_experiment(
        experiment_id="paper-competing-lineage"
    )
    with pytest.raises(InferenceContractError, match="family_already_active"):
        register_fixed_look_decision(
            competing,
            competing_hypothesis,
            policy_epoch="epoch-fixed-look",
            behavior_contract_hash="behavior-fixed-look",
        )

    late_hypothesis, late = _paper_experiment(experiment_id="paper-late-plan")
    _exposure(
        late_hypothesis,
        late,
        key="already-exposed",
        assignment="control",
        exposed_at=_iso(),
        behavior_changed=False,
        behavior_hash="different-behavior",
    )
    with pytest.raises(InferenceContractError, match="plan_not_prospective"):
        register_fixed_look_decision(
            late,
            late_hypothesis,
            policy_epoch="epoch-fixed-look",
            behavior_contract_hash="different-behavior",
        )

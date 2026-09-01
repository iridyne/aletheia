"""Economic Knowledge is a read-only projection of consumed confirmatory truth."""

import json

from aletheia.universe.outcome_economics import COST_MODEL_VERSION


def _verdict(
    *,
    verdict_id: str = "verdict-qualified",
    actual_n: int = 50,
    suffix: str = "",
    stage: str = "paper_10",
    window_end: str = "2026-08-08T00:00:00+00:00",
):
    from aletheia.learning.ledger import (
        insert_experiment,
        insert_hypothesis,
        insert_record,
    )
    from aletheia.learning.models import (
        LearningContributionVerdict,
        LearningExperiment,
        LearningHypothesis,
        LearningInferenceDecision,
    )

    hypothesis = LearningHypothesis(
        hypothesis_id=f"hypothesis-qualified{suffix}",
        fingerprint=f"fingerprint-qualified{suffix}",
        cohort_id=f"cohort-qualified{suffix}",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="reflection",
        source_artifact_ids_json='["case-1","case-2"]',
        scope_json='{"family":"momentum","direction":"long","market_type":"perp"}',
        action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
        consumer="universe_allocator",
        target_horizon="4h",
        effect_threshold=0.1,
        harm_budget_json='{"max_loss_rate_increase":0.15}',
        behavior_contract_version="universe-family-calibration/v11",
    )
    insert_hypothesis(hypothesis)
    experiment = LearningExperiment(
        experiment_id=f"experiment-qualified{suffix}",
        hypothesis_id=hypothesis.hypothesis_id,
        stage=stage,
        assignment_salt=f"salt{suffix}",
        allocation_pct=0.5,
        min_samples_per_arm=50,
        min_market_days=7,
        primary_metric="utility-v6",
        status="active",
        created_at="2026-08-02T00:00:00+00:00",
    )
    insert_experiment(experiment)
    verdict = LearningContributionVerdict(
        verdict_id=verdict_id,
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=stage,
        window_start="2026-08-01T00:00:00+00:00",
        window_end=window_end,
        unique_treatment_n=50,
        unique_control_n=50,
        baseline=0.1,
        observed=0.5,
        delta=0.4,
        confidence_low=0.2,
        confidence_high=0.6,
        harm_metrics_json=json.dumps({
            "actual_intervention_treatment_n": actual_n,
            "expected_cost_model_version": COST_MODEL_VERSION,
        }),
        verdict="positive",
        reason="fixed look confirms cost-aware improvement",
        policy_epoch="epoch-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="utility-v6",
        behavior_contract_hash="behavior-v1",
        inference_contract_version="ufc-fixed-look/v2",
        inference_role="confirmatory",
        authority_eligible=True,
        inference_plan_hash="a" * 64,
        inference_cutoff_fingerprint="b" * 64,
        created_at="2026-08-08T00:00:00+00:00",
    )
    insert_record("learning_contribution_verdicts", verdict)
    insert_record("learning_inference_decisions", LearningInferenceDecision(
        experiment_id=experiment.experiment_id,
        inference_family_id="fixed-look",
        inference_contract_version="ufc-fixed-look/v2",
        plan_json="{}",
        plan_hash="a" * 64,
        status="consumed",
        cutoff_json="{}",
        cutoff_fingerprint="b" * 64,
        cutoff_frozen_at="2026-08-08T00:00:00+00:00",
        consumed_verdict_id=verdict.verdict_id,
        consumed_at="2026-08-08T00:00:00+00:00",
        authority_alpha=0.05,
    ))
    return verdict


def test_positive_consumed_fixed_look_projects_one_reproducible_fact():
    from aletheia.learning.economic_knowledge import assess_economic_knowledge

    verdict = _verdict()
    first = assess_economic_knowledge(verdict.verdict_id)
    second = assess_economic_knowledge(verdict.verdict_id)

    assert first.status == "qualified"
    assert first.fact == second.fact
    assert first.fact is not None
    assert first.fact.verdict_id == verdict.verdict_id
    assert first.fact.actual_intervention_treatment_n == 50
    assert first.fact.delta == 0.4
    assert first.fact.operational_actionability == "separate_authority_resolution"


def test_positive_without_actual_intervention_is_not_economic_knowledge():
    from aletheia.learning.economic_knowledge import assess_economic_knowledge

    verdict = _verdict(verdict_id="verdict-shadow", actual_n=0)
    assessment = assess_economic_knowledge(verdict.verdict_id)

    assert assessment.status == "not_qualified"
    assert assessment.fact is None
    assert "no_actual_intervention" in assessment.reasons


def test_current_projection_is_read_only_and_zero_is_honest():
    from aletheia.learning.economic_knowledge import project_current_economic_knowledge
    from aletheia.storage.sqlite import get_db

    assert project_current_economic_knowledge() == []
    _verdict()
    before = {
        table: get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in (
            "learning_hypotheses",
            "learning_experiments",
            "learning_contribution_verdicts",
            "learning_inference_decisions",
        )
    }
    facts = project_current_economic_knowledge()
    after = {
        table: get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in before
    }
    assert len(facts) == 1
    assert before == after

"""Deterministic Paper fixture proving the #2325 semantic loop, not production gain."""

import json


def test_memory_to_shadow_to_paper_knowledge_yield_and_refreeze():
    from aletheia.learning.contrastive_evidence import (
        ContrastiveEvidenceItemV1,
        build_contrastive_bundle,
    )
    from aletheia.learning.economic_knowledge import (
        assess_economic_knowledge,
        project_current_economic_knowledge,
    )
    from aletheia.learning.hypothesis_compiler import (
        admit_nomination_to_shadow,
        assess_experimentability,
        compile_hypothesis_draft,
    )
    from aletheia.learning.hypotheses import (
        HypothesisGenerationManifestV1,
        HypothesisNominationEnvelopeV1,
        publish_hypothesis_nomination,
    )
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.ledger import insert_lifecycle_event, insert_record
    from aletheia.learning.memory_assets import MemoryAssetRefV1
    from aletheia.learning.models import (
        LearningContributionVerdict,
        LearningInferenceDecision,
        LearningLifecycleEvent,
    )
    from aletheia.learning.registry import get_learning_path
    from aletheia.storage.sqlite import get_db
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION
    from tests.test_learning_hypothesis_nominations import _envelope

    base = _envelope("e2e-foundation")
    discovery = MemoryAssetRefV1(
        asset_id="case-1",
        asset_type="trade_case_study",
        evidence_class="settled_quantitative",
        canonical_source_ref="position:1",
        root_outcome_identity="position:1:4h",
        observed_at="2026-08-01T04:00:00+00:00",
        source_hash="a" * 64,
        quantitative_weight=1,
    )
    challenge = MemoryAssetRefV1(
        asset_id="case-2",
        asset_type="trade_case_study",
        evidence_class="settled_quantitative",
        canonical_source_ref="position:2",
        root_outcome_identity="position:2:4h",
        observed_at="2026-08-01T04:00:00+00:00",
        source_hash="b" * 64,
        quantitative_weight=1,
    )
    bundle = build_contrastive_bundle(
        path_id="universe_family_calibration",
        discovery_items=[ContrastiveEvidenceItemV1(role="anchor", asset=discovery)],
        challenge_items=[ContrastiveEvidenceItemV1(
            role="nearest_counterexample", asset=challenge,
        )],
    )
    assert bundle.challenge_separation_state == "disjoint"

    draft = compile_hypothesis_draft(
        path_id="universe_family_calibration",
        research_evidence=base.research_evidence,
        t0_foundation=base.t0_foundation,
        scope={"family": "momentum", "direction": "long", "market_type": "perp"},
        baseline_action={"type": "opportunity_multiplier", "multiplier": 1.0},
        treatment_action={"type": "opportunity_multiplier", "multiplier": 1.05},
        source_type="contrastive_reflection",
        source_artifact_ids=["case-1", "case-2"],
        source_cohort="e2e-paper-fixture",
        target_horizon="4h",
        effect_threshold=0.1,
        harm_budget={"max_loss_rate_increase": 0.15},
    )
    assert draft.status == "compilable"
    nomination = publish_hypothesis_nomination(HypothesisNominationEnvelopeV1(
        research_evidence=base.research_evidence,
        t0_foundation=base.t0_foundation,
        draft=draft,
        generation_manifest=HypothesisGenerationManifestV1(
            method="reflection",
            producer="deterministic-paper-fixture",
            producer_version="v1",
            source_bundle_hash="e2e-bundle",
            parent_hypothesis_ids=[],
            agent_run_ids=[],
            tool_run_ids=[],
            estimated_cost_usd=0.02,
        ),
    ))
    hypothesis_id = nomination.hypothesis_id or "missing"
    preflight = assess_experimentability(
        path_id="universe_family_calibration",
        hypothesis_id=hypothesis_id,
        historical_eligible_n=200,
        distinct_market_days=20,
        eligible_event_rate_per_day=20,
        outcome_adapter_available=True,
        outcome_settlement_rate=0.9,
        expected_treatment_assignment_rate=0.5,
        expected_actual_intervention_rate=0.8,
        required_samples_per_arm=50,
        required_market_days=7,
    )
    shadow = admit_nomination_to_shadow(nomination.nomination_id, preflight)
    assert shadow is not None and shadow.stage == "shadow"

    # Paper fixture only: simulate the existing owner-gated lifecycle's resulting state.
    get_db().execute(
        "UPDATE learning_experiments SET stage='paper_10', status='active' WHERE experiment_id=?",
        (shadow.experiment_id,),
    )
    path = get_learning_path("universe_family_calibration")
    positive = LearningContributionVerdict(
        verdict_id="e2e-positive",
        experiment_id=shadow.experiment_id,
        hypothesis_id=hypothesis_id,
        stage="paper_10",
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-08-08T00:00:00+00:00",
        unique_treatment_n=50,
        unique_control_n=50,
        baseline=0.1,
        observed=0.5,
        delta=0.4,
        confidence_low=0.2,
        confidence_high=0.6,
        harm_metrics_json=json.dumps({
            "actual_intervention_treatment_n": 50,
            "expected_cost_model_version": COST_MODEL_VERSION,
        }),
        verdict="positive",
        reason="paper fixture fixed look",
        policy_epoch="fixture-epoch",
        target_horizon="4h",
        outcome_contract_version=path.outcome_contract_version,
        metric_version=path.metric_version,
        behavior_contract_hash="fixture-behavior",
        inference_contract_version="ufc-fixed-look/v2",
        inference_role="confirmatory",
        authority_eligible=True,
        inference_plan_hash="c" * 64,
        inference_cutoff_fingerprint="d" * 64,
    )
    insert_record("learning_contribution_verdicts", positive)
    insert_record("learning_inference_decisions", LearningInferenceDecision(
        experiment_id=shadow.experiment_id,
        inference_family_id="ufc-fixed-look",
        inference_contract_version="ufc-fixed-look/v2",
        plan_json="{}",
        plan_hash="c" * 64,
        status="consumed",
        cutoff_json="{}",
        cutoff_fingerprint="d" * 64,
        cutoff_frozen_at="2026-08-08T00:00:00+00:00",
        consumed_verdict_id=positive.verdict_id,
        consumed_at="2026-08-08T00:00:00+00:00",
        authority_alpha=0.05,
    ))
    assert assess_economic_knowledge(positive.verdict_id).status == "qualified"
    assert len(project_current_economic_knowledge()) == 1
    before_death = build_knowledge_yield_report(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
    )
    assert before_death.slices[0].qualified_economic_knowledge_n == 1

    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="e2e-negative",
        experiment_id=shadow.experiment_id,
        hypothesis_id=hypothesis_id,
        stage="paper_10",
        window_start="2026-08-09T00:00:00+00:00",
        window_end="2026-08-10T00:00:00+00:00",
        unique_treatment_n=50,
        unique_control_n=50,
        harm_metrics_json='{"breached":true}',
        verdict="negative",
        reason="monitoring harm",
        inference_role="monitoring",
        created_at="9999-01-01T00:00:00+00:00",
    ))
    insert_lifecycle_event(LearningLifecycleEvent(
        dedup_key="e2e-refreeze",
        hypothesis_id=hypothesis_id,
        experiment_id=shadow.experiment_id,
        verdict_id="e2e-negative",
        from_stage="paper_10",
        to_stage="shadow",
        action="refreeze",
        reason="monitoring harm",
        created_at="9999-01-01T00:00:01+00:00",
    ))
    assert project_current_economic_knowledge() == []
    assert assess_economic_knowledge(positive.verdict_id).fact is not None

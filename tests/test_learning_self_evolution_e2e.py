"""Contrastive memory can publish a nomination without granting experiment authority."""



def test_contrastive_memory_publishes_nomination_without_experiment_authority():
    from extensions.learning.contrastive_evidence import (
        ContrastiveEvidenceItemV1,
        build_contrastive_bundle,
    )
    from extensions.learning.hypothesis_compiler import (
        assess_experimentability,
        compile_hypothesis_draft,
    )
    from extensions.learning.hypotheses import (
        HypothesisGenerationManifestV1,
        HypothesisNominationEnvelopeV1,
        publish_hypothesis_nomination,
    )
    from aletheia.storage.sqlite import get_db
    from tests.test_learning_hypothesis_nominations import _envelope
    from extensions.learning.memory_assets import MemoryAssetRefV1
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
    assert preflight.status in {"ready", "slow_but_viable"}
    assert nomination.hypothesis_id is not None
    assert get_db().execute("SELECT COUNT(*) FROM learning_experiments").fetchone()[0] == 0

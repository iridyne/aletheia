"""Knowledge Yield counts qualified facts over prospective hypotheses."""

from tests.test_economic_knowledge import _verdict


def test_fifty_hypotheses_to_two_facts_yields_four_percent():
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    _verdict(verdict_id="qualified-1", suffix="-1")
    _verdict(verdict_id="qualified-2", suffix="-2")
    for index in range(48):
        hypothesis = LearningHypothesis(
            hypothesis_id=f"yield-hypothesis-{index}",
            fingerprint=f"yield-fingerprint-{index}",
            cohort_id=f"yield-cohort-{index}",
            path_id="universe_family_calibration",
            overlap_group="universe",
            source_type="reflection",
            scope_json='{"family":"momentum"}',
            action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
            consumer="universe_allocator",
            target_horizon="4h",
            effect_threshold=0.1,
            harm_budget_json='{"max_loss_rate_increase":0.15}',
            behavior_contract_version="universe-family-calibration/v11",
        )
        insert_hypothesis(hypothesis)
        insert_experiment(LearningExperiment(
            experiment_id=f"yield-experiment-{index}",
            hypothesis_id=hypothesis.hypothesis_id,
            stage="shadow",
            assignment_salt=f"salt-{index}",
            min_samples_per_arm=50,
            min_market_days=7,
            primary_metric="utility-v6",
            created_at="2026-08-02T00:00:00+00:00",
            status="registered",
        ))

    report = build_knowledge_yield_report(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
    )

    assert report.cross_slice_aggregate is None
    assert len(report.slices) == 1
    result = report.slices[0]
    assert result.hypotheses_entering_prospective_test_n == 50
    assert result.qualified_economic_knowledge_n == 2
    assert result.knowledge_yield == 0.04
    assert result.estimated_generation_cost_usd is None
    assert "generation_cost_incomplete" in result.unavailable_reasons


def test_fact_after_report_window_is_not_counted_retroactively():
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report

    _verdict(window_end="2026-09-02T00:00:00+00:00")
    report = build_knowledge_yield_report(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
    )
    result = report.slices[0]
    assert result.hypotheses_entering_prospective_test_n == 1
    assert result.qualified_economic_knowledge_n == 0
    assert result.knowledge_yield == 0.0


def test_incompatible_metric_versions_are_separate_slices():
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.ledger import insert_experiment, insert_hypothesis
    from aletheia.learning.models import LearningExperiment, LearningHypothesis

    _verdict()
    hypothesis = LearningHypothesis(
        hypothesis_id="legacy-metric-hypothesis",
        fingerprint="legacy-metric-fingerprint",
        cohort_id="legacy-metric-cohort",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="reflection",
        scope_json='{"family":"momentum"}',
        action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
        consumer="universe_allocator",
        target_horizon="4h",
        harm_budget_json="{}",
        behavior_contract_version="universe-family-calibration/v10",
    )
    insert_hypothesis(hypothesis)
    insert_experiment(LearningExperiment(
        experiment_id="legacy-metric-experiment",
        hypothesis_id=hypothesis.hypothesis_id,
        stage="shadow",
        assignment_salt="legacy-salt",
        created_at="2026-08-02T00:00:00+00:00",
        primary_metric="utility-v5",
        status="registered",
    ))

    report = build_knowledge_yield_report(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
    )
    assert len(report.slices) == 2
    assert {item.metric_version for item in report.slices} == {"utility-v5", "utility-v6"}
    assert report.cross_slice_aggregate is None


def test_rejected_attempt_without_hypothesis_remains_in_generation_yield():
    from aletheia.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from tests.test_learning_hypothesis_nominations import _envelope

    nomination = publish_hypothesis_nomination(
        _envelope("rejected-yield-bundle", status="rejected"),
    )
    assert nomination.hypothesis_id is None
    report = build_knowledge_yield_report(
        window_start="2000-01-01T00:00:00+00:00",
        window_end="2999-01-01T00:00:00+00:00",
    )
    assert report.slices == []
    assert report.generation_attempt_n == 1
    assert report.published_nomination_n == 0
    assert report.unpublished_nomination_n == 1
    assert report.rejected_or_advisory_nomination_n == 1
    assert report.unique_nominated_hypothesis_n == 0
    assert report.generation_to_hypothesis_conversion == 0.0
    assert report.total_recorded_generation_cost_usd == 0.01


def test_later_facts_do_not_change_closed_window_report():
    """#2541: later nominations, verdicts, and lifecycle facts stay out of old reports."""
    import json

    from aletheia.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.ledger import insert_experiment, insert_lifecycle_event, insert_record
    from aletheia.learning.models import (
        LearningContributionVerdict,
        LearningExperiment,
        LearningLifecycleEvent,
    )
    from aletheia.storage.sqlite import get_db
    from tests.test_learning_hypothesis_nominations import _envelope

    nomination = publish_hypothesis_nomination(_envelope("replay-stable-bundle"))
    assert nomination.hypothesis_id is not None
    experiment = LearningExperiment(
        experiment_id="replay-stable-experiment",
        hypothesis_id=nomination.hypothesis_id,
        stage="paper_10",
        assignment_salt="replay-stable-salt",
        primary_metric="utility-v6",
        status="active",
    )
    insert_experiment(experiment)
    window = {
        "window_start": "2000-01-01T00:00:00Z",
        "window_end": "2999-01-01T00:00:00Z",
    }
    get_db().execute(
        "INSERT INTO learning_hypothesis_nominations "
        "(nomination_id, behavior_fingerprint, source_bundle_hash, hypothesis_id, "
        "research_status, publication_status, envelope_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "edge-nomination",
            "edge-fingerprint",
            "edge-bundle",
            nomination.hypothesis_id,
            nomination.research_status,
            nomination.publication_status,
            nomination.envelope_json,
            "2998-12-31T23:59:59.999999Z",
        ),
    )
    before = build_knowledge_yield_report(**window)
    assert before.slices[0].generation_attempt_n == 2

    get_db().execute(
        "INSERT INTO learning_hypothesis_nominations "
        "(nomination_id, behavior_fingerprint, source_bundle_hash, hypothesis_id, "
        "research_status, publication_status, envelope_json, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "later-nomination",
            "later-fingerprint",
            "later-bundle",
            nomination.hypothesis_id,
            nomination.research_status,
            nomination.publication_status,
            nomination.envelope_json,
            window["window_end"],
        ),
    )
    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="later-invalid-verdict",
        experiment_id=experiment.experiment_id,
        hypothesis_id=nomination.hypothesis_id,
        stage="paper_10",
        window_start="2998-12-01T00:00:00+00:00",
        window_end=window["window_end"],
        unique_treatment_n=20,
        unique_control_n=20,
        harm_metrics_json=json.dumps({"actual_intervention_treatment_n": 20}),
        verdict="invalid",
        reason="later_invalid_measurement",
        inference_role="monitoring",
        created_at=window["window_end"],
    ))
    insert_lifecycle_event(LearningLifecycleEvent(
        dedup_key="later-refreeze",
        hypothesis_id=nomination.hypothesis_id,
        experiment_id=experiment.experiment_id,
        from_stage="paper_10",
        action="refreeze",
        reason="later_negative_measurement",
        created_at=window["window_end"],
    ))

    assert build_knowledge_yield_report(**window) == before


def test_later_disqualification_changes_current_projection_not_closed_yield():
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.ledger import insert_record

    verdict = _verdict()
    window = {
        "window_start": "2026-08-01T00:00:00Z",
        "window_end": "2026-09-01T00:00:00Z",
    }
    before = build_knowledge_yield_report(**window)
    insert_record(
        "learning_contribution_verdicts",
        verdict.model_copy(update={
            "verdict_id": "later-invalid-disqualification",
            "window_start": "2026-09-01T00:00:00+00:00",
            "window_end": "2026-09-02T00:00:00+00:00",
            "verdict": "invalid",
            "reason": "later_invalid_measurement",
            "inference_role": "monitoring",
            "authority_eligible": False,
            "created_at": "2026-09-01T00:00:00+00:00",
        }),
    )
    after = build_knowledge_yield_report(**window)

    assert before.current_economic_knowledge_n == 1
    assert after.current_economic_knowledge_n == 0
    before_interval = before.model_dump(exclude={"current_economic_knowledge_n"})
    after_interval = after.model_dump(exclude={"current_economic_knowledge_n"})
    assert after_interval == before_interval


def test_outcome_identities_returned_and_lineage_not_misflagged():
    """#2542: current research evidence exposes its canonical outcome identities."""
    from aletheia.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.ledger import insert_experiment
    from aletheia.learning.models import LearningExperiment
    from tests.test_learning_hypothesis_nominations import _envelope

    nomination = publish_hypothesis_nomination(_envelope("lineage-bundle"))
    assert nomination.hypothesis_id is not None
    insert_experiment(LearningExperiment(
        experiment_id="lineage-experiment",
        hypothesis_id=nomination.hypothesis_id,
        stage="shadow",
        assignment_salt="lineage-salt",
        primary_metric="utility-v6",
        status="registered",
    ))
    report = build_knowledge_yield_report(
        window_start="2000-01-01T00:00:00+00:00",
        window_end="2999-01-01T00:00:00+00:00",
    )
    result = report.slices[0]
    assert result.root_outcome_identities
    assert "root_outcome_lineage_incomplete" not in result.unavailable_reasons


def test_duplicate_invalid_and_refreeze_share_one_drag_entity():
    """#2543: overlapping harm events count one affected real-intervention hypothesis."""
    import json
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.learning.ledger import (
        insert_experiment, insert_hypothesis, insert_lifecycle_event, insert_record,
    )
    from aletheia.learning.models import (
        LearningContributionVerdict, LearningExperiment, LearningHypothesis,
        LearningLifecycleEvent,
    )
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    h = LearningHypothesis(
        hypothesis_id="drag-h-1",
        fingerprint="drag-fp-1",
        cohort_id="drag-cohort-1",
        path_id="universe_family_calibration",
        overlap_group="universe",
        source_type="reflection",
        scope_json='{"family":"momentum"}',
        action_json='{"type":"opportunity_multiplier","multiplier":1.1}',
        consumer="universe_allocator",
        target_horizon="4h",
        behavior_contract_version="universe-family-calibration/v11",
    )
    insert_hypothesis(h)
    insert_experiment(LearningExperiment(
        experiment_id="drag-exp-1",
        hypothesis_id=h.hypothesis_id,
        stage="paper_10",
        assignment_salt="drag-salt-1",
        primary_metric="utility-v6",
        status="active",
    ))
    harm = json.dumps({
        "actual_intervention_treatment_n": 20,
        "expected_cost_model_version": COST_MODEL_VERSION,
    })
    # Two invalid verdicts for the SAME hypothesis within window
    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="drag-verdict-1",
        experiment_id="drag-exp-1",
        hypothesis_id=h.hypothesis_id,
        stage="paper_10",
        window_start="2026-08-05T00:00:00+00:00",
        window_end="2026-08-10T00:00:00+00:00",
        unique_treatment_n=20,
        unique_control_n=20,
        baseline=0.1,
        observed=-0.2,
        delta=-0.3,
        harm_metrics_json=harm,
        verdict="invalid",
        reason="measurement_invalid",
        created_at="2026-08-10T00:00:00+00:00",
        inference_role="monitoring",
        policy_epoch="epoch-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="utility-v6",
        behavior_contract_hash="behavior-v1",
        inference_contract_version="ufc-fixed-look/v2",
        authority_eligible=False,
        inference_plan_hash="a" * 64,
        inference_cutoff_fingerprint="b" * 64,
    ))
    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id="drag-verdict-2",
        experiment_id="drag-exp-1",
        hypothesis_id=h.hypothesis_id,
        stage="paper_10",
        window_start="2026-08-12T00:00:00+00:00",
        window_end="2026-08-15T00:00:00+00:00",
        unique_treatment_n=20,
        unique_control_n=20,
        baseline=0.1,
        observed=-0.3,
        delta=-0.4,
        harm_metrics_json=harm,
        verdict="invalid",
        reason="measurement_invalid_again",
        created_at="2026-08-15T00:00:00+00:00",
        inference_role="monitoring",
        policy_epoch="epoch-a",
        target_horizon="4h",
        outcome_contract_version="v1",
        metric_version="utility-v6",
        behavior_contract_hash="behavior-v1",
        inference_contract_version="ufc-fixed-look/v2",
        authority_eligible=False,
        inference_plan_hash="a" * 64,
        inference_cutoff_fingerprint="b" * 64,
    ))
    # Also a refreeze lifecycle event for the same hypothesis within window
    insert_lifecycle_event(LearningLifecycleEvent(
        dedup_key="drag-refreeze-1",
        hypothesis_id=h.hypothesis_id,
        experiment_id="drag-exp-1",
        from_stage="paper_10",
        action="refreeze",
        reason="negative_measurement_refreeze",
        policy_epoch="epoch-a",
        created_at="2026-08-16T00:00:00+00:00",
    ))
    report = build_knowledge_yield_report(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
    )
    assert len(report.slices) == 1
    result = report.slices[0]
    assert result.invalid_verdict_n == 2
    assert result.refrozen_hypothesis_n == 1
    assert result.operational_drag_ratio == 1.0


def test_zero_real_intervention_drag_is_none_and_hold():
    """#2543: zero real-intervention hypotheses → drag is None, meta says HOLD."""
    from aletheia.learning.knowledge_yield import (
        KnowledgeYieldReportV1, KnowledgeYieldSliceV1,
    )
    from aletheia.learning.meta_evolution import recommend_meta_evolution

    slice_zero = KnowledgeYieldSliceV1(
        path_id="ranker_case_score",
        behavior_contract_version="ranker-case-score/v1",
        metric_version="utility-v6",
        outcome_contract_version="v1",
        target_horizon="4h",
        consumers=["paper ranker"],
        hypotheses_entering_prospective_test_n=0,
        qualified_economic_knowledge_n=0,
        knowledge_yield=None,
        invalid_verdict_n=0,
        refrozen_hypothesis_n=0,
        real_intervention_hypothesis_n=0,
        confirmatory_outcome_n=0,
        operational_drag_ratio=None,
        availability="partial",
        unavailable_reasons=["no_prospective_hypotheses"],
    )
    report = KnowledgeYieldReportV1(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
        current_economic_knowledge_n=0,
        slices=[slice_zero],
    )
    rec = recommend_meta_evolution(report)[0]
    assert rec.research_action == "HOLD_INSUFFICIENT_EVIDENCE"
    assert rec.harm_refreeze_rate == 0.0

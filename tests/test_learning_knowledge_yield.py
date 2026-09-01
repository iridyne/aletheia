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

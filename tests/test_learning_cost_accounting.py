"""Generation cost is explicit and missing cost stays unavailable."""

from tests.test_learning_hypothesis_nominations import _envelope


def test_nomination_manifest_cost_is_reported_without_fabricated_runtime_cost():
    """Shadow admission was retired; Knowledge Yield now reports unavailable
    via Observation Lab authority (not yet wired). Verify cost accounting
    does not fabricate runtime cost when unavailable.
    """
    from extensions.learning.hypothesis_compiler import assess_experimentability
    from extensions.learning.hypotheses import publish_hypothesis_nomination
    from extensions.learning.knowledge_yield import build_knowledge_yield_report

    nomination = publish_hypothesis_nomination(_envelope("cost-bundle"))
    hypothesis_id = nomination.hypothesis_id or "missing"
    preflight = assess_experimentability(
        path_id="ranker_case_score",
        hypothesis_id=hypothesis_id,
        historical_eligible_n=100,
        distinct_market_days=10,
        eligible_event_rate_per_day=20,
        outcome_adapter_available=True,
        outcome_settlement_rate=0.9,
        expected_treatment_assignment_rate=0.5,
        expected_actual_intervention_rate=0.8,
        required_samples_per_arm=50,
        required_market_days=7,
    )
    assert preflight.status in {"ready", "slow_but_viable", "not_experimentable_now"}

    report = build_knowledge_yield_report(
        window_start="2000-01-01T00:00:00+00:00",
        window_end="2999-01-01T00:00:00+00:00",
        path_id="ranker_case_score",
    )
    assert report.slices == []
    assert report.availability == "unavailable"
    assert "canonical_lab" in report.unavailable_reasons[0]
    assert report.generation_cost_availability == "unavailable"
    assert report.total_recorded_generation_cost_usd is None

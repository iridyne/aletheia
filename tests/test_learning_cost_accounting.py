"""Generation cost is explicit and missing cost stays unavailable."""

from tests.test_learning_hypothesis_nominations import _envelope


def test_nomination_manifest_cost_is_reported_without_fabricated_runtime_cost():
    from aletheia.learning.hypothesis_compiler import (
        admit_nomination_to_shadow,
        assess_experimentability,
    )
    from aletheia.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report

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
    assert admit_nomination_to_shadow(nomination.nomination_id, preflight) is not None

    report = build_knowledge_yield_report(
        window_start="2000-01-01T00:00:00+00:00",
        window_end="2999-01-01T00:00:00+00:00",
        path_id="ranker_case_score",
    )
    assert len(report.slices) == 1
    result = report.slices[0]
    assert result.hypotheses_entering_prospective_test_n == 1
    assert result.qualified_economic_knowledge_n == 0
    assert result.knowledge_yield == 0.0
    assert result.estimated_generation_cost_usd == 0.01
    assert result.availability == "partial"
    assert result.unavailable_reasons == ["root_outcome_lineage_incomplete"]

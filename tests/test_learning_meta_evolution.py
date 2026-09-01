"""Meta-Evolution recommends research attention and owns no action authority."""

import pytest


def _slice(**overrides):
    from aletheia.learning.knowledge_yield import KnowledgeYieldSliceV1

    values = {
        "path_id": "ranker_case_score",
        "behavior_contract_version": "ranker-case-score/v1",
        "metric_version": "utility-v6",
        "outcome_contract_version": "v1",
        "target_horizon": "4h",
        "consumers": ["paper ranker"],
        "hypotheses_entering_prospective_test_n": 20,
        "qualified_economic_knowledge_n": 0,
        "knowledge_yield": 0.0,
        "invalid_verdict_n": 0,
        "refrozen_hypothesis_n": 0,
        "real_intervention_hypothesis_n": 20,
        "confirmatory_outcome_n": 10,
        "availability": "complete",
    }
    values.update(overrides)
    return KnowledgeYieldSliceV1(**values)


def _recommend(item):
    from aletheia.learning.knowledge_yield import KnowledgeYieldReportV1
    from aletheia.learning.meta_evolution import recommend_meta_evolution

    report = KnowledgeYieldReportV1(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
        current_economic_knowledge_n=item.qualified_economic_knowledge_n,
        slices=[item],
    )
    return recommend_meta_evolution(report)[0]


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        (_slice(
            real_intervention_hypothesis_n=4,
            confirmatory_outcome_n=4,
        ), "HOLD_INSUFFICIENT_EVIDENCE"),
        (_slice(
            invalid_verdict_n=4,
            refrozen_hypothesis_n=1,
            operational_drag_ratio=0.25,
        ), "RETIRE"),
        (_slice(), "DIFFERENT_TEST"),
        (_slice(
            qualified_economic_knowledge_n=1,
            knowledge_yield=0.05,
        ), "REPLICATE"),
        (_slice(
            qualified_economic_knowledge_n=5,
            knowledge_yield=0.25,
        ), "EXTEND"),
    ],
)
def test_deterministic_1093_vocabulary_and_conservative_rules(item, expected):
    recommendation = _recommend(item)

    assert recommendation.research_action == expected
    assert recommendation.mutation_method == "single_parameter"
    assert recommendation.advisory_only is True
    assert recommendation.may_launch_agent is False
    assert recommendation.may_open_experiment is False
    assert recommendation.may_mutate_trading is False


def test_recommendation_does_not_mutate_learning_ledgers():
    from aletheia.storage.sqlite import get_db

    tables = (
        "learning_hypotheses",
        "learning_experiments",
        "learning_exposures",
        "learning_contribution_verdicts",
    )
    before = {
        table: get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    _recommend(_slice(qualified_economic_knowledge_n=5, knowledge_yield=0.25))
    after = {
        table: get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in tables
    }
    assert before == after


def test_meta_uses_bounded_drag_ratio_directly():
    recommendation = _recommend(_slice(operational_drag_ratio=1.0))
    assert recommendation.research_action == "RETIRE"
    assert recommendation.harm_refreeze_rate == 1.0


def test_none_drag_ratio_treated_as_zero():
    """#2543: zero real-intervention slice has None drag; meta must not error and holds."""
    recommendation = _recommend(_slice(
        real_intervention_hypothesis_n=0,
        confirmatory_outcome_n=0,
        operational_drag_ratio=None,
    ))
    assert recommendation.research_action == "HOLD_INSUFFICIENT_EVIDENCE"
    assert recommendation.harm_refreeze_rate == 0.0

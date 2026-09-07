"""Knowledge Yield counts qualified facts over prospective hypotheses."""



def test_lab_authority_knowledge_yield_is_unavailable():
    from aletheia.learning.knowledge_yield import build_knowledge_yield_report
    from aletheia.observation_lab.canonical import set_authority_source

    set_authority_source("lab", actor="owner", reason="knowledge-yield-authority-test")

    report = build_knowledge_yield_report(
        window_start="2026-08-01T00:00:00+00:00",
        window_end="2026-09-01T00:00:00+00:00",
    )

    assert report.availability == "unavailable"
    assert report.slices == []
    assert report.current_economic_knowledge_n == 0
    assert report.unavailable_reasons == ["canonical_lab_result_projection_unavailable"]


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

"""Executable semantics for Memory/Reflection → Economic Knowledge."""

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_contract"


@spec_claim(SpecClaim(
    "LEK-S01", _CONTRACT, "Economic Knowledge semantics", "L2a",
    "Active Lesson/Skill artifact state alone is never Economic Knowledge.",
    status="implemented",
))
def test_active_lesson_or_skill_is_not_economic_knowledge():
    from aletheia.learning.economic_knowledge import (
        EconomicKnowledgeFactV1,
        project_current_economic_knowledge,
    )

    assert project_current_economic_knowledge() == []
    assert "lesson_id" not in EconomicKnowledgeFactV1.model_fields
    assert "skill_id" not in EconomicKnowledgeFactV1.model_fields


@spec_claim(SpecClaim(
    "LEK-S02", _CONTRACT, "Shadow semantics", "L2a",
    "A Shadow-only arm difference cannot economically qualify a hypothesis.",
    status="implemented",
))
def test_shadow_only_arm_difference_is_not_economic_knowledge():
    from aletheia.learning.economic_knowledge import assess_economic_knowledge
    from tests.test_economic_knowledge import _verdict

    verdict = _verdict(stage="shadow", actual_n=0)
    assessment = assess_economic_knowledge(verdict.verdict_id)

    assert assessment.status == "not_qualified"
    assert "experiment_stage_not_paper" in assessment.reasons
    assert "no_actual_intervention" in assessment.reasons


@spec_claim(SpecClaim(
    "LEK-S03", _CONTRACT, "Economic Knowledge projection", "L2a",
    "Consumed current positive UFC evidence projects read-only without a second verdict truth.",
    status="implemented",
))
def test_consumed_current_positive_ufc_evidence_projects_read_only():
    from aletheia.learning.economic_knowledge import (
        assess_economic_knowledge,
        project_current_economic_knowledge,
    )
    from tests.test_economic_knowledge import _verdict

    verdict = _verdict()
    knowledge = assess_economic_knowledge(verdict.verdict_id)

    assert knowledge.status == "qualified"
    assert knowledge.fact is not None
    assert knowledge.fact.verdict_id == verdict.verdict_id
    assert project_current_economic_knowledge() == [knowledge.fact]
    assert "verdict" not in type(knowledge.fact).model_fields


@spec_claim(SpecClaim(
    "LEK-S04", _CONTRACT, "Nomination lineage", "L2a",
    "Repeated and new-evidence generation attempts have identities separate from behavior identity.",
    status="implemented",
))
def test_new_evidence_nomination_is_not_collapsed_into_one_hypothesis_row():
    from aletheia.learning.hypotheses import nomination_identity

    behavior = "b" * 64
    exact_a = nomination_identity(behavior_fingerprint=behavior, source_bundle_hash="source-a")
    exact_retry = nomination_identity(behavior_fingerprint=behavior, source_bundle_hash="source-a")
    new_evidence = nomination_identity(behavior_fingerprint=behavior, source_bundle_hash="source-b")

    assert exact_a == exact_retry
    assert new_evidence != exact_a

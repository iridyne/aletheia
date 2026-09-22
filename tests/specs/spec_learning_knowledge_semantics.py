"""Executable semantics for Memory/Reflection → Economic Knowledge."""

from tests.specs.spec_meta import SpecClaim, spec_claim


_CONTRACT = "learning_contract"


@spec_claim(SpecClaim(
    "LEK-S01", _CONTRACT, "Economic Knowledge semantics", "L2a",
    "Active Lesson/Skill artifact state alone is never Economic Knowledge.",
    status="implemented",
))
def test_active_lesson_or_skill_is_not_economic_knowledge():
    from extensions.learning.economic_knowledge import (
        EconomicKnowledgeFactV1,
        project_current_economic_knowledge,
    )

    assert project_current_economic_knowledge() == []
    assert "lesson_id" not in EconomicKnowledgeFactV1.model_fields
    assert "skill_id" not in EconomicKnowledgeFactV1.model_fields




@spec_claim(SpecClaim(
    "LEK-S04", _CONTRACT, "Nomination lineage", "L2a",
    "Repeated and new-evidence generation attempts have identities separate from behavior identity.",
    status="implemented",
))
def test_new_evidence_nomination_is_not_collapsed_into_one_hypothesis_row():
    from extensions.learning.hypotheses import nomination_identity

    behavior = "b" * 64
    exact_a = nomination_identity(behavior_fingerprint=behavior, source_bundle_hash="source-a")
    exact_retry = nomination_identity(behavior_fingerprint=behavior, source_bundle_hash="source-a")
    new_evidence = nomination_identity(behavior_fingerprint=behavior, source_bundle_hash="source-b")

    assert exact_a == exact_retry
    assert new_evidence != exact_a

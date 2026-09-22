"""Economic Knowledge inheritance stays scoped and uses existing authority seams."""



def test_unqualified_inheritance_keeps_baseline():
    from extensions.learning.economic_knowledge import inherit_behavioral_economic_knowledge

    result = inherit_behavioral_economic_knowledge(
        verdict_id="archived-positive", candidate_key="opportunity-1",
        baseline=100.0, treatment=110.0, consumer_stage="universe_allocation",
    )
    assert result.value == 100.0
    assert result.applied is False


def test_cognitive_inheritance_is_labeled_scoped_and_not_broadcast():
    from extensions.learning.economic_knowledge import (
        retrieve_cognitive_economic_knowledge,
    )

    matching = retrieve_cognitive_economic_knowledge(
        path_id="universe_family_calibration",
        consumer="universe_allocator",
        scope={"family": "momentum", "direction": "long"},
    )
    other_consumer = retrieve_cognitive_economic_knowledge(
        path_id="universe_family_calibration",
        consumer="paper ranker",
        scope={"family": "momentum", "direction": "long"},
    )

    assert matching.label == "economic_knowledge_evidence"
    assert matching.facts == []
    assert matching.behavior_authority is False
    assert matching.skill_activation is False
    assert other_consumer.facts == []

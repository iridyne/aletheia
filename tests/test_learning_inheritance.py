"""Economic Knowledge inheritance stays scoped and uses existing authority seams."""



def test_retired_behavioral_inheritance_has_no_runtime_entrypoint():
    import inspect

    from aletheia.learning import economic_knowledge, experiments

    assert not hasattr(economic_knowledge, "inherit_behavioral_economic_knowledge")
    parameters = inspect.signature(experiments.apply_numeric_treatment).parameters
    assert not {
        "expected_hypothesis_fingerprint", "expected_experiment_id",
        "suppress_shadow_if_paper_active",
        "deferred_exposure_writes",
    }.intersection(parameters)


def test_cognitive_inheritance_is_labeled_scoped_and_not_broadcast():
    from aletheia.learning.economic_knowledge import (
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

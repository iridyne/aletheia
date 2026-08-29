"""Economic Knowledge inheritance stays scoped and uses existing authority seams."""

from tests.test_economic_knowledge import _verdict


def test_behavioral_inheritance_delegates_to_existing_path_adapter(monkeypatch):
    from aletheia.learning import experiments
    from aletheia.learning.economic_knowledge import (
        inherit_behavioral_economic_knowledge,
    )

    verdict = _verdict()
    captured = {}
    expected = experiments.InfluenceDecision(
        100.0, False, "control", "baseline:control_assignment",
    )

    def fake_adapter(**kwargs):
        captured.update(kwargs)
        return expected

    monkeypatch.setattr(experiments, "apply_numeric_treatment", fake_adapter)
    result = inherit_behavioral_economic_knowledge(
        verdict_id=verdict.verdict_id,
        candidate_key="opportunity-1",
        baseline=100.0,
        treatment=110.0,
        consumer_stage="universe_allocation",
    )

    assert result is expected
    assert captured["path_id"] == "universe_family_calibration"
    assert captured["consumer"] == "universe_allocator"
    assert captured["expected_experiment_id"] == verdict.experiment_id
    assert captured["expected_hypothesis_fingerprint"] == "fingerprint-qualified"


def test_cognitive_inheritance_is_labeled_scoped_and_not_broadcast():
    from aletheia.learning.economic_knowledge import (
        retrieve_cognitive_economic_knowledge,
    )

    _verdict()
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
    assert len(matching.facts) == 1
    assert matching.behavior_authority is False
    assert matching.skill_activation is False
    assert other_consumer.facts == []

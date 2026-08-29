"""Later harm and refreeze remove current qualification but preserve history."""

import json

import pytest

from tests.test_economic_knowledge import _verdict


@pytest.mark.parametrize(
    ("verdict_value", "harm_metrics"),
    [
        ("negative", {}),
        ("invalid", {}),
        ("neutral", {"breached": True}),
    ],
)
def test_later_negative_invalid_or_harm_removes_current_qualification(
    verdict_value, harm_metrics,
):
    from aletheia.learning.economic_knowledge import (
        assess_economic_knowledge,
        project_current_economic_knowledge,
    )
    from aletheia.learning.ledger import insert_record
    from aletheia.learning.models import LearningContributionVerdict

    qualified = _verdict()
    insert_record("learning_contribution_verdicts", LearningContributionVerdict(
        verdict_id=f"later-{verdict_value}",
        experiment_id=qualified.experiment_id,
        hypothesis_id=qualified.hypothesis_id,
        stage="paper_10",
        window_start="2026-08-09T00:00:00+00:00",
        window_end="2026-08-10T00:00:00+00:00",
        unique_treatment_n=50,
        unique_control_n=50,
        harm_metrics_json=json.dumps(harm_metrics),
        verdict=verdict_value,
        reason="later safety evidence",
        created_at="9999-01-01T00:00:00+00:00",
        inference_role="monitoring",
    ))

    assert project_current_economic_knowledge() == []
    assert assess_economic_knowledge(qualified.verdict_id).status == "qualified"


def test_refreeze_retains_history_and_requalification_needs_new_experiment():
    from aletheia.learning.economic_knowledge import (
        assess_economic_knowledge,
        project_current_economic_knowledge,
    )
    from aletheia.learning.ledger import insert_lifecycle_event
    from aletheia.learning.models import LearningLifecycleEvent

    original = _verdict()
    insert_lifecycle_event(LearningLifecycleEvent(
        dedup_key="refreeze-qualified",
        hypothesis_id=original.hypothesis_id,
        experiment_id=original.experiment_id,
        verdict_id=original.verdict_id,
        from_stage="paper_10",
        to_stage="shadow",
        action="refreeze",
        reason="determinate harm",
        created_at="9999-01-01T00:00:00+00:00",
    ))

    assert project_current_economic_knowledge() == []
    assert assess_economic_knowledge(original.verdict_id).fact is not None

    replacement = _verdict(verdict_id="verdict-requalified", suffix="-replacement")
    current = project_current_economic_knowledge()
    assert [fact.verdict_id for fact in current] == [replacement.verdict_id]
    assert current[0].experiment_id != original.experiment_id

"""The compiler emits one declared bounded policy delta or advisory-only."""

from tests.test_learning_hypothesis_nominations import _envelope


def test_compiler_uses_registry_consumer_and_one_action_parameter():
    from extensions.learning.hypothesis_compiler import compile_hypothesis_draft

    envelope = _envelope("compiler-source")
    draft = compile_hypothesis_draft(
        path_id="ranker_case_score",
        research_evidence=envelope.research_evidence,
        t0_foundation=envelope.t0_foundation,
        scope={"symbol": "BTC/USDT", "direction": "long"},
        baseline_action={"type": "score_adjustment", "delta": 0.0},
        treatment_action={"type": "score_adjustment", "delta": -1.0},
        source_type="reflection",
        source_artifact_ids=["case-1"],
        source_cohort="compiler-cohort",
        target_horizon="4h",
        effect_threshold=0.01,
        harm_budget={"max_loss_rate_increase": 0.15},
    )

    assert draft.status == "compilable"
    assert draft.consumer == "paper ranker"
    assert draft.reasons == []


def test_compiler_rejects_free_form_or_multi_parameter_treatment():
    from extensions.learning.hypothesis_compiler import compile_hypothesis_draft

    envelope = _envelope("compiler-invalid")
    draft = compile_hypothesis_draft(
        path_id="ranker_case_score",
        research_evidence=envelope.research_evidence,
        t0_foundation=envelope.t0_foundation,
        scope={"symbol": "BTC/USDT", "direction": "long"},
        baseline_action={"type": "score_adjustment", "delta": 0.0, "cap": 0.0},
        treatment_action={"type": "score_adjustment", "delta": -1.0, "cap": 2.0},
        source_type="reflection",
        source_artifact_ids=["case-1"],
        source_cohort="compiler-invalid-cohort",
        target_horizon="4h",
        effect_threshold=0.01,
        harm_budget={"max_loss_rate_increase": 0.15},
    )

    assert draft.status == "advisory_only"
    assert "treatment_must_change_exactly_one_parameter" in draft.reasons

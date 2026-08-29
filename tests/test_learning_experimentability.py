"""Feasibility gates the existing Shadow experiment ledger without new authority."""

from tests.test_learning_hypothesis_nominations import _envelope


def _preflight(path_id: str, hypothesis_id: str, **overrides):
    from aletheia.learning.hypothesis_compiler import assess_experimentability

    values = {
        "path_id": path_id,
        "hypothesis_id": hypothesis_id,
        "historical_eligible_n": 200,
        "distinct_market_days": 20,
        "eligible_event_rate_per_day": 20.0,
        "outcome_adapter_available": True,
        "outcome_settlement_rate": 0.9,
        "expected_treatment_assignment_rate": 0.5,
        "expected_actual_intervention_rate": 0.8,
        "required_samples_per_arm": 50,
        "required_market_days": 7,
    }
    values.update(overrides)
    return assess_experimentability(**values)


def test_unsupported_and_nonviable_preflight_open_no_experiment():
    from aletheia.learning.hypothesis_compiler import admit_nomination_to_shadow
    from aletheia.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.learning.ledger import list_records

    nomination = publish_hypothesis_nomination(_envelope("preflight-unsupported"))
    unsupported = _preflight(
        "ranker_learned_guard", nomination.hypothesis_id or "missing",
    )
    assert unsupported.status == "unsupported"
    assert admit_nomination_to_shadow(nomination.nomination_id, unsupported) is None

    nonviable = _preflight(
        "ranker_learned_guard",
        nomination.hypothesis_id or "missing",
        outcome_adapter_available=False,
        historical_eligible_n=0,
        eligible_event_rate_per_day=None,
    )
    assert nonviable.status == "unsupported"
    assert admit_nomination_to_shadow(nomination.nomination_id, nonviable) is None
    assert list_records("learning_experiments") == []


def test_viable_preflight_opens_exactly_one_shadow_experiment(monkeypatch):
    from aletheia.learning.hypothesis_compiler import admit_nomination_to_shadow
    from aletheia.learning.hypotheses import publish_hypothesis_nomination
    from aletheia.learning.ledger import list_records

    envelope = _envelope("preflight-ready")
    nomination = publish_hypothesis_nomination(envelope)
    hypothesis_id = nomination.hypothesis_id or "missing"
    preflight = _preflight("ranker_case_score", hypothesis_id)

    assert preflight.status == "ready"
    experiment = admit_nomination_to_shadow(nomination.nomination_id, preflight)
    assert experiment is not None
    assert experiment.stage == "shadow"
    assert experiment.status == "registered"
    repeated = admit_nomination_to_shadow(nomination.nomination_id, preflight)
    assert repeated is not None
    assert repeated.experiment_id == experiment.experiment_id
    assert len(list_records("learning_experiments")) == 1
    from aletheia.learning import experiments

    monkeypatch.setattr(experiments, "_learning_gate_reason", lambda: None)
    decision = experiments.apply_numeric_treatment(
        path_id="ranker_case_score",
        scope={"symbol": "BTC/USDT", "direction": "long"},
        action={"type": "score_adjustment", "delta": -1.0},
        source_cohort="cohort-1",
        target_horizon="4h",
        candidate_key="opportunity-shadow",
        baseline=100.0,
        treatment=99.0,
        consumer="paper ranker",
        consumer_stage="ranking",
    )
    assert decision.value == 100.0
    assert decision.applied is False
    assert decision.reason == "baseline:experiment_stage_not_paper"
    assert len(list_records("learning_exposures")) == 1
    assert list_records("learning_contribution_verdicts") == []

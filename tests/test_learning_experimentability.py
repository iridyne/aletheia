"""Feasibility is evidence; Observation Lab owns experiment admission."""

from tests.test_learning_hypothesis_nominations import _envelope


def _preflight(path_id: str, hypothesis_id: str, **overrides):
    from extensions.learning.hypothesis_compiler import assess_experimentability

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
    """Retired-capability guard: shadow admission was removed by the 2026-09-07
    single-mainline cutover (Observation Lab is the only Experiment authority).
    Preflight still classifies feasibility; nothing may open an experiment.
    """
    import extensions.learning.hypothesis_compiler as compiler
    from extensions.learning.hypotheses import publish_hypothesis_nomination
    from extensions.learning.ledger import list_records

    assert not hasattr(compiler, "admit_nomination_to_shadow"), (
        "admit_nomination_to_shadow was retired; a reinstated shadow-admission "
        "path would bypass Observation Lab experiment authority"
    )

    nomination = publish_hypothesis_nomination(_envelope("preflight-unsupported"))
    unsupported = _preflight(
        "ranker_learned_guard", nomination.hypothesis_id or "missing",
    )
    assert unsupported.status == "unsupported"

    nonviable = _preflight(
        "ranker_learned_guard",
        nomination.hypothesis_id or "missing",
        outcome_adapter_available=False,
        historical_eligible_n=0,
        eligible_event_rate_per_day=None,
    )
    assert nonviable.status == "unsupported"
    assert list_records("learning_experiments") == []


def test_viable_preflight_does_not_open_an_experiment():
    """Retired-capability guard: a `ready` preflight is feasibility evidence only.
    Without shadow admission no experiment may be opened by this path.
    """
    from extensions.learning.hypotheses import publish_hypothesis_nomination
    from extensions.learning.ledger import list_records

    envelope = _envelope("preflight-ready")
    nomination = publish_hypothesis_nomination(envelope)
    hypothesis_id = nomination.hypothesis_id or "missing"
    preflight = _preflight("ranker_case_score", hypothesis_id)

    assert preflight.status == "ready"
    assert list_records("learning_experiments") == []

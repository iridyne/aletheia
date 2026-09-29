"""Contract A (Issue #3729): the AI declares a risk posture, never a number."""

import math
from types import SimpleNamespace

import pytest
from typing import cast
from pydantic import ValidationError

from aletheia.config.schema import ModeRiskPolicy, RiskLossPolicy
from aletheia.trading.entry_analysis import EntryHypothesis
from aletheia.trading.risk_intent import (
    REASON_RISK_INTENT_INVALID,
    RiskIntent,
    RiskIntentInvalid,
    resolve_risk_intent_multiplier,
)
from tests.test_entry_analysis import _anchor, _risk_intent


def _entry_plan_kwargs(primary, invalidation, *, risk_intent=None) -> dict:
    return dict(
        decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="grounded-anchor-direct", capture_priority="PRICE_FIRST",
        capture_reason="fixture current evidence", capture_evidence_refs=(invalidation.anchor_id,),
        risk_tier="LOW", horizon="INTRADAY", supporting_fact_refs=(invalidation.anchor_id,),
        risk_intent=risk_intent,
    )


def test_baseline_multiplier_is_one_regardless_of_policy():
    intent = RiskIntent(posture="BASELINE", reason="hold owner baseline", evidence_refs=("candidate:c1",))
    multiplier, source = resolve_risk_intent_multiplier(intent, ModeRiskPolicy(policy_id="test"))
    assert (multiplier, source) == (1.0, "baseline")


def test_reduced_multiplier_reads_owner_policy_value():
    intent = RiskIntent(posture="REDUCED", reason="thin liquidity", evidence_refs=("candidate:c1",))
    policy = ModeRiskPolicy(policy_id="test", loss=RiskLossPolicy(reduced_risk_multiplier=0.3))
    multiplier, source = resolve_risk_intent_multiplier(intent, policy)
    assert (multiplier, source) == (0.3, "policy.loss.reduced_risk_multiplier")


@pytest.mark.parametrize("bad_multiplier", [1.0, 1.5, 0.0, -0.2, None, math.nan, math.inf])
def test_reduced_multiplier_rejects_out_of_bounds_policy_value(bad_multiplier):
    intent = RiskIntent(posture="REDUCED", reason="thin liquidity", evidence_refs=("candidate:c1",))
    policy = SimpleNamespace(loss=SimpleNamespace(reduced_risk_multiplier=bad_multiplier))
    with pytest.raises(RiskIntentInvalid) as exc_info:
        resolve_risk_intent_multiplier(intent, cast(ModeRiskPolicy, policy))
    assert exc_info.value.reason == REASON_RISK_INTENT_INVALID


def test_resolve_multiplier_rejects_none_intent_typed_not_attributeerror():
    with pytest.raises(RiskIntentInvalid) as exc_info:
        resolve_risk_intent_multiplier(None, ModeRiskPolicy(policy_id="test"))
    assert exc_info.value.reason == REASON_RISK_INTENT_INVALID


def test_reduced_multiplier_rejects_missing_loss_policy():
    intent = RiskIntent(posture="REDUCED", reason="thin liquidity", evidence_refs=("candidate:c1",))
    with pytest.raises(RiskIntentInvalid) as exc_info:
        resolve_risk_intent_multiplier(intent, cast(ModeRiskPolicy, SimpleNamespace(loss=None)))
    assert exc_info.value.reason == REASON_RISK_INTENT_INVALID


def test_risk_intent_has_no_increased_posture():
    with pytest.raises(ValidationError):
        RiskIntent(**{"posture": "INCREASED", "reason": "scale up", "evidence_refs": ("candidate:c1",)})


@pytest.mark.parametrize("evidence_refs", [(), tuple(f"candidate:c{i}" for i in range(9))])
def test_risk_intent_evidence_refs_bounded_one_to_eight(evidence_refs):
    with pytest.raises(ValidationError):
        RiskIntent(posture="BASELINE", reason="hold", evidence_refs=evidence_refs)


def test_risk_intent_reason_must_be_nonempty():
    with pytest.raises(ValidationError):
        RiskIntent(posture="BASELINE", reason="", evidence_refs=("candidate:c1",))


@pytest.mark.parametrize(
    "intent",
    [
        None,
        "BASELINE",
        {"posture": "BASELINE"},
        {"posture": "BASELINE", "reason": "", "evidence_refs": []},
    ],
)
def test_omitted_or_partial_risk_intent_resolves_to_bounded_baseline(intent):
    """A model that omits or under-specifies the intent expresses no reduction.

    The Owner baseline posture therefore stands, with the reason and evidence the
    hypothesis already had to supply — the planner keeps working, and the AI still
    cannot express a number or increase risk (that is asserted separately below).
    """
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    hypothesis = EntryHypothesis(
        **_entry_plan_kwargs(primary, invalidation, risk_intent=intent)
    )

    assert hypothesis.risk_intent is not None
    assert hypothesis.risk_intent.posture == "BASELINE"
    assert hypothesis.risk_intent.reason == hypothesis.capture_reason
    assert hypothesis.risk_intent.evidence_refs == tuple(hypothesis.capture_evidence_refs)


def test_partial_reduced_intent_keeps_its_posture():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    hypothesis = EntryHypothesis(
        **_entry_plan_kwargs(primary, invalidation, risk_intent="REDUCED")
    )

    assert hypothesis.risk_intent is not None
    assert hypothesis.risk_intent.posture == "REDUCED"
    assert hypothesis.risk_intent.evidence_refs == tuple(hypothesis.capture_evidence_refs)


def test_increased_posture_is_never_accepted():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    with pytest.raises(ValidationError):
        EntryHypothesis(**_entry_plan_kwargs(
            primary, invalidation,
            risk_intent={"posture": "INCREASED", "reason": "scale up", "evidence_refs": ["candidate:c1"]},
        ))


def test_entry_plan_carries_valid_risk_intent():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    intent = _risk_intent(invalidation.anchor_id)
    hypothesis = EntryHypothesis(**_entry_plan_kwargs(primary, invalidation, risk_intent=intent))
    assert hypothesis.risk_intent == intent


def test_no_plan_clears_risk_intent():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    hypothesis = EntryHypothesis(
        decision="NO_PLAN", reason_code="CONFLICTING_SIGNALS",
        mechanism="reversal", direction="long", risk_tier="LOW", horizon="INTRADAY",
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="grounded-anchor-direct", capture_priority="PRICE_FIRST",
        capture_reason="fixture current evidence", capture_evidence_refs=(invalidation.anchor_id,),
        supporting_fact_refs=(invalidation.anchor_id,),
        risk_intent=_risk_intent(invalidation.anchor_id),
    )
    assert hypothesis.risk_intent is None


def test_entry_hypothesis_rejects_numeric_leverage_field():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    kwargs = _entry_plan_kwargs(primary, invalidation, risk_intent=_risk_intent(invalidation.anchor_id))
    with pytest.raises(ValidationError):
        EntryHypothesis(**{**kwargs, "leverage": 3})

"""Compiler capture semantics remain bounded by supplied evidence and Plan geometry."""
import pytest

from aletheia.trading.entry_analysis import EntryAnalysisContext, EntryHypothesis, validate_hypothesis_references
from aletheia.trading.entry_capture import CAPTURE_POLICIES
from aletheia.trading.entry_contract import entry_contract_fingerprints
from aletheia.trading.entry_plan_compiler import compile_entry_plan
from tests.test_entry_analysis import _anchor, _context_v2


def capture_input(monkeypatch, profile, priority, direction="long"):
    from aletheia.config.loader import load_config
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *a: 5000.)
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    primary = _anchor("confirmed_swing", 100, "swing:p")
    invalidation = _anchor("confirmed_swing", 95 if direction == "long" else 105, "swing:i")
    data = _context_v2((primary, invalidation)).model_dump(exclude={"context_fingerprint"})
    behavior, schema = entry_contract_fingerprints()
    generation = {"id": "g1", "last_candidate_id": "c1", "candidate_family": "mean_reversion",
        "venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": direction,
        "entry_behavior_fingerprint": behavior, "schema_fingerprint": schema,
        "producer_commit": "isolated-test", "planning_identity": "a"*64, "cycle_number": 1}
    data["planning_generation"].update(entry_behavior_fingerprint=behavior, schema_fingerprint=schema)
    data["candidate"]["candidate_price"] = 100
    data["candidate_opportunity_context"]["source"] = {"kind": "universe", "id": "c1", "trade_school": "short_term"}
    data["market_structure"]["15m"].update(atr14=2, break_tolerance=.1)
    data["execution_context"].update(bid=99.9, ask=100.1, spread_bps=2, depth_10bps_usd=100000, estimated_costs=.1)
    data["instrument"]["direction"] = direction
    data["candidate_opportunity_context"]["opportunity"]["direction"] = direction
    context = EntryAnalysisContext.model_validate(data)
    hypothesis = EntryHypothesis(decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction=direction,
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id=profile, capture_priority=priority, capture_reason="supplied current structure supports capture",
        capture_evidence_refs=(invalidation.anchor_id,), risk_tier="LOW", horizon="SWING",
        supporting_fact_refs=(invalidation.anchor_id,))
    return generation, context, hypothesis, cfg


@pytest.mark.parametrize("profile,activation", [("grounded-anchor-direct", "IMMEDIATE"), ("grounded-anchor-retrace-reclaim", "CONDITIONAL")])
@pytest.mark.parametrize("priority", list(CAPTURE_POLICIES))
def test_six_capture_cells_freeze_compiler_policy(monkeypatch, profile, activation, priority):
    plan = compile_entry_plan(*capture_input(monkeypatch, profile, priority))
    policy = CAPTURE_POLICIES[priority]
    assert plan.activation_policy == activation
    assert plan.capture_priority == priority
    assert plan.capture_policy_id == policy.policy_id
    assert plan.max_wait_sec == policy.passive_attempt_timeout_sec
    assert plan.worst_price is not None
    assert plan.entry_zone[0] <= plan.worst_price <= plan.entry_zone[1]
    assert plan.target_quantity * max(plan.entry_zone) == pytest.approx(plan.max_notional)


def test_capture_requires_current_supplied_evidence(monkeypatch):
    _, context, hypothesis, _ = capture_input(monkeypatch, "grounded-anchor-direct", "PRICE_FIRST")
    with pytest.raises(ValueError, match="fact_ref_not_supplied"):
        validate_hypothesis_references(hypothesis.model_copy(update={"capture_evidence_refs": ("invented",)}), context)
    with pytest.raises(ValueError, match="capture_current_fact_required"):
        validate_hypothesis_references(hypothesis.model_copy(update={"capture_evidence_refs": ()}), context)
    no_plan = EntryHypothesis.model_validate({**hypothesis.model_dump(), "decision": "NO_PLAN", "reason_code": "INSUFFICIENT_EVIDENCE"})
    assert no_plan.capture_priority is None and no_plan.capture_reason is None and not no_plan.capture_evidence_refs


def test_capture_plan_rejects_policy_or_timeout_tampering(monkeypatch):
    from aletheia.trading.entry_plan import EntryPlan
    plan = compile_entry_plan(*capture_input(monkeypatch, "grounded-anchor-direct", "MAKER_PREFERRED"))
    for changes, reason in [({"max_wait_sec": 600}, "timeout_mismatch"),
                            ({"capture_policy_id": "invented"}, "policy_not_registered"),
                            ({"worst_price": plan.entry_zone[1]+10}, "price_bound_required")]:
        with pytest.raises(ValueError, match=reason):
            EntryPlan.model_validate({**plan.model_dump(), **changes})


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("priority", ["MAKER_PREFERRED", "FILL_FIRST"])
def test_compiled_direct_ioc_accepts_improvement_without_repricing(monkeypatch, direction, priority):
    from aletheia.execution.entry_plan_execution import bounded_entry_ioc_price
    plan = compile_entry_plan(*capture_input(monkeypatch, "grounded-anchor-direct", priority, direction))
    original = plan.model_dump_json()
    assert plan.entry_zone == (100., 100.)
    bid, ask = (99.98, 99.99) if direction == "long" else (100.01, 100.02)
    assert bounded_entry_ioc_price(plan, bid=bid, ask=ask) == 100.
    assert plan.model_dump_json() == original
    worse_bid, worse_ask = (100.01, 100.02) if direction == "long" else (99.98, 99.99)
    with pytest.raises(ValueError, match="worst_price_exceeded"):
        bounded_entry_ioc_price(plan, bid=worse_bid, ask=worse_ask)
    conditional = compile_entry_plan(*capture_input(monkeypatch, "grounded-anchor-retrace-reclaim", priority, direction))
    with pytest.raises(ValueError, match="outside_frozen_bounds"):
        bounded_entry_ioc_price(conditional, bid=bid, ask=ask)
    passive = compile_entry_plan(*capture_input(monkeypatch, "grounded-anchor-direct", "PRICE_FIRST", direction))
    with pytest.raises(ValueError, match="taker_bounds_required"):
        bounded_entry_ioc_price(passive, bid=bid, ask=ask)

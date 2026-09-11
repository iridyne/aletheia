"""Accepted capture decisions over real compiled policies; no external authority."""
from dataclasses import replace

import pytest

from aletheia.trading.entry_execution_projection import EntryExecutionFacts, resolve_entry_execution
from aletheia.trading.entry_plan import entry_plan_payload
from aletheia.trading.entry_plan_compiler import compile_entry_plan
from tests.test_entry_capture import capture_input


def plan_for(monkeypatch, priority="MAKER_PREFERRED", conditional=False):
    return compile_entry_plan(*capture_input(monkeypatch,
        "grounded-anchor-retrace-reclaim" if conditional else "grounded-anchor-direct", priority))


def facts(**changes):
    return replace(EntryExecutionFacts(plan_status="active", activation_current=True,
        filled_quantity=0, passive_eligible=True, ioc_eligible=True), **changes)


@pytest.mark.parametrize("conditional", [False, True])
@pytest.mark.parametrize("priority,command", [
    ("PRICE_FIRST", "SUBMIT_PASSIVE"), ("MAKER_PREFERRED", "SUBMIT_PASSIVE"), ("FILL_FIRST", "SUBMIT_IOC")])
def test_six_cells_request_gateway_and_preserve_immutable_plan(monkeypatch, conditional, priority, command):
    plan = plan_for(monkeypatch, priority, conditional)
    before = entry_plan_payload(plan)
    result = resolve_entry_execution(plan, facts())
    assert (result.action, result.command, result.leg_index) == ("COMMAND", command, 0)
    assert result.quantity == plan.target_quantity
    assert entry_plan_payload(plan) == before


@pytest.mark.parametrize("priority,age,expected", [
    ("PRICE_FIRST", 3600, None), ("MAKER_PREFERRED", 59, None),
    ("MAKER_PREFERRED", 60, "CANCEL_PASSIVE"), ("MAKER_PREFERRED", 61, "CANCEL_PASSIVE"),
])
def test_only_maker_preferred_has_passive_timeout(monkeypatch, priority, age, expected):
    result = resolve_entry_execution(plan_for(monkeypatch, priority), facts(
        order_status="partially_filled", order_tactic="POST_ONLY_LIMIT", order_age_sec=age))
    assert result.command == expected


def test_confirmed_remainder_is_one_leg_and_does_not_retrigger(monkeypatch):
    plan = plan_for(monkeypatch, conditional=True)
    result = resolve_entry_execution(plan, facts(activation_current=False, order_status="cancelled",
        order_tactic="POST_ONLY_LIMIT", filled_quantity=plan.target_quantity*.4))
    assert (result.command, result.leg_index) == ("SUBMIT_IOC", 1)
    assert result.quantity == pytest.approx(plan.target_quantity*.6)
    terminal = resolve_entry_execution(plan, facts(order_status="cancelled", order_leg_index=1,
        order_tactic="OPPOSING_BBO_IOC", filled_quantity=plan.target_quantity*.7))
    assert terminal.action == "DONE" and terminal.command is None


@pytest.mark.parametrize("priority,action,command", [
    ("PRICE_FIRST", "WAIT", None), ("MAKER_PREFERRED", "COMMAND", "SUBMIT_IOC"),
    ("FILL_FIRST", "COMMAND", "SUBMIT_IOC"),
])
def test_crossing_anchor_selects_only_accepted_capture_tactic(monkeypatch, priority, action, command):
    result = resolve_entry_execution(plan_for(monkeypatch, priority), facts(passive_eligible=False))
    assert (result.action, result.command) == (action, command)


def test_pending_immutable_passive_proposal_cannot_turn_into_ioc(monkeypatch):
    result = resolve_entry_execution(plan_for(monkeypatch), facts(passive_eligible=False,
        proposal_status="pending", proposal_leg_index=0, proposal_tactic="POST_ONLY_LIMIT"))
    assert result.action == "WAIT" and result.command is None


@pytest.mark.parametrize("changes,expected", [
    ({"activation_current": False}, "WAIT"),
    ({"market_wait_reason": "entry_submission_market_stale"}, "WAIT"),
    ({"preparation_claim_held": True}, "WAIT"),
    ({"risk_disposition": "DEFER"}, "WAIT"),
    ({"risk_disposition": "REJECT"}, "STOP"),
    ({"authority_error": "entry_plan_hash_mismatch"}, "STOP"),
    ({"order_status": "submission_unknown"}, "RECONCILE"),
    ({"proposal_status": "materialization_missing", "order_status": "filled"}, "RECONCILE"),
    ({"proposal_status": "executing", "proposal_leg_index": 1, "order_status": "cancelled",
      "order_tactic": "POST_ONLY_LIMIT"}, "RECONCILE"),
    ({"order_status": "cancelled", "order_tactic": "POST_ONLY_LIMIT", "filled_quantity": None}, "RECONCILE"),
    ({"order_status": "partially_filled", "cancellation_unresolved": True}, "RECONCILE"),
    ({"order_status": "submission_rejected"}, "DONE"),
])
def test_no_new_command_across_unresolved_or_revoked_boundaries(monkeypatch, changes, expected):
    result = resolve_entry_execution(plan_for(monkeypatch, conditional=True), facts(**changes))
    assert result.action == expected and result.command is None


@pytest.mark.parametrize("authority", ["entry_plan_expired", "entry_plan_risk_authority_revoked"])
def test_terminal_authority_cancels_working_maker(monkeypatch, authority):
    result = resolve_entry_execution(plan_for(monkeypatch), facts(order_status="submitted",
        order_tactic="POST_ONLY_LIMIT", authority_error=authority))
    assert result.command == "CANCEL_PASSIVE" and result.reason == authority


def test_fill_wins_cancel_race_without_another_leg(monkeypatch):
    plan = plan_for(monkeypatch)
    result = resolve_entry_execution(plan, facts(order_status="filled", order_tactic="POST_ONLY_LIMIT",
        filled_quantity=plan.target_quantity, authority_error="entry_plan_expired"))
    assert result.action == "DONE" and result.reason == "entry_target_filled"


def test_projection_has_no_effects_or_implicit_clock(monkeypatch):
    import builtins
    import socket
    from aletheia.storage import repositories as repo
    plan = plan_for(monkeypatch)
    snapshot = facts(order_status="submitted", order_tactic="POST_ONLY_LIMIT", order_age_sec=60)
    before = entry_plan_payload(plan)
    def forbidden(*args, **kwargs):
        pytest.fail("projection performed I/O")
    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(socket, "socket", forbidden)
        guard.setattr(repo, "get_db", forbidden)
        first = resolve_entry_execution(plan, snapshot)
        assert resolve_entry_execution(plan, snapshot) == first
        assert entry_plan_payload(plan) == before
    assert first.command == "CANCEL_PASSIVE"


def test_ioc_limit_is_frozen_while_current_bbo_moves_inside_envelope(monkeypatch):
    from aletheia.execution.entry_plan_execution import bounded_entry_ioc_price
    plan = plan_for(monkeypatch, "FILL_FIRST", conditional=True)
    first = bounded_entry_ioc_price(plan, bid=100.08, ask=100.10)
    later = bounded_entry_ioc_price(plan, bid=100.13, ask=100.15)
    assert first == later
    assert 100.15 <= later <= plan.worst_price
    with pytest.raises(ValueError, match="entry_ioc_outside_frozen_bounds"):
        bounded_entry_ioc_price(plan, bid=later+.01, ask=later+.02)

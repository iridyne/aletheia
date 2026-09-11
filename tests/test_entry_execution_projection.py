"""Stage 1 equivalence plus executable, deliberately unimplemented product cases."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aletheia.trading.entry_execution_projection import (
    EntryExecutionFacts, normalize_entry_execution, resolve_entry_execution,
)
from aletheia.trading.entry_plan import EntryPlan, entry_plan_payload
from tests.test_entry_plan_execution import _plan


def facts(**changes):
    return replace(EntryExecutionFacts(
        plan_status="waiting", authority_error=None, activation_current=True,
        market_wait_reason=None, risk_approved=True, risk_reason=None,
        preparation_claim_held=False, proposal_status=None, order_status=None,
        order_age_sec=None, cancellation_unresolved=False,
    ), **changes)


@pytest.mark.parametrize("mode,tactic,activation,capture", [
    ("RESTING_MAKER", "POST_ONLY_LIMIT", "IMMEDIATE", "PRICE_FIRST"),
    ("TRIGGERED", "POST_ONLY_LIMIT", "CONDITIONAL", "PRICE_FIRST"),
    ("TRIGGERED", "OPPOSING_BBO_IOC", "CONDITIONAL", "FILL_FIRST"),
])
def test_normalization_preserves_bytes_and_matches_current_intent(monkeypatch, mode, tactic, activation, capture):
    from aletheia.execution import entry_plan_execution as execution
    plan = _plan(mode=mode, tactic=tactic)
    before = entry_plan_payload(plan)
    projection = normalize_entry_execution(plan)
    assert (projection.activation, projection.capture_priority) == (activation, capture)
    assert projection.max_wait_sec == plan.max_wait_sec
    monkeypatch.setattr(execution, "require_entry_plan_runtime_authority", lambda p: None)
    monkeypatch.setattr("aletheia.trading.entry_activation.require_entry_trigger_activation", lambda *a, **k: None)
    now = datetime.now(timezone.utc)
    intent = execution.build_entry_plan_order_intent(plan, bid=99.9, ask=100.1,
        observed_at=now.isoformat(), source_observed_at=now.isoformat(), now=now)
    directive = resolve_entry_execution(plan, facts())
    assert directive.action == "COMMAND"
    assert directive.command == ("SUBMIT_PASSIVE" if intent.post_only else "SUBMIT_IOC")
    assert entry_plan_payload(plan) == before
    assert resolve_entry_execution(plan, facts()) == directive


@pytest.mark.parametrize("changes,action,command", [
    ({"market_wait_reason": "post_only_would_cross_bbo"}, "WAIT", None),
    ({"market_wait_reason": "entry_submission_market_stale"}, "WAIT", None),
    ({"preparation_claim_held": True}, "WAIT", None),
    ({"risk_approved": None}, "WAIT", None),
    ({"risk_approved": False, "risk_reason": "hard_risk_rejection"}, "STOP", None),
    ({"authority_error": "entry_plan_hash_mismatch"}, "STOP", None),
    ({"plan_status": "expired"}, "STOP", None),
    ({"proposal_status": "executing"}, "RECONCILE", None),
    ({"order_status": "submission_unknown", "plan_status": "expired"}, "RECONCILE", None),
    ({"order_status": "partial", "cancellation_unresolved": True}, "RECONCILE", None),
    ({"order_status": "filled"}, "DONE", None),
    ({"order_status": "submission_rejected"}, "DONE", None),
    ({"order_status": "submitted", "order_age_sec": 59}, "WAIT", None),
    ({"order_status": "partial", "order_age_sec": 60}, "COMMAND", "CANCEL_PASSIVE"),
    ({"order_status": "cancelled"}, "DONE", None),
])
def test_existing_owner_boundaries(changes, action, command):
    directive = resolve_entry_execution(_plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT"), facts(**changes))
    assert (directive.action, directive.command) == (action, command)


def test_activation_cannot_reauthorize_an_existing_order():
    plan = _plan(mode="TRIGGERED")
    assert resolve_entry_execution(plan, facts(activation_current=False)).action == "WAIT"
    assert resolve_entry_execution(plan, facts(activation_current=False,
        order_status="submission_unknown")).action == "RECONCILE"
    assert resolve_entry_execution(plan, facts(activation_current=False,
        order_status="filled")).action == "DONE"


@pytest.mark.parametrize("case", ["immediate_ioc", "maker_preferred", "partial_fallback", "ambiguous_cancel"])
@pytest.mark.xfail(strict=True, raises=ValueError, reason="Stage 2/4 capture contract is intentionally absent in Stage 1")
def test_missing_capture_contract_red(case):
    """Run with --runxfail to retain RED evidence; never enable future authority here."""
    data = _plan(mode="TRIGGERED", tactic="OPPOSING_BBO_IOC" if case == "immediate_ioc" else "POST_ONLY_LIMIT", provenance=False).model_dump()
    if case == "immediate_ioc":
        data.update(execution_mode="RESTING_MAKER", trigger_spec_id=None, trigger_params={}, trigger_data_requirements=(), trigger_freshness_ms=None)
    else:
        data["capture_priority"] = "MAKER_PREFERRED"
    # This is the real current immutable contract, not an invented unsupported stub.
    try:
        plan = EntryPlan.model_validate(data)
    except ValueError as exc:
        expected = "urgent_taker_requires_triggered_plan" if case == "immediate_ioc" else "capture_priority"
        assert expected in str(exc), "RED must expose the missing product contract"
        raise
    current = facts()
    expected = "SUBMIT_IOC" if case == "immediate_ioc" else "SUBMIT_PASSIVE"
    if case == "partial_fallback":
        current = facts(order_status="cancelled")
        expected = "SUBMIT_IOC"
    if case == "ambiguous_cancel":
        current = facts(order_status="partial", cancellation_unresolved=True)
        assert resolve_entry_execution(plan, current).action == "RECONCILE"
    else:
        assert resolve_entry_execution(plan, current).command == expected


def test_projection_has_no_effects_or_implicit_clock(monkeypatch):
    import builtins
    import socket
    from aletheia.storage import repositories as repo
    plan = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    snapshot = facts(order_status="submitted", order_age_sec=60)
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


@pytest.mark.asyncio
@pytest.mark.parametrize("age", [59, 60, 61])
async def test_original_watcher_timeout_matches_projection(monkeypatch, age):
    from datetime import timedelta
    from types import SimpleNamespace
    from aletheia.agent_runtime import fast_trigger_watch as watch
    from aletheia.execution import gateway
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as repo
    plan = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT", provenance=False)
    payload, digest = entry_plan_payload(plan)
    now = datetime.now(timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(watch, "datetime", Clock)
    monkeypatch.setattr(watch, "historical_plan_version", lambda p: None)
    monkeypatch.setattr(repo, "list_watchable_entry_plans", lambda *a: [{
        "id": plan.plan_id, "plan_json": payload, "plan_hash": digest,
        "status": "ordered", "order_id": "existing-order",
    }])
    monkeypatch.setattr(repo, "get_candidate_planning_generation", lambda *a: {"is_current": True})
    monkeypatch.setattr(repo, "get_order", lambda *a: {"created_at": (now-timedelta(seconds=age)).isoformat()})
    monkeypatch.setattr("aletheia.risk.policy.resolve_risk_policy", lambda *a: SimpleNamespace(
        policy=SimpleNamespace(policy_id=plan.risk_policy_id, revision=plan.risk_policy_revision), policy_hash=plan.risk_policy_hash))
    monkeypatch.setattr(watch, "fast_market_data_unavailability_reason", lambda *a: None)
    cancelled = []
    monkeypatch.setattr(gateway, "cancel_entry_plan_order", lambda *a, **k: cancelled.append(a))
    cfg = load_config()
    cfg.exchange.paper_execution_mode = plan.execution_account_mode
    await watch._load_entry_plan_watchers(cfg)
    directive = resolve_entry_execution(plan, facts(plan_status="ordered", order_status="submitted", order_age_sec=age))
    assert bool(cancelled) == (directive.command == "CANCEL_PASSIVE")


@pytest.mark.parametrize("proposal_status,order_status", [
    ("partially_filled", None), ("executed", None),
    ("materialization_missing", None), ("materialization_missing", "filled"),
])
def test_handoff_evidence_cannot_become_new_authority(proposal_status, order_status):
    result = resolve_entry_execution(_plan(mode="TRIGGERED"),
        facts(proposal_status=proposal_status, order_status=order_status))
    assert result.action == "RECONCILE"
    assert result.command is None


@pytest.mark.parametrize("order_status", ["submitted", "partial", "partially_filled"])
@pytest.mark.parametrize("authority", ["entry_plan_expired", "entry_plan_risk_authority_revoked"])
def test_terminal_authority_cancels_known_working_maker(order_status, authority):
    result = resolve_entry_execution(_plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT"),
        facts(order_status=order_status, authority_error=authority))
    assert result.command == "CANCEL_PASSIVE"
    assert result.reason == authority

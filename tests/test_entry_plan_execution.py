from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, RiskDecision, UniverseMember, UniverseSnapshot
from aletheia.trading.entry_plan import EntryPlan, entry_plan_payload


def _plan(*, mode="TRIGGERED", tactic="OPPOSING_BBO_IOC", account_mode="bitget_demo") -> EntryPlan:
    now = datetime.now(timezone.utc)
    triggered = mode == "TRIGGERED"
    maker = tactic == "POST_ONLY_LIMIT"
    return EntryPlan(
        planning_generation_id="gen-1",
        candidate_id="candidate-1",
        candidate_family="momentum",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        direction="long",
        thesis="bounded continuation",
        scenario="retrace reclaim",
        execution_mode=mode,
        trigger_spec_id="momentum-retrace-reclaim" if triggered else None,
        trigger_spec_version="v1" if triggered else None,
        trigger_params={
            "breakout_level": 100,
            "pullback_low": 99,
            "pullback_high": 100,
            "entry_low": 100,
            "entry_high": 101,
            "invalidation_price": 98,
        }
        if triggered
        else {},
        trigger_data_requirements=("quote", "trade", "books5", "candle1m") if triggered else (),
        trigger_freshness_ms=2_000 if triggered else None,
        entry_zone=(100, 101),
        invalidation_conditions=("price_below_98",),
        invalidation_price=98,
        stop_price=98,
        target_price=105,
        expected_horizon_hours=4,
        approved_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        order_tactic=tactic,
        maker_required=maker,
        time_in_force="POST_ONLY" if maker else "IOC",
        limit_price=100 if maker else None,
        price_rule="EXACT_LIMIT" if maker else "OPPOSING_BBO",
        bbo_reference=None if maker else "ask",
        max_wait_sec=60 if maker else 0,
        cancel_replace_policy="CANCEL_ONLY",
        max_requotes=0,
        partial_fill_policy="KEEP_PARTIAL_CANCEL_REMAINDER",
        max_slippage_bps=None if maker else 10,
        max_notional=100,
        max_risk_pct=0.25,
        max_leverage=2,
        required_permission="Paper",
        execution_account_mode=account_mode,
        risk_policy_id="demo-v1",
        risk_policy_revision=1,
        risk_policy_hash="risk-hash",
        behavior_version="entry-planner/v1",
    )


def test_plain_limit_is_not_maker_and_post_only_crossing_fails_closed():
    plan = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    with pytest.raises(ValueError, match="post_only_would_cross_bbo"):
        build_entry_plan_order_intent(
            plan,
            bid=99.9,
            ask=100,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
        )


def test_urgent_entry_uses_exact_opposing_bbo_ioc():
    plan = _plan()
    received_at = datetime.now(timezone.utc).isoformat()
    source_at = datetime.now(timezone.utc).isoformat()
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=received_at,
        source_observed_at=source_at,
    )
    assert proposal.order_type == "limit"
    assert proposal.price == 100.05
    assert proposal.time_in_force == "ioc"
    assert proposal.post_only is False
    evidence = json.loads(proposal.attribution_snapshot)
    assert evidence["market_received_at"] == received_at
    assert evidence["market_source_at"] == source_at


def test_urgent_entry_never_chases_beyond_worst_price():
    plan = _plan().model_copy(update={"worst_price": 100.04})
    with pytest.raises(ValueError, match="bbo_worst_price_exceeded"):
        build_entry_plan_order_intent(
            plan,
            bid=100,
            ask=100.05,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
        )


def test_urgent_entry_cannot_escape_the_plan_zone():
    with pytest.raises(ValueError, match="entry_price_outside_plan_zone"):
        build_entry_plan_order_intent(
            _plan(),
            bid=101.09,
            ask=101.1,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
        )


@pytest.mark.parametrize(
    "direction,invalidation,stop_price,target_price,error",
    [
        ("long", 100.5, 98, 105, "long_invalidation_above_entry"),
        ("short", 99.5, 102, 95, "short_invalidation_below_entry"),
    ],
)
def test_entry_plan_invalidation_must_stay_protective_to_selected_entry(
    direction, invalidation, stop_price, target_price, error,
):
    payload = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT").model_dump()
    payload.update(
        direction=direction,
        entry_zone=(99, 101),
        limit_price=100,
        invalidation_price=invalidation,
        stop_price=stop_price,
        target_price=target_price,
    )

    with pytest.raises(ValueError, match=error):
        EntryPlan(**payload)


def test_dynamic_entry_plan_invalidation_covers_the_full_entry_zone():
    payload = _plan().model_dump()
    payload.update(entry_zone=(99, 101), invalidation_price=99.5)
    payload["trigger_params"] = {
        **payload["trigger_params"],
        "entry_low": 99,
        "entry_high": 101,
        "invalidation_price": 99.5,
    }

    with pytest.raises(ValueError, match="long_invalidation_above_entry"):
        EntryPlan(**payload)


def test_realized_liquidity_comes_only_from_private_fill_evidence():
    from aletheia.execution.exchange_materialization import parse_bitget_private_fills

    base = {
        "orderId": "EX-1",
        "tradeId": "fill-1",
        "fillPrice": "100",
        "fillQuantity": "1",
        "fee": "0.01",
        "feeCoin": "USDT",
        "cTime": "1787788800000",
    }
    assert parse_bitget_private_fills([{**base, "tradeScope": "M"}])[0].realized_liquidity == "maker"
    assert parse_bitget_private_fills([base])[0].realized_liquidity is None


def test_canonical_risk_enforces_the_bound_entry_plan_envelope(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    ).model_copy(update={"quantity": 2.0})
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    violations = engine._entry_plan_authority_violations(
        proposal,
        capital_base=1_000,
        resolved_policy=resolved,
    )

    assert "entry_plan_max_notional_exceeded" in violations
    assert "entry_plan_max_risk_exceeded" in violations


@pytest.mark.parametrize(
    ("field", "value", "violation"),
    [
        ("order_type", "market", "entry_plan_order_type_mismatch"),
        ("stop_price", 99, "entry_plan_stop_price_mismatch"),
        ("take_profit", 106, "entry_plan_take_profit_mismatch"),
        ("expected_horizon_hours", 5, "entry_plan_expected_horizon_hours_mismatch"),
        ("order_tactic", "POST_ONLY_LIMIT", "entry_plan_order_tactic_mismatch"),
        ("time_in_force", "GTC", "entry_plan_time_in_force_mismatch"),
        ("post_only", True, "entry_plan_post_only_mismatch"),
        ("bbo_reference", "bid", "entry_plan_bbo_reference_mismatch"),
        ("max_slippage_bps", 9, "entry_plan_max_slippage_bps_mismatch"),
        ("worst_price", 100.5, "entry_plan_worst_price_mismatch"),
        ("price", 102, "entry_plan_entry_envelope_exceeded"),
    ],
)
def test_canonical_risk_rejects_proposal_execution_fields_outside_entry_plan(
    monkeypatch, field, value, violation
):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    ).model_copy(update={field: value})
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    violations = engine._entry_plan_authority_violations(
        proposal,
        capital_base=1_000,
        resolved_policy=resolved,
    )

    assert violation in violations


def test_canonical_risk_accepts_exact_intent_and_bounded_size_reduction(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan()
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )

    assert engine._entry_plan_authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    ) == []
    reduced = proposal.model_copy(
        update={"quantity": proposal.quantity / 2, "leverage": 1}
    )
    assert engine._entry_plan_authority_violations(
        reduced, capital_base=1_000, resolved_policy=resolved
    ) == []


def test_canonical_risk_accepts_execution_fields_from_a_new_approved_plan(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    original = _plan()
    changed = EntryPlan.model_validate(
        {
            **original.model_dump(),
            "plan_id": "new-plan-version",
            "target_price": 106,
            "expected_horizon_hours": 6,
        }
    )
    payload, digest = entry_plan_payload(changed)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        changed,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    assert engine._entry_plan_authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    ) == []


def test_canonical_risk_requires_exact_resting_maker_limit(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    plan = _plan(mode="RESTING_MAKER", tactic="POST_ONLY_LIMIT")
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "waiting"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 1},
    )
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.1,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    ).model_copy(update={"price": 100.5})
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    assert "entry_plan_limit_price_mismatch" in engine._entry_plan_authority_violations(
        proposal, capital_base=1_000, resolved_policy=resolved
    )


def test_canonical_risk_rejects_superseded_or_expired_entry_plan(monkeypatch):
    from types import SimpleNamespace

    from aletheia.risk import engine

    executable_plan = _plan()
    plan = executable_plan.model_copy(
        update={
            "approved_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        }
    )
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(
        engine,
        "get_entry_plan",
        lambda _plan_id: {"plan_json": payload, "plan_hash": digest, "status": "triggered"},
    )
    monkeypatch.setattr(
        engine,
        "get_candidate_planning_generation",
        lambda _generation_id: {"is_current": 0},
    )
    proposal = build_entry_plan_order_intent(
        executable_plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
        source_observed_at=datetime.now(timezone.utc).isoformat(),
    )
    resolved = SimpleNamespace(
        policy=SimpleNamespace(policy_id="demo-v1", revision=1),
        policy_hash="risk-hash",
    )

    violations = engine._entry_plan_authority_violations(
        proposal,
        capital_base=1_000,
        resolved_policy=resolved,
    )

    assert "entry_plan_generation_superseded" in violations
    assert "entry_plan_expired" in violations


def _persist_waiting_plan(
    *,
    account_mode="bitget_demo",
    mode="TRIGGERED",
    tactic="OPPOSING_BBO_IOC",
    plan_updates=None,
) -> EntryPlan:
    snapshot = UniverseSnapshot(expires_at="2099-01-01T00:00:00+00:00")
    repo.insert_universe_snapshot(snapshot)
    candidate = UniverseMember(
        id="candidate-1",
        snapshot_id=snapshot.id,
        asset_id="asset-btc",
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        rank=1,
        total_score=90,
        scanner_family="momentum",
        scanner_direction="long",
        regime_tag="trending",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    repo.insert_candidate(candidate)
    generation, _ = repo.ensure_candidate_planning_generation(candidate.id)
    repo.claim_next_candidate_planning("owner")
    plan = _plan(account_mode=account_mode, mode=mode, tactic=tactic).model_copy(
        update={"planning_generation_id": generation["id"], **(plan_updates or {})}
    )
    repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)
    return plan


@pytest.mark.parametrize(
    ("account_mode", "expected_outcome", "expected_status", "authority_released"),
    [
        ("paper_internal", "invalidated", "invalidated", True),
        ("bitget_demo", "external_execution", "risk_reserved", False),
    ],
)
def test_watcher_capacity_cleanup_handles_interrupted_pre_order_reservation(
    account_mode,
    expected_outcome,
    expected_status,
    authority_released,
):
    from aletheia.agent_runtime.fast_trigger_watch import (
        _invalidate_pre_order_entry_plan,
    )

    plan = _persist_waiting_plan(
        account_mode=account_mode,
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
    )
    assert repo.transition_entry_plan(
        plan.plan_id,
        expected_status="waiting",
        status="triggered",
    )
    assert repo.transition_entry_plan(
        plan.plan_id,
        expected_status="triggered",
        status="risk_reserved",
    )
    now = datetime.now(timezone.utc)
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    repo.insert_trade_proposal(proposal)
    repo.update_trade_proposal(proposal.id, status="executing")
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        execution_mode=proposal.execution_mode,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
    )
    assert repo.insert_proposal_execution_claim(
        proposal.id,
        "interrupted-owner",
        (now - timedelta(seconds=601)).isoformat(),
        (now - timedelta(seconds=1)).isoformat(),
    )

    outcome = _invalidate_pre_order_entry_plan(
        plan.plan_id,
        expected_status="risk_reserved",
        reason="entry_plan_monitoring_capacity_unavailable",
        now=now,
    )

    assert outcome == expected_outcome
    assert repo.get_entry_plan(plan.plan_id)["status"] == expected_status
    assert (repo.get_entry_risk_reservation(proposal.id) is None) is authority_released
    assert (repo.get_proposal_execution_claim(proposal.id) is None) is authority_released
    assert repo.has_order_for_proposal(proposal.id) is False


def _persist_ordered_plan() -> tuple[EntryPlan, Order]:
    plan = _persist_waiting_plan()
    assert repo.transition_entry_plan(plan.plan_id, expected_status="waiting", status="triggered")
    assert repo.transition_entry_plan(plan.plan_id, expected_status="triggered", status="risk_reserved")
    order = Order(
        entry_plan_id=plan.plan_id,
        symbol=plan.symbol,
        market_type="usdt_futures",
        direction=plan.direction,
        order_type="limit",
        quantity=1,
        price=100,
        execution_mode="bitget_demo",
        exchange_order_id="exchange-1",
        status="submitted",
        source="adopted",
    )
    repo.insert_order(order)
    assert repo.transition_entry_plan(
        plan.plan_id,
        expected_status="risk_reserved",
        status="ordered",
        order_id=order.id,
    )
    return plan, order


def test_paper_broker_rejects_forged_entry_plan_limit_authority():
    from aletheia.execution.paper_broker import execute_paper_order

    with pytest.raises(ValueError, match="paper_internal_entry_plan_limit_authority_required"):
        execute_paper_order(
            proposal_id="forged-proposal",
            risk_decision_id="forged-risk",
            symbol="BTC/USDT",
            market_type="perp",
            direction="long",
            order_type="limit",
            quantity=1,
            price=100,
            execution_mode="paper_internal",
            entry_plan_id="forged-plan",
            order_tactic="OPPOSING_BBO_IOC",
            time_in_force="ioc",
            post_only=False,
        )


@pytest.mark.parametrize(
    ("mode", "tactic", "expected_tif", "expected_post_only", "requested_liquidity"),
    (
        ("TRIGGERED", "OPPOSING_BBO_IOC", "ioc", 0, "taker"),
        ("RESTING_MAKER", "POST_ONLY_LIMIT", "post_only", 1, "maker"),
    ),
)
def test_paper_internal_entry_plan_executes_through_risk_and_gateway(
    monkeypatch,
    mode,
    tactic,
    expected_tif,
    expected_post_only,
    requested_liquidity,
):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode=mode,
        tactic=tactic,
    )

    def approve(proposal, *, decision_role):
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_role=decision_role,
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            proposal_execution_mode=proposal.execution_mode,
        )
        repo.insert_risk_decision(decision)
        return SimpleNamespace(
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_id=decision.id,
            decision_role=decision_role,
        )

    monkeypatch.setattr(gateway, "evaluate_proposal", approve)
    now = datetime.now(timezone.utc)
    result = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    assert result["status"] == "executed", result
    proposal = repo.get_trade_proposal_by_entry_plan(plan.plan_id)
    order = repo.get_order(result["order_id"])
    assert proposal["execution_mode"] == "paper_internal"
    assert order["entry_plan_id"] == plan.plan_id
    assert order["order_tactic"] == tactic
    assert order["time_in_force"] == expected_tif
    assert order["post_only"] == expected_post_only
    assert order["requested_liquidity"] == requested_liquidity
    assert repo.list_fills(order_id=order["id"])[0]["realized_liquidity"] is None
    assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"


def test_paper_internal_entry_plan_passes_real_risk_gate(monkeypatch):
    from aletheia.config.loader import load_config
    from aletheia.data import price_feed
    from aletheia.portfolio import local_funding
    from aletheia.risk.policy import resolve_risk_policy
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    cfg = load_config()
    resolved = resolve_risk_policy(cfg, "paper_internal")
    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        plan_updates={
            "max_notional": 10.0,
            "risk_policy_id": resolved.policy.policy_id,
            "risk_policy_revision": resolved.policy.revision,
            "risk_policy_hash": resolved.policy_hash,
        },
    )
    monkeypatch.setattr(
        price_feed,
        "get_fresh_price_for_protective_close",
        lambda *_args, **_kwargs: 100.05,
    )
    monkeypatch.setattr(
        local_funding,
        "has_fresh_local_funding_schedule",
        lambda *_args, **_kwargs: True,
    )

    now = datetime.now(timezone.utc)
    result = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )

    assert result["status"] == "executed", result
    assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"

def test_paper_failure_terminalizes_plan_and_releases_reservation(monkeypatch):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import (
        build_entry_plan_order_intent,
    )

    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
    )
    now = datetime.now(timezone.utc)
    proposal = build_entry_plan_order_intent(
        plan,
        bid=99.9,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        now=now,
    )
    repo.insert_trade_proposal(proposal)
    repo.acquire_entry_risk_reservation(
        proposal_id=proposal.id,
        proposal_commit_hash=proposal.commit_hash,
        symbol=proposal.symbol,
        market_type=proposal.market_type,
        execution_mode=proposal.execution_mode,
        quantity=proposal.quantity,
        price=proposal.price,
        leverage=proposal.leverage,
    )

    def approve(proposal, *, decision_role):
        decision = RiskDecision(
            proposal_id=proposal.id,
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_role=decision_role,
            evaluated_quantity=proposal.quantity,
            proposal_commit_hash=proposal.commit_hash,
            proposal_execution_mode=proposal.execution_mode,
        )
        repo.insert_risk_decision(decision)
        return SimpleNamespace(
            approved=True,
            allowed_execution_mode="paper_internal",
            decision_id=decision.id,
            decision_role=decision_role,
        )

    monkeypatch.setattr(gateway, "evaluate_proposal", approve)

    def fail_paper_order(**_kwargs):
        raise RuntimeError("paper broker failed")

    monkeypatch.setattr(gateway, "execute_paper_order", fail_paper_order)
    result = gateway.execute_existing_proposal(proposal.id, "paper_internal")

    assert result["status"] == "error"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "failed"
    assert repo.get_entry_risk_reservation(proposal.id) is None



def test_stale_exact_state_cannot_advance_triggered_plan():
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan()
    with pytest.raises(ValueError, match="entry_exact_state_stale"):
        execute_entry_plan(
            plan.plan_id,
            bid=100,
            ask=100.05,
            observed_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
            source_observed_at=datetime.now(timezone.utc).isoformat(),
        )
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"


def test_stale_source_state_cannot_advance_triggered_plan():
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan()
    with pytest.raises(ValueError, match="entry_exact_state_stale"):
        execute_entry_plan(
            plan.plan_id,
            bid=100,
            ask=100.05,
            observed_at=datetime.now(timezone.utc).isoformat(),
            source_observed_at=(
                datetime.now(timezone.utc) - timedelta(seconds=5)
            ).isoformat(),
        )
    assert repo.get_entry_plan(plan.plan_id)["status"] == "waiting"


def test_gateway_cancel_rechecks_fill_and_fill_wins(monkeypatch):
    from aletheia.execution import bitget_broker, gateway

    plan, order = _persist_ordered_plan()

    def fill_during_cancel(**_kwargs):
        repo.insert_fill(Fill(order_id=order.id, fill_price=100, fill_quantity=1, fee_source="reconciled"))
        repo.update_order(order.id, status="filled")
        return {"cancelled": True, "order_id": "exchange-1"}

    monkeypatch.setattr(bitget_broker, "cancel_entry_order", fill_during_cancel)
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "filled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "filled"
    assert repo.get_order(order.id)["status"] == "filled"


def test_gateway_cancel_keeps_partial_fill_and_closes_remainder(monkeypatch):
    from aletheia.execution import bitget_broker, gateway
    from aletheia.execution import exchange_materialization, reconciliation
    from aletheia.uta import manager
    from aletheia.uta.models import OrderLifecycle, ReadCompleteness

    plan, order = _persist_ordered_plan()

    monkeypatch.setattr(
        bitget_broker,
        "cancel_entry_order",
        lambda **_kwargs: {"cancelled": True, "order_id": "exchange-1"},
    )
    async def exact_order(*_args, **_kwargs):
        return SimpleNamespace(
            completeness=ReadCompleteness.COMPLETE,
            found=SimpleNamespace(
                state=OrderLifecycle.CANCELED,
                filled_quantity=0.5,
            ),
        )

    async def exact_fills(self, **_kwargs):
        return [{"orderId": "exchange-1", "fillQuantity": "0.5"}]

    materialized = []

    def materialize_before_cancel(order_id, rows):
        assert repo.get_order(order_id)["status"] == "submitted"
        materialized.extend(rows)
        repo.insert_fill(Fill(order_id=order_id, fill_price=100, fill_quantity=0.5, fee_source="reconciled"))
        repo.update_order(order_id, status="partially_filled")

    monkeypatch.setattr(manager, "query_uta", exact_order)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_fills",
        exact_fills,
    )
    monkeypatch.setattr(
        exchange_materialization,
        "materialize_bitget_entry",
        materialize_before_cancel,
    )
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "cancelled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "cancelled"
    assert repo.get_order(order.id)["status"] == "cancelled"
    assert materialized == [{"orderId": "exchange-1", "fillQuantity": "0.5"}]
    assert sum(fill["fill_quantity"] for fill in repo.list_fills(order_id=order.id)) == 0.5


def test_gateway_cancel_ack_without_complete_private_evidence_stays_pending(monkeypatch):
    from aletheia.execution import bitget_broker, gateway
    from aletheia.uta import manager
    from aletheia.uta.models import ReadCompleteness

    plan, order = _persist_ordered_plan()
    monkeypatch.setattr(
        bitget_broker,
        "cancel_entry_order",
        lambda **_kwargs: {"cancelled": True, "order_id": "exchange-1"},
    )

    async def incomplete_order(*_args, **_kwargs):
        return SimpleNamespace(
            completeness=ReadCompleteness.UNAVAILABLE,
            found=None,
        )

    monkeypatch.setattr(manager, "query_uta", incomplete_order)
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "cancel_pending"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    assert repo.get_order(order.id)["status"] == "submitted"


def test_gateway_cancel_waits_when_order_quantity_leads_fill_history(monkeypatch):
    from aletheia.execution import bitget_broker, gateway, reconciliation
    from aletheia.uta import manager
    from aletheia.uta.models import OrderLifecycle, ReadCompleteness

    plan, order = _persist_ordered_plan()
    monkeypatch.setattr(
        bitget_broker,
        "cancel_entry_order",
        lambda **_kwargs: {"cancelled": True, "order_id": "exchange-1"},
    )

    async def terminal_order(*_args, **_kwargs):
        return SimpleNamespace(
            completeness=ReadCompleteness.COMPLETE,
            found=SimpleNamespace(
                state=OrderLifecycle.CANCELED,
                filled_quantity=0.5,
            ),
        )

    async def lagging_fills(self, **_kwargs):
        return []

    monkeypatch.setattr(manager, "query_uta", terminal_order)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_fills",
        lagging_fills,
    )

    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")

    assert result["status"] == "cancel_pending"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"
    assert repo.get_order(order.id)["status"] == "submitted"


@pytest.mark.parametrize(
    "materialized_state,starting_status,expected_reason",
    [
        ("evidence_missing", "submitted", "ioc_zero_fill_terminal"),
        ("partially_filled", "partially_filled", "ioc_remainder_terminal"),
    ],
)
def test_reconciliation_terminalizes_ioc_remainder_from_exact_order_evidence(
    monkeypatch,
    materialized_state,
    starting_status,
    expected_reason,
):
    from aletheia.execution import reconciliation
    from aletheia.uta.models import OrderLifecycle

    plan, order = _persist_ordered_plan()
    repo.update_order(
        order.id,
        source="native",
        order_tactic="OPPOSING_BBO_IOC",
        time_in_force="ioc",
        status=starting_status,
    )

    async def no_rows(self, **_kwargs):
        return []

    async def terminal_order(self, **_kwargs):
        return SimpleNamespace(
            state=OrderLifecycle.CANCELED,
            filled_quantity=0.5 if materialized_state == "partially_filled" else 0,
        )

    def materialize_ioc(order_id, *_args, **_kwargs):
        if materialized_state == "partially_filled" and not repo.list_fills(order_id=order_id):
            repo.insert_fill(
                Fill(
                    order_id=order_id,
                    fill_price=100,
                    fill_quantity=0.5,
                    fee_source="reconciled",
                )
            )
        return SimpleNamespace(state=materialized_state, new_fill_count=0)

    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_open_orders", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_fills", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_order_fills", no_rows)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_status",
        terminal_order,
    )
    monkeypatch.setattr(
        reconciliation,
        "materialize_bitget_entry",
        materialize_ioc,
    )

    reconciliation.reconcile()
    reconciliation.reconcile()

    stored = repo.get_order(order.id)
    assert stored["status"] == "cancelled"
    assert stored["error_reason"] == expected_reason
    assert repo.get_entry_plan(plan.plan_id)["status"] == "cancelled"


def test_reconciliation_waits_for_lagging_ioc_fill_history(monkeypatch):
    from aletheia.execution import reconciliation
    from aletheia.uta.models import OrderLifecycle

    plan, order = _persist_ordered_plan()
    repo.update_order(
        order.id,
        source="native",
        order_tactic="OPPOSING_BBO_IOC",
        time_in_force="ioc",
    )

    async def no_rows(self, **_kwargs):
        return []

    async def terminal_before_fills(self, **_kwargs):
        return SimpleNamespace(
            state=OrderLifecycle.CANCELED,
            filled_quantity=0.5,
        )

    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_open_orders", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_fills", no_rows)
    monkeypatch.setattr(reconciliation._UTAReconciliationProjection, "get_order_fills", no_rows)
    monkeypatch.setattr(
        reconciliation._UTAReconciliationProjection,
        "get_order_status",
        terminal_before_fills,
    )
    monkeypatch.setattr(
        reconciliation,
        "materialize_bitget_entry",
        lambda *_args, **_kwargs: SimpleNamespace(
            state="evidence_missing",
            new_fill_count=0,
        ),
    )

    reconciliation.reconcile()

    assert repo.get_order(order.id)["status"] == "submitted"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "ordered"

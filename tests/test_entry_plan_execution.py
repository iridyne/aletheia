from datetime import datetime, timedelta, timezone

import pytest

from aletheia.execution.entry_plan_execution import build_entry_plan_order_intent
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, UniverseMember, UniverseSnapshot
from aletheia.trading.entry_plan import EntryPlan, entry_plan_payload


def _plan(*, mode="TRIGGERED", tactic="OPPOSING_BBO_IOC") -> EntryPlan:
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
        execution_account_mode="bitget_demo",
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
        )


def test_urgent_entry_uses_exact_opposing_bbo_ioc():
    plan = _plan()
    proposal = build_entry_plan_order_intent(
        plan,
        bid=100,
        ask=100.05,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    assert proposal.order_type == "limit"
    assert proposal.price == 100.05
    assert proposal.time_in_force == "ioc"
    assert proposal.post_only is False


def test_urgent_entry_never_chases_beyond_worst_price():
    plan = _plan().model_copy(update={"worst_price": 100.04})
    with pytest.raises(ValueError, match="bbo_worst_price_exceeded"):
        build_entry_plan_order_intent(
            plan,
            bid=100,
            ask=100.05,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )


def test_urgent_entry_cannot_escape_the_plan_zone():
    with pytest.raises(ValueError, match="entry_price_outside_plan_zone"):
        build_entry_plan_order_intent(
            _plan(),
            bid=101.09,
            ask=101.1,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )


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


def _persist_waiting_plan() -> EntryPlan:
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
    plan = _plan().model_copy(update={"planning_generation_id": generation["id"]})
    repo.complete_candidate_planning_with_plan(generation["id"], "owner", plan)
    return plan


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


def test_stale_exact_state_cannot_advance_triggered_plan():
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan()
    with pytest.raises(ValueError, match="entry_exact_state_stale"):
        execute_entry_plan(
            plan.plan_id,
            bid=100,
            ask=100.05,
            observed_at=(datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat(),
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

    plan, order = _persist_ordered_plan()
    repo.insert_fill(Fill(order_id=order.id, fill_price=100, fill_quantity=0.5, fee_source="reconciled"))
    repo.update_order(order.id, status="partially_filled")

    monkeypatch.setattr(
        bitget_broker,
        "cancel_entry_order",
        lambda **_kwargs: {"cancelled": True, "order_id": "exchange-1"},
    )
    result = gateway.cancel_entry_plan_order(plan.plan_id, reason="price_invalidated")
    assert result["status"] == "cancelled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "cancelled"
    assert repo.get_order(order.id)["status"] == "cancelled"
    assert sum(fill["fill_quantity"] for fill in repo.list_fills(order_id=order.id)) == 0.5

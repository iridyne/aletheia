"""Every leg consumes the original Plan envelope, including unresolved exposure."""
from types import SimpleNamespace

import pytest

from aletheia.risk import engine
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, TradeProposal
from aletheia.trading.entry_plan import entry_plan_payload
from tests.test_entry_plan_execution import _plan


@pytest.fixture(autouse=True)
def _pin_account_authority(monkeypatch):
    """`_plan()` resolves financing through the canonical sizer (#3729).

    That path fails closed when no cached account snapshot exists, which a test
    data root never has; pin the same fixture capital the other entry tests use.
    """
    monkeypatch.setattr("aletheia.risk.engine.account_equity_for_execution", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *a, **k: 5000.0)
    monkeypatch.setattr("aletheia.risk.engine.account_available_for_execution", lambda *a, **k: 5000.0)


@pytest.mark.parametrize("state,filled,new_quantity,exceeded", [
    ("cancelled", .4, .6, False),
    ("cancelled", .4, .7, True),
    ("submission_unknown", .4, .1, True),
    ("submitted", .4, .1, True),
    ("filled", 1., .1, True),
])
def test_same_plan_fills_and_unresolved_remainder_share_caps(monkeypatch, state, filled, new_quantity, exceeded):
    from tests.helpers.entry_plan_financing_fixture import apply_entry_financing

    # A small but COHERENT Plan envelope: for a financed Plan the financed
    # exposure is its notional ceiling and its frozen loss budget is its risk
    # ceiling (#3729), so the two caps cannot be stated independently. 100 USDT
    # of exposure at this Plan's 2% stop is 2.0 USDT of risk, which is exactly
    # the `capital_base 1000 x 0.2%` the leg arithmetic below was written for.
    financed = apply_entry_financing(_plan(), executable_notional_cap_usdt=100.0)
    plan = financed.model_copy(update={
        "max_risk_pct": .2,
        "sizing_resolution": financed.sizing_resolution.model_copy(update={
            "baseline_loss_budget_usdt": 2.0, "effective_loss_budget_usdt": 2.0,
        }),
    })
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(engine, "get_entry_plan", lambda _: {
        "plan_json": payload, "plan_hash": digest, "status": "active"})
    monkeypatch.setattr(engine, "get_candidate_planning_generation", lambda _: {"is_current": 1})
    first = TradeProposal(symbol=plan.symbol, market_type="usdt_futures", direction="long",
        execution_mode="bitget_demo", order_type="limit", price=100., quantity=1.,
        entry_plan_id=plan.plan_id, entry_leg_index=0)
    repo.insert_trade_proposal(first)
    order = Order(proposal_id=first.id, symbol=plan.symbol, market_type="usdt_futures", direction="long",
        execution_mode="bitget_demo", order_type="limit", price=100., quantity=1.,
        entry_plan_id=plan.plan_id, status=state)
    # Durable snapshot fixture: this test targets Risk accounting, not submission.
    repo._insert("orders", order.model_dump())
    repo.insert_fill(Fill(order_id=order.id, fill_price=100., fill_quantity=filled))
    second = first.model_copy(update={"id": "next-leg", "entry_leg_index": 1,
                                      "quantity": new_quantity, "stop_price": plan.stop_price})
    violations = engine._entry_plan_authority_violations(second, capital_base=1000.,
        resolved_policy=SimpleNamespace(
            policy=SimpleNamespace(
                policy_id="demo-v1",
                revision=1,
                # The new financing checks read the Owner policy for the
                # same-symbol risk-unit bound; 0 keeps this test's claim about
                # the Plan's own caps isolated from it.
                capital=SimpleNamespace(max_per_symbol_entries=0),
            ),
            policy_hash="risk-hash",
        ))
    assert "entry_plan_contract_invalid" not in violations
    assert ("entry_plan_max_notional_exceeded" in violations) is exceeded
    assert ("entry_plan_max_risk_exceeded" in violations) is exceeded


@pytest.mark.parametrize("violations,expected", [
    (["max_demo_positions_reached:3", "stale_market_data_demo:age=1900"], "DEFER"),
    (["capital_base_unavailable:account_sync_pending"], "DEFER"),
    (["max_demo_positions_reached:3", "kill_switch_active"], "REJECT"),
    (["entry_plan_risk_authority_revoked"], "REJECT"),
    (["unknown_future_condition:retry please"], "REJECT"),
    ([], "REJECT"),
    # An unevaluated gate is not a refused one: the Plan retries under its own
    # expires_at instead of being failed. DEFER still admits no trade.
    (["demo_entry_quality_unavailable:policy_epoch"], "DEFER"),
    (["demo_entry_quality_unavailable:regime"], "DEFER"),
    (["unresolved_entry_exposure_unavailable", "entry_risk_reservation_unavailable"], "DEFER"),
    (["lesson_check_error:internal_failure"], "DEFER"),
    (["correlation_check_error:internal_failure"], "DEFER"),
    # Measured refusals and permanent states stay terminal.
    (["demo_entry_quality_unavailable:regime", "kill_switch_active"], "REJECT"),
    (["regime_sideways_standdown:state=sideways"], "REJECT"),
    (["family_not_demo_eligible:momentum:long:n=20:exp=-1.0<0.0"], "REJECT"),
    # A superseded generation can never become current again, so retrying it
    # would only spin until expiry.
    (["entry_plan_generation_superseded"], "REJECT"),
    (["stop_distance_below_vol_target:0.2532<1.5000"], "REJECT"),
    # Durable row corruption shares the first segment with the transient
    # :internal_failure variant, so the whole prefix stays terminal.
    (["account_leverage_check_error:invalid_position_exposure:position=p:field=quantity"], "REJECT"),
    (["account_exposure_check_error:non_finite_projected_exposure"], "REJECT"),
])
def test_risk_owns_retry_classification_including_durable_reload(violations, expected):
    import json
    result = engine._risk_result_from_row({"approved": False, "violations": json.dumps(violations)})
    assert result.disposition == expected


@pytest.mark.parametrize("stage", ["preflight", "authoritative"])
def test_deferred_entry_risk_keeps_proposal_pending_and_releases_claim(monkeypatch, stage):
    from datetime import datetime, timedelta, timezone
    from aletheia.execution import gateway

    proposal = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        execution_mode="bitget_demo", order_type="limit", price=100., quantity=1., entry_plan_id="plan")
    repo.insert_trade_proposal(proposal)
    now = datetime.now(timezone.utc)
    repo.insert_proposal_execution_claim(proposal.id, "owner", now.isoformat(), (now+timedelta(seconds=30)).isoformat())
    deferred = engine.RiskCheckResult(violations=["max_demo_positions_reached:3"])
    monkeypatch.setattr(engine, "_evaluate_proposal_locked", lambda *_args, **_kwargs: deferred)
    def evaluate(value, *, decision_role, **_kwargs):
        if decision_role == stage:
            return engine.evaluate_proposal(value, decision_role=decision_role)
        return engine.RiskCheckResult(approved=True, allowed_execution_mode="bitget_demo")
    monkeypatch.setattr(gateway, "evaluate_proposal", evaluate)
    monkeypatch.setattr(gateway, "_prepare_exchange_quantity", lambda *_args, **_kwargs: (1., None))
    result = gateway._execute_proposal(proposal, owner_token="owner")
    assert result["status"] == "waiting_risk"
    assert repo.get_trade_proposal(proposal.id)["status"] == "pending"
    assert repo.get_proposal_execution_claim(proposal.id) is None
    assert not repo.has_order_for_proposal(proposal.id)


@pytest.mark.parametrize("defect", [None, "working", "wrong_plan", "unverified", "closed", "excess", "close_pending", "wrong_position_owner"])
def test_remainder_reuses_only_exact_materialized_same_plan_position(monkeypatch, defect):
    from aletheia.storage.models import Position, RiskDecision
    from aletheia.trading.entry_plan_compiler import compile_entry_plan
    from tests.test_entry_capture import capture_input

    plan = compile_entry_plan(*capture_input(monkeypatch, "grounded-anchor-direct", "MAKER_PREFERRED"))
    payload, digest = entry_plan_payload(plan)
    monkeypatch.setattr(engine, "get_entry_plan", lambda _: {"plan_json": payload, "plan_hash": digest, "status": "active"})
    target = plan.target_quantity
    first = TradeProposal(symbol=plan.symbol, market_type="usdt_futures", direction=plan.direction,
        execution_mode="bitget_demo", order_type="limit", price=plan.limit_price, quantity=target,
        entry_plan_id=plan.plan_id, entry_leg_index=0, order_tactic="POST_ONLY_LIMIT", post_only=True)
    repo.insert_trade_proposal(first)
    decision = RiskDecision(proposal_id=first.id, approved=True, allowed_execution_mode="bitget_demo",
        proposal_execution_mode="bitget_demo", decision_role="authoritative", evaluated_quantity=target,
        proposal_commit_hash=first.commit_hash)
    repo.insert_risk_decision(decision)
    order = Order(proposal_id=first.id, risk_decision_id=decision.id, symbol=first.symbol,
        market_type=first.market_type, direction=first.direction, execution_mode=first.execution_mode,
        order_type="limit", price=first.price, quantity=target, client_order_id=first.id,
        entry_plan_id=plan.plan_id, order_tactic="POST_ONLY_LIMIT", post_only=True, status="cancelled")
    repo.insert_order(order)
    fill = Fill(order_id=order.id, fill_price=first.price, fill_quantity=target*.4, fee_source="reconciled")
    repo.insert_fill(fill)
    position = Position(symbol=first.symbol, market_type=first.market_type, direction=first.direction,
        execution_mode=first.execution_mode, quantity=fill.fill_quantity, entry_price=first.price,
        order_id=order.id, proposal_id=first.id, entry_fill_verified=True, entry_price_source="broker_fill")
    repo.insert_position(position)
    repo.update_order_metadata(order.id, position_id=position.id)
    second = first.model_copy(update={"id": "remainder", "entry_leg_index": 1, "quantity": target*.6})
    if defect == "working":
        repo.execute_write("UPDATE orders SET status='submitted' WHERE id=?", (order.id,))
    elif defect == "wrong_plan":
        second = second.model_copy(update={"entry_plan_id": "different"})
    elif defect == "unverified":
        repo.execute_write("UPDATE positions SET entry_fill_verified=0 WHERE id=?", (position.id,))
    elif defect == "closed":
        repo.execute_write("UPDATE positions SET status='closed' WHERE id=?", (position.id,))
    elif defect == "wrong_position_owner":
        repo.execute_write("UPDATE positions SET proposal_id=NULL WHERE id=?", (position.id,))
    elif defect == "close_pending":
        repo.execute_write("UPDATE positions SET pending_close_order_id='closing' WHERE id=?", (position.id,))
    elif defect == "excess":
        second = second.model_copy(update={"quantity": target*.7})
    result = engine._confirmed_entry_remainder_order(second)
    if defect:
        assert result is None
    else:
        assert result["id"] == order.id and result["position_id"] == position.id

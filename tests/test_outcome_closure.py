"""Economic honesty and idempotency for verified/admin position closure."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position


def _insert_entry_chain(
    *,
    position_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    quantity: float,
    price: float,
    execution_mode: str,
    entry_fee: float = 0.0,
    proposal_id: str | None = None,
    order_id: str | None = None,
    opportunity_id: str | None = None,
    strategy_id: str | None = None,
) -> tuple[str, str, str]:
    """Persist the real entry lineage required by an eligible close fixture."""
    from aletheia.storage.models import (
        Fill,
        Opportunity,
        Order,
        RiskDecision,
        TradeProposal,
    )

    opportunity_id = opportunity_id or f"opportunity-{position_id}"
    proposal_id = proposal_id or f"proposal-{position_id}"
    order_id = order_id or f"order-{position_id}"
    opportunity = Opportunity(
        id=opportunity_id,
        symbol=symbol,
        market="spot" if market_type == "spot" else "perp",
        direction_bias=direction,
        source_agent="fixture",
        scanner_family="fixture",
        linked_strategy_id=strategy_id,
    )
    repo.insert_opportunity(opportunity)
    proposal = TradeProposal(
        id=proposal_id,
        strategy_id=strategy_id,
        opportunity_id=opportunity.id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        status="executed",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        id=f"risk-{position_id}",
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=execution_mode,
        proposal_execution_mode=execution_mode,
        decision_role="authoritative",
        evaluated_quantity=quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    repo.insert_order(Order(
        id=order_id,
        proposal_id=proposal.id,
        risk_decision_id=decision.id,
        position_id=position_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        client_order_id=proposal.id,
        status="filled",
    ))
    repo.insert_fill(Fill(
        id=f"entry-fill-{position_id}",
        order_id=order_id,
        fill_price=price,
        fill_quantity=quantity,
        fee=entry_fee,
        fee_source="reconciled",
    ))
    return opportunity.id, proposal.id, order_id


def _position(*, execution_mode: str = "paper_internal") -> Position:
    position_id = "position-close-split"
    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position_id,
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=2.0,
        price=100.0,
        execution_mode=execution_mode,
        entry_fee=0.2,
    )
    return Position(
        id=position_id, symbol="BTC/USDT",
        market_type="usdt_futures", direction="long", quantity=2.0,
        entry_price=100.0, current_price=105.0, unrealized_pnl=10.0,
        execution_mode=execution_mode, entry_fee=0.2,
        entry_price_source=(
            "local_fill" if execution_mode == "paper_internal" else "broker_fill"
        ),
        entry_fill_verified=True,
        funding_cost=0.0,
        funding_source=(
            "local_simulation"
            if execution_mode == "paper_internal"
            else "broker_ledger"
        ),
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        order_id=order_id,
    )


def _verified_result(position: Position):
    from aletheia.execution.verified_exit import (
        VerifiedExitFill, VerifiedExitRequest, classify_verified_exit,
    )
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    request = VerifiedExitRequest(
        broker_id=position.execution_mode,
        account_environment=(
            "local" if position.execution_mode == "paper_internal" else "demo"
        ),
        account_scope="account-scope-1", execution_mode=position.execution_mode,
        position_id=position.id, symbol=position.symbol,
        market_type=position.market_type, direction=position.direction,
        quantity=position.quantity, history_start_ms=now_ms - 60_000,
        history_end_ms=now_ms,
    )
    fills = (
        VerifiedExitFill(
            fill_id="fill-1", order_id="order-1", price=110.0, quantity=1.0,
            fee=0.1, fee_currency="USDT", realized_pnl=10.0,
            filled_at_ms=now_ms - 2_000,
        ),
        VerifiedExitFill(
            fill_id="fill-2", order_id="order-1", price=112.0, quantity=1.0,
            fee=0.1, fee_currency="USDT", realized_pnl=12.0,
            filled_at_ms=now_ms - 1_000,
        ),
    )
    return classify_verified_exit(
        request, position_absent=True, history_complete=True,
        close_fills=fills, ambiguous_activity=False, pages=1,
        source_endpoint="local-fill-fixture",
    )


def _admin_identity(position: Position) -> dict:
    return {
        "broker_id": position.execution_mode,
        "account_environment": (
            "local" if position.execution_mode == "paper_internal" else "demo"
        ),
        "account_scope": "account-scope-1",
        "execution_mode": position.execution_mode,
        "position_id": position.id,
        "symbol": position.symbol,
        "market_type": position.market_type,
        "direction": position.direction,
    }


def test_administrative_close_converges_state_without_economic_side_effects(monkeypatch):
    from aletheia.portfolio import outcome_closure
    from aletheia.portfolio.paper_account import get_paper_account, record_position_opened

    position = _position()
    repo.insert_position(position)
    record_position_opened(position.id)
    before = get_paper_account(event_limit=100)
    monkeypatch.setattr(
        outcome_closure, "_run_outcome_hooks",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("administrative close must not run outcome hooks")
        ),
    )

    result = outcome_closure.close_position_administrative(
        position.id, reason="reconciled_gone",
        reconciliation_identity=_admin_identity(position),
        reconciliation_evidence={"candidate_id": "candidate-1"},
        actor="position_reconciler", source="durable_disappearance",
    )
    assert result["closed"] is True
    assert result["closure_type"] == "administrative"

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "closed"
    assert stored["close_category"] == "administrative"
    assert stored["exit_price_source"] == "unavailable"
    assert stored["exit_fill_verified"] == 0
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "administrative_close"
    assert stored["realized_pnl"] is None
    assert stored["exit_fee"] is None
    assert stored["slippage_cost"] is None
    assert stored["current_price"] == 105.0

    after = get_paper_account(event_limit=100)
    assert after["cash_balance_usdt"] == before["cash_balance_usdt"]
    assert not repo.has_paper_account_position_event("position_closed", position.id)
    assert repo.list_outcome_hook_jobs(position_id=position.id) == []
    from aletheia.portfolio.pnl import today_eligible_strategy_pnl
    assert today_eligible_strategy_pnl() == 0.0
    assert any(
        row["event_type"] == "position_administratively_closed"
        and row["entity_id"] == position.id
        for row in repo.list_audit_events(limit=20)
    )

    repeated = outcome_closure.close_position_administrative(
        position.id, reason="reconciled_gone",
        reconciliation_identity=_admin_identity(position),
        reconciliation_evidence={"candidate_id": "candidate-1"},
    )
    assert repeated["already_closed"] is True
    assert len([
        row for row in repo.list_audit_events(limit=20)
        if row["event_type"] == "position_administratively_closed"
    ]) == 1


def test_verified_close_uses_exact_fill_fees_and_settles_once(monkeypatch):
    from aletheia.portfolio import outcome_closure
    from aletheia.portfolio.paper_account import get_paper_account, record_position_opened

    position = _position()
    repo.insert_position(position)
    record_position_opened(position.id)
    before_cash = get_paper_account(event_limit=100)["cash_balance_usdt"]
    hook_calls = []
    monkeypatch.setattr(
        outcome_closure, "_run_outcome_hooks",
        lambda row, realized, reason: hook_calls.append(
            (row["id"], realized, reason, row["closed_at"])
        ),
    )

    evidence = _verified_result(position)
    result = outcome_closure.close_position_verified(
        position.id, evidence, reason="take_profit", actor="system",
        source="verified_exit_protocol",
    )
    assert result["exit_price"] == pytest.approx(111.0)
    assert result["realized_pnl"] == pytest.approx(21.6)
    stored = repo._select_one("positions", position.id)
    assert stored["close_category"] == "strategy"
    assert stored["exit_price_source"] == "local_fill"
    assert stored["exit_fill_verified"] == 1
    assert stored["outcome_eligible"] == 1
    assert stored["exit_fee"] == pytest.approx(0.2)
    assert stored["slippage_cost"] is None
    expected_closed_at = datetime.fromtimestamp(
        max(fill.filled_at_ms for fill in evidence.fills) / 1000,
        tz=timezone.utc,
    ).isoformat()
    assert stored["closed_at"] == expected_closed_at
    assert get_paper_account(event_limit=100)["cash_balance_usdt"] == pytest.approx(
        before_cash + 21.6
    )
    from aletheia.portfolio.pnl import today_eligible_strategy_pnl
    assert today_eligible_strategy_pnl() == pytest.approx(21.6)
    assert len(hook_calls) == 1
    assert hook_calls[0][0] == position.id
    assert hook_calls[0][1] == pytest.approx(21.6)
    assert hook_calls[0][3] == expected_closed_at

    repeated = outcome_closure.close_position_verified(
        position.id, evidence, reason="take_profit"
    )
    assert repeated["already_closed"] is True
    assert len(hook_calls) == 1
    assert len([
        row for row in repo.list_paper_account_events(limit=100)
        if row["action"] == "position_closed" and row["position_id"] == position.id
    ]) == 1


def test_exit_result_router_never_closes_inconclusive_and_routes_absent_to_admin():
    from aletheia.portfolio.outcome_closure import close_position_from_verified_exit

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    verified = _verified_result(position)
    inconclusive = replace(
        verified, state="inconclusive", reason="fill_history_incomplete",
        fills=(), history_complete=False,
    )
    result = close_position_from_verified_exit(position.id, inconclusive)
    assert result["closed"] is False
    assert repo._select_one("positions", position.id)["status"] == "open"

    absent = replace(
        verified, state="confirmed_absent_without_fill",
        reason="position_absent_complete_history_no_fill", fills=(),
    )
    result = close_position_from_verified_exit(position.id, absent)
    assert result["closure_type"] == "administrative"
    assert repo._select_one("positions", position.id)["realized_pnl"] is None


def test_verified_close_rejects_missing_fee_or_quantity_mismatch():
    from aletheia.portfolio.outcome_closure import close_position_verified

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    evidence = _verified_result(position)
    bad_fee = replace(
        evidence, fills=(replace(evidence.fills[0], fee=None), evidence.fills[1]),
    )
    with pytest.raises(ValueError, match="fee"):
        close_position_verified(position.id, bad_fee)
    assert repo._select_one("positions", position.id)["status"] == "open"

    bad_quantity = replace(
        evidence,
        fills=(replace(evidence.fills[0], quantity=0.5), evidence.fills[1]),
    )
    with pytest.raises(ValueError, match="quantity"):
        close_position_verified(position.id, bad_quantity)
    assert repo._select_one("positions", position.id)["status"] == "open"


def test_verified_futures_close_does_not_apply_untrusted_funding_value():
    """A number without a ledger source is not realized funding economics."""
    from aletheia.portfolio.outcome_closure import close_position_verified

    position = _position(execution_mode="bitget_demo")
    position.entry_price_source = "broker_fill"
    position.entry_fill_verified = True
    position.proposal_id = "proposal-1"
    position.order_id = "order-1"
    position.opportunity_id = "opportunity-1"
    position.funding_cost = 5.0
    position.funding_source = "unavailable"
    repo.insert_position(position)

    result = close_position_verified(position.id, _verified_result(position))

    stored = repo._select_one("positions", position.id)
    assert result["realized_pnl"] == pytest.approx(21.6)
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"


def test_verified_close_applies_clean_window_before_running_outcome_hooks(monkeypatch):
    from aletheia.portfolio import outcome_closure

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    evidence = _verified_result(position)
    old_ms = int(
        datetime(2026, 7, 10, tzinfo=timezone.utc).timestamp() * 1000
    )
    old_evidence = replace(
        evidence,
        fills=tuple(
            replace(fill, filled_at_ms=old_ms + index * 1000)
            for index, fill in enumerate(evidence.fills)
        ),
    )
    hook_calls = []
    monkeypatch.setattr(
        outcome_closure,
        "_run_outcome_hooks",
        lambda row, realized, reason: hook_calls.append(row["id"]),
    )

    result = outcome_closure.close_position_verified(
        position.id,
        old_evidence,
        reason="take_profit",
    )

    stored = repo._select_one("positions", position.id)
    assert result["outcome_eligible"] is False
    assert result["outcome_exclusion_reason"] == "pre_clean_window"
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "pre_clean_window"
    assert hook_calls == []


def test_ready_candidate_uses_protocol_and_resolves_after_admin_close(monkeypatch):
    from dataclasses import replace
    from aletheia.portfolio import disappearance_settlement
    from aletheia.storage.models import PositionDisappearanceCandidate

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    candidate = PositionDisappearanceCandidate(
        id="candidate-ready", position_id=position.id, identity_key="identity-ready",
        broker_id="bitget_demo", account_environment="demo",
        account_fingerprint="account-scope-1", execution_mode="bitget_demo",
        symbol=position.symbol, market_type=position.market_type,
        direction=position.direction, first_observed_at=position.opened_at,
        last_observed_at=position.opened_at, confirmation_count=2,
        semantics_version="oi-v1",
    )
    repo.insert_position_disappearance_candidate(candidate)
    absent = replace(
        _verified_result(position), state="confirmed_absent_without_fill",
        reason="position_absent_complete_history_no_fill", fills=(),
    )

    class FakeClient:
        async def get_verified_position_exit(self, request):
            return replace(
                absent,
                account_environment=request.account_environment,
                account_scope=request.account_scope,
            )

    monkeypatch.setattr(
        disappearance_settlement, "_client_for_mode", lambda mode: FakeClient(),
    )
    result = disappearance_settlement.settle_ready_disappearance_candidate(
        candidate.id
    )
    assert result["closure_type"] == "administrative"
    assert repo._select_one("positions", position.id)["status"] == "closed"
    stored_candidate = repo.list_position_disappearance_candidates(limit=10)[0]
    assert stored_candidate["status"] == "settled_administrative"


def test_live_ready_candidate_queries_read_only_and_inconclusive_stays_open(monkeypatch):
    from dataclasses import replace
    from aletheia.portfolio import disappearance_settlement
    from aletheia.storage.models import PositionDisappearanceCandidate

    position = _position(execution_mode="bitget_live_limited")
    repo.insert_position(position)
    candidate = PositionDisappearanceCandidate(
        id="candidate-live", position_id=position.id, identity_key="identity-live",
        broker_id="bitget_live_limited", account_environment="live",
        account_fingerprint="account-scope-live", execution_mode="bitget_live_limited",
        symbol=position.symbol, market_type=position.market_type,
        direction=position.direction, first_observed_at=position.opened_at,
        last_observed_at=position.opened_at, confirmation_count=2,
        semantics_version="oi-v1",
    )
    repo.insert_position_disappearance_candidate(candidate)
    queried_modes = []

    class FakeClient:
        async def get_verified_position_exit(self, request):
            queried_modes.append(request.execution_mode)
            return replace(
                _verified_result(position),
                state="inconclusive",
                reason="fill_history_incomplete",
                fills=(),
                history_complete=False,
                account_environment=request.account_environment,
                account_scope=request.account_scope,
            )

    monkeypatch.setattr(
        disappearance_settlement,
        "_client_for_mode",
        lambda mode: FakeClient(),
    )
    result = disappearance_settlement.settle_ready_disappearance_candidate(
        candidate.id
    )
    assert queried_modes == ["bitget_live_limited"]
    assert result["reason"] == "fill_history_incomplete"
    assert repo._select_one("positions", position.id)["status"] == "open"


def test_legacy_close_api_rejects_administrative_reasons():
    from aletheia.portfolio.pnl import close_position

    position = _position()
    repo.insert_position(position)
    with pytest.raises(ValueError, match="administrative"):
        close_position(position.id, exit_price=105.0, reason="reconciled_gone")
    assert repo._select_one("positions", position.id)["status"] == "open"

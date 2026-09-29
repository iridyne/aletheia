"""Economic honesty and idempotency for verified/admin position closure."""

from __future__ import annotations

from tests.helpers.repo_test_helpers import list_outcome_hook_jobs

from tests.helpers.outcome_feedback import configured_outcome_feedback as configured_outcome_feedback

from tests.helpers.position_fixture import force_update_position
from tests.test_broker_funding_economics import configured_funding_retention as configured_funding_retention

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position
from aletheia.portfolio.pnl import _account_environment


def clean_window_admits(closed_at: str) -> bool:
    """Whether a close time is inside the versioned clean window.

    Outcome eligibility is a property of the clean-window anchor, not of whatever
    a test is actually asserting. A fixture that needs a close on an earlier
    calendar day must derive its eligibility expectation instead of hardcoding one:
    the anchor advances on every re-anchor, so "yesterday" silently crosses it and
    an unrelated test starts failing (that is how the daily-PnL bucketing test
    broke 15 hours after the 2026-09-23 anchor was set).
    """
    from aletheia.analytics.learning_window import CLEAN_WINDOW_ANCHOR_ISO

    anchor = _fixture_utc(CLEAN_WINDOW_ANCHOR_ISO, field="clean_window_anchor")
    return _fixture_utc(closed_at, field="closed_at") >= anchor


def _assert_complete_closed_position_fixture(
    position_id: str,
    *,
    expected_outcome_eligible: bool = True,
) -> dict:
    """Require the same durable facts that a production strategy close writes."""
    from aletheia.storage.sqlite import get_db

    position = repo._select_one("positions", position_id)
    assert position is not None
    assert position["status"] == "closed"
    assert bool(position["outcome_eligible"]) is expected_outcome_eligible
    if expected_outcome_eligible:
        assert position["outcome_exclusion_reason"] == "eligible"
    assert json.loads(position["reconciliation_identity"])
    evidence = json.loads(position["reconciliation_evidence"])
    assert evidence.get("source")

    db = get_db()
    entry_order = repo.get_order(position["order_id"])
    assert entry_order is not None
    entry_fills = [
        dict(row) for row in db.execute(
            "SELECT * FROM fills WHERE order_id = ? ORDER BY rowid ASC",
            (entry_order["id"],),
        ).fetchall()
    ]
    assert len(entry_fills) == 1

    close_orders = [
        dict(row) for row in db.execute(
            "SELECT * FROM orders WHERE position_id = ? "
            "AND close_reason IS NOT NULL ORDER BY rowid ASC",
            (position_id,),
        ).fetchall()
    ]
    assert len(close_orders) == 1
    close_order = close_orders[0]
    assert close_order["status"] == "filled"
    assert close_order["client_order_id"] == f"close-{position_id}"
    close_fills = [
        dict(row) for row in db.execute(
            "SELECT * FROM fills WHERE order_id = ? ORDER BY rowid ASC",
            (close_order["id"],),
        ).fetchall()
    ]
    assert close_fills
    assert sum(float(fill["fill_quantity"]) for fill in close_fills) == pytest.approx(
        float(position["quantity"])
    )

    opened_at = datetime.fromisoformat(position["opened_at"].replace("Z", "+00:00"))
    closed_at = datetime.fromisoformat(position["closed_at"].replace("Z", "+00:00"))
    entry_filled_at = datetime.fromisoformat(
        entry_fills[0]["filled_at"].replace("Z", "+00:00")
    )
    close_filled_at = max(
        datetime.fromisoformat(fill["filled_at"].replace("Z", "+00:00"))
        for fill in close_fills
    )
    entry_created_at = datetime.fromisoformat(
        entry_order["created_at"].replace("Z", "+00:00")
    )
    assert entry_created_at <= entry_filled_at <= opened_at < closed_at
    assert closed_at <= datetime.now(timezone.utc)
    assert close_filled_at == closed_at

    jobs = list_outcome_hook_jobs(position_id=position_id)
    # #3704 retired the `shadow_trades` hook that made every eligible close
    # enqueue a job. The surviving producer (`outcome_hooks.enqueue_outcome_hook_jobs`)
    # enqueues `serenity_post_mortem` only, and this fixture is never a Serenity
    # close, so the current contract is zero jobs for either eligibility.
    assert position["trade_school"] != "serenity"
    assert jobs == []
    assert any(
        event["event_type"] == "position_closed"
        and event["entity_id"] == position_id
        for event in repo.list_audit_events(limit=10_000)
    )

    close_audits = [
        dict(row) for row in db.execute(
            "SELECT * FROM audit_events WHERE entity_type = 'position' "
            "AND entity_id = ? AND event_type IN "
            "('position_closed', 'trade_notification') ORDER BY rowid ASC",
            (position_id,),
        ).fetchall()
    ]
    assert close_audits
    assert all(
        datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        == closed_at
        for event in close_audits
    )

    assert entry_order["exchange_order_id"]
    assert close_order["exchange_order_id"]
    assert entry_fills[0]["fee_source"] == "reconciled"
    assert all(fill["fee_source"] == "reconciled" for fill in close_fills)
    assert evidence.get("fill_ids")
    assert evidence.get("durable_fill_ids")

    for job in jobs:
        for field in ("created_at", "updated_at", "next_attempt_at"):
            assert datetime.fromisoformat(
                job[field].replace("Z", "+00:00")
            ) == closed_at
        if job["completed_at"] is not None:
            assert datetime.fromisoformat(
                job["completed_at"].replace("Z", "+00:00")
            ) == closed_at

    return position


def _fixture_utc(value: str, *, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _align_historical_producer_run(
    position_id: str,
    *,
    closed_at: str,
) -> None:
    """Align isolated historical close audit and hook timestamps."""
    db = repo.get_db()
    with repo.transaction(immediate=True):
        db.execute(
            "UPDATE audit_events SET timestamp = ? "
            "WHERE entity_type = 'position' AND entity_id = ? "
            "AND event_type IN ('position_closed', 'trade_notification')",
            (closed_at, position_id),
        )
        db.execute(
            "UPDATE outcome_hook_jobs SET created_at = ?, updated_at = ?, "
            "next_attempt_at = ?, "
            "completed_at = CASE WHEN completed_at IS NULL THEN NULL ELSE ? END "
            "WHERE position_id = ?",
            (closed_at, closed_at, closed_at, closed_at, position_id),
        )


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
    opened_at: str | None = None,
    universe_snapshot_id: str | None = None,
    universe_member_id: str | None = None,
    scanner_family: str = "fixture",
    confidence: str = "speculative",
    confidence_score: int | None = None,
    agent_rationale: str = "",
) -> tuple[str, str, str]:
    """Persist the real entry lineage required by an eligible close fixture."""
    from aletheia.storage.models import (
        Fill,
        Opportunity,
        Order,
        RiskDecision,
        TradeProposal,
    )

    entry_at = (
        _fixture_utc(opened_at, field="opened_at")
        if opened_at is not None
        else datetime.now(timezone.utc)
    ).isoformat()
    opportunity_id = opportunity_id or f"opportunity-{position_id}"
    proposal_id = proposal_id or f"proposal-{position_id}"
    order_id = order_id or f"order-{position_id}"
    opportunity = Opportunity(
        id=opportunity_id,
        symbol=symbol,
        market="spot" if market_type == "spot" else "perp",
        direction_bias=direction,
        source_agent="fixture",
        scanner_family=scanner_family,
        confidence=confidence,
        confidence_score=confidence_score,
        linked_strategy_id=strategy_id,
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        created_at=entry_at,
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
        agent_rationale=agent_rationale,
        created_at=entry_at,
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
        created_at=entry_at,
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
        exchange_order_id=f"entry-exchange-order-{position_id}",
        client_order_id=proposal.id,
        status="filled",
        created_at=entry_at,
        filled_at=entry_at,
    ))
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
    )

    fill_id = durable_private_fill_id(
        execution_mode,
        f"remote-entry-fill-{position_id}",
    )
    fee_source = "reconciled"
    repo.insert_fill(Fill(
        id=fill_id,
        order_id=order_id,
        fill_price=price,
        fill_quantity=quantity,
        fee=entry_fee,
        fee_source=fee_source,
        filled_at=entry_at,
    ))
    return opportunity.id, proposal.id, order_id


def _insert_closed_position_via_producer(
    *,
    position_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    quantity: float,
    entry_price: float,
    realized_pnl: float,
    execution_mode: str,
    closed_at: str,
    opened_at: str | None = None,
    close_reason: str = "verified_exit",
    entry_fee: float = 0.0,
    btc_entry_price: float | None = None,
    exit_fee: float = 0.0,
    funding_cost: float = 0.0,
    strategy_id: str | None = None,
    proposal_id: str | None = None,
    order_id: str | None = None,
    opportunity_id: str | None = None,
    universe_snapshot_id: str | None = None,
    universe_member_id: str | None = None,
    scanner_family: str = "fixture",
    confidence: str = "speculative",
    confidence_score: int | None = None,
    expected_outcome_eligible: bool = True,
    agent_rationale: str = "",
) -> dict:
    """Open a complete fixture chain, then close it through production PnL."""
    from aletheia.portfolio.pnl import close_position
    from aletheia.storage.models import Fill, Order

    closed_dt = _fixture_utc(closed_at, field="closed_at")
    if closed_dt > datetime.now(timezone.utc):
        raise ValueError("closed_at cannot be in the future")
    opened_dt = (
        _fixture_utc(opened_at, field="opened_at")
        if opened_at is not None
        else closed_dt - timedelta(hours=1)
    )
    if opened_dt >= closed_dt:
        raise ValueError("opened_at must be before closed_at")
    opened_iso = opened_dt.isoformat()
    closed_iso = closed_dt.isoformat()

    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id=position_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        price=entry_price,
        execution_mode=execution_mode,
        entry_fee=entry_fee,
        proposal_id=proposal_id,
        order_id=order_id,
        opportunity_id=opportunity_id,
        strategy_id=strategy_id,
        opened_at=opened_iso,
        universe_snapshot_id=universe_snapshot_id,
        universe_member_id=universe_member_id,
        scanner_family=scanner_family,
        confidence=confidence,
        confidence_score=confidence_score,
        agent_rationale=agent_rationale,
    )
    funding_source = (
        "not_applicable"
        if market_type == "spot"
        else "broker_ledger"
    )
    position = Position(
        id=position_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        quantity=quantity,
        entry_price=entry_price,
        current_price=entry_price,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        order_id=order_id,
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        entry_fee=entry_fee,
        btc_entry_price=btc_entry_price,
        funding_cost=funding_cost,
        funding_source=funding_source,
        opened_at=opened_iso,
    )
    repo.insert_position(position)
    repo.update_order_metadata(order_id, position_id=position_id)

    target_gross = realized_pnl + entry_fee + exit_fee + funding_cost
    if direction in {"long", "buy"}:
        exit_price = entry_price + target_gross / quantity
    elif direction in {"short", "sell"}:
        exit_price = entry_price - target_gross / quantity
    else:
        raise ValueError(f"unsupported direction: {direction}")
    if exit_price <= 0:
        raise ValueError("requested fixture PnL requires a non-positive exit price")

    close_order_id = f"close-order-{position_id}"
    remote_order_id = f"remote-close-order-{position_id}"
    close_order = Order(
        id=close_order_id,
        proposal_id=proposal_id,
        client_order_id=f"close-{position_id}",
        position_id=position_id,
        close_reason=close_reason,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type="market",
        quantity=quantity,
        price=exit_price,
        execution_mode=execution_mode,
        exchange_order_id=remote_order_id,
        status="filled",
        created_at=closed_iso,
        filled_at=closed_iso,
    )
    from aletheia.execution.exchange_materialization import (
        durable_private_fill_id,
    )

    remote_fill_id = f"remote-close-fill-{position_id}"
    close_fill_id = durable_private_fill_id(execution_mode, remote_fill_id)
    evidence_source, source_endpoint, account_environment = {
        "bitget_demo": ("bitget_private_close_fills", "private_fills", "demo"),
        "bitget_live_limited": (
            "bitget_private_close_fills", "private_fills", "live",
        ),
        "okx_demo": (
            "okx_demo_private_close_fills",
            "/api/v5/trade/fills-history",
            "demo",
        ),
        "okx_live_limited": (
            "okx_live_limited_private_close_fills",
            "/api/v5/trade/fills-history",
            "live",
        ),
        "binance_testnet": (
            "binance_testnet_private_close_fills",
            "/fapi/v1/userTrades",
            "testnet",
        ),
        "binance_live_limited": (
            "binance_live_limited_private_close_fills",
            "/fapi/v1/userTrades",
            "live",
        ),
    }[execution_mode]
    close_fee_source = "reconciled"
    verified_exit_evidence = {
        "source": evidence_source,
        "source_endpoint": source_endpoint,
        "broker_id": execution_mode,
        "account_environment": account_environment,
        "execution_mode": execution_mode,
        "exchange_order_id": remote_order_id,
        "client_order_id": close_order.client_order_id,
        "fill_ids": [remote_fill_id],
        "durable_fill_ids": [close_fill_id],
        "fill_quantity": quantity,
        "fee_in_quote": exit_fee,
        "fills": [{
            "fill_id": remote_fill_id,
            "remote_fill_id": remote_fill_id,
            "durable_fill_id": close_fill_id,
            "price": exit_price,
            "quantity": quantity,
            "fee": exit_fee,
            "fee_currency": "USDT",
            "filled_at": closed_iso,
        }],
    }
    close_fill = Fill(
        id=close_fill_id,
        order_id=close_order.id,
        fill_price=exit_price,
        fill_quantity=quantity,
        fee=exit_fee,
        fee_source=close_fee_source,
        filled_at=closed_iso,
    )

    def _materialize_close() -> None:
        repo.insert_order(close_order)
        repo.insert_fill(close_fill)

    result = close_position(
        position_id,
        exit_price=exit_price,
        reason=close_reason,
        actual_exit_fee=exit_fee,
        closed_at=closed_iso,
        verified_exit_evidence=verified_exit_evidence,
        materialize_close=_materialize_close,
        actor="test_fixture",
        source="production_close_fixture",
    )
    _align_historical_producer_run(
        position_id,
        closed_at=closed_iso,
    )
    assert result["realized_pnl"] == pytest.approx(realized_pnl)
    return _assert_complete_closed_position_fixture(
        position_id,
        expected_outcome_eligible=expected_outcome_eligible,
    )


def _position(*, execution_mode: str = "bitget_demo") -> Position:
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
        entry_price_source="broker_fill",
        entry_fill_verified=True,
        funding_cost=0.0,
        funding_source="broker_ledger",
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
        account_environment=_account_environment(position.execution_mode),
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
        source_endpoint="private-fill-fixture",
    )


def _admin_identity(position: Position) -> dict:
    return {
        "broker_id": position.execution_mode,
        "account_environment": _account_environment(position.execution_mode),
        "account_scope": "account-scope-1",
        "execution_mode": position.execution_mode,
        "position_id": position.id,
        "symbol": position.symbol,
        "market_type": position.market_type,
        "direction": position.direction,
    }


def test_administrative_close_converges_state_without_economic_side_effects(monkeypatch):
    from aletheia.portfolio import outcome_closure

    position = _position()
    repo.insert_position(position)
    monkeypatch.setattr(
        "aletheia.portfolio.pnl._run_outcome_hooks",
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

    assert repo.get_db().execute("SELECT COUNT(*) FROM paper_account_events").fetchone()[0] == 0
    assert list_outcome_hook_jobs(position_id=position.id) == []
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

    position = _position()
    repo.insert_position(position)
    hook_calls = []
    monkeypatch.setattr(
        "aletheia.portfolio.pnl._run_outcome_hooks",
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
    assert stored["exit_price_source"] == "broker_fill"
    assert stored["exit_fill_verified"] == 1
    assert stored["outcome_eligible"] == 1
    assert stored["exit_fee"] == pytest.approx(0.2)
    assert stored["slippage_cost"] is None
    expected_closed_at = datetime.fromtimestamp(
        max(fill.filled_at_ms for fill in evidence.fills) / 1000,
        tz=timezone.utc,
    ).isoformat()
    assert stored["closed_at"] == expected_closed_at
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
    assert repo.get_db().execute("SELECT COUNT(*) FROM paper_account_events").fetchone()[0] == 0
    events = repo.get_db().execute(
        "SELECT * FROM position_close_events WHERE position_id=?", (position.id,),
    ).fetchall()
    assert len(events) == 1
    assert events[0]["realized_pnl"] == pytest.approx(21.6)


def test_verified_external_close_rolls_back_all_facts_if_close_event_fails(monkeypatch):
    from aletheia.portfolio.outcome_closure import close_position_from_verified_exit

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    before_orders = repo.get_db().execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    before_fills = repo.get_db().execute("SELECT COUNT(*) FROM fills").fetchone()[0]

    def fail_event(_event):
        raise RuntimeError("close event storage failed")

    monkeypatch.setattr(repo, "insert_position_close_event", fail_event)
    with pytest.raises(RuntimeError, match="close event storage failed"):
        close_position_from_verified_exit(position.id, _verified_result(position))
    assert repo.get_position(position.id)["status"] == "open"
    assert repo.get_db().execute("SELECT COUNT(*) FROM orders").fetchone()[0] == before_orders
    assert repo.get_db().execute("SELECT COUNT(*) FROM fills").fetchone()[0] == before_fills
    assert list_outcome_hook_jobs(position_id=position.id) == []


@pytest.mark.parametrize("field", ("fill_price", "fill_quantity", "fee"))
def test_verified_close_rejects_nonfinite_same_identity_durable_fill(field):
    from aletheia.execution.exchange_materialization import (
        MaterializationIntegrityError, materialize_observed_close_chain,
    )
    from aletheia.portfolio.outcome_closure import close_position_from_verified_exit

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    evidence = _verified_result(position)
    with repo.transaction(immediate=True):
        order_ids = materialize_observed_close_chain(
            repo.get_position(position.id), evidence, reason="verified_exit",
        )
        # SQLite can retain nonnumeric text in these historical REAL columns.
        repo.get_db().execute(
            f"UPDATE fills SET {field}='NaN' WHERE order_id=?", (order_ids[0],),
        )
    before = repo.get_position(position.id)
    with pytest.raises(MaterializationIntegrityError, match="durable.*Fill"):
        close_position_from_verified_exit(position.id, evidence)
    assert repo.get_position(position.id) == before
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM position_close_events WHERE position_id=?", (position.id,),
    ).fetchone()[0] == 0
    assert list_outcome_hook_jobs(position_id=position.id) == []


def _seed_late_exit_fee(*, funding_complete=True, strategy_close=True, missing_component="amount"):
    from aletheia.portfolio.outcome_closure import close_position_from_verified_exit
    from aletheia.uta.compat import scope_from_local
    from aletheia.uta.models import UTAAccountIdentity, FillListReport, FillReport

    position = _position()
    if not funding_complete:
        position = position.model_copy(update={
            "funding_cost": None, "funding_source": "unavailable",
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        })
    repo.insert_position(position)
    identity = UTAAccountIdentity(
        account_id="fee-test-account", execution_mode="bitget_demo", venue="bitget",
        environment="demo", profile="uta_v3", credential_generation=1,
        adapter_implementation="ccxt", adapter_version="test",
    )
    repo.update_order_metadata(position.order_id, uta_account_identity_json=identity.model_dump_json())
    evidence = _verified_result(position)
    incomplete_evidence = replace(
        evidence, fills=tuple(
            replace(fill, fee=None) if missing_component == "amount" else replace(fill, fee_currency=None)
            for fill in evidence.fills
        ),
    )
    if strategy_close:
        from aletheia.execution.exchange_materialization import materialize_observed_close_chain

        with repo.transaction(immediate=True):
            materialize_observed_close_chain(repo.get_position(position.id), incomplete_evidence, reason="take_profit")
    close_position_from_verified_exit(position.id, incomplete_evidence,
        verified_strategy_reason="take_profit" if strategy_close else None)
    times = [datetime.fromtimestamp(fill.filled_at_ms / 1000, tz=timezone.utc) for fill in evidence.fills]
    scope = scope_from_local(position.symbol, position.market_type, start_at=min(times), end_at=max(times))
    report = FillListReport(
        identity=identity, completeness="complete", scope=scope,
        received_at=datetime.now(timezone.utc), profile_version="test-fee-v1",
        fills=tuple(FillReport(
            identity=identity, instrument=scope.instrument,
            venue_fill_id=fill.fill_id, venue_order_id=fill.order_id, side="sell",
            price=str(fill.price), quantity=str(fill.quantity), fee=str(fill.fee),
            fee_currency=fill.fee_currency, executed_at=at,
            received_at=datetime.now(timezone.utc),
        ) for fill, at in zip(evidence.fills, times)),
    )
    return position, report


@pytest.mark.parametrize("strategy_close", (True, False))
@pytest.mark.parametrize("missing_component", ("amount", "currency"))
def test_late_fee_scheduler_completes_once_without_rewriting_fill(monkeypatch, strategy_close, missing_component):
    import asyncio
    from aletheia.portfolio import broker_funding_settlement as settlement

    position, report = _seed_late_exit_fee(strategy_close=strategy_close, missing_component=missing_component)
    original_fills = repo.list_fills(limit=100)
    original_evidence = json.loads(repo.get_position(position.id)["reconciliation_evidence"])
    calls = []

    async def query(mode, method, query, *, expected_account):
        assert mode == position.execution_mode and method == "get_fills"
        assert expected_account == report.identity
        assert query.scope == report.scope
        calls.append(method)
        return report

    monkeypatch.setattr("aletheia.uta.manager.query_uta", query)
    result = asyncio.run(settlement.settle_unsettled_exchange_closes(limit=1))
    assert result["settled"] == 1 and result["broker_io_attempted"] == 1, result
    assert calls == ["get_fills"]
    closed = repo.get_position(position.id)
    assert closed["realized_pnl"] == pytest.approx(21.6)
    assert closed["exit_fee"] == pytest.approx(0.2)
    assert bool(closed["outcome_eligible"]) is strategy_close
    assert repo.list_fills(limit=100) == original_fills
    evidence = json.loads(closed["reconciliation_evidence"])
    assert {key: evidence[key] for key in original_evidence} == original_evidence
    assert len(evidence["fee_completion"]["fills"]) == 2
    events = repo.get_db().execute(
        "SELECT * FROM position_close_events WHERE position_id=?", (position.id,),
    ).fetchall()
    assert len(events) == 1 and events[0]["exit_fee"] == pytest.approx(0.2)
    assert events[0]["realized_pnl"] == pytest.approx(21.6)
    assert asyncio.run(settlement.settle_unsettled_exchange_closes(limit=1))["checked"] == 0
    assert settlement.materialize_exchange_close_costs(closed, fee_report=report)["status"] == "already_current"
    assert repo.get_position(position.id) == closed
    assert len([row for row in repo.list_audit_events(limit=100) if row["event_type"] == "exchange_position_fee_completed"]) == 1


@pytest.mark.parametrize("strategy_close", (True, False))
def test_late_fee_then_funding_progress_with_one_query_budget(monkeypatch, strategy_close):
    import asyncio
    from aletheia.execution.broker_funding import make_complete_ledger
    from aletheia.portfolio import broker_funding_settlement as settlement

    position, report = _seed_late_exit_fee(funding_complete=False, strategy_close=strategy_close)
    calls = []

    async def query(*args, **kwargs):
        calls.append("fills")
        return report

    async def ledger(row, start_ms, end_ms):
        calls.append("funding")
        result = make_complete_ledger(
            broker_id=row["execution_mode"], account_environment="demo", source_endpoint="uta_v1",
            history_start_ms=start_ms, history_end_ms=end_ms, events=[],
        )
        # The second observation is after the existing private-ledger publication lag.
        result["observed_at_ms"] = end_ms + 60_000
        return result

    monkeypatch.setattr("aletheia.uta.manager.query_uta", query)
    monkeypatch.setattr(settlement, "_uta_funding_ledger", ledger)
    first = asyncio.run(settlement.settle_unsettled_exchange_closes(limit=1))
    assert first["broker_io_attempted"] == 1 and first["still_pending"] == 1, first
    pending = repo.get_position(position.id)
    assert pending["exit_fee"] == pytest.approx(0.2)
    assert pending["realized_pnl"] is None and pending["funding_cost"] is None
    assert pending["outcome_exclusion_reason"] == ("incomplete_funding_economics" if strategy_close else "administrative_close")
    assert list_outcome_hook_jobs(position_id=position.id) == []
    second = asyncio.run(settlement.settle_unsettled_exchange_closes(limit=1))
    assert second["settled"] == 1 and second["broker_io_attempted"] == 1, second
    assert calls == ["fills", "funding"]
    assert repo.get_position(position.id)["realized_pnl"] == pytest.approx(21.6)
    assert bool(repo.get_position(position.id)["outcome_eligible"]) is strategy_close
    assert asyncio.run(settlement.settle_unsettled_exchange_closes(limit=1))["checked"] == 0


def test_late_fee_audit_failure_rolls_back_fee_and_final_economics(monkeypatch):
    from aletheia.portfolio.broker_funding_settlement import materialize_exchange_close_costs

    position, report = _seed_late_exit_fee()
    before = repo.get_position(position.id)
    events = [dict(row) for row in repo.get_db().execute("SELECT * FROM position_close_events").fetchall()]

    def fail_audit(event):
        assert repo.get_position(position.id)["exit_fee"] == pytest.approx(0.2)
        raise RuntimeError("audit enrollment failed")

    monkeypatch.setattr("aletheia.portfolio.funding_audit.insert_pending_funding_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit enrollment failed"):
        materialize_exchange_close_costs(before, fee_report=report)
    assert repo.get_position(position.id) == before
    assert [dict(row) for row in repo.get_db().execute("SELECT * FROM position_close_events").fetchall()] == events
    assert list_outcome_hook_jobs(position_id=position.id) == []


@pytest.mark.parametrize("field,value", (
    ("exit_fill_verified", 0), ("exit_price_source", "unavailable"),
    ("status", "open"), ("realized_pnl", 1),
    ("outcome_exclusion_reason", "broker_funding_history_expired"),
))
def test_cost_queue_and_counts_use_same_evidence_boundary(field, value):
    from aletheia.portfolio import broker_funding_settlement as settlement

    position, report = _seed_late_exit_fee(funding_complete=False, strategy_close=False)
    settlement.materialize_exchange_close_costs(repo.get_position(position.id), fee_report=report)
    assert settlement.funding_recovery_state_counts()["retry_pending"] == 1
    rows, _ = settlement._select_funding_queue_rows(10)
    assert [row["id"] for row in rows] == [position.id]
    force_update_position(position.id, **{field: value})
    assert settlement._select_funding_queue_rows(10)[0] == []
    assert settlement.funding_recovery_state_counts()["retry_pending"] == 0


def test_verified_external_funding_expiry_preserves_actual_audit_reason(monkeypatch):
    import asyncio
    from aletheia.portfolio import broker_funding_settlement as settlement

    position, report = _seed_late_exit_fee(funding_complete=False, strategy_close=False)
    settlement.materialize_exchange_close_costs(repo.get_position(position.id), fee_report=report)
    force_update_position(position.id, opened_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    monkeypatch.setattr("aletheia.uta.manager.funding_history_retention_contract", lambda *a, **kw: (1, "test-retention"))

    async def unexpected_query(*args, **kwargs):
        pytest.fail("proven expired history must not query the broker")

    monkeypatch.setattr("aletheia.uta.manager.query_uta", unexpected_query)
    result = asyncio.run(settlement.settle_unsettled_exchange_closes(limit=1))
    assert result["history_expired"] == 1 and result["broker_io_attempted"] == 0, result
    current = repo.get_position(position.id)
    assert current["realized_pnl"] is None and current["funding_cost"] is None
    assert current["outcome_eligible"] == 0 and current["close_category"] == "administrative"
    assert settlement.funding_recovery_state_counts() == {"retry_pending": 0, "terminal": 1}
    audit = next(row for row in repo.list_audit_events(limit=100) if row["event_type"] == "exchange_position_funding_history_expired")
    assert json.loads(audit["before_state"])["outcome_exclusion_reason"] == "administrative_close"
    assert list_outcome_hook_jobs(position_id=position.id) == []


def test_native_close_late_fee_uses_same_cost_owner_and_keeps_original_fill():
    from aletheia.execution.broker_funding import make_complete_ledger, compute_stored_broker_funding
    from aletheia.execution.exchange_materialization import materialize_bitget_close
    from aletheia.portfolio.broker_funding_settlement import materialize_exchange_close_costs
    from aletheia.uta.compat import scope_from_local
    from aletheia.uta.models import UTAAccountIdentity, FillListReport, FillReport
    from tests.specs.spec_pipeline_exchange_materialization import _seed_pending_close, _private_fill

    _, entry, position, close = _seed_pending_close(exchange_order_id="native-late-fee", market_type="usdt_futures")
    at_ms = 1_784_400_000_000
    raw = _private_fill(close.exchange_order_id, "native-fee-fill", price=110.0, quantity=1.0, fee=None, filled_at_ms=at_ms)
    assert materialize_bitget_close(close.id, [raw]).state == "filled"
    original = repo.list_fills(order_id=close.id)
    assert len(original) == 1 and original[0]["fee"] is None
    pending = repo.get_position(position.id)
    assert pending["status"] == "closed" and pending["realized_pnl"] is None
    at = datetime.fromtimestamp(at_ms / 1000, tz=timezone.utc)
    scope = scope_from_local(position.symbol, position.market_type, start_at=at, end_at=at)
    identity = UTAAccountIdentity.model_validate_json(repo.get_order(entry.id)["uta_account_identity_json"])
    report = FillListReport(
        identity=identity, completeness="complete", scope=scope,
        received_at=datetime.now(timezone.utc), profile_version="native-fee-test",
        fills=(FillReport(
            identity=identity, instrument=scope.instrument, venue_order_id=close.exchange_order_id,
            venue_fill_id="native-fee-fill", side="sell", quantity="1", price="110",
            fee="0.2", fee_currency="USDT", executed_at=at, received_at=datetime.now(timezone.utc),
        ),),
    )
    assert materialize_exchange_close_costs(pending, fee_report=report)["status"] == "cost_pending"
    pending = repo.get_position(position.id)
    ledger = make_complete_ledger(
        broker_id=position.execution_mode, account_environment="live", source_endpoint="uta_v1",
        history_start_ms=int(datetime.fromisoformat(position.opened_at).timestamp() * 1000),
        history_end_ms=at_ms, events=[],
    )
    funding = compute_stored_broker_funding(repo.get_db(), position=pending, closed_at=pending["closed_at"], ledger=ledger)
    result = materialize_exchange_close_costs(pending, funding_cost=funding.funding_cost,
        funding_evidence=funding.evidence, funding_ledger=ledger)
    assert result["status"] == "settled" and result["realized_pnl"] == pytest.approx(9.74)
    assert repo.list_fills(order_id=close.id) == original
    assert materialize_bitget_close(close.id, report.fills).state == "filled"
    assert repo.list_fills(order_id=close.id) == original


@pytest.mark.parametrize("field,value", (
    ("price", 999), ("quantity", 3), ("venue_fill_id", "other"),
    ("venue_order_id", "other"), ("fee", None), ("fee", "-0.1"),
    ("fee_currency", "BGB"),
))
def test_late_fee_rejects_conflicting_or_incomplete_observation(field, value):
    from decimal import Decimal
    from aletheia.portfolio.broker_funding_settlement import materialize_exchange_close_costs

    position, report = _seed_late_exit_fee()
    before = repo.get_position(position.id)
    if field in {"price", "quantity", "fee"} and value is not None:
        value = Decimal(str(value))
    report = report.model_copy(update={"fills": (
        report.fills[0].model_copy(update={field: value}), report.fills[1],
    )})
    with pytest.raises(ValueError):
        materialize_exchange_close_costs(before, fee_report=report)
    assert repo.get_position(position.id) == before
    assert list_outcome_hook_jobs(position_id=position.id) == []


@pytest.mark.parametrize("fault", ("account", "scope", "partial", "time", "duplicate", "known_fee"))
def test_late_fee_preserves_account_scope_and_original_known_cost(fault):
    from decimal import Decimal
    from aletheia.portfolio.broker_funding_settlement import materialize_exchange_close_costs
    from aletheia.uta.models import ReadCompleteness

    position, report = _seed_late_exit_fee(missing_component="currency" if fault == "known_fee" else "amount")
    before = repo.get_position(position.id)
    if fault == "account":
        identity = report.identity.model_copy(update={"account_id": "other-account"})
        report = report.model_copy(update={
            "identity": identity,
            "fills": tuple(fill.model_copy(update={"identity": identity}) for fill in report.fills),
        })
    elif fault == "scope":
        report = report.model_copy(update={"scope": report.scope.model_copy(update={"instrument": None})})
    elif fault == "partial":
        report = report.model_copy(update={"completeness": ReadCompleteness.PARTIAL, "reason_code": "private_fill_surface_partial"})
    elif fault == "duplicate":
        report = report.model_copy(update={"fills": (*report.fills, report.fills[0])})
    else:
        change = {"fee": Decimal("0.2")} if fault == "known_fee" else {"executed_at": report.fills[0].executed_at + timedelta(milliseconds=1)}
        report = report.model_copy(update={"fills": (report.fills[0].model_copy(update=change), report.fills[1])})
    with pytest.raises(ValueError):
        materialize_exchange_close_costs(before, fee_report=report)
    assert repo.get_position(position.id) == before
    assert list_outcome_hook_jobs(position_id=position.id) == []


def test_verified_exit_normalization_has_no_second_close_writer():
    import ast
    import inspect
    from aletheia.portfolio import outcome_closure, pnl, broker_funding_settlement

    owners = set()
    settlement_owners = set()
    for module in (outcome_closure, pnl, broker_funding_settlement):
        tree = ast.parse(inspect.getsource(module))
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef):
                continue
            if any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "finalize_position_close"
                for node in ast.walk(function)
            ):
                owners.add((module.__name__, function.name))
            if any(
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update_closed_position_settlement"
                for node in ast.walk(function)
            ):
                settlement_owners.add((module.__name__, function.name))
    assert owners == {
        (pnl.__name__, "_commit_close_economics"),
        (outcome_closure.__name__, "close_position_administrative"),
    }
    assert settlement_owners == {
        (pnl.__name__, "_commit_close_economics"),
        (broker_funding_settlement.__name__, "_terminalize_funding_history_expired"),
    }


def test_economic_commit_requires_the_callers_atomic_transaction():
    from aletheia.portfolio.pnl import _commit_close_economics

    assert not repo.get_db().in_transaction
    with pytest.raises(RuntimeError, match="writer transaction"):
        _commit_close_economics({}, {})


@pytest.mark.parametrize("bad_fee", (-1.0, float("inf")), ids=("negative", "infinite"))
def test_verified_close_rejects_invalid_persisted_entry_fee(bad_fee):
    from aletheia.portfolio.outcome_closure import close_position_verified

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    force_update_position(position.id, entry_fee=bad_fee)

    with pytest.raises(ValueError, match="entry_fee must be a finite non-negative value"):
        close_position_verified(position.id, _verified_result(position))

    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "open"
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason IS NOT NULL",
        (position.id,),
    ).fetchone()[0] == 0
    assert list_outcome_hook_jobs(position_id=position.id) == []


def test_verified_external_close_converges_with_unknown_entry_fee():
    """Verified disappearance closes lifecycle while withholding net economics."""
    from aletheia.portfolio.outcome_closure import close_position_from_verified_exit

    position = _position(execution_mode="bitget_demo")
    position.entry_fee = None
    repo.insert_position(position)
    evidence = _verified_result(position)

    result = close_position_from_verified_exit(position.id, evidence)

    assert result["closed"] is True
    assert result["realized_pnl"] is None
    assert result["outcome_eligible"] is False
    assert result["outcome_exclusion_reason"] == "incomplete_realized_economics"
    stored = repo._select_one("positions", position.id)
    assert stored["status"] == "closed"
    assert stored["entry_fee"] is None
    assert stored["exit_fee"] == pytest.approx(0.2)
    assert stored["realized_pnl"] is None
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "incomplete_realized_economics"

    db = repo.get_db()
    close_order = db.execute(
        "SELECT * FROM orders WHERE position_id=? AND close_reason=?",
        (position.id, "verified_external"),
    ).fetchone()
    assert close_order is not None
    assert close_order["source"] == "adopted"
    recorded = json.loads(stored["reconciliation_evidence"])
    assert recorded["materialized_close_order_ids"] == [close_order["id"]]
    assert json.loads(stored["reconciliation_identity"])["account_scope"] == evidence.account_scope
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 2

    repeated = close_position_from_verified_exit(position.id, evidence)
    assert repeated["already_closed"] is True
    assert db.execute(
        "SELECT COUNT(*) FROM orders WHERE position_id=? AND close_reason=?",
        (position.id, "verified_external"),
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT COUNT(*) FROM fills WHERE order_id=?", (close_order["id"],)
    ).fetchone()[0] == 2


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


def test_verified_close_rejects_malformed_fee_or_quantity_mismatch():
    from aletheia.portfolio.outcome_closure import close_position_verified

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    evidence = _verified_result(position)
    bad_fee = replace(
        evidence, fills=(replace(evidence.fills[0], fee=float("nan")), evidence.fills[1]),
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


@pytest.mark.parametrize("fee,currency", [(None, "USDT"), (0.1, ""), (0.1, "BGB")])
def test_verified_external_close_preserves_unknown_costs_and_execution(fee, currency):
    from aletheia.portfolio.outcome_closure import close_position_from_verified_exit
    from aletheia.portfolio.pnl import position_realized_pnl_view

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    evidence = _verified_result(position)
    evidence = replace(evidence, fills=(replace(evidence.fills[0], fee=fee, fee_currency=currency), evidence.fills[1]))
    result = close_position_from_verified_exit(position.id, evidence)
    stored = repo.get_position(position.id)
    assert result["closed"] is True
    assert stored["status"] == "closed"
    assert stored["exit_fill_verified"] == 1
    assert stored["exit_fee"] is None
    assert stored["realized_pnl"] is None
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "incomplete_realized_economics"
    fills = repo.get_db().execute(
        "SELECT f.* FROM fills f JOIN orders o ON o.id=f.order_id "
        "WHERE o.position_id=? AND o.close_reason IS NOT NULL ORDER BY f.filled_at", (position.id,),
    ).fetchall()
    assert len(fills) == 2
    assert fills[0]["fee"] == fee
    assert fills[0]["fee_currency"] == currency
    assert fills[0]["fee_source"] == ("unavailable" if fee is None else "reconciled")
    assert "exit_fee" in position_realized_pnl_view(stored)["known_economics_missing_components"]
    assert list_outcome_hook_jobs(position_id=position.id) == []
    assert close_position_from_verified_exit(position.id, evidence)["already_closed"] is True
    assert repo.get_db().execute(
        "SELECT COUNT(*) FROM position_close_events WHERE position_id=?", (position.id,),
    ).fetchone()[0] == 1


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
    assert result["realized_pnl"] is None
    assert stored["realized_pnl"] is None
    assert stored["funding_cost"] is None
    assert stored["funding_source"] == "unavailable"
    assert stored["outcome_eligible"] == 0
    assert stored["outcome_exclusion_reason"] == "incomplete_funding_economics"


def test_verified_close_applies_clean_window_before_running_outcome_hooks(monkeypatch):
    from aletheia.portfolio import outcome_closure

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    evidence = _verified_result(position)
    # pre-anchor-intentional: this test proves the clean window actually excludes,
    # so the instant must sit before the anchor by construction.
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
        "aletheia.portfolio.pnl._run_outcome_hooks",
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


@pytest.mark.parametrize("presence, expected_reason", [
    ("absent", None),
    ("present", "direct_query_position_present"),
    ("unavailable", "direct_query_unavailable"),
])
def test_ready_candidate_uses_independent_account_presence(monkeypatch, presence, expected_reason):
    from aletheia.portfolio import disappearance_settlement
    from aletheia.storage.models import PositionDisappearanceCandidate
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.models import (
        PositionListReport, PositionStatusReport, QueryScope, ReadCompleteness,
        VerifiedExitReport, VerifiedExitState,
    )
    from tests.uta.test_execution_coordinator import _identity

    position = _position(execution_mode="bitget_demo")
    repo.insert_position(position)
    identity = _identity("bitget_demo").model_copy(update={"account_fingerprint": "account-scope-1"})
    repo.update_order_metadata(position.order_id, uta_account_identity_json=identity.model_dump_json())
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
    instrument = instrument_from_local(position.symbol, position.market_type)

    async def query_account(mode, method, query, *, expected_account):
        assert expected_account == identity
        if method == "verify_position_exit":
            return VerifiedExitReport(
                identity=identity, completeness=ReadCompleteness.COMPLETE,
                scope=QueryScope(instrument=instrument), received_at=datetime.now(timezone.utc),
                profile_version="1", local_position_id=position.id,
                state=VerifiedExitState.CONFIRMED_ABSENT_WITHOUT_FILL,
            )
        assert method == "get_positions"
        if presence == "unavailable":
            raise RuntimeError("positions endpoint unavailable")
        positions = () if presence == "absent" else (PositionStatusReport(
            identity=identity, instrument=instrument, side="long", quantity=2,
            received_at=datetime.now(timezone.utc),
        ),)
        return PositionListReport(
            identity=identity, completeness=ReadCompleteness.COMPLETE,
            scope=query.scope, received_at=datetime.now(timezone.utc), profile_version="1",
            positions=positions,
        )

    monkeypatch.setattr("aletheia.uta.manager.query_uta", query_account)
    result = disappearance_settlement.settle_ready_disappearance_candidate(candidate.id)
    stored_candidate = repo.list_position_disappearance_candidates(limit=10)[0]
    if expected_reason is None:
        assert result["closure_type"] == "administrative"
        assert repo.get_position(position.id)["status"] == "closed"
        assert stored_candidate["status"] == "settled_administrative"
        assert stored_candidate["direct_query_state"] == "confirmed_absent"
    else:
        assert result["closed"] is False
        assert result["reason"] == expected_reason
        assert repo.get_position(position.id)["status"] == "open"
        assert stored_candidate["status"] == "pending"


def test_live_ready_candidate_queries_read_only_and_inconclusive_stays_open(monkeypatch):
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

    from tests.uta.test_execution_coordinator import _identity
    from aletheia.uta.models import VerifiedExitReport, VerifiedExitState, ReadCompleteness, QueryScope
    identity = _identity("bitget_live_limited").model_copy(update={"account_fingerprint": "account-scope-live"})
    repo.update_order_metadata(position.order_id, uta_account_identity_json=identity.model_dump_json())

    async def query_account(mode, method, query, *, expected_account):
        assert expected_account == identity
        assert method == "verify_position_exit"
        queried_modes.append(mode)
        return VerifiedExitReport(
            identity=identity, completeness=ReadCompleteness.PARTIAL,
            scope=QueryScope(instrument=query.instrument), received_at=datetime.now(timezone.utc),
            profile_version="1", local_position_id=position.id,
            state=VerifiedExitState.INCONCLUSIVE, reason_code="fill_history_incomplete",
        )

    monkeypatch.setattr("aletheia.uta.manager.query_uta", query_account)
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

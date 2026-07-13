"""Economic honesty and idempotency for verified/admin position closure."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from aletheia.storage import repositories as repo
from aletheia.storage.models import Position


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

    jobs = repo.list_outcome_hook_jobs(position_id=position_id)
    if expected_outcome_eligible:
        assert jobs
    else:
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

    if position["execution_mode"] == "paper_internal":
        events = [
            dict(row) for row in db.execute(
                "SELECT * FROM paper_account_events ORDER BY rowid ASC"
            ).fetchall()
        ]
        assert sum(
            event["action"] == "position_opened"
            and event["position_id"] == position_id
            for event in events
        ) == 1
        assert sum(
            event["action"] == "position_closed"
            and event["position_id"] == position_id
            for event in events
        ) == 1
        opened_event = next(
            event for event in events
            if event["action"] == "position_opened"
            and event["position_id"] == position_id
        )
        closed_event = next(
            event for event in events
            if event["action"] == "position_closed"
            and event["position_id"] == position_id
        )
        assert datetime.fromisoformat(
            opened_event["created_at"].replace("Z", "+00:00")
        ) == opened_at
        assert datetime.fromisoformat(
            closed_event["created_at"].replace("Z", "+00:00")
        ) == closed_at
        assert closed_event["amount_usdt"] == pytest.approx(
            position["realized_pnl"]
        )
        assert closed_event["cash_balance_after_usdt"] == pytest.approx(
            closed_event["cash_balance_before_usdt"]
            + closed_event["amount_usdt"]
        )
        funding_events = [
            event for event in events if event["action"] == "initial_funding"
        ]
        assert len(funding_events) == 1
        all_opened_at = [
            datetime.fromisoformat(event["created_at"].replace("Z", "+00:00"))
            for event in events
            if event["action"] == "position_opened"
        ]
        funding_at = datetime.fromisoformat(
            funding_events[0]["created_at"].replace("Z", "+00:00")
        )
        assert funding_at <= min(all_opened_at)
        for previous, current in zip(events, events[1:]):
            assert current["cash_balance_before_usdt"] == pytest.approx(
                previous["cash_balance_after_usdt"]
            )
        events_by_time = [
            dict(row) for row in db.execute(
                "SELECT * FROM paper_account_events "
                "ORDER BY created_at ASC, "
                "CASE action WHEN 'initial_funding' THEN 0 "
                "WHEN 'position_opened' THEN 1 "
                "WHEN 'position_closed' THEN 3 ELSE 2 END ASC, rowid ASC"
            ).fetchall()
        ]
        assert [event["id"] for event in events_by_time] == [
            event["id"] for event in events
        ]
        for previous, current in zip(events_by_time, events_by_time[1:]):
            assert current["cash_balance_before_usdt"] == pytest.approx(
                previous["cash_balance_after_usdt"]
            )
    else:
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
    opened_at: str,
    closed_at: str,
    local_paper: bool,
) -> None:
    """Align one historical close and keep any paper ledger in true time order."""
    db = repo.get_db()
    with repo.transaction(immediate=True):
        if local_paper:
            db.execute(
                "UPDATE paper_account_events SET created_at = ? "
                "WHERE action = 'initial_funding' "
                "AND created_at > ?",
                (opened_at, opened_at),
            )
            db.execute(
                "UPDATE paper_account_events SET created_at = ? "
                "WHERE action = 'position_opened' AND position_id = ?",
                (opened_at, position_id),
            )
            db.execute(
                "UPDATE paper_account_events SET created_at = ? "
                "WHERE action = 'position_closed' AND position_id = ?",
                (closed_at, position_id),
            )
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

        if local_paper:
            # Historical fixtures may be inserted out of chronological order or
            # overlap in time. Rebuild this isolated test ledger in event-time order
            # so its physical append order and cached balance snapshots tell the same
            # story. Event identities, amounts, actors, sources, and notes are kept.
            events = [
                dict(row) for row in db.execute(
                    "SELECT * FROM paper_account_events "
                    "ORDER BY created_at ASC, "
                    "CASE action WHEN 'initial_funding' THEN 0 "
                    "WHEN 'position_opened' THEN 1 "
                    "WHEN 'position_closed' THEN 3 ELSE 2 END ASC, rowid ASC"
                ).fetchall()
            ]
            db.execute("DELETE FROM paper_account_events")
            balance = 0.0
            for event in events:
                before = round(balance, 8)
                after = round(before + float(event["amount_usdt"]), 8)
                if after < -1e-8:
                    raise AssertionError(
                        "historical paper fixture would produce negative cash"
                    )
                db.execute(
                    "INSERT INTO paper_account_events "
                    "(id, action, amount_usdt, cash_balance_before_usdt, "
                    "cash_balance_after_usdt, actor, source, position_id, note, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event["id"], event["action"], event["amount_usdt"],
                        before, max(0.0, after), event["actor"], event["source"],
                        event["position_id"], event["note"], event["created_at"],
                    ),
                )
                balance = after


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
        exchange_order_id=(
            None
            if execution_mode == "paper_internal"
            else f"entry-exchange-order-{position_id}"
        ),
        client_order_id=(
            None if execution_mode == "paper_internal" else proposal.id
        ),
        status="filled",
        created_at=entry_at,
        filled_at=entry_at,
    ))
    if execution_mode == "paper_internal":
        fill_id = f"paper-entry:{position_id}:{entry_at}"
        fee_source = "estimated"
    else:
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
    )
    local_paper = execution_mode == "paper_internal"
    funding_source = (
        "not_applicable"
        if market_type == "spot"
        else "local_simulation" if local_paper else "broker_ledger"
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
        entry_price_source="local_fill" if local_paper else "broker_fill",
        entry_fill_verified=True,
        entry_fee=entry_fee,
        funding_cost=funding_cost,
        funding_source=funding_source,
        slippage_cost=0.0 if local_paper else None,
        opened_at=opened_iso,
    )
    repo.insert_position(position)
    repo.update_order(order_id, position_id=position_id)
    if local_paper:
        from aletheia.portfolio.paper_account import record_position_opened

        record_position_opened(position_id)
        opened_event = repo.get_db().execute(
            "SELECT id FROM paper_account_events "
            "WHERE action = 'position_opened' AND position_id = ?",
            (position_id,),
        ).fetchone()
        assert opened_event is not None

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
        exchange_order_id=None if local_paper else remote_order_id,
        status="filled",
        created_at=closed_iso,
        filled_at=closed_iso,
    )
    if local_paper:
        close_fill_id = f"paper-close:{position_id}:{closed_iso}"
        close_fee_source = "estimated"
        verified_exit_evidence = None
    else:
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
    if local_paper:
        closed_event = repo.get_db().execute(
            "SELECT id FROM paper_account_events "
            "WHERE action = 'position_closed' AND position_id = ?",
            (position_id,),
        ).fetchone()
        assert closed_event is not None
    _align_historical_producer_run(
        position_id,
        opened_at=opened_iso,
        closed_at=closed_iso,
        local_paper=local_paper,
    )
    assert result["realized_pnl"] == pytest.approx(realized_pnl)
    return _assert_complete_closed_position_fixture(
        position_id,
        expected_outcome_eligible=expected_outcome_eligible,
    )


def test_historical_paper_fixture_keeps_funding_and_ledger_in_true_time_order():
    now = datetime.now(timezone.utc)
    later_opened = now - timedelta(hours=5)
    later_closed = now - timedelta(hours=1)
    earlier_opened = now - timedelta(hours=6)
    earlier_closed = now - timedelta(hours=3)

    # Insert the later trade first and overlap both holding intervals. The helper
    # must still produce one chronology whose event-time and rowid order agree.
    for position_id, opened_at, closed_at, realized_pnl in (
        ("historical-later", later_opened, later_closed, 2.0),
        ("historical-earlier", earlier_opened, earlier_closed, -1.0),
    ):
        _insert_closed_position_via_producer(
            position_id=position_id,
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            quantity=1.0,
            entry_price=100.0,
            realized_pnl=realized_pnl,
            execution_mode="paper_internal",
            opened_at=opened_at.isoformat(),
            closed_at=closed_at.isoformat(),
        )

    db = repo.get_db()
    events = [
        dict(row) for row in db.execute(
            "SELECT * FROM paper_account_events ORDER BY rowid ASC"
        ).fetchall()
    ]
    assert datetime.fromisoformat(
        events[0]["created_at"].replace("Z", "+00:00")
    ) == earlier_opened
    assert events[0]["action"] == "initial_funding"
    assert [event["id"] for event in events] == [
        row["id"] for row in db.execute(
            "SELECT id FROM paper_account_events "
            "ORDER BY created_at ASC, "
            "CASE action WHEN 'initial_funding' THEN 0 "
            "WHEN 'position_opened' THEN 1 "
            "WHEN 'position_closed' THEN 3 ELSE 2 END ASC, rowid ASC"
        ).fetchall()
    ]


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

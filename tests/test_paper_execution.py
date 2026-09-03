from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import time

import pytest

from aletheia.data.symbols import InstrumentId
from aletheia.execution.paper_execution import (
    EntryExecutionMarketSnapshotV1,
    EntryExecutionReceiptV1,
    PaperMarketState,
    ReceiptDepth,
    consume_known_depth,
    normalize_paper_order,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import Order, RiskDecision, UniverseAsset
from aletheia.universe.fast_stream import FastMarketEvent
from tests.test_entry_plan_execution import _paper_snapshot, _persist_waiting_plan


def _approve(proposal, *, decision_role, execution_receipt=None):
    decision = RiskDecision(
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode="paper_internal",
        decision_role=decision_role,
        evaluated_quantity=proposal.quantity,
        proposal_commit_hash=proposal.commit_hash,
        proposal_execution_mode=proposal.execution_mode,
        attempt_id=getattr(execution_receipt, "attempt_id", None),
        receipt_hash=getattr(execution_receipt, "receipt_hash", None),
    )
    repo.insert_risk_decision(decision)
    if decision_role == "authoritative":
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
    return SimpleNamespace(
        approved=True,
        allowed_execution_mode="paper_internal",
        decision_id=decision.id,
        decision_role=decision_role,
    )


def _execute_maker(monkeypatch, plan):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    monkeypatch.setattr(gateway, "evaluate_proposal", _approve)
    now = datetime.now(timezone.utc)
    return execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        execution_market_snapshot=_paper_snapshot(now),
        now=now,
    )


def _trade(*, order, price, size, side, sequence):
    time.sleep(0.002)
    placed = datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
    timestamp_ms = max(
        int(datetime.now(timezone.utc).timestamp() * 1_000),
        int(placed.timestamp() * 1_000) + 1,
    )
    return FastMarketEvent(
        event_type="trade",
        instrument=InstrumentId.from_parts("bitget", "BTC/USDT", "perp"),
        exchange_timestamp_ms=timestamp_ms,
        received_timestamp_ms=timestamp_ms,
        sequence=sequence,
        price=price,
        size=size,
        side=side,
    )


def test_receipt_rejects_stale_channels_and_hash_tampering():
    now = datetime.now(timezone.utc)
    snapshot = _paper_snapshot(now)
    receipt = EntryExecutionReceiptV1.create(
        snapshot,
        attempt_id="attempt-1",
        proposal_id="proposal-1",
        entry_plan_id="plan-1",
    )
    receipt.validate_now(now)
    with pytest.raises(ValueError, match="entry_execution_receipt_hash_mismatch"):
        receipt.model_copy(update={"proposal_id": "forged"}).validate_now(now)
    with pytest.raises(ValueError, match="entry_execution_receipt_stale"):
        receipt.validate_now(now + timedelta(seconds=3))


def test_preorder_generation_change_is_unavailable_then_retries_with_new_receipt(monkeypatch):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan
    from aletheia.execution.paper_execution import paper_market_state

    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
    )
    monkeypatch.setattr(gateway, "evaluate_proposal", _approve)
    now = datetime.now(timezone.utc)
    stale_generation = _paper_snapshot(now)
    paper_market_state.begin_stream(
        {InstrumentId.from_parts("bitget", "BTC/USDT", "perp")}
    )
    unavailable = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        execution_market_snapshot=stale_generation,
        now=now,
    )
    assert unavailable["status"] == "unavailable"
    assert unavailable["reason_code"] == "entry_execution_snapshot_stream_generation_mismatch"
    assert repo.get_order_for_entry_plan(plan.plan_id) is None

    retry_now = datetime.now(timezone.utc)
    executed = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=retry_now.isoformat(),
        source_observed_at=retry_now.isoformat(),
        execution_market_snapshot=_paper_snapshot(retry_now),
        now=retry_now,
    )
    assert executed["status"] == "executed"
    receipt_rows = repo.get_db().execute(
        "SELECT attempt_id FROM entry_execution_receipts ORDER BY rowid"
    ).fetchall()
    assert len(receipt_rows) == 1


def test_ioc_consumes_only_known_levels_and_computes_exact_vwap():
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1_000)
    fill = consume_known_depth(
        direction="long",
        quantity=1,
        limit_price=101,
        depth=ReceiptDepth(
            bids=((100, 2),),
            asks=((100.05, 0.4), (100.5, 0.3), (101.1, 5)),
            exchange_timestamp_ms=timestamp_ms,
            received_timestamp_ms=timestamp_ms,
            sequence=1,
        ),
    )
    assert fill.quantity == pytest.approx(0.7)
    assert fill.vwap == pytest.approx((100.05 * 0.4 + 100.5 * 0.3) / 0.7)
    assert fill.levels == ((100.05, 0.4), (100.5, 0.3))


def test_maker_requires_later_strict_opposite_trade_through_and_keeps_hold(monkeypatch):
    from aletheia.execution.gateway import cancel_entry_plan_order, process_paper_market_event
    from aletheia.execution.paper_execution import mark_open_order_monitoring_gap

    plan = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
    )
    result = _execute_maker(monkeypatch, plan)
    order = repo.get_order(result["order_id"])
    reservation = repo.get_entry_risk_reservation(order["proposal_id"])
    assert repo.list_fills(order_id=order["id"]) == []
    assert mark_open_order_monitoring_gap() == 1
    assert "monitoring_gap" in repo.get_order(order["id"])["paper_execution_evidence"]
    assert repo.get_entry_risk_reservation(order["proposal_id"]) is not None

    assert process_paper_market_event(
        _trade(order=order, price=100, size=1, side="sell", sequence=1)
    ) == []
    assert process_paper_market_event(
        _trade(order=order, price=99.99, size=1, side="buy", sequence=2)
    ) == []
    event = _trade(order=order, price=99.99, size=0.4, side="sell", sequence=3)
    fills = process_paper_market_event(event)
    assert fills[0]["fill_quantity"] == pytest.approx(0.4)
    assert fills[0]["fill_price"] == 100
    assert repo.get_order(order["id"])["status"] == "partially_filled"
    assert repo.get_entry_plan(plan.plan_id)["status"] == "partially_filled"
    remaining = repo.get_entry_risk_reservation(order["proposal_id"])
    assert remaining["quantity"] == pytest.approx(reservation["quantity"] - 0.4)
    assert process_paper_market_event(event) == []

    assert cancel_entry_plan_order(plan.plan_id, reason="maker_order_unfilled_until_expiry")["status"] == "cancelled"
    assert repo.get_entry_risk_reservation(order["proposal_id"]) is None


def test_one_trade_budget_uses_price_priority_across_local_orders(monkeypatch):
    from aletheia.execution.gateway import process_paper_market_event

    low = _persist_waiting_plan(
        account_mode="paper_internal",
        mode="RESTING_MAKER",
        tactic="POST_ONLY_LIMIT",
    )
    low_result = _execute_maker(monkeypatch, low)
    low_order = repo.get_order(low_result["order_id"])
    high_order = Order.model_validate(low_order).model_copy(
        update={
            "id": "order-high",
            "price": 100.02,
            "created_at": (datetime.now(timezone.utc) + timedelta(microseconds=1)).isoformat(),
        }
    )
    repo.insert_order(high_order)
    high_order = repo.get_order(high_order.id)

    fills = process_paper_market_event(
        _trade(order=high_order, price=99.9, size=0.5, side="sell", sequence=10)
    )
    assert sum(item["fill_quantity"] for item in fills) == pytest.approx(0.5)
    assert repo.list_fills(order_id=low_order["id"]) == []
    assert repo.list_fills(order_id=high_order["id"])[0]["fill_quantity"] == pytest.approx(0.5)


def test_exact_active_venue_rules_are_required():
    with pytest.raises(ValueError, match="paper_instrument_rules_unavailable"):
        normalize_paper_order(
            venue="bitget",
            symbol="BTC/USDT",
            market_type="perp",
            price=100,
            quantity=1,
        )
    repo.insert_universe_asset(
        UniverseAsset(
            id="asset-rules",
            venue="bitget",
            symbol="BTC/USDT",
            market_type="perp",
            min_notional=10,
            price_precision=2,
            amount_precision=3,
        )
    )
    assert normalize_paper_order(
        venue="bitget",
        symbol="BTC/USDT",
        market_type="perp",
        price=100.05,
        quantity=0.1239,
    ) == (100.05, 0.123)
    with pytest.raises(ValueError, match="paper_price_precision_invalid"):
        normalize_paper_order(
            venue="bitget", symbol="BTC/USDT", market_type="perp",
            price=100.051, quantity=1,
        )
    repo.execute_write(
        "UPDATE universe_registry SET status='inactive' WHERE id='asset-rules'"
    )
    with pytest.raises(ValueError, match="paper_instrument_rules_unavailable"):
        normalize_paper_order(
            venue="bitget", symbol="BTC/USDT", market_type="perp",
            price=100.05, quantity=1,
        )


def test_ioc_partial_fill_cancels_unknown_remainder_without_extra_slippage(monkeypatch):
    from aletheia.execution import gateway
    from aletheia.execution.entry_plan_execution import execute_entry_plan

    plan = _persist_waiting_plan(account_mode="paper_internal")
    monkeypatch.setattr(gateway, "evaluate_proposal", _approve)
    now = datetime.now(timezone.utc)
    snapshot = _paper_snapshot(
        now,
        asks=((100.05, 0.4), (100.5, 0.2), (101.1, 5)),
    )
    result = execute_entry_plan(
        plan.plan_id,
        bid=100,
        ask=100.05,
        observed_at=now.isoformat(),
        source_observed_at=now.isoformat(),
        execution_market_snapshot=snapshot,
        now=now,
    )
    order = repo.get_order(result["order_id"])
    fill = repo.list_fills(order_id=order["id"])[0]
    assert result["fill_quantity"] == pytest.approx(0.6)
    assert fill["fill_price"] == pytest.approx((100.05 * 0.4 + 100.5 * 0.2) / 0.6)
    assert order["status"] == "cancelled"
    assert order["error_reason"] == "paper_ioc_remainder_cancelled"
    assert repo.get_entry_plan(plan.plan_id)["terminal_reason"] == "paper_ioc_remainder_cancelled"
    assert repo.get_entry_risk_reservation(order["proposal_id"]) is None


def test_monitoring_requires_all_fresh_channels_and_preserves_unchanged_instrument():
    state = PaperMarketState()
    instrument = InstrumentId.from_parts("binance", "BTC/USDT", "perp")
    other = InstrumentId.from_parts("binance", "ETH/USDT", "spot")
    state.begin_stream({instrument})
    timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1_000)
    facts = (
        FastMarketEvent(
            event_type="quote", instrument=instrument, exchange_timestamp_ms=timestamp_ms,
            received_timestamp_ms=timestamp_ms, sequence=1, bid=100, ask=100.1,
        ),
        FastMarketEvent(
            event_type="trade", instrument=instrument, exchange_timestamp_ms=timestamp_ms,
            received_timestamp_ms=timestamp_ms, sequence=1, price=100, size=1, side="sell",
        ),
        FastMarketEvent(
            event_type="depth", instrument=instrument, exchange_timestamp_ms=timestamp_ms,
            received_timestamp_ms=timestamp_ms, sequence=1,
            bids=((100, 1),), asks=((100.1, 1),),
        ),
        FastMarketEvent(
            event_type="candle", instrument=instrument, exchange_timestamp_ms=timestamp_ms,
            received_timestamp_ms=timestamp_ms, sequence=1,
            candle=(100, 101, 99, 100.5, 10),
        ),
    )
    state.observe(facts[0])
    assert state.readiness(instrument, now_ms=timestamp_ms)["state"] == "warming"
    for fact in facts[1:]:
        state.observe(fact)
    before = state.readiness(instrument, now_ms=timestamp_ms)
    assert before["state"] == "ready"
    state.set_instruments({instrument, other})
    after = state.readiness(instrument, now_ms=timestamp_ms)
    assert after["subscription_generation_id"] == before["subscription_generation_id"]
    assert after["state"] == "ready"
    assert state.readiness(other, now_ms=timestamp_ms)["state"] == "warming"

    state.observe(facts[0])
    assert state.readiness(instrument, now_ms=timestamp_ms)["channels"]["quote"]["reason"] == "sequence_discontinuous"
    state.observe(facts[0].model_copy(update={"sequence": 2}))
    assert state.readiness(instrument, now_ms=timestamp_ms)["channels"]["quote"]["state"] == "ready"

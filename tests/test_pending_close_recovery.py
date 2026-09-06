"""Regression tests for stale pending-close recovery (XRP/UNI class failures)."""

from __future__ import annotations

from tests.position_fixture import force_update_position

from datetime import datetime, timedelta, timezone

import pytest

from aletheia.execution import pending_close_recovery as pcr
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, Position


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _seed_order(order: Order) -> None:
    repo._insert("orders", repo._stamp_provenance(order.model_dump()))


def _position(**kwargs) -> Position:
    defaults = {
        "symbol": "XRP/USDT",
        "market_type": "usdt_futures",
        "direction": "short",
        "quantity": 9.0,
        "entry_price": 1.1219,
        "execution_mode": "bitget_demo",
        "status": "open",
        "source": "native",
        "opened_at": _iso(30),
    }
    defaults.update(kwargs)
    return Position(**defaults)


@pytest.fixture(autouse=True)
def _skip_uta_history(monkeypatch):
    monkeypatch.setattr(pcr, "_uta_history_lookup", lambda order: None)


def test_xrp_stale_submitted_close_released_when_absent_on_exchange(monkeypatch):
    """Mimics the 2026-08-11 XRP take_profit close stuck in submitted."""
    order = Order(
        id="71ae77205561",
        proposal_id="6ab3a1c50e22",
        client_order_id="close-0da931113c25",
        position_id="0da931113c25",
        close_reason="take_profit",
        symbol="XRP/USDT",
        market_type="usdt_futures",
        direction="short",
        order_type="market",
        quantity=9.0,
        price=1.0038,
        execution_mode="bitget_demo",
        status="submitted",
        source="native",
        exchange_order_id="1471057873956356096",
        created_at=_iso(22),
    )
    _seed_order(order)
    repo.insert_position(
        _position(
            id="0da931113c25",
            pending_close_order_id="71ae77205561",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(22),
        )
    )

    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 1
    assert summary["released_position_ids"] == ["0da931113c25"]

    stored_order = repo.get_order("71ae77205561")
    assert stored_order["status"] == "cancelled"
    assert stored_order["error_reason"] == "stale_close_order_absent_unfilled"

    stored_position = repo._select_one("positions", "0da931113c25")
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["pending_close_status"] is None


def test_uni_entry_limit_on_exchange_is_not_released(monkeypatch):
    """UNI resting entry limits must not be cleared by close-order recovery."""
    order = Order(
        id="848556aed121",
        proposal_id="675aef2e849a",
        client_order_id="675aef2e849a",
        symbol="UNI/USDT",
        market_type="usdt_futures",
        direction="short",
        order_type="limit",
        quantity=3.0,
        price=3.811,
        execution_mode="bitget_demo",
        status="submitted",
        source="native",
        exchange_order_id="1471082516914225152",
        created_at=_iso(22),
    )
    _seed_order(order)

    monkeypatch.setattr(
        pcr,
        "_exchange_open_order_ids",
        lambda order: {"1471082516914225152"},
    )

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0
    assert repo.get_order("848556aed121")["status"] == "submitted"


def test_recent_close_claim_is_not_released(monkeypatch):
    """Ack-lag floor: a just-submitted close must not look 'absent' yet."""
    _seed_order(
        Order(
            id="close-fresh",
            client_order_id="close-pos-fresh",
            position_id="pos-fresh",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="ex-fresh",
            created_at=_iso(0.08),
        )
    )
    repo.insert_position(
        _position(
            id="pos-fresh",
            pending_close_order_id="close-fresh",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(0.08),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0
    assert repo.get_order("close-fresh")["status"] == "submitted"


def test_absent_unfilled_close_released_after_ack_lag(monkeypatch):
    """XRP r9 class: order gone, no fills, position still open — do not wait 4h."""
    _seed_order(
        Order(
            id="close-ack-lost",
            client_order_id="close-pos-ack-lost-r9",
            position_id="pos-ack-lost",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="ex-ack-lost",
            created_at=_iso(0.3),
        )
    )
    repo.insert_position(
        _position(
            id="pos-ack-lost",
            pending_close_order_id="close-ack-lost",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(0.3),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 1
    stored_order = repo.get_order("close-ack-lost")
    assert stored_order["status"] == "cancelled"
    assert stored_order["error_reason"] == "stale_close_order_absent_unfilled"
    stored_position = repo._select_one("positions", "pos-ack-lost")
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["pending_close_status"] is None


@pytest.mark.parametrize("history_status", ("accepted", "open", "partially_filled", "unknown"))
def test_exact_nonterminal_close_state_keeps_claim(monkeypatch, history_status):
    order_id = f"close-nonterminal-{history_status}"
    position_id = f"pos-nonterminal-{history_status}"
    _seed_order(
        Order(
            id=order_id,
            client_order_id=f"close-{position_id}",
            position_id=position_id,
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id=f"ex-{history_status}",
            created_at=_iso(1),
        )
    )
    repo.insert_position(
        _position(
            id=position_id,
            pending_close_order_id=order_id,
            pending_close_status="submitted",
            pending_close_requested_at=_iso(1),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(
        pcr,
        "_uta_history_lookup",
        lambda order: {"orderStatus": history_status, "cancelReason": ""},
    )

    summary = pcr.recover_absent_stale_close_orders()

    assert summary["released"] == 0
    assert repo.get_order(order_id)["status"] == "submitted"
    stored_position = repo.get_position(position_id)
    assert stored_position["pending_close_order_id"] == order_id
    assert stored_position["pending_close_status"] == "submitted"


def test_exact_terminal_close_state_can_release(monkeypatch):
    order_id = "close-terminal-canceled"
    position_id = "pos-terminal-canceled"
    _seed_order(
        Order(
            id=order_id,
            client_order_id=f"close-{position_id}",
            position_id=position_id,
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="ex-terminal-canceled",
            created_at=_iso(1),
        )
    )
    repo.insert_position(
        _position(
            id=position_id,
            pending_close_order_id=order_id,
            pending_close_status="submitted",
            pending_close_requested_at=_iso(1),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(
        pcr,
        "_uta_history_lookup",
        lambda order: {"orderStatus": "canceled", "cancelReason": ""},
    )

    summary = pcr.recover_absent_stale_close_orders()

    assert summary["released"] == 1
    assert repo.get_order(order_id)["status"] == "cancelled"
    assert repo.get_position(position_id)["pending_close_order_id"] is None


def test_absent_recovery_collects_exchange_evidence_outside_writer_lock(monkeypatch):
    _seed_order(
        Order(
            id="close-evidence-once",
            client_order_id="close-pos-evidence-once",
            position_id="pos-evidence-once",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="ex-evidence-once",
            created_at=_iso(0.3),
        )
    )
    repo.insert_position(
        _position(
            id="pos-evidence-once",
            pending_close_order_id="close-evidence-once",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(0.3),
        )
    )
    from aletheia.storage.sqlite import write_lock_held_by_current_thread

    lock_states: list[bool] = []
    monkeypatch.setattr(
        pcr,
        "_exchange_open_order_ids",
        lambda order: (lock_states.append(write_lock_held_by_current_thread()), set())[1],
    )
    monkeypatch.setattr(
        pcr,
        "_exchange_fill_rows",
        lambda order: (lock_states.append(write_lock_held_by_current_thread()), [])[1],
    )

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 1
    assert lock_states == [False, False]


def test_absent_recovery_refuses_changed_local_claim_after_evidence(monkeypatch):
    _seed_order(
        Order(
            id="close-claim-changed",
            client_order_id="close-pos-claim-changed",
            position_id="pos-claim-changed",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="ex-claim-changed",
            created_at=_iso(0.3),
        )
    )
    repo.insert_position(
        _position(
            id="pos-claim-changed",
            pending_close_order_id="close-claim-changed",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(0.3),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())

    def mutate_claim(_order):
        force_update_position(
            "pos-claim-changed", pending_close_status="replaced_by_owner"
        )
        return []

    monkeypatch.setattr(pcr, "_exchange_fill_rows", mutate_claim)

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0
    assert repo.get_order("close-claim-changed")["status"] == "submitted"
    assert repo.get_position("pos-claim-changed")["pending_close_status"] == "replaced_by_owner"


def test_close_with_local_fill_is_not_released(monkeypatch):
    _seed_order(
        Order(
            id="close-filled",
            client_order_id="close-pos-filled",
            position_id="pos-filled",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="ex-filled",
            created_at=_iso(10),
        )
    )
    repo.insert_position(
        _position(
            id="pos-filled",
            pending_close_order_id="close-filled",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(10),
        )
    )
    repo.insert_fill(
        Fill(order_id="close-filled", fill_price=1.0, fill_quantity=9.0)
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0


def test_submission_rejected_claim_released_after_soft_window():
    repo.insert_position(
        _position(
            id="pos-rejected",
            pending_close_order_id="prior-close",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(6),
        )
    )

    summary = pcr.release_stale_submission_rejected_claims()
    assert summary["released"] == 1
    stored = repo._select_one("positions", "pos-rejected")
    assert stored["pending_close_order_id"] is None
    assert stored["pending_close_status"] is None


def test_submission_rejected_with_recoverable_cancel_releases_immediately():
    _seed_order(
        Order(
            id="71ae77205561",
            client_order_id="close-0da931113c25",
            position_id="0da931113c25",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0038,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            error_reason="stale_close_order_absent_unfilled",
            created_at=_iso(0.1),
        )
    )
    repo.insert_position(
        _position(
            id="0da931113c25",
            pending_close_order_id="71ae77205561",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(0.1),
        )
    )

    summary = pcr.release_stale_submission_rejected_claims()
    assert summary["released"] == 1
    stored = repo._select_one("positions", "0da931113c25")
    assert stored["pending_close_order_id"] is None


def test_next_close_client_oid_increments_after_recoverable_cancel():
    _seed_order(
        Order(
            id="close-base",
            client_order_id="close-pos-oid",
            position_id="pos-oid",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            error_reason="stale_close_order_absent_unfilled",
            created_at=_iso(10),
        )
    )
    assert pcr.next_close_client_oid("pos-oid") == "close-pos-oid-r1"
    assert pcr.is_durable_close_client_oid("pos-oid", "close-pos-oid")
    assert pcr.is_durable_close_client_oid("pos-oid", "close-pos-oid-r1")
    assert not pcr.is_durable_close_client_oid("pos-oid", "close-other-r1")
    assert not pcr.is_durable_close_client_oid("pos-oid", "close-pos-oid-retry")


def test_rate_limited_cleanup_cancel_releases_submission_rejected_immediately():
    """XRP-class: cleanup cancelled an unfilled close for trade rate limits."""
    _seed_order(
        Order(
            id="919fb14a85c2",
            client_order_id="close-0da931113c25-r4",
            position_id="0da931113c25",
            close_reason="operator_exit_after_cooldown",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0112,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            error_reason="operator_authorized_cleanup_trade_rate_limited",
            created_at=_iso(0.1),
        )
    )
    repo.insert_position(
        _position(
            id="0da931113c25",
            pending_close_order_id="919fb14a85c2",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(0.1),
        )
    )

    summary = pcr.release_stale_submission_rejected_claims()
    assert summary["released"] == 1
    stored = repo._select_one("positions", "0da931113c25")
    assert stored["pending_close_order_id"] is None
    assert stored["pending_close_status"] is None


def test_submission_rejected_recent_claim_is_retained():
    repo.insert_position(
        _position(
            id="pos-recent-reject",
            pending_close_order_id="prior-close",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(1),
        )
    )
    _seed_order(
        Order(
            id="prior-close",
            client_order_id="close-pos-recent-reject",
            position_id="pos-recent-reject",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="error",
            source="native",
            error_reason="close_submission_rejected",
            created_at=_iso(1),
        )
    )

    summary = pcr.release_stale_submission_rejected_claims()
    assert summary["released"] == 0
    stored = repo._select_one("positions", "pos-recent-reject")
    assert stored["pending_close_status"] == "submission_rejected"


def test_uta_trade_rate_limit_parks_claim_instead_of_releasing(monkeypatch):
    """XRP r10 class: Bitget 40725 must not free the claim for another place-order close."""
    _seed_order(
        Order(
            id="b2bec540efe3",
            client_order_id="close-0da931113c25-r10",
            position_id="0da931113c25",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="1471627178020290560",
            created_at=_iso(0.08),
        )
    )
    repo.insert_position(
        _position(
            id="0da931113c25",
            pending_close_order_id="b2bec540efe3",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(0.08),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(
        pcr,
        "_uta_history_lookup",
        lambda order: {
            "orderId": "1471627178020290560",
            "orderStatus": "cancelled",
            "cancelReason": "trade_too_many_times_cancel",
            "cumExecQty": "0",
        },
    )

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0
    assert summary["parked"] == 1
    assert summary["parked_position_ids"] == ["0da931113c25"]

    stored_order = repo.get_order("b2bec540efe3")
    assert stored_order["status"] == "cancelled"
    assert stored_order["error_reason"] == "trade_too_many_times_cancel"
    assert not pcr.is_recoverable_cancelled_close(stored_order)

    stored_position = repo._select_one("positions", "0da931113c25")
    assert stored_position["pending_close_order_id"] == "b2bec540efe3"
    assert stored_position["pending_close_status"] == "venue_trade_rate_limited"


def test_owner_authorized_release_frees_parked_claim_for_close_retry():
    """Owner unlock of a 40725 park: claim cleared, retry oid allocated, audit kept."""
    _seed_order(
        Order(
            id="b2bec540efe3",
            client_order_id="close-0da931113c25-r10",
            position_id="0da931113c25",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            exchange_order_id="1471627178020290560",
            error_reason="trade_too_many_times_cancel",
            created_at=_iso(0.5),
        )
    )
    repo.insert_position(
        _position(
            id="0da931113c25",
            pending_close_order_id="b2bec540efe3",
            pending_close_status="venue_trade_rate_limited",
            pending_close_requested_at=_iso(0.5),
        )
    )

    result = pcr.release_rate_limited_close_claim(
        "0da931113c25",
        actor="human",
        reason="owner-authorized cleanup after 40725 cooldown elapsed",
    )
    assert result["released"] is True
    assert result["retry_client_order_id"] == "close-0da931113c25-r11"

    stored_order = repo.get_order("b2bec540efe3")
    assert stored_order["status"] == "cancelled"
    assert stored_order["error_reason"] == "operator_authorized_cleanup_trade_rate_limited"
    assert pcr.is_recoverable_cancelled_close(stored_order) is True

    stored_position = repo._select_one("positions", "0da931113c25")
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["pending_close_status"] is None
    assert stored_position["pending_close_requested_at"] is None

    events = repo.list_audit_events(limit=10)
    release_events = [
        e for e in events
        if e.get("event_type") == "pending_close_owner_authorized_release"
    ]
    assert release_events and release_events[0]["actor"] == "human"


def test_owner_authorized_release_refuses_non_parked_claim():
    _seed_order(
        Order(
            id="claim-sub-rej",
            client_order_id="close-pos-sub-rej",
            position_id="pos-sub-rej",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            error_reason="stale_close_order_absent_unfilled",
            created_at=_iso(1),
        )
    )
    repo.insert_position(
        _position(
            id="pos-sub-rej",
            pending_close_order_id="claim-sub-rej",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(1),
        )
    )

    result = pcr.release_rate_limited_close_claim(
        "pos-sub-rej", actor="human", reason="not actually parked"
    )
    assert result["released"] is False
    # The cancelled-but-different-reason order fails the admissible-state
    # checks rather than the claim-status gate (both refuse, state unchanged).
    assert result["code"] == "parked_order_mismatch"
    stored_position = repo._select_one("positions", "pos-sub-rej")
    assert stored_position["pending_close_status"] == "submission_rejected"


def test_owner_authorized_release_accepts_unreached_venue_submission_rejection():
    """A venue-rejected close that never reached the exchange (no exchange id,
    terminal error) is safely owner-releasable without the 4h age-out."""
    _seed_order(
        Order(
            id="claim-sub-rej-unreached",
            client_order_id="close-pos-sub-rej2-r1",
            position_id="pos-sub-rej2",
            close_reason="stop_loss",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=424.0,
            price=1.48,
            execution_mode="bitget_demo",
            status="error",
            source="native",
            error_reason="close_submission_rejected:Bitget order error: tick",
            created_at=_iso(0.5),
        )
    )
    repo.insert_position(
        _position(
            id="pos-sub-rej2",
            pending_close_order_id="claim-sub-rej-unreached",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(0.5),
        )
    )

    result = pcr.release_rate_limited_close_claim(
        "pos-sub-rej2", actor="human", reason="tick-quantization fix deployed"
    )
    assert result["released"] is True
    stored_position = repo._select_one("positions", "pos-sub-rej2")
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["pending_close_status"] is None
    # The rejected order keeps its own error reason — it never reached matching,
    # so it must not enter the clip-halving cancel history.
    stored_order = repo.get_order("claim-sub-rej-unreached")
    assert stored_order["error_reason"].startswith("close_submission_rejected")


def test_owner_authorized_release_refuses_submitted_rejection_with_exchange_id():
    _seed_order(
        Order(
            id="claim-sub-rej-reached",
            client_order_id="close-pos-sub-rej3-r1",
            position_id="pos-sub-rej3",
            close_reason="stop_loss",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="error",
            source="native",
            exchange_order_id="1475000000000000000",
            error_reason="close_submission_rejected:late",
            created_at=_iso(0.5),
        )
    )
    repo.insert_position(
        _position(
            id="pos-sub-rej3",
            pending_close_order_id="claim-sub-rej-reached",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(0.5),
        )
    )

    result = pcr.release_rate_limited_close_claim(
        "pos-sub-rej3", actor="human", reason="must stay for reconciliation"
    )
    assert result["released"] is False
    assert result["code"] == "parked_order_mismatch"
    stored_position = repo._select_one("positions", "pos-sub-rej3")
    assert stored_position["pending_close_status"] == "submission_rejected"


def test_owner_authorized_release_is_idempotent_on_already_released_order():
    _seed_order(
        Order(
            id="claim-done",
            client_order_id="close-pos-done-r1",
            position_id="pos-done",
            close_reason="stop_loss",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            error_reason="operator_authorized_cleanup_trade_rate_limited",
            created_at=_iso(1),
        )
    )
    repo.insert_position(
        _position(
            id="pos-done",
            pending_close_order_id="claim-done",
            pending_close_status="venue_trade_rate_limited",
            pending_close_requested_at=_iso(1),
        )
    )

    result = pcr.release_rate_limited_close_claim(
        "pos-done", actor="human", reason="double release attempt"
    )
    assert result["released"] is False
    assert result["code"] == "parked_order_mismatch"
    stored_position = repo._select_one("positions", "pos-done")
    assert stored_position["pending_close_status"] == "venue_trade_rate_limited"


def test_uta_history_failure_does_not_release_absent_close(monkeypatch):
    _seed_order(
        Order(
            id="close-hist-fail",
            client_order_id="close-pos-hist-fail",
            position_id="pos-hist-fail",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="submitted",
            source="native",
            exchange_order_id="ex-hist-fail",
            created_at=_iso(0.3),
        )
    )
    repo.insert_position(
        _position(
            id="pos-hist-fail",
            pending_close_order_id="close-hist-fail",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(0.3),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(pcr, "_uta_history_lookup", lambda order: pcr._HISTORY_FAILED)

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0
    assert summary["parked"] == 0
    assert repo.get_order("close-hist-fail")["status"] == "submitted"


def test_rate_limited_park_is_not_released_after_four_hour_floor():
    _seed_order(
        Order(
            id="close-40725",
            client_order_id="close-pos-40725-r10",
            position_id="pos-40725",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            error_reason="trade_too_many_times_cancel",
            created_at=_iso(6),
        )
    )
    repo.insert_position(
        _position(
            id="pos-40725",
            pending_close_order_id="close-40725",
            pending_close_status="venue_trade_rate_limited",
            pending_close_requested_at=_iso(6),
        )
    )

    summary = pcr.release_stale_submission_rejected_claims()
    assert summary["released"] == 0
    stored = repo._select_one("positions", "pos-40725")
    assert stored["pending_close_order_id"] == "close-40725"
    assert stored["pending_close_status"] == "venue_trade_rate_limited"


def test_legacy_submission_rejected_40725_is_still_not_released():
    """Pre-normalization rows must remain parked even on submission_rejected."""
    _seed_order(
        Order(
            id="close-40725-legacy",
            client_order_id="close-pos-40725-legacy",
            position_id="pos-40725-legacy",
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status="cancelled",
            source="native",
            error_reason="trade_too_many_times_cancel",
            created_at=_iso(6),
        )
    )
    repo.insert_position(
        _position(
            id="pos-40725-legacy",
            pending_close_order_id="close-40725-legacy",
            pending_close_status="submission_rejected",
            pending_close_requested_at=_iso(6),
        )
    )

    summary = pcr.release_stale_submission_rejected_claims()
    assert summary["released"] == 0
    stored = repo._select_one("positions", "pos-40725-legacy")
    assert stored["pending_close_order_id"] == "close-40725-legacy"
    assert stored["pending_close_status"] == "submission_rejected"

"""Regression tests for stale pending-close recovery (XRP/UNI class failures)."""

from __future__ import annotations

from tests.helpers.position_fixture import force_update_position

import logging
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.execution import pending_close_recovery as pcr
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, OrderStatus, Position


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


def _terminal_unfilled_history(
    *,
    status: str = "canceled",
    cancel_reason: str = "",
    original_quantity: float = 9.0,
) -> dict:
    """The venue's positive report of one exact order: terminal and fully unfilled."""
    return {
        "orderStatus": status,
        "status": status,
        "cancelReason": cancel_reason,
        "original_quantity": original_quantity,
        "filled_quantity": 0.0,
        "remaining_quantity": original_quantity,
    }


def test_xrp_stale_submitted_close_stays_unreleased_without_positive_evidence(
    monkeypatch,
):
    """Mimics the 2026-08-11 XRP take_profit close stuck in submitted.

    A complete read that does not find the order proves only that the read window
    completed — never that the venue did not receive the order
    (execution_contract §10.2.4). With no positive venue report this claim must
    stay unresolved; the 24h terminal bound and the operator resolver own it.
    """
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
    assert summary["released"] == 0
    assert _order_row("71ae77205561")["status"] == "submitted"

    stored_position = _position_row("0da931113c25")
    assert stored_position["pending_close_order_id"] == "71ae77205561"
    assert stored_position["pending_close_status"] == "submitted"


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
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0
    assert repo.get_order("close-fresh")["status"] == "submitted"


def test_absent_unfilled_close_released_after_ack_lag(monkeypatch):
    """XRP r9 class: venue reports the close terminal and unfilled — do not wait 4h."""
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
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

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
    assert _order_row(order_id)["status"] == "submitted"
    stored_position = _position_row(position_id)
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
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

    summary = pcr.recover_absent_stale_close_orders()

    assert summary["released"] == 1
    assert repo.get_order(order_id)["status"] == "cancelled"
    assert repo.get_position(position_id)["pending_close_order_id"] is None


def test_terminal_close_without_reported_quantities_is_not_released(monkeypatch):
    """A terminal state alone is not proof of zero fill.

    The typed adapter maps an absent payload field to ``0``, so a venue that
    never reported a filled quantity reads exactly like a reported zero. The
    release requires the venue to also report the full original quantity still
    remaining; anything less stays unresolved.
    """
    order_id = "close-terminal-no-quantity"
    position_id = "pos-terminal-no-quantity"
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
            exchange_order_id="ex-terminal-no-quantity",
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

    assert summary["released"] == 0
    assert _order_row(order_id)["status"] == "submitted"
    stored_position = _position_row(position_id)
    assert stored_position["pending_close_order_id"] == order_id
    assert stored_position["pending_close_status"] == "submitted"


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
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
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
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

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
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

    summary = pcr.recover_absent_stale_close_orders()
    assert summary["released"] == 0


@pytest.mark.parametrize("read", ["open_orders", "fills"])
def test_incomplete_venue_read_logs_the_typed_reason(read, caplog, monkeypatch):
    """INV-EX-1: a partial/unsupported venue read must be attributable, not silent.

    The newly production-reachable recovery reads three UTA surfaces; a
    structural PARTIAL on any of them leaves the claim unresolved, so the venue's
    own reason code has to reach ``errors.log`` instead of only changing a return
    value. (Exercised through the two readers the suite's autouse fixture does not
    replace; the exact-order read shares the same warning helper.)
    """
    from types import SimpleNamespace

    import aletheia.uta.manager as uta_manager
    from aletheia.uta.models import ReadCompleteness
    from tests.helpers.fill_reports import DEFAULT_IDENTITY

    order = {
        "id": "close-unavailable",
        "client_order_id": "close-pos-unavailable",
        "execution_mode": "bitget_demo",
        "symbol": "XRP/USDT",
        "market_type": "usdt_futures",
        "created_at": _iso(1),
        "uta_account_identity_json": DEFAULT_IDENTITY.model_dump_json(),
    }
    report = SimpleNamespace(
        completeness=ReadCompleteness.PARTIAL,
        reason_code="private_order_history_start_required",
    )

    async def _incomplete(*_args, **_kwargs):
        return report

    monkeypatch.setattr(uta_manager, "query_uta", _incomplete)
    reader = (
        pcr._exchange_open_order_ids if read == "open_orders"
        else pcr._exchange_fill_rows
    )

    with caplog.at_level(logging.WARNING, logger="aletheia.pending_close_recovery"):
        assert reader(order) is None

    assert "private_order_history_start_required" in caplog.text
    assert "partial" in caplog.text


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


def test_owner_authorized_release_preserves_cancelled_order_timestamp():
    """The reason-only cleanup CAS must not rewrite unrelated Order lifecycle fields."""
    filled_at = _iso(0.25)
    _seed_order(
        Order(
            id="preserve-filled-at",
            client_order_id="close-preserve-filled-at",
            position_id="position-preserve-filled-at",
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
            filled_at=filled_at,
            created_at=_iso(0.5),
        )
    )
    repo.insert_position(
        _position(
            id="position-preserve-filled-at",
            pending_close_order_id="preserve-filled-at",
            pending_close_status="venue_trade_rate_limited",
            pending_close_requested_at=_iso(0.5),
        )
    )

    result = pcr.release_rate_limited_close_claim(
        "position-preserve-filled-at",
        actor="human",
        reason="owner-authorized cleanup",
    )

    assert result["released"] is True
    stored = repo.get_order("preserve-filled-at")
    assert stored["filled_at"] == filled_at


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


# ── #3719: the 40725 park is a close claim, not an unclaimed position ───────


def test_parked_claim_holds_the_close_fence_without_immediate_reconciliation():
    from aletheia.execution.close_convergence import (
        CLOSE_CLAIM_STATES,
        IMMEDIATE_RECONCILIATION_STATES,
        has_unresolved_close_claim,
    )

    assert pcr.VENUE_TRADE_RATE_LIMITED_STATUS in CLOSE_CLAIM_STATES
    assert pcr.VENUE_TRADE_RATE_LIMITED_STATUS not in IMMEDIATE_RECONCILIATION_STATES
    assert has_unresolved_close_claim({
        "pending_close_order_id": "close-pos-parked-40725",
        "pending_close_status": pcr.VENUE_TRADE_RATE_LIMITED_STATUS,
    }) is True


@pytest.mark.parametrize(
    ("age_hours", "escalated"),
    [
        (0.5, False),   # fresh park: still inside the ordinary flight window
        (22.5, True),   # stale park: escalated, but the owner action is unchanged
    ],
)
def test_parked_claim_view_names_the_owner_unlock_at_any_age(age_hours, escalated):
    from aletheia.execution.close_convergence import close_convergence_view

    view = close_convergence_view({
        "status": "open",
        "pending_close_order_id": "close-pos-parked-40725",
        "pending_close_status": pcr.VENUE_TRADE_RATE_LIMITED_STATUS,
        "pending_close_requested_at": _iso(age_hours),
    })

    assert view["pending_close"] is True
    assert view["close_convergence_state"] == pcr.VENUE_TRADE_RATE_LIMITED_STATUS
    assert view["close_convergence_owner_status"] == (
        "venue rate limited; owner unlock required"
    )
    assert view["close_convergence_requires_reconciliation"] is escalated
    assert view["close_convergence_escalated"] is escalated


def test_repeated_close_on_parked_claim_returns_already_pending():
    """#3719: the second close must return the parked claim, never claim an Order."""
    _seed_parked_40725()

    from aletheia.execution.coordinator import close_position

    result = close_position("pos-parked", reason="stop_loss")

    assert result["already_pending"] is True
    assert result["closed"] is False
    assert result["order_id"] == "close-pos-parked"
    assert result["status"] == pcr.VENUE_TRADE_RATE_LIMITED_STATUS

    orders = _close_orders_for("pos-parked")
    assert [order["id"] for order in orders] == ["close-pos-parked"]
    assert orders[0]["status"] == "cancelled"
    assert orders[0]["error_reason"] == pcr.TRADE_RATE_LIMIT_CANCEL_REASON


def test_reconcile_is_the_production_entry_to_absent_close_recovery(monkeypatch):
    """#3719: the recovery is runtime-reachable through the reconcile job."""
    _seed_order(
        Order(
            id="close-ack-lost",
            client_order_id="close-pos-ack-lost",
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
            created_at=_iso(1),
        )
    )
    repo.insert_position(
        _position(
            id="pos-ack-lost",
            pending_close_order_id="close-ack-lost",
            pending_close_status="submitted",
            pending_close_requested_at=_iso(1),
        )
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

    from aletheia.execution.reconciliation import reconcile

    summary = reconcile()

    assert summary["pending_close_released"] == 1
    assert summary["pending_close_released_ids"] == ["pos-ack-lost"]
    stored = repo.get_position("pos-ack-lost")
    assert stored is not None
    assert stored["pending_close_order_id"] is None
    assert stored["pending_close_status"] is None


def test_reconcile_parks_a_40725_close_claim_without_releasing_it(monkeypatch):
    """The production path must park 40725 and never free the claim for a retry.

    The candidate Order is in a *recoverable* local state, so it really is
    selected by the recovery's own query — the outcome depends on the park
    guard, not on the fixture being invisible to the decision.
    """
    position_id = _seed_unacked_close("close-40725-live", status="submitted")
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(
        pcr,
        "_uta_history_lookup",
        lambda order: {
            "orderStatus": "cancelled",
            "cancelReason": "trade_too_many_times_cancel",
        },
    )

    from aletheia.execution.reconciliation import reconcile

    summary = reconcile()

    assert summary["pending_close_released"] == 0
    assert summary["pending_close_parked"] == 1
    stored_order = _order_row("close-40725-live")
    assert stored_order["status"] == "cancelled"
    assert stored_order["error_reason"] == pcr.TRADE_RATE_LIMIT_CANCEL_REASON
    stored = _position_row(position_id)
    assert stored["pending_close_order_id"] == "close-40725-live"
    assert stored["pending_close_status"] == pcr.VENUE_TRADE_RATE_LIMITED_STATUS

    # A parked claim is never a release candidate: a second pass must leave the
    # owner-unlock-only park exactly where it is.
    second = reconcile()
    assert second["pending_close_released"] == 0
    resumed = _position_row(position_id)
    assert resumed["pending_close_order_id"] == "close-40725-live"
    assert resumed["pending_close_status"] == pcr.VENUE_TRADE_RATE_LIMITED_STATUS


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


# ---------------------------------------------------------------------------
# Issue #3719 validation checkpoint (2026-09-29, #3728).
#
# V2 pinned the pre-fix contract (the 40725 park invisible to
# `has_unresolved_close_claim`) and was written to be inverted by the vocabulary
# fix. It is gone: the parked-claim fence is pinned by
# `test_parked_claim_holds_the_close_fence_without_immediate_reconciliation`,
# `test_parked_claim_view_names_the_owner_unlock_at_any_age`,
# `test_repeated_close_on_parked_claim_returns_already_pending` and
# `test_parked_close_claim_is_not_resubmitted_or_rewritten` (position_manager).
# V3/V4 pin the recovery semantics that survive that fix and the wiring.
# ---------------------------------------------------------------------------


def _bound_entry_order(position: Position) -> Order:
    """Entry Order carrying the UTA identity `coordinator._position_account` reads."""
    from aletheia.uta.models import UTAAccountIdentity

    identity = UTAAccountIdentity(
        account_id=position.execution_mode,
        execution_mode=position.execution_mode,
        venue="bitget",
        environment="demo",
        profile=position.execution_mode,
        credential_generation=1,
        adapter_implementation="CCXTUTAAdapter",
        adapter_version="1",
    )
    order = Order(
        proposal_id=position.proposal_id,
        symbol=position.symbol,
        market_type=position.market_type,
        direction=position.direction,
        order_type="market",
        quantity=position.quantity,
        price=position.entry_price,
        execution_mode=position.execution_mode,
        status="filled",
        source="adopted",
        uta_account_identity_json=identity.model_dump_json(),
    )
    _seed_order(order)
    return order


def _insert_open_position_with_entry(position_id: str, **claim_fields) -> Position:
    """Insert an open native Position bound to its entry Order (authority source)."""
    position = _position(id=position_id, status="open", **claim_fields)
    entry = _bound_entry_order(position)
    position.order_id = entry.id
    repo.insert_position(position)
    return position


def _seed_parked_40725(position_id: str = "pos-parked", *, hours_ago: float = 6.0) -> None:
    """Seed the exact parked state `_apply_park_rate_limited_close` commits."""
    close_order_id = f"close-{position_id}"
    _seed_order(
        Order(
            id=close_order_id,
            client_order_id=close_order_id,
            position_id=position_id,
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
            error_reason=pcr.TRADE_RATE_LIMIT_CANCEL_REASON,
            created_at=_iso(hours_ago),
        )
    )
    _insert_open_position_with_entry(
        position_id,
        pending_close_order_id=close_order_id,
        pending_close_status=pcr.VENUE_TRADE_RATE_LIMITED_STATUS,
        pending_close_requested_at=_iso(hours_ago),
    )


def _seed_unacked_close(
    order_id: str,
    *,
    client_order_id: str | None = None,
    status: OrderStatus = "pending",
    hours_ago: float = 0.3,
) -> str:
    """Seed a close Order committed locally whose venue acknowledgement is missing."""
    position_id = order_id.removeprefix("close-")
    _seed_order(
        Order(
            id=order_id,
            client_order_id=client_order_id or f"close-{position_id}",
            position_id=position_id,
            close_reason="take_profit",
            symbol="XRP/USDT",
            market_type="usdt_futures",
            direction="short",
            order_type="market",
            quantity=9.0,
            price=1.0,
            execution_mode="bitget_demo",
            status=status,
            source="native",
            created_at=_iso(hours_ago),
        )
    )
    repo.insert_position(
        _position(
            id=position_id,
            pending_close_order_id=order_id,
            pending_close_status=status,
            pending_close_requested_at=_iso(hours_ago),
        )
    )
    return position_id


def _order_row(order_id: str) -> dict:
    """Read one Order row, failing loudly when it is missing."""
    order = repo.get_order(order_id)
    assert order is not None, order_id
    return order


def _position_row(position_id: str) -> dict:
    """Read one Position row, failing loudly when it is missing."""
    position = repo.get_position(position_id)
    assert position is not None, position_id
    return position


def _close_orders_for(position_id: str) -> list[dict]:
    return [
        dict(row)
        for row in repo.get_db()
        .execute("SELECT * FROM orders WHERE position_id=?", (position_id,))
        .fetchall()
    ]


@pytest.mark.parametrize("venue_evidence", ["open_order", "exact_fill"])
def test_v3a_accepted_response_loss_is_never_released(monkeypatch, venue_evidence):
    """V3 Case A: the venue accepted the close; only the local response was lost."""
    position_id = _seed_unacked_close("close-pos-ack-lost")
    if venue_evidence == "open_order":
        # The venue still shows the exact order, matched by the stable clientOid.
        monkeypatch.setattr(
            pcr, "_exchange_open_order_ids", lambda order: {"close-pos-ack-lost"}
        )
        monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    else:
        # The order already filled and left the open book; only the fill proves it.
        monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
        monkeypatch.setattr(
            pcr,
            "_exchange_fill_rows",
            lambda order: [{"fillId": "f-1", "quantity": 9.0}],
        )

    summary = pcr.recover_absent_stale_close_orders()

    assert summary["released"] == 0
    assert summary["parked"] == 0
    stored_order = _order_row("close-pos-ack-lost")
    assert stored_order["status"] == "pending"
    assert stored_order["error_reason"] is None
    stored_position = _position_row(position_id)
    assert stored_position["pending_close_order_id"] == "close-pos-ack-lost"
    assert stored_position["pending_close_status"] == "pending"
    assert len(_close_orders_for(position_id)) == 1


def test_v3b_proven_terminal_unfilled_release_happens_once_and_advances_retry_identity(
    monkeypatch,
):
    """V3 Case B: the venue reports the close terminal and unfilled; release once."""
    position_id = _seed_unacked_close("close-pos-absent")
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

    first = pcr.recover_absent_stale_close_orders()
    assert first["released"] == 1
    assert first["released_position_ids"] == [position_id]

    stored_order = _order_row("close-pos-absent")
    assert stored_order["status"] == "cancelled"
    assert stored_order["error_reason"] == "stale_close_order_absent_unfilled"
    stored_position = _position_row(position_id)
    assert stored_position["pending_close_order_id"] is None
    assert stored_position["pending_close_status"] is None

    second = pcr.recover_absent_stale_close_orders()
    assert second["released"] == 0
    assert pcr.next_close_client_oid(position_id) == f"close-{position_id}-r1"


def test_v3b_silent_venue_never_releases_and_stays_fail_closed(monkeypatch):
    """V3 Case B': a complete read that finds no order authorizes nothing."""
    position_id = _seed_unacked_close("close-pos-silent")
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])

    summary = pcr.recover_absent_stale_close_orders()

    assert summary["released"] == 0
    assert summary["parked"] == 0
    assert _order_row("close-pos-silent")["status"] == "pending"
    stored_position = _position_row(position_id)
    assert stored_position["pending_close_order_id"] == "close-pos-silent"
    assert stored_position["pending_close_status"] == "pending"


def test_v3b_manual_reconciliation_pending_is_never_a_recovery_candidate(
    monkeypatch,
):
    """A human-owned terminal claim is excluded from every automatic release.

    The fixture is the worst case on purpose: the order is in a recoverable local
    state and the venue positively reports it terminal and unfilled, so only the
    claim-state exclusion keeps it human-owned. Both layers are asserted — the
    candidate query (so a human-owned claim costs no private reads) and the
    decision itself (so any other caller fails closed too).
    """
    position_id = _seed_unacked_close(
        "close-pos-manual", status="submission_unknown"
    )
    force_update_position(
        position_id,
        pending_close_status=pcr.MANUAL_RECONCILIATION_PENDING_STATUS,
    )
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: set())
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: [])
    monkeypatch.setattr(
        pcr, "_uta_history_lookup", lambda order: _terminal_unfilled_history()
    )

    order_row, position_row = (
        _order_row("close-pos-manual"),
        _position_row(position_id),
    )
    assert pcr._eligible_absent_close_recovery(order_row, position_row) is None

    summary = pcr.recover_absent_stale_close_orders()

    assert summary["released"] == 0
    assert summary["parked"] == 0
    assert _order_row("close-pos-manual")["status"] == "submission_unknown"
    stored_position = _position_row(position_id)
    assert stored_position["pending_close_order_id"] == "close-pos-manual"
    assert (
        stored_position["pending_close_status"]
        == pcr.MANUAL_RECONCILIATION_PENDING_STATUS
    )


@pytest.mark.parametrize(
    ("open_ids", "fill_rows"),
    [(None, None), (set(), None), (None, [])],
)
def test_v3c_partial_or_unavailable_evidence_keeps_claim(
    monkeypatch, open_ids, fill_rows
):
    """V3 Case C: unavailable/partial remote evidence stays fail-closed."""
    position_id = _seed_unacked_close("close-pos-partial")
    monkeypatch.setattr(pcr, "_exchange_open_order_ids", lambda order: open_ids)
    monkeypatch.setattr(pcr, "_exchange_fill_rows", lambda order: fill_rows)

    summary = pcr.recover_absent_stale_close_orders()

    assert summary["released"] == 0
    assert summary["parked"] == 0
    stored_order = _order_row("close-pos-partial")
    assert stored_order["status"] == "pending"
    assert stored_order["error_reason"] is None
    stored_position = _position_row(position_id)
    assert stored_position["pending_close_order_id"] == "close-pos-partial"
    assert stored_position["pending_close_status"] == "pending"


def test_v4_claim_committed_before_send_stays_pending_without_a_releaser():
    """V4 (#3719): reproduce the reported `pending` class from its durable state.

    `coordinator._claim_close` commits the close Order and the Position claim as
    `pending` in one writer transaction; the venue acknowledgement is written
    afterwards, in a separate transaction, with no guard for the case where that
    write never happens. Nothing in the scheduled lifecycle releases the claim
    that is left behind.
    """
    position_id = "pos-v4"
    _insert_open_position_with_entry(position_id)

    from aletheia.execution.coordinator import _claim_close

    _, close_order = _claim_close(position_id, "take_profit", None)
    assert close_order is not None
    assert close_order.status == "pending"

    stored_order = _order_row(close_order.id)
    assert stored_order["status"] == "pending"
    assert stored_order["exchange_order_id"] is None
    assert stored_order["execution_attempt_id"] is None
    assert stored_order["execution_receipt_hash"] is None

    stored_position = _position_row(position_id)
    assert stored_position["pending_close_order_id"] == close_order.id
    assert stored_position["pending_close_status"] == "pending"

    # position_manager's guard sees the claim: it waits and escalates, never releases.
    from aletheia.execution.close_convergence import has_unresolved_close_claim

    assert has_unresolved_close_claim(stored_position) is True

    # reconciliation reads the Order, but its only claim-clearing helper covers
    # already-closed Positions only; the absent-close release needs positive
    # venue evidence and there is none here.
    from aletheia.execution.reconciliation import (
        _clear_verified_closed_pending,
        _list_unresolved_core_orders,
    )

    assert any(
        order["id"] == close_order.id for order in _list_unresolved_core_orders()
    )
    assert _clear_verified_closed_pending() == []
    assert _position_row(position_id)["pending_close_order_id"] == close_order.id

    # Every later protective trigger short-circuits on the same claim, so the
    # Position never reaches the venue again until something releases it —
    # the reconcile job releases it once the venue reports the order terminal
    # and unfilled (pinned by the reconcile tests above), or the 24h bound hands
    # it to the operator resolver.
    from aletheia.execution.coordinator import close_position

    retry = close_position(position_id, "stop_loss")
    assert retry["already_pending"] is True
    assert retry["order_id"] == close_order.id
    assert retry["status"] == "pending"
    assert [order["id"] for order in _close_orders_for(position_id)] == [close_order.id]

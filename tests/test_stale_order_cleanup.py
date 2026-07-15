"""Regression tests for stale orphan-order cleanup.

Backs the fix for the ``job_consecutive_failure:reconciliation`` alert: 6 okx_demo
orphan orders (no credentials configured) made reconciliation report the same
errors every cycle forever. cleanup_stale_orphan_orders() ages them out — while
never touching an Order that carries real economics.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aletheia.execution import stale_order_cleanup as soc
from aletheia.storage import repositories as repo
from aletheia.storage.models import Fill, Order, Position


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _seed_order(order: Order) -> None:
    """Insert an Order row directly, bypassing native-entry/close business rules.

    These tests deliberately seed legacy/stale rows (e.g. a close Order whose
    Position is already closed) that current validation would refuse to create
    fresh — that is exactly the state this GC exists to clean up. Cleanup reads
    only the persisted row, so the low-level insert is the honest fixture path.
    """
    repo._insert("orders", repo._stamp_provenance(order.model_dump()))


def _order(*, oid: str, hours_ago: float, execution_mode: str = "okx_demo",
           status: str = "submitted", close_reason: str | None = None,
           position_id: str | None = None,
           exchange_order_id: str | None = "ex-1",
           client_order_id: str | None = "cli-1") -> Order:
    return Order(
        id=oid,
        symbol="MON/USDT",
        market_type="usdt_futures",
        direction="long",
        order_type="limit",
        quantity=4.0,
        price=1.0,
        execution_mode=execution_mode,
        status=status,
        source="native",
        created_at=_iso(hours_ago),
        close_reason=close_reason,
        position_id=position_id,
        exchange_order_id=exchange_order_id,
        client_order_id=client_order_id,
    )


def _position(*, pid: str, status: str, execution_mode: str = "okx_demo",
              order_id: str | None = None,
              pending_close_order_id: str | None = None,
              pending_close_status: str | None = None) -> Position:
    return Position(
        id=pid,
        symbol="MON/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=4.0,
        entry_price=1.0,
        execution_mode=execution_mode,
        status=status,
        source="native",
        opened_at=_iso(300),
        closed_at=_iso(200) if status == "closed" else None,
        order_id=order_id,
        pending_close_order_id=pending_close_order_id,
        pending_close_status=pending_close_status,
    )


def _status(oid: str) -> str:
    return repo.get_order(oid)["status"]


def _audit_count(oid: str) -> int:
    row = repo.get_db().execute(
        "SELECT COUNT(*) c FROM audit_events "
        "WHERE entity_id=? AND source='stale_order_cleanup'",
        (oid,),
    ).fetchone()
    return int(row["c"])


def test_pure_orphan_aged_out_cancelled(monkeypatch):
    """okx_demo orphan past the hard window, unverifiable → cancelled + audited."""
    # No exchange reader for okx → verification returns None → aged-out path.
    _seed_order(_order(oid="orphan-old", hours_ago=200))
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 1
    assert _status("orphan-old") == "cancelled"
    assert repo.get_order("orphan-old")["error_reason"] == "stale_order_unverifiable_aged_out"
    assert _audit_count("orphan-old") == 1


def test_order_with_fill_is_never_cancelled():
    _seed_order(_order(oid="has-fill", hours_ago=300))
    repo.insert_fill(Fill(order_id="has-fill", fill_price=1.0, fill_quantity=4.0))
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 0
    assert _status("has-fill") == "submitted"


def test_order_linked_to_open_position_is_never_cancelled():
    _seed_order(_order(oid="entry-open", hours_ago=300))
    repo.insert_position(_position(pid="pos-open", status="open", order_id="entry-open"))
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 0
    assert _status("entry-open") == "submitted"


def test_close_order_for_open_position_is_never_cancelled():
    _seed_order(_order(oid="close-live", hours_ago=300, close_reason="stop_loss"))
    repo.insert_position(_position(
        pid="pos-live", status="open", pending_close_order_id="close-live",
        pending_close_status="pending",
    ))
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 0
    assert _status("close-live") == "submitted"


def test_stale_close_order_on_closed_position_clears_pending(monkeypatch):
    """The NEAR 320d83b19803 case: closed position still names a pending close."""
    _seed_order(_order(oid="close-stale", hours_ago=200, close_reason="take_profit"))
    repo.insert_position(_position(
        pid="pos-closed", status="closed", pending_close_order_id="close-stale",
        pending_close_status="pending",
    ))
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 1
    assert summary["cleared_pending"] == 1
    assert _status("close-stale") == "cancelled"
    assert repo.get_order("close-stale")["error_reason"] == "stale_close_order_position_already_closed"
    pos = repo.get_db().execute(
        "SELECT pending_close_order_id, pending_close_status FROM positions WHERE id=?",
        ("pos-closed",),
    ).fetchone()
    assert pos["pending_close_order_id"] is None
    assert pos["pending_close_status"] is None


def test_recent_order_below_soft_window_is_untouched():
    _seed_order(_order(oid="fresh", hours_ago=1.0))
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 0
    assert _status("fresh") == "submitted"


def test_exchange_still_open_is_never_cancelled(monkeypatch):
    """Evidence path: venue still lists the order as open → skip, even when old."""
    _seed_order(_order(
        oid="still-open", hours_ago=300, execution_mode="bitget_demo",
        exchange_order_id="ex-99",
    ))
    monkeypatch.setattr(soc, "_exchange_open_order_ids", lambda order: {"ex-99"})
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 0
    assert _status("still-open") == "submitted"


def test_exchange_confirms_absent_cancels_before_hard_window(monkeypatch):
    """Evidence path: venue reachable + order absent → cancel at the soft window."""
    _seed_order(_order(
        oid="gone", hours_ago=72, execution_mode="bitget_demo",
        exchange_order_id="ex-7",
    ))
    monkeypatch.setattr(soc, "_exchange_open_order_ids", lambda order: {"ex-other"})
    summary = soc.cleanup_stale_orphan_orders()
    assert summary["cancelled"] == 1
    assert repo.get_order("gone")["error_reason"] == "stale_order_absent_on_exchange"

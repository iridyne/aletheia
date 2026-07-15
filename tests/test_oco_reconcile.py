"""Tests for OCO resolution + CR-EX-01 reconciliation decision logic (M3.5 refinement).

Pure decision logic — the exchange adapters (place/poll/cancel, get_open_orders) are the
needs-demo stubs and are NOT exercised here.
"""

from aletheia.execution.oco import OcoLeg, resolve_oco
from aletheia.execution.reconciliation import resolve_orders_missing_from_exchange


# ── OCO resolution ───────────────────────────────────────────────────────────

def _leg(kind, status, filled=0.0, total=0.0):
    return OcoLeg(kind=kind, status=status, filled_qty=filled, total_qty=total)


def test_limit_filled_cancels_stop():
    r = resolve_oco(_leg("limit_exit", "filled"), _leg("protective_stop", "open"))
    assert r.position_closed is True
    assert r.cancel == ["protective_stop"]
    assert r.reason == "limit_filled"


def test_stop_filled_cancels_limit():
    r = resolve_oco(_leg("limit_exit", "open"), _leg("protective_stop", "filled"))
    assert r.position_closed is True
    assert r.cancel == ["limit_exit"]
    assert r.reason == "stop_filled"


def test_both_filled_race_no_cancel():
    r = resolve_oco(_leg("limit_exit", "filled"), _leg("protective_stop", "filled"))
    assert r.position_closed is True
    assert r.cancel == []
    assert r.reason == "both_filled_race"


def test_limit_partial_shrinks_stop():
    # limit ate 0.4 of 1.0 → stop must cover the remaining 0.6, position stays open
    r = resolve_oco(_leg("limit_exit", "partial", filled=0.4, total=1.0), _leg("protective_stop", "open"))
    assert r.position_closed is False
    assert r.cancel == []
    assert r.reduce_stop_to_qty == 0.6
    assert r.reason == "limit_partial"


def test_both_open_no_action():
    r = resolve_oco(_leg("limit_exit", "open"), _leg("protective_stop", "open"))
    assert r.position_closed is False
    assert r.cancel == []
    assert r.reduce_stop_to_qty is None
    assert r.reason == "both_open"


def test_leg_remaining_qty():
    assert _leg("limit_exit", "partial", filled=0.3, total=1.0).remaining_qty == 0.7
    assert _leg("limit_exit", "filled", filled=1.0, total=1.0).remaining_qty == 0.0


# ── CR-EX-01 reconciliation decision ─────────────────────────────────────────

def _order(oid, mode, status, ex_id="ex-1"):
    return {"id": oid, "execution_mode": mode, "status": status, "exchange_order_id": ex_id}


def test_missing_order_resolved_as_filled():
    orders = [_order("o1", "okx_live_limited", "submitted", ex_id="ex-1")]
    # ex-1 is NOT in the exchange's still-open set → should converge to filled
    assert resolve_orders_missing_from_exchange(orders, exchange_open_ids=set()) == ["ex-1"]


def test_still_open_order_not_resolved():
    orders = [_order("o1", "okx_live_limited", "submitted", ex_id="ex-1")]
    assert resolve_orders_missing_from_exchange(orders, exchange_open_ids={"ex-1"}) == []


def test_only_target_modes_and_open_states():
    orders = [
        _order("o1", "bitget_live_limited", "submitted", ex_id="a"),  # not a target mode
        _order("o2", "okx_live_limited", "filled", ex_id="b"),        # not an open state
        _order("o3", "okx_live_limited", "submitted", ex_id=None),    # no exchange id
        _order("o4", "binance_live_limited", "partial", ex_id="d"),   # target + open + id → resolved
    ]
    assert resolve_orders_missing_from_exchange(orders, exchange_open_ids=set()) == ["d"]

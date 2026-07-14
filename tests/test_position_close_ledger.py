"""Tests for the per-close ledger (exit-intent R1, Stage 1).

Every close via close_position writes exactly one position_close_events row whose
realized_pnl equals the position's realized_pnl — the equivalence the daily-loss
breaker rewire (Stage 2) will rely on. Additive: no existing behavior changes.
"""

from aletheia.portfolio.pnl import close_position
from aletheia.storage.repositories import _select_one, list_position_close_events


def _insert_open(db, pid, *, entry=100.0, qty=1.0, direction="long", mode="bitget_demo"):
    # bitget_demo avoids the paper_account settle path (which needs a prior open event).
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,entry_fee) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, "BTC/USDT", "usdt_futures", direction, qty, entry, mode, "open",
         round(entry * qty * 0.0006, 8)),
    )


def test_close_writes_one_ledger_event_matching_realized():
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _insert_open(db, "p1", entry=100.0, qty=1.0)
    close_position("p1", exit_price=110.0, reason="take_profit")

    pos = _select_one("positions", "p1")
    events = list_position_close_events(position_id="p1")
    assert len(events) == 1
    ev = events[0]
    assert ev["close_reason"] == "take_profit"
    assert ev["closed_qty"] == 1.0
    assert ev["is_partial"] == 0
    assert ev["exit_price"] == 110.0
    # equivalence — the ledger event carries the SAME realized_pnl the breaker sums today
    assert ev["realized_pnl"] == pos["realized_pnl"]


def test_ledger_sum_equals_positions_sum_for_full_closes():
    """Aggregate equivalence (Stage 2 foundation): summing realized_pnl from the close
    ledger equals summing it from closed positions, when every close is a full close."""
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import list_closed_positions

    db = get_db()
    _insert_open(db, "a", entry=100.0, qty=1.0)
    _insert_open(db, "b", entry=200.0, qty=0.5)
    close_position("a", exit_price=90.0, reason="stop_loss")     # a loss
    close_position("b", exit_price=220.0, reason="take_profit")  # a win

    ledger_sum = round(sum(e["realized_pnl"] for e in list_position_close_events()), 6)
    positions_sum = round(
        sum(p["realized_pnl"] for p in list_closed_positions() if p["realized_pnl"] is not None), 6
    )
    assert ledger_sum == positions_sum


# ── Stage 2: daily-loss breaker counts partial reductions (additive term) ────

def test_partial_close_realized_term():
    from datetime import datetime, timezone

    from aletheia.portfolio.account_loss import _partial_close_realized_today
    from aletheia.storage.models import new_id
    from aletheia.storage.repositories import insert_position_close_event

    today = f"{datetime.now(timezone.utc).date().isoformat()}T00:00:00+00:00"
    now = datetime.now(timezone.utc).isoformat()

    # A PARTIAL reduction today (is_partial=1) — must be counted by the breaker term.
    insert_position_close_event({
        "id": new_id(), "position_id": "z1", "execution_mode": "bitget_demo",
        "close_reason": "operator_reduce", "realized_pnl": -5.0, "is_partial": 1,
        "closed_qty": 0.5, "exit_price": 90.0, "closed_at": now,
    })
    # A FULL close (is_partial=0) — NOT counted here (it's summed via closed positions).
    insert_position_close_event({
        "id": new_id(), "position_id": "z2", "execution_mode": "bitget_demo",
        "close_reason": "take_profit", "realized_pnl": 10.0, "is_partial": 0,
        "closed_qty": 1.0, "exit_price": 110.0, "closed_at": now,
    })

    assert _partial_close_realized_today("bitget_demo", today) == -5.0
    assert _partial_close_realized_today("okx_demo", today) == 0.0     # other mode
    # no partial events at all → 0 (equivalence for the current full-close-only world)


def test_partial_term_zero_without_partials():
    from datetime import datetime, timezone

    from aletheia.portfolio.account_loss import _partial_close_realized_today

    today = f"{datetime.now(timezone.utc).date().isoformat()}T00:00:00+00:00"
    assert _partial_close_realized_today("bitget_demo", today) == 0.0


# ── Stage 3: reduce_position (reduce-in-place) ───────────────────────────────

def _insert_open_qty(db, pid, *, entry=100.0, qty=2.0, entry_fee=0.12, mode="bitget_demo"):
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,entry_fee) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, "BTC/USDT", "usdt_futures", "long", qty, entry, mode, "open", entry_fee),
    )


def test_reduce_position_partial():
    from aletheia.storage.sqlite import get_db
    from aletheia.portfolio.pnl import reduce_position

    db = get_db()
    _insert_open_qty(db, "p1", entry=100.0, qty=2.0, entry_fee=0.12)  # bitget_demo, fee 0.0006

    res = reduce_position("p1", 0.5, 110.0, reason="operator_reduce")
    # gross=(110-100)*0.5=5; entry_fee_portion=0.12*0.5/2=0.03; exit_fee=110*0.5*.0006=0.033
    assert res["reduced"] is True
    assert res["remaining_qty"] == 1.5
    assert res["realized_pnl"] == 4.937

    pos = _select_one("positions", "p1")
    assert pos["status"] == "open"            # position stays open
    assert pos["quantity"] == 1.5
    assert pos["entry_price"] == 100.0        # average entry frozen
    assert pos["entry_fee"] == 0.09           # 0.12 - 0.03 prorated

    events = list_position_close_events(position_id="p1")
    assert len(events) == 1 and events[0]["is_partial"] == 1
    assert events[0]["closed_qty"] == 0.5 and events[0]["realized_pnl"] == 4.937


def test_reduce_guards():
    from aletheia.storage.sqlite import get_db
    from aletheia.portfolio.pnl import reduce_position

    db = get_db()
    _insert_open_qty(db, "g1", qty=1.0)
    assert reduce_position("g1", 1.0, 110.0).get("error") == "reduce_qty_not_less_than_position"
    assert reduce_position("g1", 0.0, 110.0).get("error") == "reduce_qty_must_be_positive"
    assert reduce_position("missing", 0.1, 110.0).get("error") == "position_not_found"


def test_reduce_rejected_on_paper_internal():
    """Product boundary: paper_internal is limited to full closes — partial reduce is
    honestly rejected and routed to a demo exchange mode."""
    from aletheia.storage.sqlite import get_db
    from aletheia.portfolio.pnl import reduce_position
    from aletheia.storage.repositories import list_position_close_events

    db = get_db()
    _insert_open_qty(db, "pi1", qty=2.0, mode="paper_internal")
    res = reduce_position("pi1", 0.5, 110.0)
    assert res.get("error") == "partial_reduce_unsupported_on_paper_internal"
    assert "bitget_demo" in res.get("hint", "")
    # rejected before any side effect: no ledger event, position unchanged
    assert list_position_close_events(position_id="pi1") == []
    assert _select_one("positions", "pi1")["quantity"] == 2.0


def test_reduce_then_full_close_economics_consistent():
    """Reduce 0.5@110 then close remaining 1.5@120 — the breaker's total (full-close sum
    + partial term) must equal the true multi-leg realized, with no double-count."""
    from datetime import datetime, timezone

    from aletheia.storage.sqlite import get_db
    from aletheia.portfolio.pnl import reduce_position, close_position
    from aletheia.portfolio.account_loss import _partial_close_realized_today
    from aletheia.storage.repositories import list_closed_positions

    db = get_db()
    _insert_open_qty(db, "m1", entry=100.0, qty=2.0, entry_fee=0.12)

    reduce_position("m1", 0.5, 110.0, reason="operator_reduce")   # realized 4.937
    close_position("m1", exit_price=120.0, reason="take_profit")  # remaining 1.5

    today = f"{datetime.now(timezone.utc).date().isoformat()}T00:00:00+00:00"
    partial_term = _partial_close_realized_today("bitget_demo", today)
    full_close_sum = sum(
        p["realized_pnl"] for p in list_closed_positions() if p["realized_pnl"] is not None
    )
    breaker_total = round(full_close_sum + partial_term, 6)

    # true economics: gross 5 + 30 = 35; fees entry 0.12 + exits (0.033 + 0.108) = 0.261
    assert breaker_total == round(35.0 - 0.261, 6)   # 34.739
    assert partial_term == 4.937                     # the reduction is counted once

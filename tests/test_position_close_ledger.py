"""Full-close ledger and historical partial-event readers; no synthetic reducer."""

from aletheia.portfolio.pnl import close_position
from aletheia.storage.repositories import _select_one, list_position_close_events


def _insert_open(db, pid, *, entry=100.0, qty=1.0, direction="long", mode="bitget_demo"):
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,entry_fee) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, "BTC/USDT", "usdt_futures", direction, qty, entry, mode, "open",
         round(entry * qty * 0.0006, 8)),
    )


def test_unverified_close_records_event_without_inventing_pnl():
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
    assert ev["realized_pnl"] is None
    assert pos["realized_pnl"] is None
    assert pos["outcome_eligible"] == 0


def test_ledger_sum_equals_positions_sum_for_full_closes():
    """Aggregate equivalence (Stage 2 foundation): summing realized_pnl from the close
    ledger equals summing it from closed positions, when every close is a full close."""
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import list_closed_positions

    db = get_db()
    _insert_open(db, "a", entry=100.0, qty=1.0)
    _insert_open(db, "b", entry=200.0, qty=0.5)
    # bitget_demo usdt_futures requires explicit funding to compute realized_pnl
    close_position("a", exit_price=90.0, reason="stop_loss",
                   funding_cost=0.0, funding_source="broker_ledger")
    close_position("b", exit_price=220.0, reason="take_profit",
                   funding_cost=0.0, funding_source="broker_ledger")

    ledger_sum = round(sum(e["realized_pnl"] for e in list_position_close_events()
                           if e["realized_pnl"] is not None), 6)
    positions_sum = round(
        sum(p["realized_pnl"] for p in list_closed_positions() if p["realized_pnl"] is not None), 6
    )
    assert ledger_sum == positions_sum == -0.24


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

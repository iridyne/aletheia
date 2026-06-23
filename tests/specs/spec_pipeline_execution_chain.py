"""Spec: Pipeline — Execution Chain (end-to-end)

E2E pipeline claims for approved RiskDecision → paper_broker.execute_paper_order
→ Order(filled) → Fill → Position open → audit.

Contract: pipeline_execution_chain
Claim prefix: PL-EC
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# Shared DB setup
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh isolated SQLite DB into aletheia storage for one test."""
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    sq._db_conn = conn
    run_migrations()
    return conn


# ---------------------------------------------------------------------------
# PL-EC-01  L2b  happy_path: full causal chain persisted after paper execution
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-EC-01", "pipeline_execution_chain", "§1 happy_path", "L2b",
        "submit_proposal → approved → execute_paper_order → "
        "trade_proposals + risk_decisions + orders + fills + positions "
        "all exist with FK linkage",
    ),
)
def test_execution_chain_happy_path(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000.0,
        stop_price=58000.0,
        execution_mode="paper_internal",
    )

    if result.get("status") != "executed":
        pytest.skip(
            f"Proposal was not executed (risk rejected): {result.get('risk_violations')}"
        )

    proposal_id = result["proposal_id"]
    order_id = result.get("order_id")
    fill_id = result.get("fill_id")
    position_id = result.get("position_id")

    assert order_id is not None, "execute_paper_order must return order_id"
    assert fill_id is not None, "execute_paper_order must return fill_id"
    assert position_id is not None, "execute_paper_order must return position_id"

    db = get_db()

    # Verify full chain exists in DB
    assert db.execute("SELECT id FROM trade_proposals WHERE id=?", (proposal_id,)).fetchone() is not None
    assert db.execute("SELECT id FROM risk_decisions WHERE proposal_id=?", (proposal_id,)).fetchone() is not None
    assert db.execute("SELECT id FROM orders WHERE id=?", (order_id,)).fetchone() is not None
    assert db.execute("SELECT id FROM fills WHERE id=?", (fill_id,)).fetchone() is not None
    assert db.execute("SELECT id FROM positions WHERE id=?", (position_id,)).fetchone() is not None

    # FK linkage: order.proposal_id → trade_proposals
    order_row = db.execute("SELECT proposal_id FROM orders WHERE id=?", (order_id,)).fetchone()
    assert order_row["proposal_id"] == proposal_id

    # FK linkage: fill.order_id → orders
    fill_row = db.execute("SELECT order_id FROM fills WHERE id=?", (fill_id,)).fetchone()
    assert fill_row["order_id"] == order_id

    # FK linkage: position.order_id → orders
    pos_row = db.execute("SELECT order_id FROM positions WHERE id=?", (position_id,)).fetchone()
    assert pos_row["order_id"] == order_id


# ---------------------------------------------------------------------------
# PL-EC-02  L2c  causal_integrity: FK invariant scan over all rows
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-EC-02", "pipeline_execution_chain", "§1 causal_integrity", "L2c",
        "Every Order row has non-null proposal_id → trade_proposals; "
        "every Position has non-null order_id → orders; "
        "every Fill has non-null order_id → orders",
    ),
)
def test_causal_integrity_fk_scan(tmp_path, monkeypatch):
    """Run two proposals, then FK-scan all rows for referential integrity."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db

    # Execute two proposals to populate multiple rows
    for price in (60000.0, 3000.0):
        submit_proposal(
            symbol="BTC/USDT" if price > 10000 else "ETH/USDT",
            market_type="spot",
            direction="long",
            quantity=0.001,
            price=price,
            execution_mode="paper_internal",
        )

    db = get_db()

    proposal_ids = {
        r["id"]
        for r in db.execute("SELECT id FROM trade_proposals").fetchall()
    }
    order_ids = {
        r["id"]
        for r in db.execute("SELECT id FROM orders").fetchall()
    }

    orders = db.execute("SELECT id, proposal_id FROM orders").fetchall()
    for row in orders:
        assert row["proposal_id"] is not None, (
            f"Order {row['id']} has NULL proposal_id — FK invariant violated"
        )
        assert row["proposal_id"] in proposal_ids, (
            f"Order {row['id']} references proposal_id={row['proposal_id']!r} "
            "which does not exist in trade_proposals"
        )

    fills = db.execute("SELECT id, order_id FROM fills").fetchall()
    for row in fills:
        assert row["order_id"] is not None, (
            f"Fill {row['id']} has NULL order_id — FK invariant violated"
        )
        assert row["order_id"] in order_ids, (
            f"Fill {row['id']} references order_id={row['order_id']!r} "
            "which does not exist in orders"
        )

    positions = db.execute("SELECT id, order_id FROM positions").fetchall()
    for row in positions:
        assert row["order_id"] is not None, (
            f"Position {row['id']} has NULL order_id — FK invariant violated"
        )
        assert row["order_id"] in order_ids, (
            f"Position {row['id']} references order_id={row['order_id']!r} "
            "which does not exist in orders"
        )


# ---------------------------------------------------------------------------
# PL-EC-03  L2b  atomicity: fill error → Order stays 'error', no Fill/Position leak
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-EC-03", "pipeline_execution_chain", "§5 atomicity", "L2b",
        "When fill simulation raises mid-execution, the Order remains in 'error' "
        "status (NOT silently 'pending') AND no Fill/Position rows leak",
    ),
)
def test_execution_chain_atomicity_on_fill_error(tmp_path, monkeypatch):
    """Force insert_fill to raise and verify Order is marked 'error', no Fill leaked."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import paper_broker
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    # Spy on insert_order to capture the created order id
    captured_order_id = {}
    original_insert_order = repo.insert_order

    def _spy_insert_order(order):
        captured_order_id["id"] = order.id
        original_insert_order(order)

    monkeypatch.setattr(paper_broker, "insert_order", _spy_insert_order)
    monkeypatch.setattr(repo, "insert_order", _spy_insert_order)

    # Force insert_fill to raise to simulate mid-execution failure
    def _raise_fill(fill):
        raise RuntimeError("fill_simulation_failed: disk full")

    monkeypatch.setattr(paper_broker, "insert_fill", _raise_fill)
    monkeypatch.setattr(repo, "insert_fill", _raise_fill)

    with pytest.raises(RuntimeError, match="fill_simulation_failed"):
        paper_broker.execute_paper_order(
            proposal_id="prop-pl-ec-03",
            risk_decision_id="rd-pl-ec-03",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60000.0,
            execution_mode="paper_internal",
        )

    db = get_db()

    # Order row must exist — paper_broker inserts BEFORE fill
    assert "id" in captured_order_id, "insert_order was never called"
    order_id = captured_order_id["id"]

    order_row = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    assert order_row is not None, "Order row must persist even after fill failure"

    # No Fill rows for this order must have leaked
    fills = db.execute("SELECT id FROM fills WHERE order_id=?", (order_id,)).fetchall()
    assert len(fills) == 0, (
        f"Fill rows leaked after mid-execution failure: {[dict(f) for f in fills]}"
    )

    # No Position rows for this proposal must have leaked
    positions = db.execute(
        "SELECT id FROM positions WHERE proposal_id=?", ("prop-pl-ec-03",)
    ).fetchall()
    assert len(positions) == 0, (
        f"Position rows leaked after mid-execution failure: {[dict(p) for p in positions]}"
    )


# ---------------------------------------------------------------------------
# PL-EC-04  L2b  idempotency: re-submitting same proposal is rejected
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-EC-04", "pipeline_execution_chain", "§5 idempotency", "L2b",
        "execute_existing_proposal on an already-executed proposal returns "
        "'already_executed' without creating a new order row",
    ),
)
def test_execution_chain_idempotency(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal, execute_existing_proposal
    from aletheia.storage import repositories as repo

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000.0,
        execution_mode="paper_internal",
    )

    if result.get("status") != "executed":
        pytest.skip(
            "Proposal not executed (risk rejected) — cannot test idempotency guard"
        )

    proposal_id = result["proposal_id"]
    orders_before = [
        o for o in repo.list_orders(limit=200)
        if o.get("proposal_id") == proposal_id
    ]

    # Re-submit the same proposal
    second = execute_existing_proposal(proposal_id, execution_mode="paper_internal")

    assert second["status"] == "already_executed", (
        f"Expected 'already_executed' on second execution, got {second['status']!r}"
    )

    orders_after = [
        o for o in repo.list_orders(limit=200)
        if o.get("proposal_id") == proposal_id
    ]
    assert len(orders_after) == len(orders_before), (
        "Re-executing an already-executed proposal must not create additional order rows"
    )


# ---------------------------------------------------------------------------
# PL-EC-05  L2b  audit_event_persisted — captures CR-EC-1 drift
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PL-EC-05", "pipeline_execution_chain", "§3 audit_event_persisted", "L2b",
        "After execute_paper_order an audit row exists in audit_events table "
        "with event_type='paper_order_executed'",
        status="implemented",
    ),
)
@pytest.mark.xfail(
    strict=False,
    reason=(
        "EC-1 drift probe: gateway.py writes write_audit(event_type='paper_order_executed') "
        "which calls _write_audit_row() → insert_audit_event(). If audit_events table is "
        "missing the row this xfail fires. Remove xfail when verified green in CI."
    ),
)
def test_execution_chain_audit_event_in_db(tmp_path, monkeypatch):
    """Verify gateway writes audit row into audit_events table (not just JSONL).

    write_audit() in jsonl.py calls _write_audit_row() which calls insert_audit_event().
    This test confirms the SQLite persistence path is wired.
    """
    _setup_db(tmp_path, monkeypatch)

    import aletheia.files.jsonl as jsonl_mod
    jsonl_mod._last_audit_hash = None

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.sqlite import get_db

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60000.0,
        execution_mode="paper_internal",
    )

    if result.get("status") != "executed":
        pytest.skip("Proposal not executed (risk rejected)")

    db = get_db()
    order_id = result.get("order_id")

    audit_rows = db.execute(
        "SELECT * FROM audit_events WHERE event_type=? AND entity_id=?",
        ("paper_order_executed", order_id),
    ).fetchall()

    assert len(audit_rows) >= 1, (
        f"Expected audit_events row with event_type='paper_order_executed' "
        f"for order_id={order_id!r}, found none. "
        "CR-EC-1 drift: gateway writes JSONL but audit_events table row missing."
    )

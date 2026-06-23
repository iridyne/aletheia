"""Spec: Execution Contract (execution_contract.md)

The causal chain trade_proposals -> risk_decisions -> orders -> fills -> positions
is the most critical invariant in the entire system.
"""

import ast
import textwrap

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-S01", "execution_contract", "§1 entry points", "L2a",
        "submit_proposal() and execute_existing_proposal() exist at gateway.py",
    ),
)
def test_execution_gateway_entry_points():
    from aletheia.execution import gateway

    assert callable(gateway.submit_proposal)
    assert callable(gateway.execute_existing_proposal)


@spec_claim(
    SpecClaim(
        "EX-S02", "execution_contract", "§1 internal", "L2a",
        "_execute_proposal is private (single underscore prefix)",
    ),
)
def test_execution_internal_is_private():
    from aletheia.execution import gateway

    assert callable(gateway._execute_proposal)


@spec_claim(
    SpecClaim(
        "EX-S03", "execution_contract", "§2 brokers", "L2a",
        "Paper + Bitget + OKX brokers exist as separate modules",
    ),
)
def test_broker_modules_exist():
    from aletheia.execution import paper_broker, bitget_broker, okx_broker

    assert callable(paper_broker.execute_paper_order)
    assert callable(bitget_broker.execute_live_order)
    assert callable(okx_broker.execute_live_order)


@spec_claim(
    SpecClaim(
        "EX-S04", "execution_contract", "§4 reconciliation", "L2a",
        "reconciliation.reconcile() exists and is independent of order placement",
    ),
)
def test_reconciliation_module():
    from aletheia.execution.reconciliation import reconcile

    assert callable(reconcile)


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B01", "execution_contract", "§1 risk gate", "L2b",
        "submit_proposal() calls evaluate_proposal before execution",
    ),
)
def test_submit_calls_risk_first(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()

    result = submit_proposal(
        symbol="BTC/USDT", direction="long", quantity=0.001,
        price=60000.0, execution_mode="paper_internal",
        market_type="spot",
    )
    decisions = repo.list_risk_decisions(limit=10)
    assert len(decisions) >= 1, "submit_proposal must trigger risk evaluation"


@spec_claim(
    SpecClaim(
        "EX-B02", "execution_contract", "§2 proposal status", "L2b",
        "TradeProposal default status is 'pending'",
    ),
)
def test_proposal_default_status():
    from aletheia.storage.models import TradeProposal

    p = TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=1, price=3000,
    )
    assert p.status == "pending"


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-C01", "execution_contract", "§1 causal chain", "L2c",
        "After paper execution: proposal -> risk_decision -> order chain is unbroken",
    ),
)
def test_causal_chain_integrity(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()

    result = submit_proposal(
        symbol="BTC/USDT", direction="long", quantity=0.001,
        price=60000.0, execution_mode="paper_internal",
        market_type="spot",
    )
    proposal_id = result.get("proposal_id")
    if not proposal_id:
        pytest.skip("Proposal rejected by risk - cannot verify full chain")

    proposals = repo.list_trade_proposals(limit=10)
    assert any(p["id"] == proposal_id for p in proposals)

    decisions = repo.list_risk_decisions(limit=10)
    assert any(d.get("proposal_id") == proposal_id for d in decisions)


# ---------------------------------------------------------------------------
# Helper: shared DB setup for behavioral claims
# ---------------------------------------------------------------------------

def _setup_db(tmp_path, monkeypatch):
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
    monkeypatch.setattr(sq, "_db_conn", conn)
    run_migrations()


# ---------------------------------------------------------------------------
# EX-B03 — atomicity: Order row written before exchange call
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B03", "execution_contract", "§5 atomicity", "L2b",
        "Order(pending) row exists in DB before exchange call; on exchange failure row remains with status=error",
    ),
)
def test_order_persisted_before_exchange_call(tmp_path, monkeypatch):
    """bitget_broker inserts Order(pending) before place_order; if place_order raises
    the row must still exist with status='error' (contract §5, bitget_broker.py:87-99)."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import bitget_broker
    from aletheia.storage import repositories as repo

    captured_order_id = {}

    original_insert = repo.insert_order

    def _spy_insert(order):
        captured_order_id["id"] = order.id
        original_insert(order)

    monkeypatch.setattr(repo, "insert_order", _spy_insert)
    monkeypatch.setattr(bitget_broker, "insert_order", _spy_insert)

    # Patch _run_coro so the exchange call raises immediately.
    def _raise(_coro):
        raise ConnectionError("exchange unavailable")

    monkeypatch.setattr(bitget_broker, "_run_coro", _raise)

    with pytest.raises(ConnectionError):
        bitget_broker.execute_live_order(
            proposal_id="prop-ex-b03",
            risk_decision_id="rd-ex-b03",
            symbol="BTC/USDT",
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=0.001,
            price=60000.0,
            execution_mode="bitget_demo",
        )

    assert "id" in captured_order_id, "insert_order was never called before the exchange raised"
    orders = repo.list_orders(limit=50)
    row = next((o for o in orders if o["id"] == captured_order_id["id"]), None)
    assert row is not None, "Order row must persist after exchange failure"
    assert row["status"] == "error", f"Expected status='error', got {row['status']!r}"


# ---------------------------------------------------------------------------
# EX-B04 — idempotency: re-executing an already-executed proposal is rejected
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B04", "execution_contract", "§5 idempotency", "L2b",
        "execute_existing_proposal on an already-executed proposal returns already_executed without creating a new order",
    ),
)
def test_re_execute_already_executed_proposal_rejected(tmp_path, monkeypatch):
    """gateway.execute_existing_proposal checks proposal.status=='executed' and short-
    circuits with status='already_executed' (gateway.py:61-66)."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal, execute_existing_proposal
    from aletheia.storage import repositories as repo

    result = submit_proposal(
        symbol="BTC/USDT", direction="long", quantity=0.001,
        price=60000.0, execution_mode="paper_internal",
        market_type="spot",
    )
    proposal_id = result.get("proposal_id")
    if result.get("status") != "executed":
        pytest.skip("Proposal not executed (risk rejected) — cannot test re-execute guard")

    orders_before = repo.list_orders(limit=200)
    order_count_before = sum(1 for o in orders_before if o.get("proposal_id") == proposal_id)

    second = execute_existing_proposal(proposal_id, execution_mode="paper_internal")

    assert second["status"] == "already_executed", (
        f"Expected 'already_executed', got {second['status']!r}"
    )
    orders_after = repo.list_orders(limit=200)
    order_count_after = sum(1 for o in orders_after if o.get("proposal_id") == proposal_id)
    assert order_count_after == order_count_before, (
        "Re-executing an already-executed proposal must not create additional order rows"
    )


# ---------------------------------------------------------------------------
# EX-B05 — reconciliation never imports place_order
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B05", "execution_contract", "§6 reconciliation invariant", "L2c",
        "reconciliation.py never imports a place_order function (reconciliation must not place orders)",
    ),
)
def test_reconciliation_never_imports_place_order():
    """AST-scan reconciliation.py imports — no 'place_order' name must appear."""
    import importlib.util
    from pathlib import Path

    src = Path(__file__).parents[2] / "aletheia" / "execution" / "reconciliation.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names]
            for name in names:
                assert "place_order" not in name, (
                    f"reconciliation.py imports 'place_order' ({name!r}) — "
                    "reconciliation must never place orders (contract §6)"
                )


# ---------------------------------------------------------------------------
# EX-B06 — reconciliation never inserts a Fill row directly
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B06", "execution_contract", "§6 reconciliation", "L2c",
        "reconciliation.py never inserts a Fill row directly (only updates Order.status)",
    ),
)
def test_reconciliation_never_inserts_fill():
    """AST-scan reconciliation.py for insert_fill calls or Fill( construction."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "aletheia" / "execution" / "reconciliation.py"
    text = src.read_text(encoding="utf-8")

    forbidden = ["insert_fill", 'Fill(']
    for token in forbidden:
        assert token not in text, (
            f"reconciliation.py contains {token!r} — "
            "reconciliation must not create Fill rows directly (contract §6)"
        )


# ---------------------------------------------------------------------------
# EX-S05 — _is_live() is defined and private in risk/engine.py
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-S05", "execution_contract", "§3 execution mode", "L2a",
        "_is_live() is defined in risk/engine.py and is private (single underscore prefix)",
    ),
)
def test_is_live_defined_and_private():
    """AST-scan risk/engine.py — _is_live must exist as a module-level function
    with a single leading underscore (contract §3: engine.py:35)."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "aletheia" / "risk" / "engine.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    function_names = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    ]
    assert "_is_live" in function_names, (
        "_is_live not found in risk/engine.py — contract §3 requires it"
    )
    # Must start with exactly one underscore (private, not dunder).
    assert not "_is_live".startswith("__"), "_is_live must not be dunder-private"
    assert "_is_live".startswith("_"), "_is_live must start with a single underscore"


# ---------------------------------------------------------------------------
# EX-B07 — paper_broker reachable only through gateway in production code
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B07", "execution_contract", "§1 entry", "L2b",
        "In production (non-test) code only gateway.py imports execute_paper_order from paper_broker",
    ),
)
def test_paper_broker_only_imported_by_gateway():
    """AST-scan all .py files under aletheia/ (excluding tests/).  The only file
    that may import execute_paper_order from paper_broker is gateway.py — all other
    callers must go through submit_proposal / execute_existing_proposal
    (contract §1: 'all orders must flow through Execution Gateway')."""
    from pathlib import Path

    root = Path(__file__).parents[2] / "aletheia"
    violators = []
    for py_file in root.rglob("*.py"):
        if py_file.name == "gateway.py":
            continue
        text = py_file.read_text(encoding="utf-8", errors="replace")
        if "execute_paper_order" not in text:
            continue
        tree = ast.parse(text, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names = [alias.name for alias in node.names]
                if "execute_paper_order" in imported_names:
                    violators.append(str(py_file.relative_to(root.parent)))
    assert not violators, (
        "execute_paper_order imported outside gateway.py in production code: "
        + ", ".join(violators)
    )


# ---------------------------------------------------------------------------
# EX-C02 — every Order row has a proposal_id matching a trade_proposals row
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-C02", "execution_contract", "§1 causal chain", "L2c",
        "Every Order row in DB has a proposal_id that matches an existing trade_proposals row",
    ),
)
def test_order_proposal_foreign_key_integrity(tmp_path, monkeypatch):
    """Build one full execution chain via submit_proposal, then assert every
    orders.proposal_id resolves to a row in trade_proposals."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    result = submit_proposal(
        symbol="BTC/USDT", direction="long", quantity=0.001,
        price=60000.0, execution_mode="paper_internal",
        market_type="spot",
    )

    db = get_db()
    orders = db.execute("SELECT id, proposal_id FROM orders").fetchall()
    if not orders:
        pytest.skip("No order rows created (proposal may have been risk-rejected)")

    proposal_ids_in_proposals = {
        row["id"]
        for row in db.execute("SELECT id FROM trade_proposals").fetchall()
    }

    for order_row in orders:
        pid = order_row["proposal_id"]
        assert pid is not None, f"Order {order_row['id']} has NULL proposal_id"
        assert pid in proposal_ids_in_proposals, (
            f"Order {order_row['id']} references proposal_id={pid!r} "
            "which does not exist in trade_proposals"
        )

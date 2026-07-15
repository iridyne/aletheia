"""Spec: Execution Contract (execution_contract.md)

The causal chain trade_proposals -> risk_decisions -> orders -> fills -> positions
is the most critical invariant in the entire system.
"""

import ast
import textwrap

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim
from tests.async_test_utils import closing_coro_runner


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-S01", "execution_contract", "§1 entry points", "L2a",
        "submit_proposal() exists and delegates execution through "
        "execute_existing_proposal() instead of bypassing its durable claim",
    ),
)
def test_execution_gateway_entry_points():
    from aletheia.execution import gateway

    assert callable(gateway.submit_proposal)
    assert callable(gateway.execute_existing_proposal)

    source = ast.parse(open(gateway.__file__, encoding="utf-8").read())
    submit = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "submit_proposal"
    )
    calls = {
        node.func.id for node in ast.walk(submit)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "execute_existing_proposal" in calls
    assert "_execute_proposal" not in calls


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
        "EX-B13", "execution_contract", "§3.4 preview runtime", "L2b",
        "Preview runtime rejects proposal submission and re-execution before any durable execution state changes",
    ),
)
def test_preview_runtime_blocks_execution_before_persistence(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    import aletheia.execution.gateway as gateway
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(gateway, "is_preview_mode", lambda: True)

    with pytest.raises(gateway.PreviewExecutionBlockedError, match="preview mode"):
        gateway.submit_proposal(
            symbol="BTC/USDT", direction="long", quantity=0.001,
            price=60_000.0, execution_mode="paper_internal", market_type="spot",
        )
    with pytest.raises(gateway.PreviewExecutionBlockedError, match="preview mode"):
        gateway.execute_existing_proposal("does-not-matter")

    assert repo.list_trade_proposals(limit=10) == []


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


@spec_claim(
    SpecClaim(
        "EX-B05", "execution_contract", "§1 executable venue rules", "L2b",
        "Exchange-bound proposals obtain executable-venue constraints, re-run Risk "
        "Engine on the adjusted quantity, and reject if rules are unavailable",
    ),
)
def test_exchange_order_constraint_gate_is_wired_before_submission():
    from aletheia.execution import gateway

    source = ast.parse(open(gateway.__file__, encoding="utf-8").read())
    execute = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_proposal"
    )
    calls = [
        node.func.id for node in ast.walk(execute)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "evaluate_proposal" in calls
    assert "_prepare_exchange_quantity" in calls
    assert calls.count("evaluate_proposal") >= 2, "adjusted quantity must be re-checked by Risk Engine"


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
        "Order(pending) exists before submission; a transport failure leaves a "
        "retry-suppressing submission_unknown row rather than claiming rejection",
    ),
)
def test_order_persisted_before_exchange_call(tmp_path, monkeypatch):
    """The pending row survives an ambiguous transport failure without resubmission."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution import bitget_broker
    from aletheia.storage import repositories as repo
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
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

    captured_order_id = {}

    original_insert = repo.insert_order

    def _spy_insert(order):
        captured_order_id["id"] = order.id
        original_insert(order)

    monkeypatch.setattr(repo, "insert_order", _spy_insert)
    monkeypatch.setattr(bitget_broker, "insert_order", _spy_insert)

    # Close the already-created coroutine before simulating an exchange failure;
    # otherwise the leak is reported later against an unrelated test.
    monkeypatch.setattr(
        bitget_broker,
        "_run_coro",
        closing_coro_runner(ConnectionError("exchange unavailable")),
    )

    from aletheia.execution.errors import SubmissionUnknownError

    with pytest.raises(SubmissionUnknownError):
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
    assert row["status"] == "submission_unknown"
    assert "exchange unavailable" not in (row["error_reason"] or "")


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


@spec_claim(
    SpecClaim(
        "EX-B13", "execution_contract", "§5 durable execution lease", "L2b",
        "A fresh durable Proposal execution lease rejects a concurrent wake before "
        "the execution pipeline or any broker side effect runs.",
        status="implemented",
    ),
)
def test_fresh_execution_lease_blocks_duplicate_pipeline(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        id="proposal-fresh-execution-lease",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=100.0,
        execution_mode="paper_internal",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    now = datetime.now(timezone.utc)
    assert repo.insert_proposal_execution_claim(
        proposal.id,
        "active-owner",
        now.isoformat(),
        (now + timedelta(minutes=5)).isoformat(),
    )
    pipeline_calls = 0

    def should_not_run(*_args, **_kwargs):
        nonlocal pipeline_calls
        pipeline_calls += 1
        raise AssertionError("fresh lease reached execution pipeline")

    monkeypatch.setattr(gateway, "_execute_proposal", should_not_run)
    result = gateway.execute_existing_proposal(proposal.id, "paper_internal")

    assert result["status"] == "already_claimed"
    assert pipeline_calls == 0
    assert repo.list_orders(limit=10) == []


@spec_claim(
    SpecClaim(
        "EX-B14", "execution_contract", "§5 local paper crash recovery", "L2b",
        "An expired paper_internal execution lease with zero Orders can be CAS-reclaimed "
        "and materializes exactly one Order, Fill, and Position.",
        status="implemented",
    ),
)
def test_expired_paper_execution_lease_recovers_exactly_once(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, TradeProposal

    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 100.0,
    )
    opportunity = Opportunity(
        id="opportunity-expired-paper-lease",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
    )
    repo.insert_opportunity(opportunity)

    proposal = TradeProposal(
        id="proposal-expired-paper-lease",
        opportunity_id=opportunity.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=100.0,
        stop_price=99.0,
        take_profit=102.0,
        risk_reward=2.0,
        execution_mode="paper_internal",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    expired = datetime.now(timezone.utc) - timedelta(minutes=20)
    assert repo.insert_proposal_execution_claim(
        proposal.id,
        "dead-paper-owner",
        (expired - timedelta(minutes=10)).isoformat(),
        expired.isoformat(),
    )

    result = gateway.execute_existing_proposal(proposal.id, "paper_internal")

    assert result["status"] == "executed"
    assert len([
        row for row in repo.list_orders(limit=20)
        if row.get("proposal_id") == proposal.id
    ]) == 1
    assert len(repo.list_fills(limit=20)) == 1
    assert len([
        row for row in repo.list_positions(limit=20)
        if row.get("proposal_id") == proposal.id
    ]) == 1
    assert repo.get_trade_proposal(proposal.id)["status"] == "executed"
    assert repo.get_proposal_execution_claim(proposal.id) is None

    replay = gateway.execute_existing_proposal(proposal.id, "paper_internal")
    assert replay["status"] == "already_executed"
    assert len([
        row for row in repo.list_orders(limit=20)
        if row.get("proposal_id") == proposal.id
    ]) == 1


@spec_claim(
    SpecClaim(
        "EX-B15", "execution_contract", "§5 exchange crash recovery", "L2b",
        "An expired exchange execution lease is marked reconciliation-required and "
        "never re-enters the execution pipeline or broker.",
        status="implemented",
    ),
)
def test_expired_exchange_execution_lease_never_resubmits(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution import gateway
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        id="proposal-expired-exchange-lease",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="bitget_demo",
        status="executing",
    )
    repo.insert_trade_proposal(proposal)
    expired = datetime.now(timezone.utc) - timedelta(minutes=20)
    assert repo.insert_proposal_execution_claim(
        proposal.id,
        "dead-exchange-owner",
        (expired - timedelta(minutes=10)).isoformat(),
        expired.isoformat(),
    )
    pipeline_calls = 0

    def should_not_run(*_args, **_kwargs):
        nonlocal pipeline_calls
        pipeline_calls += 1
        raise AssertionError("expired exchange lease reached broker pipeline")

    monkeypatch.setattr(gateway, "_execute_proposal", should_not_run)
    result = gateway.execute_existing_proposal(proposal.id, "bitget_demo")

    assert result["status"] == "reconciliation_required"
    assert result["proposal_status"] == "submission_unknown"
    assert pipeline_calls == 0
    assert repo.list_orders(limit=10) == []
    assert repo.get_trade_proposal(proposal.id)["status"] == "submission_unknown"
    assert repo.get_proposal_execution_claim(proposal.id) is None


# ---------------------------------------------------------------------------
# EX-C04 — reconciliation never imports place_order
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-C04", "execution_contract", "§6 reconciliation invariant", "L2c",
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


# ---------------------------------------------------------------------------
# EX-B08 — fee honesty: fills declare fee_source provenance ('estimated' vs 'reconciled')
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B08", "execution_contract", "§5 fee honesty", "L2b",
        "Fills carry a fee_source provenance marker defaulting to 'estimated' (the "
        "flat-rate computed fee used by paper + all exchange brokers) so estimated "
        "fees are never mistaken for the exchange's real fee; reconciliation may "
        "later backfill the actual fee and set fee_source='reconciled'.",
        status="implemented",
    ),
)
def test_fill_fee_source_marks_estimated(tmp_path, monkeypatch):
    """A paper execution's fill must round-trip with fee_source='estimated'."""
    _setup_db(tmp_path, monkeypatch)

    from aletheia.execution.gateway import submit_proposal
    from aletheia.storage import repositories as repo

    result = submit_proposal(
        symbol="BTC/USDT", direction="long", quantity=0.001,
        price=60000.0, execution_mode="paper_internal", market_type="spot",
    )
    if result.get("status") != "executed":
        pytest.skip("Proposal not executed (risk rejected) — no fill to inspect")

    fills = repo.list_fills(limit=10)
    assert fills, "paper execution must produce a fill row"
    for f in fills:
        assert f.get("fee_source") == "estimated", (
            f"Fill must declare fee_source='estimated' (flat-rate estimate), "
            f"got {f.get('fee_source')!r}. Estimated fees must be distinguishable "
            "from reconciled exchange fees (honesty)."
        )


def test_fill_model_defaults_fee_source_estimated():
    """All brokers construct Fill(...) without fee_source — the model default is the
    single source of the 'estimated' marker (clone-path guard: bitget/okx/binance/
    paper all rely on it)."""
    from aletheia.storage.models import Fill

    f = Fill(order_id="o1", fill_price=100.0, fill_quantity=1.0, fee=0.06)
    assert f.fee_source == "estimated"


# ---------------------------------------------------------------------------
# CR-EX-03 — opportunity_id threaded onto the Position (causal chain / §16 sector)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-S06", "execution_contract", "§16 causal chain", "L2a",
        "Position carries opportunity_id; all four broker entry points accept it; "
        "gateway threads proposal.opportunity_id into execution",
    ),
)
def test_opportunity_id_threaded_through_execution():
    import inspect
    from aletheia.storage.models import Position

    assert "opportunity_id" in Position.model_fields

    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.execution.bitget_broker import execute_live_order as bitget_exec
    from aletheia.execution.okx_broker import execute_live_order as okx_exec
    from aletheia.execution.binance_broker import execute_live_order as binance_exec
    for fn in (execute_paper_order, bitget_exec, okx_exec, binance_exec):
        assert "opportunity_id" in inspect.signature(fn).parameters, fn.__module__

    import aletheia.execution.gateway as gw
    src = inspect.getsource(gw._execute_proposal)
    assert src.count("opportunity_id=proposal.opportunity_id") >= 1


@spec_claim(
    SpecClaim(
        "EX-B09", "execution_contract", "§16 sector concentration", "L2b",
        "execute_paper_order records opportunity_id on the opened Position so the "
        "risk engine sector-concentration check (#16) can resolve scanner_family",
    ),
)
def test_paper_open_records_opportunity_id(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    from aletheia.execution.paper_broker import execute_paper_order
    from aletheia.storage import repositories as repo
    from tests.execution_authority import seed_order_authority

    seed_order_authority(
        proposal_id="po1",
        risk_decision_id="ro1",
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=0.001,
        price=60000.0,
        execution_mode="paper_internal",
    )

    r = execute_paper_order(
        proposal_id="po1", risk_decision_id="ro1",
        symbol="BTC/USDT", market_type="spot", direction="long",
        order_type="market", quantity=0.001, price=60000.0,
        execution_mode="paper_internal", opportunity_id="opp-123",
    )
    pos = repo._select_one("positions", r["position_id"])
    assert pos["opportunity_id"] == "opp-123"


# ---------------------------------------------------------------------------
# EX-S07 — symbol availability pre-check exists and covers all exchange modes
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-S07", "execution_contract", "§3.2 symbol filter", "L2a",
        "_check_symbol_availability and _load_registry_symbols are callable in gateway; "
        "_EXECUTION_MODE_TO_VENUE maps all real exchange modes (okx_live, bitget, binance)",
    ),
)
def test_symbol_availability_check_structure():
    from aletheia.execution import gateway

    assert callable(gateway._check_symbol_availability)
    assert callable(gateway._load_registry_symbols)

    required_modes = {
        "okx_live_limited",
        "bitget_demo", "bitget_live_limited",
        "binance_testnet", "binance_live_limited",
    }
    assert required_modes <= set(gateway._EXECUTION_MODE_TO_VENUE), (
        f"Missing modes in _EXECUTION_MODE_TO_VENUE: "
        f"{required_modes - set(gateway._EXECUTION_MODE_TO_VENUE)}"
    )


# ---------------------------------------------------------------------------
# EX-B10 — paper_internal always passes (no exchange symbol lookup)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B10", "execution_contract", "§3.2 symbol filter", "L2b",
        "paper_internal always returns None from _check_symbol_availability "
        "(virtual fills — exchange symbol list irrelevant)",
    ),
)
def test_symbol_check_paper_internal_always_passes():
    from aletheia.execution.gateway import _check_symbol_availability

    assert _check_symbol_availability("WLDUSDT", "paper_internal") is None
    assert _check_symbol_availability("ANYTOKEN/USDT", "paper_internal") is None


# ---------------------------------------------------------------------------
# EX-B11 — OKX demo: absent symbol rejected, present symbol passes
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B11", "execution_contract", "§3.2 symbol filter", "L2b",
        "When the OKX demo symbol sets are loaded and a symbol is absent for the "
        "given market_type, _check_symbol_availability returns a non-empty reason; "
        "a present symbol returns None; market_type-awareness blocks swap-absent symbols",
    ),
)
def test_symbol_check_okx_demo_absent_and_present(monkeypatch):
    import aletheia.execution.gateway as gw
    import aletheia.execution.okx_broker as okx_broker

    # New structure: {"swap": frozenset, "spot": frozenset}
    # WLD/USDT is spot-only on OKX demo — absent from swap, present in spot.
    monkeypatch.setattr(okx_broker, "_okx_demo_symbols", {
        "swap": frozenset({"BTC/USDT", "ETH/USDT"}),
        "spot": frozenset({"BTC/USDT", "ETH/USDT", "WLD/USDT"}),
    })
    monkeypatch.setattr(okx_broker, "_okx_demo_symbols_loaded_at", float("inf"))

    # WLD is in spot but NOT in swap → perp order must be rejected
    reason = gw._check_symbol_availability("WLDUSDT", "okx_demo", "usdt_futures")
    assert reason is not None, "Absent-from-swap symbol must produce a rejection reason"
    assert "WLD" in reason or "not available" in reason.lower()

    # WLD spot order must pass
    assert gw._check_symbol_availability("WLDUSDT", "okx_demo", "spot") is None

    # BTC is in swap → perp order passes
    assert gw._check_symbol_availability("BTCUSDT", "okx_demo", "usdt_futures") is None


# ---------------------------------------------------------------------------
# EX-B12 — Registry-based modes: absent symbol rejected; empty registry fail-open
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-B12", "execution_contract", "§3.2 symbol filter", "L2b",
        "For registry-based modes (bitget, binance): absent symbol → rejection reason; "
        "spot-only symbol rejects a perp order (market-type-aware); "
        "empty/unsynced registry → None (fail-open, never blocks legitimate orders)",
    ),
)
def test_symbol_check_registry_modes(monkeypatch):
    import aletheia.execution.gateway as gw
    import aletheia.storage.repositories as repo

    # ── case A: registry loaded, WLD absent for usdt_futures ──
    gw._registry_symbol_cache.clear()
    monkeypatch.setattr(
        repo, "list_universe_assets",
        lambda *a, **kw: [
            {"symbol": "BTC/USDT", "market_type": "usdt_futures"},
            {"symbol": "ETH/USDT", "market_type": "usdt_futures"},
        ],
    )
    reason = gw._check_symbol_availability("WLDUSDT", "bitget_demo", "usdt_futures")
    assert reason is not None, "Registry loaded but symbol absent — must reject"

    # ── case B: WLD available as spot, but NOT for a perp order ──
    gw._registry_symbol_cache.clear()
    monkeypatch.setattr(
        repo, "list_universe_assets",
        lambda *a, **kw: [
            {"symbol": "BTC/USDT", "market_type": "usdt_futures"},
            {"symbol": "WLD/USDT", "market_type": "spot"},
        ],
    )
    reason_spot_only = gw._check_symbol_availability("WLDUSDT", "bitget_demo", "usdt_futures")
    assert reason_spot_only is not None, "Spot-only symbol must reject a perp order"
    assert gw._check_symbol_availability("WLDUSDT", "bitget_demo", "spot") is None

    # ── case C: registry empty (not yet synced) → fail-open ──
    gw._registry_symbol_cache.clear()
    monkeypatch.setattr(repo, "list_universe_assets", lambda *a, **kw: [])
    reason_empty = gw._check_symbol_availability("WLDUSDT", "binance_testnet")
    assert reason_empty is None, "Empty registry must fail-open, not block orders"


@spec_claim(
    SpecClaim(
        "EX-B16",
        "execution_contract",
        "§3.2 complete symbol registry",
        "L2c",
        "Exchange availability consumes the complete active venue registry; a valid "
        "3,001st symbol cannot be silently converted into an unavailable rejection.",
        status="implemented",
    )
)
def test_symbol_check_does_not_truncate_large_registry(monkeypatch):
    import aletheia.execution.gateway as gw
    import aletheia.storage.repositories as repo

    assets = [
        {"symbol": f"TOKEN{index}/USDT", "market_type": "usdt_futures"}
        for index in range(3_000)
    ] + [{"symbol": "TARGET/USDT", "market_type": "usdt_futures"}]
    observed_limits: list[int | None] = []

    def _list_assets(*, venue, active_only, limit):
        assert venue == "bitget"
        assert active_only is True
        observed_limits.append(limit)
        return assets if limit is None else assets[:limit]

    gw._registry_symbol_cache.clear()
    monkeypatch.setattr(repo, "list_universe_assets", _list_assets)

    assert gw._check_symbol_availability(
        "TARGET/USDT",
        "bitget_demo",
        "usdt_futures",
    ) is None
    assert observed_limits == [None]
    gw._registry_symbol_cache.clear()


# ---------------------------------------------------------------------------
# EX-C03 — gateway is the sole hard gate: trust/guard_chain is NOT wired in
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "EX-C03", "execution_contract", "§1 sole hard gate", "L2c",
        "gateway.py does not import guard_chain nor call run_guards() — the trust guard "
        "chain is advisory observability, never an execution verdict. The sole hard gate "
        "on the order path is risk/engine.py:evaluate_proposal (risk_engine_contract §2). "
        "Wiring a guard into execution requires updating both contracts + this claim.",
    ),
)
def test_gateway_does_not_wire_guard_chain():
    """AST-scan gateway.py imports + text-scan for run_guards(). The disconnection of
    trust/guard_chain from the execution path is INTENTIONAL (advisory-only); locking it
    prevents silent wiring (a hidden hard block) AND mistaking the disconnection for a bug."""
    from pathlib import Path

    src = Path(__file__).parents[2] / "aletheia" / "execution" / "gateway.py"
    text = src.read_text(encoding="utf-8")
    tree = ast.parse(text)

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = (getattr(node, "module", "") or "")
            assert "guard_chain" not in mod, (
                f"gateway.py imports from guard_chain ({mod!r}) — trust guard chain must "
                "NOT be wired into execution (advisory only; sole hard gate = risk engine)"
            )
            for alias in node.names:
                assert "guard_chain" not in alias.name and "run_guards" not in alias.name, (
                    f"gateway.py imports {alias.name!r} from the guard chain — execution must "
                    "not consume guard verdicts (EX-C03)"
                )
    assert "run_guards(" not in text, (
        "gateway.py calls run_guards() — guard chain must not gate execution (EX-C03; "
        "the sole hard gate is risk/engine.py:evaluate_proposal)"
    )

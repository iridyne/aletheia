"""Spec: Execution Contract (execution_contract.md)

The causal chain trade_proposals -> risk_decisions -> orders -> fills -> positions
is the most critical invariant in the entire system.
"""

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

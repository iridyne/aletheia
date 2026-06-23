"""Spec: Risk Engine Contract (risk_engine_contract.md)

Executable claims from the risk engine trust-boundary contract.
"""

import threading

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim

# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-S01", "risk_engine_contract", "§1 entry", "L2a",
              "evaluate_proposal() is the sole public entry point"),
    SpecClaim("RE-S02", "risk_engine_contract", "§1 lock", "L2a",
              "_eval_lock threading.Lock prevents TOCTOU"),
)
def test_risk_engine_entry_point_and_lock():
    import aletheia.risk.engine as eng

    assert callable(eng.evaluate_proposal)
    assert isinstance(eng._eval_lock, type(threading.Lock()))


@spec_claim(
    SpecClaim("RE-S03", "risk_engine_contract", "§3 result shape", "L2a",
              "RiskCheckResult has approved/violations/decision_id/allowed_execution_mode"),
)
def test_risk_check_result_schema():
    from aletheia.risk.engine import RiskCheckResult

    r = RiskCheckResult()
    assert hasattr(r, "approved")
    assert hasattr(r, "violations")
    assert hasattr(r, "decision_id")
    assert hasattr(r, "allowed_execution_mode")
    assert isinstance(r.violations, list)


@spec_claim(
    SpecClaim("RE-S04", "risk_engine_contract", "§2 check count", "L2a",
              "At least 16 distinct violation tokens exist in engine source"),
)
def test_risk_engine_has_minimum_check_count():
    """Count unique violation string literals in _evaluate_proposal_locked."""
    import ast
    from pathlib import Path

    src = (Path(__file__).parent.parent.parent / "aletheia" / "risk" / "engine.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(("kill_switch", "symbol_", "market_",
                                      "live_", "permission_", "max_",
                                      "daily_loss", "exchange_", "account_",
                                      "leverage_", "data_", "total_",
                                      "single_trade_", "lifecycle_",
                                      "lesson_", "correlation_")):
                violations.add(node.value)
    assert len(violations) >= 16, (
        f"Expected >= 16 distinct check violation tokens, found {len(violations)}: "
        f"{sorted(violations)}"
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B01", "risk_engine_contract", "§3 persistence", "L2b",
              "Every evaluate_proposal persists exactly one RiskDecision row"),
)
def test_risk_always_persists_decision(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(p)
    before = len(repo.list_risk_decisions(limit=100))
    evaluate_proposal(p)
    after = len(repo.list_risk_decisions(limit=100))
    assert after == before + 1


@spec_claim(
    SpecClaim("RE-B02", "risk_engine_contract", "§2 check #0", "L2b",
              "Kill switch active → all proposals rejected"),
)
def test_risk_kill_switch_blocks(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.risk.kill_switch import activate, deactivate

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()

    activate("test", actor="human")
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not result.approved
    assert any("kill_switch" in v for v in result.violations)
    deactivate(actor="human")


# ---------------------------------------------------------------------------
# L2c — Invariants
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-C01", "risk_engine_contract", "§6 restricted tools", "L2c",
              "RESTRICTED_TOOLS never permitted regardless of permission_mode"),
)
def test_restricted_tools_never_permitted():
    from aletheia.agent_runtime.tool_registry import RESTRICTED_TOOLS, init_tool_registry
    from aletheia.agent_runtime.permissions import is_tool_permitted

    init_tool_registry()
    for mode in ("Observe", "Paper", "Full"):
        for tool in RESTRICTED_TOOLS:
            assert not is_tool_permitted(tool, mode), (
                f"INVARIANT VIOLATION: {tool} permitted in {mode}"
            )

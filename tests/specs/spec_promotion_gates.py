"""Spec: Promotion Gates Contract (promotion_gates_contract.md)

Strategy lifecycle is a forward-only state machine with quantitative gates.
"""

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PG-S01", "promotion_gates_contract", "§1 states", "L2a",
        "LIFECYCLE_STATES contains exactly 10 named states in defined order",
    ),
)
def test_lifecycle_states_structure():
    from aletheia.trading.strategy_lifecycle import LIFECYCLE_STATES

    assert len(LIFECYCLE_STATES) == 10
    expected = [
        "draft", "research_note", "strategy_hypothesis", "paper_candidate",
        "paper_running", "paper_passed", "live_candidate", "live_limited",
        "retired", "deprecated",
    ]
    assert LIFECYCLE_STATES == expected


@spec_claim(
    SpecClaim(
        "PG-S02", "promotion_gates_contract", "§1 transitions", "L2a",
        "FORWARD_TRANSITIONS covers all non-terminal states",
    ),
)
def test_forward_transitions_complete():
    from aletheia.trading.strategy_lifecycle import (
        FORWARD_TRANSITIONS,
        LIFECYCLE_STATES,
    )

    non_terminal = set(LIFECYCLE_STATES) - {"deprecated"}
    assert non_terminal.issubset(set(FORWARD_TRANSITIONS.keys()))


@spec_claim(
    SpecClaim(
        "PG-S03", "promotion_gates_contract", "§2 default gates", "L2a",
        "DEFAULT_PROMOTION_GATES contains 7 gate keys with exact contract values",
    ),
)
def test_default_promotion_gate_values():
    from aletheia.trading.strategy_lifecycle import DEFAULT_PROMOTION_GATES

    assert DEFAULT_PROMOTION_GATES["min_paper_runtime_days"] == 14
    assert DEFAULT_PROMOTION_GATES["min_paper_trades"] == 30
    assert DEFAULT_PROMOTION_GATES["positive_expectancy_required"] is True
    assert DEFAULT_PROMOTION_GATES["max_drawdown_pct_allowed"] == 5.0
    assert DEFAULT_PROMOTION_GATES["max_consecutive_losses_allowed"] == 5
    assert DEFAULT_PROMOTION_GATES["critical_risk_violations_allowed"] == 0
    assert DEFAULT_PROMOTION_GATES["post_trade_reviews_required"] is True


@spec_claim(
    SpecClaim(
        "PG-S04", "promotion_gates_contract", "§3 critical tokens", "L2a",
        "CRITICAL_RISK_TOKENS set contains the expected risk violation tokens",
    ),
)
def test_critical_risk_tokens():
    from aletheia.trading.strategy_lifecycle import CRITICAL_RISK_TOKENS

    required = {
        "kill_switch_active",
        "max_daily_loss_exceeded",
        "stale_market_data",
        "strategy_not_live_eligible",
    }
    assert required.issubset(CRITICAL_RISK_TOKENS)


# ---------------------------------------------------------------------------
# L2b — Behavioral claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PG-B01", "promotion_gates_contract", "§1 forward-only", "L2b",
        "promote_strategy() rejects invalid state transitions",
    ),
)
def test_invalid_transition_rejected(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.trading.strategy_lifecycle import promote_strategy

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

    from aletheia.storage.models import StrategyRecord
    from aletheia.storage import repositories as repo

    strategy = StrategyRecord(name="test_strat", state="draft")
    repo.insert_strategy(strategy)

    result = promote_strategy(strategy.id, "live_limited")
    assert result.get("error") or result.get("success") is False or \
        "not allowed" in str(result).lower() or "invalid" in str(result).lower(), \
        "draft -> live_limited should be rejected (skip states not allowed)"


@spec_claim(
    SpecClaim(
        "PG-B02", "promotion_gates_contract", "§2 gate computation", "L2b",
        "check_promotion_gates() returns failures for insufficient paper trades",
    ),
)
def test_gates_fail_on_insufficient_trades(tmp_path, monkeypatch):
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.migrations import run_migrations
    from aletheia.trading.strategy_lifecycle import check_promotion_gates

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

    from aletheia.storage.models import StrategyRecord
    from aletheia.storage import repositories as repo

    strategy = StrategyRecord(name="gate_test", state="paper_running")
    repo.insert_strategy(strategy)

    result = check_promotion_gates(strategy.id)
    assert result["passed"] is False
    failure_str = " ".join(result.get("failures", []))
    assert "insufficient" in failure_str or "no_strategy" in failure_str


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PG-C01", "promotion_gates_contract", "§2 from records", "L2c",
        "Gates compute from stored records (orders/fills/positions), not status labels",
    ),
)
def test_gates_use_repo_queries():
    """Verify check_promotion_gates imports and calls repository query functions."""
    import ast
    from pathlib import Path

    src = Path(__file__).parent.parent.parent / "aletheia" / "trading" / "strategy_lifecycle.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imports.add(alias.name)

    required_queries = {
        "list_strategy_orders",
        "list_strategy_fills",
        "list_strategy_positions",
        "list_strategy_risk_decisions",
    }
    missing = required_queries - imports
    assert not missing, (
        f"INVARIANT: check_promotion_gates must use repo queries, missing: {missing}"
    )

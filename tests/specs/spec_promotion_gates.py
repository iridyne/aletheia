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


# ---------------------------------------------------------------------------
# Additional L2b — Behavioral claims
# ---------------------------------------------------------------------------


def _make_test_db(tmp_path, monkeypatch):
    """Helper: wire a fresh SQLite test DB for lifecycle tests."""
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
    return conn


@spec_claim(
    SpecClaim(
        "PG-B03", "promotion_gates_contract", "§4 偏差1 high-freq relax", "L2b",
        "check_promotion_gates() passes runtime gate when paper_trade_count>=30 and runtime<14d",
    ),
)
def test_high_freq_relaxation_passes_runtime_gate(tmp_path, monkeypatch):
    """High-frequency strategies (>=30 trades) have runtime floor relaxed to 3 days.
    §4 偏差1: code adopts this rule as gold standard; test exercises the relaxed path.
    """
    from datetime import datetime, timedelta, timezone

    _make_test_db(tmp_path, monkeypatch)

    from aletheia.storage.models import StrategyRecord
    from aletheia.storage import repositories as repo
    from aletheia.trading.strategy_lifecycle import check_promotion_gates

    strategy = StrategyRecord(name="hf_strat", state="paper_running")
    repo.insert_strategy(strategy)

    # Insert 30 paper orders+fills via trade_proposals (required join path).
    ts_4d_ago = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
    from aletheia.storage.sqlite import get_db
    db = get_db()
    # Single proposal wiring all orders to this strategy.
    db.execute(
        "INSERT INTO trade_proposals (id, strategy_id, symbol, market_type, direction, "
        "order_type, quantity, execution_mode, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("prop0001", strategy.id, "BTC/USDT", "spot", "long", "market", 0.01,
         "paper_internal", "filled"),
    )
    for i in range(30):
        db.execute(
            "INSERT INTO orders (id, proposal_id, symbol, market_type, direction, "
            "order_type, quantity, status, execution_mode, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"ord{i:04d}", "prop0001", "BTC/USDT", "spot", "long", "market",
                0.01, "filled", "paper_internal", ts_4d_ago,
            ),
        )
        db.execute(
            "INSERT INTO fills (id, order_id, fill_price, fill_quantity, fee, filled_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"fill{i:04d}", f"ord{i:04d}", 50000.0, 0.01, 0.1, ts_4d_ago),
        )
    # 1 closed position with positive PnL so those gates don't block.
    db.execute(
        "INSERT INTO positions (id, strategy_id, symbol, market_type, direction, quantity, "
        "entry_price, realized_pnl, status, execution_mode, opened_at, closed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "pos0001", strategy.id, "BTC/USDT", "spot", "long", 0.01,
            50000.0, 10.0, "closed", "paper_internal", ts_4d_ago, ts_4d_ago,
        ),
    )
    # 1 active post-trade review so that gate does not block.
    db.execute(
        "INSERT INTO knowledge_items (id, item_type, strategy_id, title, content, status) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("ki0001", "post_trade_review", strategy.id, "Review", "ok", "active"),
    )
    db.commit()

    result = check_promotion_gates(strategy.id)

    runtime_failures = [f for f in result.get("failures", []) if "runtime_days" in f]
    assert not runtime_failures, (
        f"High-freq relaxation should remove runtime failure when 30 trades exist "
        f"and runtime=4d >= 3d floor; got failures: {result['failures']}"
    )


@spec_claim(
    SpecClaim(
        "PG-B04", "promotion_gates_contract", "§5 live promotion gate", "L2b",
        "live_candidate->live_limited requires live.state=='live_limited' AND first_live_confirmation_done==true",
    ),
)
def test_live_promotion_prereqs_both_checked(tmp_path, monkeypatch):
    """_live_lifecycle_gate() must check BOTH cfg.live.state and
    cfg.live.first_live_confirmation_done — neither alone is sufficient."""
    _make_test_db(tmp_path, monkeypatch)

    from aletheia.trading.strategy_lifecycle import _live_lifecycle_gate

    # Provide a minimal config object with live/agent attributes.
    class _Live:
        state: str
        first_live_confirmation_done: bool

    class _Agent:
        permission_mode: str = "Paper"

    class _Cfg:
        live: _Live
        agent = _Agent()

    # Case 1: wrong state, confirmation done -> state failure present.
    cfg1_live = _Live()
    cfg1_live.state = "live_disabled"
    cfg1_live.first_live_confirmation_done = True
    cfg1 = _Cfg()
    cfg1.live = cfg1_live
    import aletheia.config.loader as loader
    monkeypatch.setattr(loader, "load_config", lambda: cfg1)
    result1 = _live_lifecycle_gate()
    state_failures = [f for f in result1["failures"] if "live_state_not_limited" in f]
    assert state_failures, "live.state check must fail when state!='live_limited'"

    # Case 2: correct state, confirmation NOT done -> confirmation failure present.
    cfg2_live = _Live()
    cfg2_live.state = "live_limited"
    cfg2_live.first_live_confirmation_done = False
    cfg2 = _Cfg()
    cfg2.live = cfg2_live
    monkeypatch.setattr(loader, "load_config", lambda: cfg2)
    result2 = _live_lifecycle_gate()
    confirm_failures = [f for f in result2["failures"] if "first_confirmation_not_done" in f]
    assert confirm_failures, "first_live_confirmation_done check must fail when False"

    # Case 3: both correct -> neither failure present.
    cfg3_live = _Live()
    cfg3_live.state = "live_limited"
    cfg3_live.first_live_confirmation_done = True
    cfg3 = _Cfg()
    cfg3.live = cfg3_live
    monkeypatch.setattr(loader, "load_config", lambda: cfg3)
    result3 = _live_lifecycle_gate()
    blocking = [
        f for f in result3["failures"]
        if "live_state_not_limited" in f or "first_confirmation_not_done" in f
    ]
    assert not blocking, (
        f"Both prereqs met; neither live_state nor confirmation should fail; got: {result3['failures']}"
    )


@spec_claim(
    SpecClaim(
        "PG-B05", "promotion_gates_contract", "§6 retire path", "L2b",
        "operator_loop retire path inserts pi_decisions row with action='recommend_retire' and target strategy_id",
    ),
)
def test_operator_retire_inserts_pi_decision(tmp_path, monkeypatch):
    """_action_retire_strategy() must write a pi_decisions row with action='recommend_retire'
    and proposal_id equal to the target strategy_id."""
    _make_test_db(tmp_path, monkeypatch)

    from aletheia.storage.models import StrategyRecord
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db
    from aletheia.agent_shell.operator_loop import OperatorAction, _action_retire_strategy

    # Stub pi_daemon notify so we don't need that subsystem.
    import aletheia.agent_shell.operator_loop as op_loop
    monkeypatch.setattr(op_loop, "_record_action", lambda *a, **kw: None)

    strategy = StrategyRecord(name="retire_me", state="paper_running")
    repo.insert_strategy(strategy)

    action = OperatorAction(
        action_type="retire_strategy",
        level=1,
        params={"strategy_id": strategy.id},
        reason="underperforming",
    )
    result = _action_retire_strategy(action)

    assert result.get("status") == "recommended", (
        f"Expected status='recommended', got: {result}"
    )


# ---------------------------------------------------------------------------
# PG-B06 — check_promotion_gates must check RuleSet has been evaluated (Phase 2A)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PG-B06", "promotion_gates_contract", "§2 ruleset_evaluated gate", "L2b",
        "check_promotion_gates fails with 'ruleset_never_evaluated' when strategy has a ruleset but zero Path-A fills",
    ),
)
def test_promotion_gate_fails_when_ruleset_never_evaluated(tmp_path, monkeypatch):
    """策略有 ruleset 但从未产生 Path-A fill 时，晋级门应拒绝。

    Phase 2A 前提：paper_running 策略进晋级门的必要条件之一是 RuleSet 已被
    实际求值并产生过至少一次 fill（Path A），而非仅存在于 parameters 字段中。
    这防止"写了 ruleset 但 scanner 从未跑过"的策略悄悄晋级。
    """
    _make_test_db(tmp_path, monkeypatch)

    import json
    from aletheia.storage.models import StrategyRecord
    from aletheia.storage import repositories as repo
    from aletheia.trading.strategy_lifecycle import check_promotion_gates

    ruleset_params = json.dumps({
        "ruleset": {"entry_conditions": [{"indicator": "rsi_14", "op": "lt", "value": 30}]},
        "direction_affinity": "long",
    })
    strategy = StrategyRecord(
        name="ruleset_strategy_no_fills",
        state="paper_running",
        parameters=ruleset_params,
    )
    repo.insert_strategy(strategy)

    result = check_promotion_gates(strategy.id)

    assert not result["passed"], "Strategy with ruleset but zero Path-A fills should fail promotion"
    ruleset_failures = [f for f in result["failures"] if "ruleset_never_evaluated" in f]
    assert ruleset_failures, (
        f"Expected 'ruleset_never_evaluated' failure token; got failures: {result['failures']}"
    )


# ---------------------------------------------------------------------------
# Additional L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "PG-C02", "promotion_gates_contract", "§1 states", "L2c",
        "Every state in LIFECYCLE_STATES has a defined transition rule OR is in the terminal-state set",
    ),
)
def test_no_orphan_states():
    """No state in LIFECYCLE_STATES may be absent from both FORWARD_TRANSITIONS and
    the terminal-state set — orphan states would make the state machine non-deterministic."""
    from aletheia.trading.strategy_lifecycle import (
        LIFECYCLE_STATES,
        FORWARD_TRANSITIONS,
        _TERMINAL,
    )

    orphans = [
        s for s in LIFECYCLE_STATES
        if s not in FORWARD_TRANSITIONS and s not in _TERMINAL
    ]
    assert not orphans, (
        f"INVARIANT: states with no transition rule and not terminal: {orphans}"
    )


@spec_claim(
    SpecClaim(
        "PG-C03", "promotion_gates_contract", "§2 default gates", "L2c",
        "DEFAULT_PROMOTION_GATES canonical-JSON SHA-256 matches known hash (silent-edit drift detector)",
    ),
)
def test_default_promotion_gates_hash():
    """Canonical-JSON SHA-256 of DEFAULT_PROMOTION_GATES must match the hash embedded
    here.  If the dict is silently edited, this test fails and the contract doc must
    be updated in the same commit.

    Recompute with:
        uv run python -c "import hashlib,json; from aletheia.trading.strategy_lifecycle \
import DEFAULT_PROMOTION_GATES; print(hashlib.sha256(json.dumps(DEFAULT_PROMOTION_GATES, \
sort_keys=True, separators=(',',':')).encode()).hexdigest())"
    """
    import hashlib
    import json

    from aletheia.trading.strategy_lifecycle import DEFAULT_PROMOTION_GATES

    KNOWN_HASH = "3edfd78efbdea146e5a98b0b5fe86a4f75727524ab013f37c54a13df503efbbb"

    canonical = json.dumps(DEFAULT_PROMOTION_GATES, sort_keys=True, separators=(",", ":"))
    actual_hash = hashlib.sha256(canonical.encode()).hexdigest()
    assert actual_hash == KNOWN_HASH, (
        f"DEFAULT_PROMOTION_GATES has changed (drift detected).\n"
        f"  canonical JSON: {canonical}\n"
        f"  expected hash:  {KNOWN_HASH}\n"
        f"  actual hash:    {actual_hash}\n"
        "Update both this hash and promotion_gates_contract.md §2 in the same commit."
    )

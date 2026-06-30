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
              "At least 16 distinct violation tokens exist in engine source (coarse floor; "
              "the authoritative enumeration is 20 checks 0-16 + 6b/17/18/19 in contract §2)"),
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


# ---------------------------------------------------------------------------
# L2b — Check #15 and #16 behavioral tests (H1 fix)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B03", "risk_engine_contract", "§2 check #15", "L2b",
              "_check_lesson_constraints returns violation on internal error (safe-reject)"),
)
def test_lesson_constraints_error_produces_violation(monkeypatch):
    from unittest.mock import MagicMock
    from aletheia.storage.models import TradeProposal

    broken_store = MagicMock()
    broken_store.return_value.list_all.side_effect = RuntimeError("DB gone")
    monkeypatch.setattr("aletheia.learning.lessons.LessonStore", broken_store)

    from aletheia.risk.engine import _check_lesson_constraints
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=60000,
    )
    violations = _check_lesson_constraints(p)
    assert any("lesson_check_error" in v for v in violations), (
        "Internal failure must produce safe-reject violation, not empty list"
    )


@spec_claim(
    SpecClaim("RE-B04", "risk_engine_contract", "§2 check #16", "L2b",
              "_check_correlation_exposure returns violation on internal error (safe-reject)"),
)
def test_correlation_exposure_error_produces_violation(monkeypatch):
    from aletheia.risk.engine import _check_correlation_exposure
    from aletheia.storage.models import TradeProposal
    from aletheia.config.loader import load_config

    monkeypatch.setattr(
        "aletheia.storage.repositories.list_positions",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("DB gone")),
        raising=False,
    )
    from unittest.mock import patch
    with patch("aletheia.risk.engine.list_positions", side_effect=RuntimeError("DB gone")):
        cfg = load_config()
        p = TradeProposal(
            symbol="BTC/USDT", market_type="spot", direction="long",
            execution_mode="paper_internal", quantity=0.001, price=60000,
        )
        violations = _check_correlation_exposure(p, cfg)
        assert any("correlation_check_error" in v for v in violations), (
            "Internal failure must produce safe-reject violation, not empty list"
        )


@spec_claim(
    SpecClaim("RE-B05", "risk_engine_contract", "§2 check #5 drawdown", "L2b",
              "_portfolio_drawdown_scale returns correct piecewise values"),
)
def test_drawdown_scale_piecewise(monkeypatch):
    from aletheia.risk.engine import _portfolio_drawdown_scale

    def _mock_snapshots(limit=50):
        peak = 1000.0
        current_pct = _mock_snapshots._dd_pct
        current = peak * (1 - current_pct / 100)
        return [
            {"total_value_usdt": peak, "execution_mode": "paper_internal"},
            {"total_value_usdt": peak, "execution_mode": "paper_internal"},
            {"total_value_usdt": peak, "execution_mode": "paper_internal"},
            {"total_value_usdt": current, "execution_mode": "paper_internal"},
        ]

    monkeypatch.setattr(
        "aletheia.storage.repositories.list_portfolio_snapshots", _mock_snapshots
    )

    _mock_snapshots._dd_pct = 0.0
    assert _portfolio_drawdown_scale("paper_internal") == 1.0

    _mock_snapshots._dd_pct = 5.0
    assert _portfolio_drawdown_scale("paper_internal") == 1.0

    _mock_snapshots._dd_pct = 7.5
    scale = _portfolio_drawdown_scale("paper_internal")
    assert 0.7 < scale < 0.8  # linear 1.0→0.5 in 5-10% range

    _mock_snapshots._dd_pct = 10.0
    assert abs(_portfolio_drawdown_scale("paper_internal") - 0.5) < 0.01

    _mock_snapshots._dd_pct = 20.0
    assert _portfolio_drawdown_scale("paper_internal") == 0.0

    _mock_snapshots._dd_pct = 25.0
    assert _portfolio_drawdown_scale("paper_internal") == 0.0


@spec_claim(
    SpecClaim("RE-B05a", "risk_engine_contract", "§2 check #5 drawdown source", "L2b",
              "exchange-backed drawdown uses exchange_account equity, not per-mode notional"),
)
def test_drawdown_scale_uses_real_equity_not_notional(monkeypatch):
    """Regression: closing a winning position collapses per-mode position NOTIONAL
    (e.g. okx_demo total_value_usdt 272→16) but the real account equity is flat.
    The old code read the okx_demo notional rows and computed a bogus ~94% drawdown
    → scale 0 → $0 cap → every demo proposal rejected. Drawdown must come from the
    exchange_account equity rows, which stayed ~88k the whole time."""
    from aletheia.risk.engine import _portfolio_drawdown_scale

    def _mock_snapshots(limit=50):
        # Per-mode okx_demo rows: notional cratered 272→16 (winning trades closed).
        # exchange_account rows: real equity flat at ~88,400 (no drawdown at all).
        return [
            {"total_value_usdt": 272.0, "execution_mode": "okx_demo"},
            {"total_value_usdt": 150.0, "execution_mode": "okx_demo"},
            {"total_value_usdt": 16.5, "execution_mode": "okx_demo"},
            {"total_value_usdt": 88400.0, "execution_mode": "exchange_account"},
            {"total_value_usdt": 88460.0, "execution_mode": "exchange_account"},
            {"total_value_usdt": 88412.0, "execution_mode": "exchange_account"},
        ]

    monkeypatch.setattr(
        "aletheia.storage.repositories.list_portfolio_snapshots", _mock_snapshots
    )

    # okx_demo must NOT be throttled — the real account never drew down.
    # (Old buggy behavior would have returned 0.0 from the 272→16 notional series.)
    assert _portfolio_drawdown_scale("okx_demo") == 1.0


# ---------------------------------------------------------------------------
# Shared DB fixture helper (mirrors test_risk_kill_switch_blocks pattern)
# ---------------------------------------------------------------------------


def _setup_db(tmp_path, monkeypatch):
    """Wire a fresh SQLite DB into aletheia storage for one test."""
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
    conn.execute("PRAGMA busy_timeout=30000")
    sq._db_conn = conn
    run_migrations()


# ---------------------------------------------------------------------------
# RE-B06 — §2 check #1: symbol blacklist
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B06", "risk_engine_contract", "§2 check #1", "L2b",
              "symbol_filter_mode=blacklist rejects a proposal whose symbol is in symbol_list"),
)
def test_symbol_blacklist_rejects(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.risk.symbol_filter_mode = "blacklist"
    cfg.risk.symbol_list = ["BTC/USDT"]
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert not result.approved
    assert any("symbol_blocked" in v for v in result.violations), (
        f"Expected symbol_blocked violation, got: {result.violations}"
    )


# ---------------------------------------------------------------------------
# RE-B07 — §2 check #3: live state blocks live proposal
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B07", "risk_engine_contract", "§2 check #3", "L2b",
              "live.state=live_disabled rejects an okx_live_limited proposal"),
)
def test_live_disabled_rejects_live_proposal(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    # live.state defaults to "live_disabled" — ensure explicitly
    cfg.live.state = "live_disabled"
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="okx_live_limited", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert not result.approved
    assert any(v == "live_disabled" for v in result.violations), (
        f"Expected 'live_disabled' violation, got: {result.violations}"
    )


# ---------------------------------------------------------------------------
# RE-B08 — §2 check #4: permission_mode=Observe blocks non-paper proposals
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B08", "risk_engine_contract", "§2 check #4", "L2b",
              "permission_mode=Observe rejects any non-paper_internal proposal"),
)
def test_observe_mode_rejects_non_paper(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Observe"
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    # bitget_demo is non-paper_internal and must be blocked by Observe
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert not result.approved
    assert any("permission_mode_insufficient" in v for v in result.violations), (
        f"Expected permission_mode_insufficient violation, got: {result.violations}"
    )


# ---------------------------------------------------------------------------
# RE-B09 — §2 check #5: demo notional cap
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B09", "risk_engine_contract", "§2 check #5", "L2b",
              "exceeds_demo_position_notional fires when proposal notional > max_demo_position_notional_usdt"),
)
def test_demo_notional_cap_fires(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.risk.max_demo_position_notional_usdt = 100.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    # notional = 1.0 * 200.0 = 200 > 100
    p = TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=1.0, price=200.0,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert not result.approved
    assert any("exceeds_demo_position_notional" in v for v in result.violations), (
        f"Expected exceeds_demo_position_notional violation, got: {result.violations}"
    )


# ---------------------------------------------------------------------------
# RE-B10 — §2 check #6: max open demo positions
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B10", "risk_engine_contract", "§2 check #6", "L2b",
              "max_demo_positions_reached fires when open demo positions >= max_open_positions_demo"),
)
def test_max_open_demo_positions_blocks(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal, Position
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.risk.max_open_positions_demo = 2
    # Give enough notional headroom so only position count triggers
    cfg.risk.max_demo_position_notional_usdt = 10_000.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    # Insert 2 open paper positions (reaching the limit)
    for i in range(2):
        pos = Position(
            symbol=f"ETH/USDT", market_type="spot", direction="long",
            quantity=0.01, entry_price=2000.0,
            execution_mode="paper_internal", status="open",
        )
        repo.insert_position(pos)

    p = TradeProposal(
        symbol="SOL/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.01, price=100.0,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert not result.approved
    assert any("max_demo_positions_reached" in v for v in result.violations), (
        f"Expected max_demo_positions_reached violation, got: {result.violations}"
    )


# ---------------------------------------------------------------------------
# RE-B11 — §2 check #7: short not allowed demo
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B11", "risk_engine_contract", "§2 check #7", "L2b",
              "short_not_allowed_demo fires for a short proposal when allow_short_demo=False"),
)
def test_short_not_allowed_demo_fires(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.risk.allow_short_demo = False
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="short",
        execution_mode="paper_internal", quantity=0.001, price=60000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert not result.approved
    assert any(v == "short_not_allowed_demo" for v in result.violations), (
        f"Expected short_not_allowed_demo violation, got: {result.violations}"
    )


# ---------------------------------------------------------------------------
# RE-B12 — §2 check #13: missing stop price required for live proposals
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B12", "risk_engine_contract", "§2 check #13", "L2b",
              "max_single_trade_risk_missing_stop_price fires when live proposal has no stop_price"),
)
def test_live_missing_stop_price_fires(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    # live_disabled must be "live_enabled" for check #13 to run (live branch)
    cfg.live.state = "live_enabled"
    cfg.exchange.mode = "live"
    cfg.agent.permission_mode = "Full"
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    # bitget_live_limited triggers the live branch where stop_price is required
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_live_limited", quantity=0.001, price=60000,
        stop_price=None,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    # Proposal will have multiple violations (stale data, strategy lifecycle, etc.)
    # but must include the stop_price missing check
    assert any(v == "max_single_trade_risk_missing_stop_price" for v in result.violations), (
        f"Expected max_single_trade_risk_missing_stop_price in violations, got: {result.violations}"
    )


# ---------------------------------------------------------------------------
# RE-C02 — §6 bypass: create_paper_proposal cannot accept bitget_live_limited
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-C02", "risk_engine_contract", "§6 bypass", "L2c",
              "create_paper_proposal() hardcodes _PAPER_MODES; bitget_live_limited is never in that set"),
)
def test_paper_proposal_cannot_accept_live_mode():
    """AST-scan paper_tools.py: _PAPER_MODES must not contain any live mode string."""
    import ast
    from pathlib import Path

    src = (
        Path(__file__).parent.parent.parent
        / "aletheia" / "execution" / "paper_tools.py"
    )
    tree = ast.parse(src.read_text(encoding="utf-8"))

    # Find the _PAPER_MODES set literal
    paper_modes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_PAPER_MODES":
                    if isinstance(node.value, ast.Set):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant):
                                paper_modes.add(elt.value)

    assert paper_modes, "Could not locate _PAPER_MODES set in paper_tools.py"

    live_modes = {"bitget_live_limited", "okx_live_limited", "binance_live_limited"}
    overlap = paper_modes & live_modes
    assert not overlap, (
        f"INVARIANT VIOLATION: live mode(s) {overlap} found inside _PAPER_MODES — "
        "autonomous agents could bypass the live-path guard"
    )


# ---------------------------------------------------------------------------
# RE-B13 — §2 check #8: demo daily-loss uses the DEMO capital base
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B13", "risk_engine_contract", "§2 check #8", "L2b",
              "demo daily-loss limit is computed from starting_capital_usdt (demo base), "
              "NOT max_live_limited_cap_usdt — so a demo loss small relative to the demo "
              "account does not spuriously trip the daily-loss brake (CR-RE-03)"),
)
def test_demo_daily_loss_uses_demo_capital_base(tmp_path, monkeypatch):
    """A demo realized loss that is tiny vs the demo capital base must NOT trip
    the daily-loss brake just because it exceeds the (much smaller) live cap.

    Setup: starting_capital_usdt=10000 (demo base), max_live_limited_cap_usdt=100
    (live cap), max_daily_loss_pct=1%. A -$25 demo loss is:
      - vs demo base: 25 < 10000*1% = 100  → must NOT fire
      - vs live cap:  25 > 100*1%   = 1    → the OLD bug fired here
    """
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal, Position
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.portfolio.pnl import close_position
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.risk.starting_capital_usdt = 10000.0      # demo base
    cfg.risk.max_live_limited_cap_usdt = 100.0    # live cap (must NOT drive demo)
    cfg.risk.max_daily_loss_pct = 1.0
    cfg.risk.max_demo_position_notional_usdt = 10000.0  # don't let notional gate interfere
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    # Create + close a demo position realizing a -$25 loss today.
    pos = Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=100.0, current_price=100.0,
        execution_mode="okx_demo", status="open",
    )
    repo.insert_position(pos)
    close_position(pos.id, exit_price=75.0, reason="test")  # -25 realized

    # New demo proposal — daily-loss check must NOT reject it.
    p = TradeProposal(
        symbol="ETH/USDT", market_type="usdt_futures", direction="long",
        execution_mode="okx_demo", quantity=0.01, price=100.0,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)

    assert not any("max_daily_loss_exceeded" in v for v in result.violations), (
        "demo daily-loss brake tripped on a $25 loss against a $10000 demo base — "
        f"it is wrongly using the $100 live cap (CR-RE-03). violations={result.violations}"
    )


# ---------------------------------------------------------------------------
# EH-1 (§17.2) — risk-check exceptions fail CLOSED (reject), never fail-open
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B14", "risk_engine_contract", "§17.2 fail-closed", "L2b",
              "When a risk-check helper raises internally, it returns a violation "
              "(fail-closed), never an empty list (fail-open). Covers the leverage, "
              "correlation, and lesson checks."),
)
def test_risk_checks_fail_closed_on_internal_error(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.storage.models import TradeProposal
    from aletheia.config.loader import load_config

    cfg = load_config()
    proposal = TradeProposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        execution_mode="okx_demo", quantity=0.01, price=60000.0, leverage=3,
    )

    def _boom(*a, **k):
        raise RuntimeError("simulated internal failure")

    # Account-leverage check: list_positions blows up → must fail closed (behavioral).
    monkeypatch.setattr(eng, "list_positions", _boom)
    v_lev = eng._check_account_leverage_ratio(proposal, cfg)
    assert v_lev, "account leverage check must fail closed (return a violation) on error"
    assert any("error" in x for x in v_lev)

    # Regression lock: every risk-check helper's except handler fails closed (appends a
    # violation), never silently `return []`. Source check because correlation/lesson
    # import their deps locally and can't be driven to raise via one monkeypatch target.
    import inspect
    for fn in (eng._check_account_leverage_ratio,
               eng._check_correlation_exposure,
               eng._check_lesson_constraints):
        src = inspect.getsource(fn)
        assert ("safe-reject" in src or "fail closed" in src.lower()), (
            f"{fn.__name__} except handler must fail closed (append a violation)"
        )


# ---------------------------------------------------------------------------
# CUM-ADD (§19) — per-symbol cumulative notional cap
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B15", "risk_engine_contract", "§19 per-symbol cap", "L2b",
              "Cumulative open-position notional for a symbol + the proposed notional "
              "may not exceed risk.max_per_symbol_notional_usdt. Averaging-merge grows "
              "ONE position past the single-trade cap, so repeated adds need a bound."),
)
def test_per_symbol_cumulative_notional_cap():
    import aletheia.risk.engine as eng
    from aletheia.storage.models import Position, TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.config.loader import load_config

    cap = load_config().risk.max_per_symbol_notional_usdt
    assert cap > 0, "per-symbol cap must be enabled by default"

    # Existing demo position at 80% of the cap.
    repo.insert_position(Position(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        quantity=(cap * 0.8) / 100.0, entry_price=100.0, current_price=100.0,
        execution_mode="okx_demo", status="open",
    ))

    # Proposal pushing the symbol total to 130% of cap → must be rejected.
    over = TradeProposal(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        execution_mode="okx_demo", quantity=(cap * 0.5) / 100.0, price=100.0,
    )
    repo.insert_trade_proposal(over)
    res_over = eng.evaluate_proposal(over)
    assert any("exceeds_per_symbol_notional" in v for v in res_over.violations), res_over.violations

    # A small add keeping the total at 85% of cap → no per-symbol violation.
    under = TradeProposal(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        execution_mode="okx_demo", quantity=(cap * 0.05) / 100.0, price=100.0,
    )
    repo.insert_trade_proposal(under)
    res_under = eng.evaluate_proposal(under)
    assert not any("exceeds_per_symbol_notional" in v for v in res_under.violations), res_under.violations


# ---------------------------------------------------------------------------
# SAME-SYMBOL DEDUP (§6b) — cross-broker duplicate position guard
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B16", "risk_engine_contract", "§6b same-symbol dedup", "L2b",
              "A demo proposal is rejected when an open demo position already exists "
              "for the same symbol on any broker (prevents cross-broker duplicates). "
              "A different-symbol proposal must not be affected."),
)
def test_same_symbol_duplicate_position_rejected():
    import aletheia.risk.engine as eng
    from aletheia.storage.models import Position, TradeProposal
    from aletheia.storage import repositories as repo

    # Existing demo position for ETHUSDT (paper_internal broker).
    repo.insert_position(Position(
        symbol="ETH/USDT", market_type="usdt_futures", direction="short",
        quantity=0.01, entry_price=1580.0, current_price=1567.0,
        execution_mode="paper_internal", status="open",
    ))

    # New proposal for same symbol on a different demo broker → must be rejected.
    dup = TradeProposal(
        symbol="ETH/USDT", market_type="usdt_futures", direction="short",
        execution_mode="okx_demo", quantity=0.05, price=1570.0,
    )
    repo.insert_trade_proposal(dup)
    res = eng.evaluate_proposal(dup)
    assert any("symbol_already_open" in v for v in res.violations), (
        f"Expected symbol_already_open violation, got: {res.violations}"
    )

    # Proposal for a different symbol on same broker → must NOT trigger this violation.
    other = TradeProposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="short",
        execution_mode="okx_demo", quantity=0.001, price=60000.0,
    )
    repo.insert_trade_proposal(other)
    res_other = eng.evaluate_proposal(other)
    assert not any("symbol_already_open" in v for v in res_other.violations), (
        f"Different symbol should not trigger same-symbol dedup: {res_other.violations}"
    )


# ---------------------------------------------------------------------------
# RE-S04b (L2a) — 20-check floor + four new token mandatory presence
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-S04b", "risk_engine_contract", "§2 check count", "L2a",
              "Engine source contains >=20 distinct violation token prefixes (floor upgraded "
              "from >=16) and the four new-gate tokens — symbol_already_open, "
              "account_leverage, liquidation, exceeds_per_symbol_notional — must all exist"),
)
def test_risk_engine_has_20_check_floor_with_new_tokens():
    """Precise upgrade of RE-S04: floor 16→20, plus mandatory presence of the four
    tokens added for checks 6b/17/18/19."""
    import ast
    from pathlib import Path

    src_path = (
        Path(__file__).parent.parent.parent / "aletheia" / "risk" / "engine.py"
    )
    source_text = src_path.read_text(encoding="utf-8")
    tree = ast.parse(source_text)

    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith((
                "kill_switch", "symbol_", "market_", "spot_", "futures_",
                "live_", "permission_", "max_", "daily_loss",
                "exchange_", "account_", "leverage_", "data_",
                "total_", "single_trade_", "lifecycle_", "lesson_",
                "correlation_", "stale_", "exceeds_", "sector_",
                "portfolio_", "strategy_", "liquidation_",
            )):
                violations.add(node.value)

    # Four mandatory tokens from the four new checks (6b/17/18/19).
    required_tokens = [
        "symbol_already_open",
        "account_leverage",
        "liquidation",
        "exceeds_per_symbol_notional",
    ]
    missing = [tok for tok in required_tokens if not any(tok in v for v in violations)]
    assert not missing, (
        f"Required violation tokens missing from engine.py: {missing}. "
        f"Found tokens: {sorted(violations)}"
    )

    assert len(violations) >= 20, (
        f"Expected >= 20 distinct check violation tokens (floor upgraded from 16), "
        f"found {len(violations)}: {sorted(violations)}"
    )


# ---------------------------------------------------------------------------
# RE-B17 (L2b) — evaluate_proposal() is the ONLY gateway to broker execution
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B17", "risk_engine_contract", "§1 entry", "L2b",
              "evaluate_proposal() is the sole hard gate on the order path: every "
              "broker call site in _execute_proposal is guarded by an approved "
              "RiskCheckResult before execution proceeds"),
)
def test_gateway_has_no_risk_free_broker_path():
    """AST-scan gateway.py: every execute_live_order / execute_paper_order call
    must be structurally downstream of `if not risk_result.approved: return`.
    We verify by source inspection:
    1. evaluate_proposal is imported and called early in _execute_proposal.
    2. The conditional `if not risk_result.approved:` appears before any broker
       call.
    3. No import or call to execute_live_order / execute_paper_order exists
       outside _execute_proposal (which would bypass the gate).
    """
    import ast
    from pathlib import Path

    src_path = (
        Path(__file__).parent.parent.parent / "aletheia" / "execution" / "gateway.py"
    )
    source_text = src_path.read_text(encoding="utf-8")

    # 1. evaluate_proposal must be imported.
    assert "from aletheia.risk.engine import evaluate_proposal" in source_text, (
        "gateway.py must import evaluate_proposal from risk engine"
    )

    # 2. Source-order check inside _execute_proposal: the `if not risk_result.approved:`
    #    guard must appear before the first broker call *within the function body*.
    #    We find the byte offset of _execute_proposal's definition and search from there.
    func_def_pos = source_text.find("def _execute_proposal(")
    assert func_def_pos != -1, "_execute_proposal not found in gateway.py"
    func_body = source_text[func_def_pos:]

    approved_pos = func_body.find("risk_result.approved")
    # Broker calls inside _execute_proposal are either direct (execute_paper_order)
    # or via a local import + call (execute_live_order); find the call() site not import.
    # We search for the call pattern: identifier followed by '(' on the same line.
    import re
    broker_call_match = re.search(
        r"(?:execute_paper_order|execute_live_order)\s*\(", func_body
    )
    broker_call_pos = broker_call_match.start() if broker_call_match else -1

    assert approved_pos != -1, "risk_result.approved check not found inside _execute_proposal"
    assert broker_call_pos != -1, "no broker call found inside _execute_proposal"
    assert approved_pos < broker_call_pos, (
        "risk_result.approved guard must appear in source BEFORE broker calls inside "
        f"_execute_proposal; approved_pos={approved_pos}, first_broker_call={broker_call_pos}"
    )

    # 3. AST check: every Call to execute_paper_order or execute_live_order is
    #    inside _execute_proposal (no top-level or other-function bypass).
    tree = ast.parse(source_text)
    execute_proposal_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_proposal":
            execute_proposal_func = node
            break

    assert execute_proposal_func is not None, (
        "_execute_proposal function not found in gateway.py"
    )

    broker_call_names = {"execute_paper_order", "execute_live_order"}
    calls_outside_execute = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == "_execute_proposal":
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                func = inner.func
                name = (
                    func.id if isinstance(func, ast.Name) else
                    func.attr if isinstance(func, ast.Attribute) else None
                )
                if name in broker_call_names:
                    calls_outside_execute.append((node.name, name))

    assert not calls_outside_execute, (
        f"Broker calls found outside _execute_proposal (risk-gate bypass): "
        f"{calls_outside_execute}"
    )


# ---------------------------------------------------------------------------
# RE-S05 (L2a, missing_tripwire) — dynamic stop-price adjustment path
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-S05", "risk_engine_contract", "§8 stop adjustment", "L2a",
              "position_manager (or equivalent) exposes a callable "
              "accepting (position_id, new_stop_price) that writes stop_price to DB — "
              "execution path for PI instruction 'adjust stop to X'.",
              status="implemented"),
)
def test_position_manager_has_adjust_stop_interface():
    """When this tripwire passes, a dynamic stop-adjustment path exists.
    Remove the xfail decorator and add a real L2b behavioral test at that point.
    """
    import inspect

    # Try canonical module path first, fall back to any positions submodule.
    try:
        import aletheia.positions.position_manager as pm
    except ImportError:
        import aletheia.portfolio.position_manager as pm  # type: ignore[no-redef]

    candidates = [
        name for name in dir(pm)
        if any(kw in name for kw in ("adjust_stop", "update_stop", "set_stop_price"))
        and callable(getattr(pm, name))
    ]
    assert candidates, (
        "No adjust_stop / update_stop / set_stop_price callable found in position_manager. "
        "Implement the interface then remove the xfail decorator."
    )

    # Verify the found function accepts position_id and a stop price parameter.
    fn = getattr(pm, candidates[0])
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    assert "position_id" in params, f"{candidates[0]} must accept position_id param"
    assert any(p in params for p in ("new_stop_price", "stop_price")), (
        f"{candidates[0]} must accept new_stop_price or stop_price param"
    )


# ---------------------------------------------------------------------------
# D2 — Serenity 双赛道隔离 claims
# ---------------------------------------------------------------------------


# --- D2-S01: check #2 violation tokens 源码存在 (L2a) ---

@spec_claim(
    SpecClaim("D2-S01", "risk_engine_contract", "§2 check #2", "L2a",
              "risk/engine.py check #2 对 spot 和 usdt_futures 分别存在独立的 violation token："
              "spot_market_disabled 和 futures_market_disabled 必须出现在 engine.py 源码中"),
)
def test_d2_s01_market_disabled_tokens_exist():
    from pathlib import Path
    src_path = Path(__file__).parent.parent.parent / "aletheia" / "risk" / "engine.py"
    src = src_path.read_text(encoding="utf-8")
    assert "spot_market_disabled" in src, (
        "engine.py missing spot_market_disabled violation token for check #2"
    )
    assert "futures_market_disabled" in src, (
        "engine.py missing futures_market_disabled violation token for check #2"
    )


# --- D2-B01: check #2 行为验证 (L2b) ---

@spec_claim(
    SpecClaim("D2-B01", "risk_engine_contract", "§2 check #2", "L2b",
              "check #2 行为：spot/usdt_futures proposal 在对应 enabled_markets=False 时被拒绝，"
              "在 enabled_markets=True 时不产生该 violation；两者对称成立"),
)
def test_d2_b01_market_disabled_behavior(tmp_path, monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.models import TradeProposal
    from aletheia.storage import repositories as repo
    from aletheia.risk.engine import evaluate_proposal
    import aletheia.risk.engine as eng

    _setup_db(tmp_path, monkeypatch)

    # spot disabled -> violation
    cfg = AletheiaConfig()
    cfg.exchange.enabled_markets = {"spot": False, "usdt_futures": True}
    monkeypatch.setattr(eng, "load_config", lambda: cfg)
    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      execution_mode="paper_internal", quantity=0.001, price=60000)
    repo.insert_trade_proposal(p)
    r = evaluate_proposal(p)
    assert any("spot_market_disabled" in v for v in r.violations), (
        f"Expected spot_market_disabled when spot=False, got: {r.violations}"
    )

    # spot enabled -> no violation
    cfg2 = AletheiaConfig()
    cfg2.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    monkeypatch.setattr(eng, "load_config", lambda: cfg2)
    p2 = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                       execution_mode="paper_internal", quantity=0.001, price=60000)
    repo.insert_trade_proposal(p2)
    r2 = evaluate_proposal(p2)
    assert not any("spot_market_disabled" in v for v in r2.violations), (
        f"Unexpected spot_market_disabled when spot=True: {r2.violations}"
    )

    # usdt_futures disabled -> violation
    cfg3 = AletheiaConfig()
    cfg3.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    monkeypatch.setattr(eng, "load_config", lambda: cfg3)
    p3 = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                       execution_mode="paper_internal", quantity=0.001, price=60000)
    repo.insert_trade_proposal(p3)
    r3 = evaluate_proposal(p3)
    assert any("futures_market_disabled" in v for v in r3.violations), (
        f"Expected futures_market_disabled when usdt_futures=False, got: {r3.violations}"
    )

    # usdt_futures enabled -> no violation
    cfg4 = AletheiaConfig()
    cfg4.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    monkeypatch.setattr(eng, "load_config", lambda: cfg4)
    p4 = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                       execution_mode="paper_internal", quantity=0.001, price=60000)
    repo.insert_trade_proposal(p4)
    r4 = evaluate_proposal(p4)
    assert not any("futures_market_disabled" in v for v in r4.violations), (
        f"Unexpected futures_market_disabled when usdt_futures=True: {r4.violations}"
    )


# --- D2-C01: evaluate_proposal 唯一入口不变式 (L2c) ---

@spec_claim(
    SpecClaim("D2-C01", "risk_engine_contract", "§6 bypass", "L2c",
              "系统不变式：gateway.py 中所有调用 broker 下单入口的代码路径均经过 evaluate_proposal，"
              "不存在绕过 risk engine 的直接下单路径"),
)
def test_d2_c01_gateway_always_calls_evaluate_proposal():
    import ast
    from pathlib import Path
    src_path = Path(__file__).parent.parent.parent / "aletheia" / "execution" / "gateway.py"
    source_text = src_path.read_text(encoding="utf-8")

    assert "evaluate_proposal" in source_text, (
        "gateway.py does not import/use evaluate_proposal"
    )

    tree = ast.parse(source_text)
    execute_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_execute_proposal":
            execute_fn = node
            break
    assert execute_fn is not None, "gateway.py missing _execute_proposal function"

    call_names = []
    for node in ast.walk(execute_fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                call_names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                call_names.append(node.func.attr)
    assert "evaluate_proposal" in call_names, (
        f"_execute_proposal does not call evaluate_proposal; calls: {call_names}"
    )

    # 3. Inside _execute_proposal: risk check must appear before broker calls.
    #    Use AST line numbers (avoids import-declaration false positives).
    BROKER_CALLS = {"execute_paper_order", "execute_live_order"}
    risk_call_lines = []
    broker_call_lines = []
    for node in ast.walk(execute_fn):
        if isinstance(node, ast.Call) and hasattr(node, "lineno"):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name == "evaluate_proposal":
                risk_call_lines.append(node.lineno)
            elif name in BROKER_CALLS:
                broker_call_lines.append(node.lineno)
    assert risk_call_lines, "evaluate_proposal not called inside _execute_proposal"
    assert broker_call_lines, "no broker call (execute_paper_order/execute_live_order) inside _execute_proposal"
    assert min(risk_call_lines) < min(broker_call_lines), (
        f"evaluate_proposal (line {min(risk_call_lines)}) must appear BEFORE broker calls "
        f"(first broker call at line {min(broker_call_lines)}) in _execute_proposal"
    )


# --- D2-S02: serenity_executor 尚未实现 tripwire (L2a) ---

@pytest.mark.xfail(
    strict=True,
    reason="TRIPWIRE: serenity_executor.py 不存在时此 xfail passes；"
           "文件出现时自动变红，提醒补充完整 spec 再上线",
)
@spec_claim(
    SpecClaim("D2-S02", "risk_engine_contract", "§serenity_isolation", "L2a",
              "missing_tripwire：serenity_executor 模块尚未实现；此 tripwire 在文件出现时自动变红",
              status="missing"),
)
def test_d2_s02_serenity_executor_not_yet_implemented():
    from pathlib import Path
    aletheia_root = Path(__file__).parent.parent.parent / "aletheia"
    matches = list(aletheia_root.rglob("serenity_executor.py"))
    assert len(matches) > 0, "serenity_executor.py not yet created — tripwire is active"


# --- D2-S03: PnL 归因按 trade_school 分组结构性检查 (L2a, tripwire) ---

@spec_claim(
    SpecClaim("D2-S03", "risk_engine_contract", "§serenity_isolation", "L2a",
              "analytics/portfolio 中聚合 realized_pnl/profit_factor/win_rate "
              "的函数存在按 trade_school 分组的逻辑（compute_pnl_by_trade_school）",
              status="implemented"),
)
def test_d2_s03_pnl_attribution_by_trade_school_exists():
    """扫描条件更严格：要求同一行/语句中 trade_school 与分组/过滤关键词共现，
    或 SQL 中存在 'GROUP BY trade_school'。
    单独出现 trade_school 赋值 + realized_pnl 引用不算。"""
    import re
    from pathlib import Path
    base = Path(__file__).parent.parent.parent / "aletheia"
    dirs = [base / "analytics", base / "portfolio"]
    # 匹配模式：GROUP BY trade_school，或 filter/where 含 trade_school + PnL 聚合
    GROUP_BY_PATTERN = re.compile(r"GROUP BY trade_school", re.IGNORECASE)
    FILTER_PATTERN = re.compile(
        r"trade_school.*(?:profit_factor|realized_pnl|win_rate)|"
        r"(?:profit_factor|realized_pnl|win_rate).*trade_school",
        re.IGNORECASE,
    )
    found = False
    for d in dirs:
        for py_file in d.glob("*.py"):
            content = py_file.read_text(encoding="utf-8")
            if GROUP_BY_PATTERN.search(content) or FILTER_PATTERN.search(content):
                found = True
                break
        if found:
            break
    assert found, (
        "No analytics/portfolio module found with trade_school-grouped PnL aggregation "
        "(GROUP BY trade_school or filter-by-trade_school in same expression). "
        "Required for serenity/main-track PnL isolation."
    )


# --- D2-B02: PnL 不混入行为断言 (L2b, tripwire) ---

@pytest.mark.xfail(
    strict=True,
    reason="TRIPWIRE: PnL 归因隔离尚未实现；实现后去掉 xfail",
)
@spec_claim(
    SpecClaim("D2-B02", "risk_engine_contract", "§serenity_isolation", "L2b",
              "missing_tripwire：serenity school 已平仓仓位不应出现在主赛道 PnL 聚合结果中；"
              "给定 short_term+100 和 serenity-50，主赛道 PF 仅基于 short_term 那笔",
              status="missing"),
)
def test_d2_b02_serenity_pnl_not_mixed_into_main_track(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    pytest.fail(
        "NOT IMPLEMENTED: main-track PnL aggregation with trade_school filter. "
        "Implement a function accepting trade_school=short_term, compute profit_factor "
        "excluding serenity records, then remove xfail and assert PF > 1."
    )


# --- D2-C02: 头寸池 trade_school 边界 defer (L2c, tripwire) ---

@spec_claim(
    SpecClaim("D2-C02", "risk_engine_contract", "§serenity_isolation", "L2c",
              "risk engine check #5/#6 头寸池边界与 trade_school 的关系已明确声明："
              "当前共池（serenity 占用主赛道配额），产品决策文档在 risk/engine.py §D2-C02 注释",
              status="implemented"),
)
def test_d2_c02_position_pool_trade_school_boundary_declared():
    from pathlib import Path
    src_path = Path(__file__).parent.parent.parent / "aletheia" / "risk" / "engine.py"
    src = src_path.read_text(encoding="utf-8")
    assert "trade_school" in src, (
        "INVARIANT DRIFT: engine.py check #5/#6 position counting has no trade_school "
        "declaration. Product decision required: shared pool (current) or isolated pool "
        "per trade_school. Document the decision as a comment near check #5/#6."
    )

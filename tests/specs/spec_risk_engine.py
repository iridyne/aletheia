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
                                      "lesson_", "correlation_", "family_")):
                violations.add(node.value)
    assert len(violations) >= 16, (
        f"Expected >= 16 distinct check violation tokens, found {len(violations)}: "
        f"{sorted(violations)}"
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "RE-B20", "risk_engine_contract", "§2 check 8 authoritative account loss", "L2b",
        "Daily-loss protection blocks when account truth is unavailable without fabricating a "
        "threshold breach, and exact authoritative loss retains the existing threshold check.",
    )
)
def test_daily_loss_uses_authoritative_account_snapshot(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.portfolio.account_loss import AccountLossSnapshot
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.001, price=100.0,
    )
    repo.insert_trade_proposal(proposal)
    monkeypatch.setattr(
        eng, "authoritative_account_realized_loss",
        lambda mode: AccountLossSnapshot(
            available=False, realized_pnl_usdt=None, loss_usdt=None,
            source="unavailable", reason="account_event_read_failed", event_count=0,
        ),
    )
    unavailable = eng.evaluate_proposal(proposal, decision_role="preflight")
    assert "account_loss_unavailable:account_event_read_failed" in unavailable.violations
    assert not any(v.startswith("max_daily_loss_exceeded") for v in unavailable.violations)

    monkeypatch.setattr(
        eng, "authoritative_account_realized_loss",
        lambda mode: AccountLossSnapshot(
            available=True, realized_pnl_usdt=-100.0, loss_usdt=100.0,
            source="paper_account_ledger", reason="available", event_count=1,
        ),
    )
    breached = eng.evaluate_proposal(proposal, decision_role="preflight")
    assert any(v.startswith("max_daily_loss_exceeded:-100.0") for v in breached.violations)


@spec_claim(
    SpecClaim(
        "RE-B21", "risk_engine_contract", "§2 check 8 complete account loss window", "L2c",
        "Daily-loss authority fails closed when the exact same-day close count exceeds "
        "the bounded raw event window; an older loss cannot disappear behind 10,000 rows.",
        status="implemented",
    )
)
def test_daily_loss_fails_closed_when_account_event_window_is_truncated():
    from datetime import datetime, timezone

    from aletheia.portfolio.account_loss import authoritative_account_realized_loss
    from aletheia.storage.sqlite import get_db

    closed_at = datetime.now(timezone.utc).isoformat()
    get_db().executemany(
        "INSERT INTO positions "
        "(id,symbol,market_type,direction,quantity,entry_price,current_price,"
        "realized_pnl,execution_mode,status,closed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (
                f"daily-loss-window-{index}", "BTC/USDT", "spot", "long",
                1.0, 100.0, 100.0, 0.0, "okx_demo", "closed", closed_at,
            )
            for index in range(10_001)
        ],
    )

    snapshot = authoritative_account_realized_loss("okx_demo")

    assert snapshot.available is False
    assert snapshot.reason == "account_event_window_truncated"
    assert snapshot.realized_pnl_usdt is None
    assert snapshot.event_count == 10_001


@spec_claim(
    SpecClaim(
        "RE-B22", "risk_engine_contract", "§2 local paper funding authority", "L2b",
        "Risk durably rejects a new native paper_internal futures entry when the "
        "exact symbol has no fresh primary-venue funding schedule anchor.",
        status="implemented",
    )
)
def test_risk_rejects_local_paper_futures_without_fresh_schedule(
    tmp_path, monkeypatch
):
    _setup_db(tmp_path, monkeypatch)

    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    proposal = TradeProposal(
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="paper_internal",
        quantity=0.001,
        price=60_000.0,
        leverage=1,
    )
    repo.insert_trade_proposal(proposal)

    result = evaluate_proposal(proposal)

    assert result.approved is False
    assert "local_paper_futures_funding_unavailable" in result.violations
    stored = [
        row for row in repo.list_risk_decisions(limit=10)
        if row["proposal_id"] == proposal.id
    ]
    assert len(stored) == 1
    assert stored[0]["approved"] == 0


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
    SpecClaim(
        "RE-B14", "risk_engine_contract", "§2 lesson constraint boundary", "L2b",
        "block_* hard rejects in _check_lesson_constraints require category='risk'. "
        "Automatic dreaming_agent block recommendations are routed to guard shadow "
        "(see LE-B19) and stored as guard_shadow_candidate — they never appear as "
        "block_* recommendations in ps_lessons, so this gate is reached only by "
        "manually created category='risk' lessons.",
    ),
)
async def test_lesson_constraint_category_gate_is_risk_only():
    """block_* enforcement requires category='risk'; other categories are silently skipped."""
    import inspect
    from aletheia.risk import engine as eng

    src = inspect.getsource(eng._check_lesson_constraints)
    assert "category" in src and "'risk'" in src, (
        "_check_lesson_constraints must gate block_* on category='risk'"
    )
    # Verify guard routing ensures dreaming_agent block_* never reaches ps_lessons
    # as a block_* recommendation (they become guard_shadow_candidate instead).
    from aletheia.learning.guard_routing import route_automatic_block_recommendation
    from tests.specs.spec_learning_attribution import _ready_research
    from unittest.mock import patch

    # Evidence must include scanner_families scope so the guard is not global (#197).
    scoped_evidence = {
        "scanner_families": ["momentum"],
        "factor_keys": ["momentum:BTCUSDT:long:medium_confidence:not_late"],
    }
    with patch("aletheia.learning.guard_routing.create_guard_candidate") as mock_create:
        mock_create.return_value = type("Item", (), {"id": "g-1"})()
        result = await route_automatic_block_recommendation(
            {"action": "block_symbol", "symbol": "BTC/USDT"},
            evidence=scoped_evidence, source_id="test-src",
            research_evidence=_ready_research("test-src"),
        )
    assert result is not None and result.get("action") == "guard_shadow_candidate", (
        "block_symbol routed by dreaming_agent must become guard_shadow_candidate, not block_symbol"
    )
    assert result.get("original_action") == "block_symbol"


@spec_claim(
    SpecClaim("RE-B04", "risk_engine_contract", "§2 check #16", "L2b",
              "_check_correlation_exposure returns violation on internal error (safe-reject)"),
)
def test_correlation_exposure_error_produces_violation(monkeypatch):
    from aletheia.risk.engine import _check_correlation_exposure
    from aletheia.storage.models import TradeProposal
    from aletheia.config.loader import load_config

    from unittest.mock import patch
    with patch(
        "aletheia.storage.repositories.list_all_open_positions",
        side_effect=RuntimeError("DB gone"),
    ):
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
    SpecClaim(
        "RE-B04a",
        "risk_engine_contract",
        "§2 check #16",
        "L2c",
        "Sector concentration and portfolio beta include authoritative pre-Order "
        "reservations and unresolved entry Orders, not only open Positions.",
        status="implemented",
    ),
)
@pytest.mark.parametrize("first_state", ("pre_order", "unresolved_order"))
def test_correlation_guards_include_unmaterialized_entry_exposure(
    tmp_path, monkeypatch, first_state
):
    """Two same-family approvals must not bypass the guard before Positions exist."""
    import aletheia.risk.engine as eng
    from aletheia.config.schema import AletheiaConfig
    from aletheia.portfolio.account_loss import AccountLossSnapshot
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, Order, TradeProposal

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Paper"
    cfg.risk.correlation_guard_enabled = True
    cfg.risk.max_sector_concentration_pct = 60.0
    cfg.risk.max_portfolio_beta = 3.0
    cfg.risk.max_open_positions_demo = 10
    cfg.risk.max_demo_position_notional_usdt = 100_000.0
    cfg.risk.max_per_symbol_notional_usdt = 100_000.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)
    monkeypatch.setattr(eng, "_portfolio_drawdown_scale", lambda _mode: 1.0)
    monkeypatch.setattr(eng, "_check_lesson_constraints", lambda _proposal: [])
    monkeypatch.setattr(eng, "_lookup_beta", lambda _symbol: 4.0)
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda symbol, market_type, *, venue: 100.0,
    )
    monkeypatch.setattr(
        eng,
        "authoritative_account_realized_loss",
        lambda _mode: AccountLossSnapshot(
            available=True,
            realized_pnl_usdt=0.0,
            loss_usdt=0.0,
            source="isolated_test",
            reason="available",
            event_count=0,
        ),
    )

    proposals = []
    for index, symbol in enumerate(("BTC/USDT", "ETH/USDT"), start=1):
        opportunity = Opportunity(
            id=f"sector-reservation-opportunity-{index}",
            symbol=symbol,
            market="spot",
            direction_bias="long",
            confidence="high",
            confidence_score=90,
            source_agent="market_observer",
            scanner_family="momentum",
        )
        repo.insert_opportunity(opportunity)
        proposal = TradeProposal(
            id=f"sector-reservation-proposal-{index}",
            opportunity_id=opportunity.id,
            symbol=symbol,
            market_type="spot",
            direction="long",
            order_type="market",
            quantity=1.0,
            price=100.0,
            stop_price=99.0,
            take_profit=102.0,
            risk_reward=2.0,
            execution_mode="paper_internal",
        )
        repo.insert_trade_proposal(proposal)
        proposals.append(proposal)

    first = eng.evaluate_proposal(proposals[0], decision_role="authoritative")
    assert first.approved, first.violations
    assert repo.get_entry_risk_reservation(proposals[0].id) is not None
    assert repo.list_all_open_positions() == []
    if first_state == "unresolved_order":
        repo.insert_order(Order(
            id="sector-reservation-unresolved-order",
            proposal_id=proposals[0].id,
            risk_decision_id=first.decision_id,
            symbol=proposals[0].symbol,
            market_type=proposals[0].market_type,
            direction=proposals[0].direction,
            order_type=proposals[0].order_type,
            quantity=proposals[0].quantity,
            price=proposals[0].price,
            execution_mode=proposals[0].execution_mode,
            status="submission_unknown",
        ))
        repo.release_entry_risk_reservation(proposals[0].id)
        assert repo.get_entry_risk_reservation(proposals[0].id) is None
        assert len(repo.list_unresolved_native_entry_orders_for_risk()) == 1

    second = eng.evaluate_proposal(proposals[1], decision_role="authoritative")

    assert not second.approved, second.violations
    assert any(
        violation.startswith("sector_concentration:momentum:")
        for violation in second.violations
    ), second.violations
    assert any(
        violation.startswith("portfolio_beta:")
        for violation in second.violations
    ), second.violations
    assert repo.get_entry_risk_reservation(proposals[1].id) is None


@spec_claim(
    SpecClaim(
        "RE-B04b",
        "risk_engine_contract",
        "§2 check #16 live/demo pool isolation",
        "L2c",
        "Sector concentration is calculated within the Proposal's live or demo pool; "
        "large unrelated exposure in the other pool cannot dilute it.",
        status="implemented",
    ),
)
@pytest.mark.parametrize(
    ("proposal_mode", "other_pool_mode"),
    [
        pytest.param(
            "okx_live_limited",
            "paper_internal",
            id="live-not-diluted-by-demo",
        ),
        pytest.param(
            "paper_internal",
            "okx_live_limited",
            id="demo-not-diluted-by-live",
        ),
    ],
)
def test_sector_concentration_isolated_by_live_demo_pool(
    tmp_path, monkeypatch, proposal_mode, other_pool_mode
):
    """An unrelated Position in the other authority pool cannot dilute a breach."""
    import aletheia.risk.engine as eng
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Opportunity, Position, TradeProposal

    _setup_db(tmp_path, monkeypatch)

    cfg = AletheiaConfig()
    cfg.risk.correlation_guard_enabled = True
    cfg.risk.max_sector_concentration_pct = 60.0
    monkeypatch.setattr(eng, "_lookup_beta", lambda _symbol: None)

    existing_opportunity = Opportunity(
        id=f"pool-existing-opportunity-{proposal_mode}",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        source_agent="market_observer",
        scanner_family="momentum",
    )
    proposal_opportunity = Opportunity(
        id=f"pool-proposal-opportunity-{proposal_mode}",
        symbol="ETH/USDT",
        market="spot",
        direction_bias="long",
        source_agent="market_observer",
        scanner_family="momentum",
    )
    other_opportunity = Opportunity(
        id=f"pool-other-opportunity-{proposal_mode}",
        symbol="SOL/USDT",
        market="spot",
        direction_bias="long",
        source_agent="market_observer",
        scanner_family="mean_reversion",
    )
    for opportunity in (
        existing_opportunity,
        proposal_opportunity,
        other_opportunity,
    ):
        repo.insert_opportunity(opportunity)

    repo.insert_position(Position(
        id=f"pool-existing-position-{proposal_mode}",
        symbol=existing_opportunity.symbol,
        market_type="spot",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode=proposal_mode,
        opportunity_id=existing_opportunity.id,
        status="open",
    ))
    proposal = TradeProposal(
        id=f"pool-proposal-{proposal_mode}",
        opportunity_id=proposal_opportunity.id,
        symbol=proposal_opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode=proposal_mode,
    )
    repo.insert_trade_proposal(proposal)

    baseline = eng._check_correlation_exposure(proposal, cfg)
    assert any(
        violation.startswith("sector_concentration:momentum:")
        for violation in baseline
    ), baseline

    repo.insert_position(Position(
        id=f"pool-other-position-{proposal_mode}",
        symbol=other_opportunity.symbol,
        market_type="spot",
        direction="long",
        quantity=100.0,
        entry_price=100.0,
        current_price=100.0,
        execution_mode=other_pool_mode,
        opportunity_id=other_opportunity.id,
        status="open",
    ))

    after_other_pool_exposure = eng._check_correlation_exposure(proposal, cfg)

    assert any(
        violation.startswith("sector_concentration:momentum:")
        for violation in after_other_pool_exposure
    ), (
        f"{proposal_mode} concentration was diluted by {other_pool_mode}: "
        f"{after_other_pool_exposure!r}"
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
            {"total_value_usdt": peak, "execution_mode": "exchange_account"},
            {"total_value_usdt": peak, "execution_mode": "exchange_account"},
            {"total_value_usdt": peak, "execution_mode": "exchange_account"},
            {"total_value_usdt": current, "execution_mode": "exchange_account"},
        ]

    monkeypatch.setattr(
        "aletheia.storage.repositories.list_portfolio_snapshots", _mock_snapshots
    )
    monkeypatch.setattr(
        "aletheia.portfolio.exchange_sync.active_broker_view_complete",
        lambda: (True, ""),
    )

    _mock_snapshots._dd_pct = 0.0
    assert _portfolio_drawdown_scale("okx_demo") == 1.0

    _mock_snapshots._dd_pct = 5.0
    assert _portfolio_drawdown_scale("okx_demo") == 1.0

    _mock_snapshots._dd_pct = 7.5
    scale = _portfolio_drawdown_scale("okx_demo")
    assert 0.7 < scale < 0.8  # linear 1.0→0.5 in 5-10% range

    _mock_snapshots._dd_pct = 10.0
    assert abs(_portfolio_drawdown_scale("okx_demo") - 0.5) < 0.01

    _mock_snapshots._dd_pct = 20.0
    assert _portfolio_drawdown_scale("okx_demo") == 0.0

    _mock_snapshots._dd_pct = 25.0
    assert _portfolio_drawdown_scale("okx_demo") == 0.0


@spec_claim(
    SpecClaim("RE-B05b", "risk_engine_contract", "§2 check #5 drawdown source", "L2b",
              "paper_internal skips drawdown scaling because neither local notional nor "
              "external account snapshots are its simulated equity"),
)
def test_paper_internal_drawdown_never_uses_snapshot_notional(monkeypatch):
    from aletheia.risk.engine import _portfolio_drawdown_scale

    monkeypatch.setattr(
        "aletheia.storage.repositories.list_portfolio_snapshots",
        lambda limit=50: [
            {"total_value_usdt": 200.0, "execution_mode": "paper_internal"},
            {"total_value_usdt": 200.0, "execution_mode": "paper_internal"},
            {"total_value_usdt": 0.0, "execution_mode": "paper_internal"},
            {"total_value_usdt": 100000.0, "execution_mode": "exchange_account"},
            {"total_value_usdt": 167.0, "execution_mode": "exchange_account"},
        ],
    )

    assert _portfolio_drawdown_scale("paper_internal") == 1.0


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


@spec_claim(
    SpecClaim("RE-B18", "risk_engine_contract", "§2 check #5", "L2b",
              "Exchange-backed orders use the lower of the configured hard cap and the "
              "matching account's cached available USDT; paper_internal uses the configured cap"),
)
def test_account_balance_tightens_matching_mode_cap(tmp_path, monkeypatch):
    """Demo/live must read separate cached accounts; local paper has no account."""
    from aletheia.config.schema import AletheiaConfig
    import aletheia.portfolio.exchange_sync as sync
    import aletheia.risk.engine as eng
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    _setup_db(tmp_path, monkeypatch)
    cfg = AletheiaConfig()
    cfg.risk.max_demo_position_notional_usdt = 200.0
    cfg.risk.max_live_position_notional_usdt = 50.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)

    def cached_state(mode):
        return {"available_usdt": 80.0 if mode == "demo" else 30.0}

    monkeypatch.setattr(sync, "get_cached_state", cached_state)

    assert eng._effective_position_notional_cap(cfg.risk, "bitget_demo") == 80.0
    assert eng._effective_position_notional_cap(cfg.risk, "bitget_live_limited") == 30.0
    assert eng._effective_position_notional_cap(cfg.risk, "paper_internal") == 200.0

    proposal = TradeProposal(
        symbol="ETH/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=1.0, price=100.0,
    )
    repo.insert_trade_proposal(proposal)
    result = eng.evaluate_proposal(proposal)
    assert any(v == "exceeds_demo_position_notional:100.0>80.0" for v in result.violations)


@spec_claim(
    SpecClaim(
        "RE-B29",
        "risk_engine_contract",
        "§2 check #5 local paper balance authority",
        "L2b",
        "An authoritative paper_internal Proposal is rejected when the local paper "
        "balance cannot be read; external account cache absence retains its existing "
        "configured-cap fallback.",
        status="implemented",
    )
)
def test_local_paper_balance_reader_failure_rejects_authoritative_proposal(
    tmp_path,
    monkeypatch,
):
    import aletheia.portfolio.exchange_sync as sync
    import aletheia.portfolio.paper_account as paper_account
    import aletheia.risk.engine as eng
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import TradeProposal

    _setup_db(tmp_path, monkeypatch)
    cfg = AletheiaConfig()
    cfg.agent.permission_mode = "Paper"
    cfg.risk.max_demo_position_notional_usdt = 200.0
    cfg.risk.max_open_positions_demo = 10
    cfg.risk.max_per_symbol_notional_usdt = 10_000.0
    monkeypatch.setattr(eng, "load_config", lambda: cfg)
    monkeypatch.setattr(eng, "_portfolio_drawdown_scale", lambda _mode: 1.0)
    monkeypatch.setattr(eng, "_check_lesson_constraints", lambda _proposal: [])

    def _balance_failure(*_args, **_kwargs):
        raise RuntimeError("local paper balance reader failed")

    monkeypatch.setattr(paper_account, "get_paper_account", _balance_failure)

    # External accounts intentionally keep the existing advisory-cache fallback.
    monkeypatch.setattr(sync, "get_cached_state", _balance_failure)
    assert (
        eng._effective_position_notional_cap(cfg.risk, "bitget_demo")
        == cfg.risk.max_demo_position_notional_usdt
    )

    proposal = TradeProposal(
        id="paper-balance-unavailable-proposal",
        symbol="ETH/USDT",
        market_type="spot",
        direction="long",
        order_type="market",
        execution_mode="paper_internal",
        quantity=1.0,
        price=100.0,
    )
    repo.insert_trade_proposal(proposal)

    result = eng.evaluate_proposal(proposal, decision_role="authoritative")

    assert result.approved is False
    assert "paper_account_balance_unavailable" in result.violations
    decisions = [
        row for row in repo.list_risk_decisions(limit=20)
        if row["proposal_id"] == proposal.id
    ]
    assert len(decisions) == 1
    assert decisions[0]["decision_role"] == "authoritative"
    assert decisions[0]["approved"] == 0
    assert not [
        row for row in repo.list_orders(limit=20)
        if row["proposal_id"] == proposal.id
    ]
    assert repo.get_entry_risk_reservation(proposal.id) is None


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
            symbol="ETH/USDT", market_type="spot", direction="long",
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
    SpecClaim(
        "RE-B31", "risk_engine_contract", "§2 percentage-limit denominator", "L2b",
        "Percentage-based risk limits default to the matching account equity; an "
        "unavailable snapshot fails closed and never silently uses stored custom capital",
    ),
)
def test_default_capital_base_tracks_account_equity_and_fails_closed(monkeypatch):
    from aletheia.config.schema import AletheiaConfig
    import aletheia.risk.engine as eng

    cfg = AletheiaConfig()
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 4321.0)
    value, reason = eng._resolve_capital_base("paper_internal", cfg.risk.demo, cfg)
    assert value == 4321.0
    assert reason is None

    monkeypatch.setattr(eng, "_account_equity", lambda *_: None)
    value, reason = eng._resolve_capital_base("paper_internal", cfg.risk.demo, cfg)
    assert value is None
    assert reason == "account_equity_unavailable:paper_internal"


@spec_claim(
    SpecClaim("RE-B13", "risk_engine_contract", "§2 check #8", "L2b",
              "demo daily-loss resolves the Demo policy capital base, not the Live policy "
              "base, and never passes merely because its capital base is unavailable"),
)
def test_demo_daily_loss_uses_demo_capital_base(tmp_path, monkeypatch):
    """A demo realized loss that is tiny vs the demo capital base must NOT trip
    the daily-loss brake just because it exceeds the (much smaller) live cap.

    Setup: explicit Demo custom base=10000, Live custom base=100, and Demo
    max_daily_loss_pct=1%. A -$25 demo loss is:
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
    cfg.risk.demo.capital.base_source = "custom"
    cfg.risk.demo.capital.base_usdt = 10000.0
    cfg.risk.demo.loss.max_daily_loss_pct = 1.0
    cfg.risk.demo.capital.max_position_notional_usdt = 10000.0
    cfg.risk.live.capital.base_source = "custom"
    cfg.risk.live.capital.base_usdt = 100.0
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

    assert not any(v.startswith("capital_base_unavailable:") for v in result.violations), (
        "the denominator must be available so this test proves the Demo base, not a skipped check"
    )
    assert not any("max_daily_loss_exceeded" in v for v in result.violations), (
        "demo daily-loss brake tripped on a $25 loss against a $10000 demo base — "
        f"it is wrongly using the $100 live cap (CR-RE-03). violations={result.violations}"
    )


# ---------------------------------------------------------------------------
# EH-1 (§17.2) — risk-check exceptions fail CLOSED (reject), never fail-open
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("RE-B19", "risk_engine_contract", "§17.2 fail-closed", "L2b",
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

    # Account-leverage check: the full authority reader blows up → fail closed.
    monkeypatch.setattr(eng, "list_all_open_positions", _boom)
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


@spec_claim(
    SpecClaim(
        "RE-B23",
        "risk_engine_contract",
        "§2 check #17 account identity",
        "L2b",
        "Account leverage counts open Positions, unresolved entry Orders, and "
        "pre-Order reservations only from the Proposal's account pool; one demo "
        "exchange must not consume another demo exchange's leverage capacity.",
        status="implemented",
    )
)
def test_account_leverage_uses_one_account_pool_for_every_exposure(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config
    from aletheia.storage.models import TradeProposal

    cfg = load_config().model_copy(deep=True)
    cfg.risk.max_account_leverage_demo = 1.0
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    monkeypatch.setattr(
        eng,
        "list_all_open_positions",
        lambda: [
                {
                    "symbol": "ETH/USDT",
                    "market_type": "usdt_futures",
                    "execution_mode": "okx_demo",
                "quantity": 0.1,
                "entry_price": 100.0,
                "leverage": 1,
            },
                {
                    "symbol": "BTC/USDT",
                    "market_type": "usdt_futures",
                    "execution_mode": "bitget_demo",
                "quantity": 10.0,
                "entry_price": 100.0,
                "leverage": 3,
            },
        ],
    )
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 100.0)
    other_order = eng._UnresolvedEntryReservation(
        order_id="bitget-order",
        symbol="AVAX/USDT",
        market_type="usdt_futures",
        execution_mode="bitget_demo",
        opportunity_id=None,
        price=100.0,
        leverage=3.0,
        order_quantity=10.0,
        persisted_fill_quantity=0.0,
        remaining_quantity=10.0,
        reserved_quantity=10.0,
        has_open_position=False,
    )
    other_reservation = eng._PreOrderEntryReservation(
        proposal_id="bitget-reservation",
        symbol="LINK/USDT",
        market_type="usdt_futures",
        execution_mode="bitget_demo",
        opportunity_id=None,
        price=100.0,
        leverage=3.0,
        reserved_quantity=10.0,
    )

    assert eng._check_account_leverage_ratio(
        proposal,
        cfg,
        unresolved_entries=[other_order],
        pre_order_entries=[other_reservation],
    ) == []

    same_account_reservation = eng._PreOrderEntryReservation(
        proposal_id="okx-reservation",
        symbol="LINK/USDT",
        market_type="usdt_futures",
        execution_mode="okx_demo",
        opportunity_id=None,
        price=100.0,
        leverage=1.0,
        reserved_quantity=2.0,
    )
    violations = eng._check_account_leverage_ratio(
        proposal,
        cfg,
        unresolved_entries=[],
        pre_order_entries=[same_account_reservation],
    )
    assert any(value.startswith("account_leverage_ratio:") for value in violations)


@spec_claim(
    SpecClaim(
        "RE-B26",
        "risk_engine_contract",
        "§2 check #17 position integrity",
        "L2b",
        "A relevant open Position with missing, non-positive, non-numeric, or "
        "non-finite quantity, entry price, or leverage rejects account leverage "
        "evaluation instead of disappearing from the numerator.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("quantity", None),
        ("quantity", float("nan")),
        ("entry_price", 0.0),
        ("entry_price", float("inf")),
        ("leverage", 0.0),
        ("leverage", 1.5),
        ("leverage", "not-a-number"),
    ],
)
def test_account_leverage_rejects_invalid_relevant_position_exposure(
    monkeypatch,
    field,
    invalid_value,
):
    """A corrupt open Position cannot disappear from the account numerator."""
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config
    from aletheia.storage.models import TradeProposal

    cfg = load_config().model_copy(deep=True)
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    position = {
        "id": "position-corrupt",
        "symbol": "ETH/USDT",
        "market_type": "usdt_futures",
        "execution_mode": "okx_demo",
        "quantity": 1.0,
        "entry_price": 100.0,
        "leverage": 2.0,
    }
    position[field] = invalid_value
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [position])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    assert eng._check_account_leverage_ratio(
        proposal,
        cfg,
        unresolved_entries=[],
        pre_order_entries=[],
    ) == [
        "account_leverage_check_error:invalid_position_exposure:"
        f"position=position-corrupt:field={field}"
    ]


def test_account_leverage_counts_unlevered_spot_position_as_one_x(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config
    from aletheia.storage.models import TradeProposal

    cfg = load_config().model_copy(deep=True)
    cfg.risk.max_account_leverage_demo = 1.0
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    monkeypatch.setattr(
        eng,
        "list_all_open_positions",
        lambda: [
            {
                "id": "spot-position",
                "symbol": "ETH/USDT",
                "market_type": "spot",
                "execution_mode": "okx_demo",
                "quantity": 1.0,
                "entry_price": 100.0,
                "leverage": None,
            }
        ],
    )
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 100.0)

    violations = eng._check_account_leverage_ratio(
        proposal,
        cfg,
        unresolved_entries=[],
        pre_order_entries=[],
    )

    assert len(violations) == 1
    assert violations[0].startswith("account_leverage_ratio:")


@pytest.mark.parametrize(
    ("market_type", "leverage"),
    [
        ("usdt_futures", None),
        (None, None),
        ("spot", 2),
        ("spot", "not-a-number"),
    ],
)
def test_account_leverage_rejects_missing_or_impossible_market_leverage(
    monkeypatch,
    market_type,
    leverage,
):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config
    from aletheia.storage.models import TradeProposal

    cfg = load_config().model_copy(deep=True)
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    monkeypatch.setattr(
        eng,
        "list_all_open_positions",
        lambda: [
            {
                "id": "position-invalid-market-leverage",
                "symbol": "ETH/USDT",
                "market_type": market_type,
                "execution_mode": "okx_demo",
                "quantity": 1.0,
                "entry_price": 100.0,
                "leverage": leverage,
            }
        ],
    )
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    expected_field = "market_type" if market_type is None else "leverage"
    assert eng._check_account_leverage_ratio(
        proposal,
        cfg,
        unresolved_entries=[],
        pre_order_entries=[],
    ) == [
        "account_leverage_check_error:invalid_position_exposure:"
        "position=position-invalid-market-leverage:"
        f"field={expected_field}"
    ]


@spec_claim(
    SpecClaim(
        "RE-B24",
        "risk_engine_contract",
        "§2 check #17 equity authority",
        "L2b",
        "Account leverage reads equity from the same named account and rejects "
        "when that account's local equity snapshot is unavailable.",
        status="implemented",
    )
)
def test_account_leverage_equity_is_named_and_unknown_rejects(monkeypatch):
    import aletheia.portfolio.exchange_sync as sync
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config
    from aletheia.storage.models import TradeProposal

    cfg = load_config().model_copy(deep=True)
    monkeypatch.setattr(
        sync,
        "get_unified_cached_state",
        lambda: {
            "age_seconds": 1.0,
            "status": "healthy",
            "total_equity_usdt": 999.0,
            "accounts": [
                {"broker_id": "okx_demo", "equity_usdt": 125.0},
                {"broker_id": "binance_testnet", "equity_usdt": 874.0},
            ],
        },
    )
    bitget_calls = []

    def _bitget_state(mode=None):
        bitget_calls.append(mode)
        return {
            "age_seconds": 1.0,
            "status": "healthy",
            "equity_usdt": 75.0,
        }

    monkeypatch.setattr(sync, "get_cached_state", _bitget_state)

    assert eng._account_equity("okx_demo", cfg) == 125.0
    assert eng._account_equity("bitget_demo", cfg) == 75.0
    assert bitget_calls == ["demo"]

    monkeypatch.setattr(sync, "get_unified_cached_state", lambda: {"accounts": []})
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [])
    assert eng._check_account_leverage_ratio(
        proposal,
        cfg,
        unresolved_entries=[],
        pre_order_entries=[],
    ) == ["account_equity_unavailable:okx_demo"]


@spec_claim(
    SpecClaim(
        "RE-B27",
        "risk_engine_contract",
        "§2 check #17 equity freshness",
        "L2b",
        "External demo/live equity is usable only from a fresh, healthy cache "
        "and a healthy exact named account row.",
        status="implemented",
    )
)
@pytest.mark.parametrize(
    "cache",
    [
        {"age_seconds": 601.0, "status": "healthy", "equity_usdt": 75.0},
        {"age_seconds": 1.0, "status": "error", "equity_usdt": 75.0},
        {"age_seconds": None, "status": "healthy", "equity_usdt": 75.0},
        {"age_seconds": -1.0, "status": "healthy", "equity_usdt": 75.0},
        {"age_seconds": float("nan"), "status": "healthy", "equity_usdt": 75.0},
    ],
)
def test_external_account_equity_rejects_stale_or_unhealthy_cache(
    monkeypatch,
    cache,
):
    import aletheia.portfolio.exchange_sync as sync
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    monkeypatch.setattr(sync, "get_cached_state", lambda _mode=None: cache)
    monkeypatch.setattr(sync, "get_unified_cached_state", lambda: cache)

    assert eng._account_equity("bitget_demo", cfg) is None
    assert eng._account_equity("bitget_live_limited", cfg) is None


def test_named_external_account_requires_healthy_fresh_account_row(monkeypatch):
    import aletheia.portfolio.exchange_sync as sync
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    cache = {
        "age_seconds": 1.0,
        "status": "healthy",
        "accounts": [
            {
                "broker_id": "okx_demo",
                "equity_usdt": 125.0,
                "status": "error",
            }
        ],
    }
    monkeypatch.setattr(sync, "get_unified_cached_state", lambda: cache)

    assert eng._account_equity("okx_demo", cfg) is None


@spec_claim(
    SpecClaim(
        "RE-B25",
        "risk_engine_contract",
        "§2 monitoring account identity",
        "L2b",
        "The default live monitoring account uses exchange.name, not the unrelated "
        "market-data provider setting.",
        status="implemented",
    )
)
def test_active_live_execution_mode_uses_exchange_name():
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    cfg.exchange.mode = "live"
    cfg.exchange.name = "bitget"
    assert eng._config_active_execution_mode(cfg) == "bitget_live_limited"


@spec_claim(
    SpecClaim(
        "RE-B28",
        "risk_engine_contract",
        "§2 monitoring honesty",
        "L2b",
        "Public account-leverage monitoring reports unavailable, with no ratio or "
        "boolean safety verdict, when a relevant Position, unresolved entry, pre-Order "
        "reservation, or account equity cannot be trusted; all valid same-account "
        "exposure is counted and spot NULL leverage remains the explicit 1x case.",
        status="implemented",
    )
)
def test_public_account_leverage_counts_null_spot_leverage_as_one_x(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    monkeypatch.setattr(
        eng,
        "list_all_open_positions",
        lambda: [
            {
                "id": "spot-position",
                "symbol": "ETH/USDT",
                "market_type": "spot",
                "execution_mode": "okx_demo",
                "quantity": 2.0,
                "entry_price": 100.0,
                "current_price": None,
                "leverage": None,
            }
        ],
    )
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 200.0)

    result = eng.check_account_leverage(cfg, execution_mode="okx_demo")

    assert result["status"] == "available"
    assert result["reason"] is None
    assert result["ratio"] == 1.0
    assert result["violation"] is False
    assert result["positions"] == [
        {"symbol": "ETH/USDT", "notional": 200.0, "leverage": 1.0}
    ]


@pytest.mark.parametrize(
    ("market_type", "field", "invalid_value", "expected_field"),
    [
        ("usdt_futures", "leverage", None, "leverage"),
        (None, "leverage", None, "market_type"),
        ("usdt_futures", "leverage", "not-a-number", "leverage"),
        ("usdt_futures", "leverage", float("nan"), "leverage"),
        ("usdt_futures", "leverage", 1.5, "leverage"),
        ("usdt_futures", "leverage", 1e308, "position_exposure"),
        ("usdt_futures", "quantity", float("inf"), "quantity"),
        ("usdt_futures", "current_price", -1.0, "current_price"),
    ],
)
def test_public_account_leverage_marks_invalid_position_unavailable(
    monkeypatch,
    market_type,
    field,
    invalid_value,
    expected_field,
):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    position = {
        "id": "position-corrupt",
        "symbol": "ETH/USDT",
        "market_type": market_type,
        "execution_mode": "okx_demo",
        "quantity": 1.0,
        "entry_price": 100.0,
        "current_price": 101.0,
        "leverage": 2.0,
    }
    position[field] = invalid_value
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [position])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    result = eng.check_account_leverage(cfg, execution_mode="okx_demo")

    assert result["status"] == "unavailable"
    assert result["reason"] == "invalid_position_exposure"
    assert result["ratio"] is None
    assert result["violation"] is None
    assert result["invalid_position"] == {
        "field_category": expected_field,
        "position_id": "position-corrupt",
    }


def test_public_account_leverage_marks_unknown_equity_unavailable(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: None)

    result = eng.check_account_leverage(cfg, execution_mode="okx_demo")

    assert result["status"] == "unavailable"
    assert result["reason"] == "account_equity_unavailable:okx_demo"
    assert result["ratio"] is None
    assert result["violation"] is None


def test_public_account_leverage_marks_equity_reader_failure_unavailable(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [])
    monkeypatch.setattr(eng, "_unresolved_entry_reservations", lambda: [])
    monkeypatch.setattr(eng, "_pre_order_entry_reservations", lambda: [])

    def _equity_failure(*_args):
        raise RuntimeError("local equity reader failed")

    monkeypatch.setattr(eng, "_account_equity", _equity_failure)

    result = eng.check_account_leverage(cfg, execution_mode="okx_demo")

    assert result["status"] == "unavailable"
    assert result["reason"] == "account_equity_unavailable:okx_demo"
    assert result["ratio"] is None
    assert result["violation"] is None


def test_public_account_leverage_counts_unresolved_and_pre_order_exposure(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    cfg.risk.max_account_leverage_demo = 2.5
    same_account_order = eng._UnresolvedEntryReservation(
        order_id="okx-order",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        execution_mode="okx_demo",
        opportunity_id=None,
        price=100.0,
        leverage=2.0,
        order_quantity=1.0,
        persisted_fill_quantity=0.0,
        remaining_quantity=1.0,
        reserved_quantity=1.0,
        has_open_position=False,
    )
    other_account_order = eng._UnresolvedEntryReservation(
        order_id="bitget-order",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        execution_mode="bitget_demo",
        opportunity_id=None,
        price=1_000.0,
        leverage=3.0,
        order_quantity=1.0,
        persisted_fill_quantity=0.0,
        remaining_quantity=1.0,
        reserved_quantity=1.0,
        has_open_position=False,
    )
    same_account_pre_order = eng._PreOrderEntryReservation(
        proposal_id="okx-proposal",
        symbol="SOL/USDT",
        market_type="usdt_futures",
        execution_mode="okx_demo",
        opportunity_id=None,
        price=100.0,
        leverage=1.0,
        reserved_quantity=1.0,
    )
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [])
    monkeypatch.setattr(
        eng,
        "_unresolved_entry_reservations",
        lambda: [same_account_order, other_account_order],
    )
    monkeypatch.setattr(
        eng,
        "_pre_order_entry_reservations",
        lambda **_: [same_account_pre_order],
    )
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 100.0)

    result = eng.check_account_leverage(cfg, execution_mode="okx_demo")

    assert result["status"] == "available"
    assert result["ratio"] == 3.0
    assert result["violation"] is True


@pytest.mark.parametrize(
    ("reservation_kind", "invalid_reasons", "missing_field"),
    [
        ("unresolved", ("unresolved_entry_authority_invalid",), None),
        ("unresolved", (), "reserved_quantity"),
        ("pre_order", ("entry_risk_reservation_authority_invalid",), None),
        ("pre_order", (), "leverage"),
    ],
)
def test_account_leverage_monitor_and_gate_reject_invalid_reservation(
    monkeypatch,
    reservation_kind,
    invalid_reasons,
    missing_field,
):
    import aletheia.risk.engine as eng
    from aletheia.config.loader import load_config
    from aletheia.storage.models import TradeProposal

    cfg = load_config().model_copy(deep=True)
    proposal = TradeProposal(
        symbol="SOL/USDT",
        market_type="usdt_futures",
        direction="long",
        execution_mode="okx_demo",
        quantity=0.1,
        price=100.0,
        leverage=1,
    )
    if reservation_kind == "unresolved":
        values = {
            "order_id": "bad-order",
            "symbol": "ETH/USDT",
            "market_type": "usdt_futures",
            "execution_mode": "okx_demo",
            "opportunity_id": None,
            "price": 100.0,
            "leverage": 2.0,
            "order_quantity": 1.0,
            "persisted_fill_quantity": 0.0,
            "remaining_quantity": 1.0,
            "reserved_quantity": 1.0,
            "has_open_position": False,
            "invalid_reasons": invalid_reasons,
        }
        if missing_field is not None:
            values[missing_field] = None
        reservation = eng._UnresolvedEntryReservation(**values)
        unresolved_entries = [reservation]
        pre_order_entries = []
    else:
        values = {
            "proposal_id": "bad-proposal",
            "symbol": "ETH/USDT",
            "market_type": "usdt_futures",
            "execution_mode": "okx_demo",
            "opportunity_id": None,
            "price": 100.0,
            "leverage": 2.0,
            "reserved_quantity": 1.0,
            "invalid_reasons": invalid_reasons,
        }
        if missing_field is not None:
            values[missing_field] = None
        reservation = eng._PreOrderEntryReservation(**values)
        unresolved_entries = []
        pre_order_entries = [reservation]

    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [])
    monkeypatch.setattr(
        eng,
        "_unresolved_entry_reservations",
        lambda: unresolved_entries,
    )
    monkeypatch.setattr(
        eng,
        "_pre_order_entry_reservations",
        lambda **_: pre_order_entries,
    )
    monkeypatch.setattr(eng, "_account_equity", lambda *_: 1_000.0)

    monitor = eng.check_account_leverage(cfg, execution_mode="okx_demo")
    gate = eng._check_account_leverage_ratio(
        proposal,
        cfg,
        unresolved_entries=unresolved_entries,
        pre_order_entries=pre_order_entries,
    )

    assert monitor["status"] == "unavailable"
    assert monitor["reason"] == "invalid_entry_reservation"
    assert monitor["ratio"] is None
    assert monitor["violation"] is None
    assert gate == ["account_leverage_check_error:invalid_entry_reservation"]


def test_futures_context_keeps_unavailable_account_leverage_empty(monkeypatch):
    import aletheia.risk.engine as eng
    from aletheia.agent_shell.trading_context import _build_futures_context
    from aletheia.config.loader import load_config

    cfg = load_config().model_copy(deep=True)
    cfg.exchange.paper_execution_mode = "okx_demo"
    monkeypatch.setattr(eng, "list_all_open_positions", lambda: [])
    monkeypatch.setattr(eng, "_account_equity", lambda *_: None)

    result = _build_futures_context({"features": [], "positions": []}, cfg)

    assert result["account_leverage_ratio"] is None


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
        leverage=1,
    )
    repo.insert_trade_proposal(over)
    res_over = eng.evaluate_proposal(over)
    assert any("exceeds_per_symbol_notional" in v for v in res_over.violations), res_over.violations

    # A small add keeping the total at 85% of cap → no per-symbol violation.
    under = TradeProposal(
        symbol="SOL/USDT", market_type="usdt_futures", direction="long",
        execution_mode="okx_demo", quantity=(cap * 0.05) / 100.0, price=100.0,
        leverage=1,
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
        execution_mode="okx_demo", quantity=0.05, price=1570.0, leverage=1,
    )
    repo.insert_trade_proposal(dup)
    res = eng.evaluate_proposal(dup)
    assert any("symbol_already_open" in v for v in res.violations), (
        f"Expected symbol_already_open violation, got: {res.violations}"
    )

    # Proposal for a different symbol on same broker → must NOT trigger this violation.
    other = TradeProposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="short",
        execution_mode="okx_demo", quantity=0.001, price=60000.0, leverage=1,
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
                "portfolio_", "strategy_", "liquidation_", "family_",
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

    # 1. evaluate_proposal must be imported (alone or in a grouped import).
    tree = ast.parse(source_text)
    imported_risk_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "aletheia.risk.engine"
        for alias in node.names
    }
    assert "evaluate_proposal" in imported_risk_names, (
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
                       execution_mode="paper_internal", quantity=0.001, price=60000,
                       leverage=1)
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
                       execution_mode="paper_internal", quantity=0.001, price=60000,
                       leverage=1)
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


# --- RE-S06: check #14 rewired to scanner-family evidence gate (Position C) ---

@spec_claim(
    SpecClaim("RE-S06", "risk_engine_contract", "§2 check #14", "L2a",
              "Check #14 (live-eligibility) is evidence-based on the proposal's "
              "scanner_family (family_direction_edge: >= risk.live_family_min_samples "
              "settled outcomes AND win_rate >= live_family_min_win_rate_pct), NOT the "
              "deleted strategy-FSM state. Emits family_not_live_eligible:* and no "
              "longer references get_strategy / _LIVE_ELIGIBLE_STATES for #14.",
              status="implemented"),
)
def test_re_b17_check14_is_family_evidence_gate():
    from pathlib import Path
    src = (Path(__file__).parent.parent.parent / "aletheia" / "risk" / "engine.py").read_text(encoding="utf-8")
    # New evidence-based gate present …
    assert "family_not_live_eligible" in src, "check #14 must emit family_not_live_eligible"
    assert "_resolve_proposal_family" in src and "_family_live_edge" in src, (
        "check #14 must resolve scanner_family + query family_direction_edge"
    )
    assert "live_family_min_samples" in src and "live_family_min_win_rate_pct" in src, (
        "check #14 thresholds must be config-driven (risk.live_family_*)"
    )
    # … and the deleted strategy-FSM eligibility gate is gone.
    assert "strategy_not_live_eligible" not in src, (
        "check #14 must no longer emit the strategy-FSM token strategy_not_live_eligible"
    )
    assert "_LIVE_ELIGIBLE_STATES" not in src, (
        "the strategy-FSM eligible-states set must be removed with the rules engine"
    )


@spec_claim(
    SpecClaim(
        "RE-B30",
        "risk_engine_contract",
        "§2 paper equity not zeroed by unsettled funding",
        "L2b",
        "A paper_internal proposal submitted within the funding-settler window "
        "(economics_complete=False) must not be rejected with account_equity_unavailable "
        "or exceeds_demo_position_notional:X>0.0 when the account has sufficient cash (#316).",
        status="implemented",
    ),
)
def test_paper_internal_proposal_not_rejected_during_funding_settler_window(
    tmp_path, monkeypatch
):
    """Regression: unsettled funding economics must not zero available_balance_usdt."""
    _setup_db(tmp_path, monkeypatch)

    import math

    from aletheia.portfolio.paper_account import (
        ensure_paper_account,
        record_position_opened,
    )
    from aletheia.risk.engine import evaluate_proposal
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import Position, TradeProposal
    from aletheia.storage.sqlite import get_db

    ensure_paper_account()

    # Insert a closed futures position with unsettled funding (mimics post-close window).
    closed_at = "2026-07-22T10:00:00+00:00"
    pos = Position(
        id="unsettled-close-pos",
        symbol="ETH/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=0.1,
        entry_price=3000.0,
        current_price=3010.0,
        unrealized_pnl=0.0,
        realized_pnl=None,
        execution_mode="paper_internal",
        status="closed",
        close_reason="take_profit",
        close_category="strategy",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=False,
        outcome_exclusion_reason="incomplete_funding_economics",
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=None,
        funding_source="unavailable",
        opened_at="2026-07-22T09:00:00+00:00",
        closed_at=closed_at,
    )
    repo.insert_position(pos)
    record_position_opened(pos.id)
    # Do NOT call settle_position_closed — simulating the settler window

    from aletheia.portfolio.paper_account import get_paper_account
    account = get_paper_account(event_limit=0)
    assert account["economics_complete"] is False, "test setup: economics must be incomplete"
    assert account["available_balance_usdt"] > 0, (
        "available_balance_usdt must reflect actual cash, not zero (#316)"
    )

    from aletheia.storage.models import Opportunity
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      thesis="test", source_agent="market_observer", scanner_family="test")
    repo.insert_opportunity(opp)

    proposal = TradeProposal(
        opportunity_id=opp.id,
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        execution_mode="paper_internal",
        order_type="market",
        quantity=0.001,
        price=100.0,
        leverage=1,
        status="approved",
    )
    repo.insert_trade_proposal(proposal)

    result = evaluate_proposal(proposal)

    violation_strings = " ".join(result.violations)
    assert "account_equity_unavailable" not in violation_strings, (
        f"account_equity_unavailable must not fire during settler window; "
        f"violations: {result.violations}"
    )
    assert "exceeds_demo_position_notional" not in violation_strings or (
        ">0.0" not in violation_strings
    ), (
        f"effective_cap must not collapse to 0.0 during settler window; "
        f"violations: {result.violations}"
    )

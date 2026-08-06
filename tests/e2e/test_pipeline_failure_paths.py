"""Failure path e2e: system behavior when signals fail, risk rejects, or kill switch fires.

The Golden Path (test_active_mainline_golden_path.py) proves the happy path chain works.
These tests prove the system fails *correctly* when it should fail — each test verifies
both the returned result AND the resulting DB state, because silent failures (code that
runs without error but writes wrong state) are the hardest class of bugs to catch.

Four断点场景:
  1. 信号低于置信阈值 → ranker 返回空，paper trader 静默收尾，DB 无副作用
  2. 风险白名单拒绝   → proposal 以 rejected 状态写入 DB，不是静默丢弃
  3. 今日亏损超限     → 新 proposal 被拒，拒绝原因可查
  4. Kill switch 激活  → operator loop 不写任何 config_patch，只发 notify_operator
"""

from __future__ import annotations

import pytest

from aletheia.config.loader import load_config, save_config
from aletheia.storage import repositories as repo
from aletheia.storage.models import Opportunity


# ─── 1. Zero eligible candidates ─────────────────────────────────────────────

async def test_paper_trader_no_eligible_candidates_completes_without_side_effects(
    e2e_db, monkeypatch, tmp_path
):
    """When all opportunities are below the confidence threshold, paper trader exits cleanly.

    Verifies the seam between ranker (reads DB) and paper trader loop:
    - errors == [] (no crash or silent exception swallowed)
    - DB contains zero pi_decisions and zero trade_proposals
    """
    import aletheia.config.loader as loader
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agents.paper_trader import run_paper_trader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.trading.min_confidence_to_trade = "high"
    cfg.agent.autonomous_learning = False
    save_config(cfg)

    # Seed only below-threshold opportunities — observer would produce these when
    # signals are weak; the ranker must filter them out without crashing.
    repo.insert_opportunity(Opportunity(
        symbol="ETH/USDT", market="spot", direction_bias="long",
        confidence="speculative", confidence_score=35,
        source_agent="market_observer", scanner_family="momentum",
        status="open", execution_eligible=True,
    ))
    repo.insert_opportunity(Opportunity(
        symbol="SOL/USDT", market="spot", direction_bias="long",
        confidence="medium", confidence_score=55,
        source_agent="market_observer", scanner_family="momentum",
        status="open", execution_eligible=True,
    ))

    init_tool_registry()
    result = await run_paper_trader(
        permission_mode="Paper", run_id="failure-path-no-candidates"
    )

    assert result["errors"] == [], f"Expected no errors, got: {result['errors']}"

    decisions = e2e_db.execute("SELECT * FROM pi_decisions").fetchall()
    proposals = e2e_db.execute("SELECT * FROM trade_proposals").fetchall()
    assert len(decisions) == 0, (
        "No pi_decisions should exist when no candidates pass the confidence threshold"
    )
    assert len(proposals) == 0, (
        "No proposals should exist when no candidates pass the confidence threshold"
    )


# ─── 2. Risk whitelist rejection ─────────────────────────────────────────────

def test_risk_rejects_symbol_not_in_whitelist_proposal_archived(e2e_db, monkeypatch, tmp_path):
    """A proposal for an unlisted symbol is rejected and persisted in DB with a clear reason.

    The failure mode being guarded: a silent drop (no row written) would look like the
    proposal was never submitted, making the rejection invisible to audit and the UI.
    """
    import aletheia.config.loader as loader
    from aletheia.execution.gateway import submit_proposal

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._invalidate_config_cache()

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTCUSDT", "ETHUSDT"]
    save_config(cfg)

    result = submit_proposal(
        symbol="SOL/USDT",
        market_type="spot",
        direction="long",
        quantity=0.5,
        price=200.0,
        execution_mode="paper_internal",
    )

    assert result["status"] == "rejected", (
        f"Expected rejected, got {result['status']!r} — whitelist gate may be broken"
    )
    # Rejection reason must be visible
    violations = result.get("risk_violations", [])
    assert any("symbol_not_allowed" in v or "symbol_blocked" in v for v in violations), (
        f"Expected symbol rejection in violations, got: {violations}"
    )

    # Proposal must be archived in DB — not silently dropped
    proposals = e2e_db.execute("SELECT * FROM trade_proposals").fetchall()
    assert len(proposals) == 1, (
        "Rejected proposal must still be written to DB for audit trail"
    )
    assert proposals[0]["status"] == "rejected"


# ─── 3. Daily loss limit exceeded ─────────────────────────────────────────────

def test_risk_rejects_when_daily_loss_limit_exceeded_proposal_archived(e2e_db, monkeypatch, tmp_path):
    """When today's realized loss exceeds the configured limit, new proposals are rejected.

    The account loss read (DB → snapshot) is fixed to a known loss. The business
    logic under test is the Risk Engine's decision to reject given that snapshot,
    and the gateway's archival of the rejected proposal.
    """
    import aletheia.config.loader as loader
    from aletheia.portfolio.account_loss import AccountLossSnapshot
    from aletheia.execution.gateway import submit_proposal

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._invalidate_config_cache()

    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    cfg.risk.starting_capital_usdt = 1000.0
    cfg.risk.max_daily_loss_pct = 2.0   # daily limit = $20
    cfg.risk.symbol_filter_mode = "off"
    save_config(cfg)

    # Fix the account snapshot to show a $25 loss today — exceeds the $20 limit.
    # This substitutes the DB ledger read (I/O boundary), not the Risk Engine logic.
    monkeypatch.setattr(
        "aletheia.risk.engine.authoritative_account_realized_loss",
        lambda mode: AccountLossSnapshot(
            available=True,
            realized_pnl_usdt=-25.0,
            loss_usdt=25.0,
            source="test_fixture",
            reason="",
            event_count=3,
        ),
    )

    result = submit_proposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        price=60_000.0,
        execution_mode="paper_internal",
    )

    assert result["status"] == "rejected", (
        f"Expected rejection due to daily loss limit, got: {result['status']!r}"
    )
    violations = result.get("risk_violations", [])
    assert any("max_daily_loss_exceeded" in v for v in violations), (
        f"Expected daily loss violation, got: {violations}"
    )

    proposals = e2e_db.execute("SELECT * FROM trade_proposals").fetchall()
    assert len(proposals) == 1
    assert proposals[0]["status"] == "rejected"


# ─── 4. Kill switch active ────────────────────────────────────────────────────

def test_kill_switch_suppresses_config_patches_emits_notify(e2e_db, monkeypatch, tmp_path):
    """With kill switch active, the operator loop writes no config changes.

    The断点 being tested: operator loop is still scheduled and runs (it is NOT
    skipped entirely), but all control actions (tighten_confidence, reduce_sizing,
    etc.) are suppressed. Only a notify_operator request should be dispatched.

    Verifies:
    - audit_events has zero config_patch rows (config was not changed)
    - At least one notify_operator result is requested or rate_limited
    - The returned assessment correctly reflects kill_switch=True
    """
    import aletheia.config.loader as loader
    import aletheia.agent_shell.operator_loop as operator_loop
    import aletheia.agent_shell.trading_context as trading_context

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    loader._invalidate_config_cache()

    cfg = load_config()
    cfg.runtime.paused = False
    cfg.runtime.kill_switch_active = True
    cfg.operator.loop_enabled = True
    cfg.trading.min_confidence_to_trade = "high"
    save_config(cfg)

    # Provide deterministic signal health so the assessment has context to act on.
    # Without this, _get_data_health_summary() tries to read from a live CCXT
    # connection which is not available in the isolated e2e environment.
    monkeypatch.setattr(
        trading_context, "_get_data_health_summary",
        lambda: {
            "ohlcv_ready": True,
            "members_with_full_coverage": 8,
            "total_members": 8,
            "signal_win_rate_24h": 30.0,
            "signal_status": "degraded",
        },
    )

    summary = operator_loop.run_operator_loop(trigger="failure-path-kill-switch")

    assert summary["assessment"]["kill_switch"] is True, (
        "Assessment must reflect kill_switch=True"
    )

    config_patches = e2e_db.execute(
        "SELECT event_type, entity_id FROM audit_events WHERE event_type='config_patch'"
    ).fetchall()
    assert len(config_patches) == 0, (
        f"Kill switch must suppress all config_patch writes, found: {[dict(r) for r in config_patches]}"
    )

    notify_results = [r for r in summary["results"] if r.get("action") == "notify_operator"]
    assert len(notify_results) >= 1, (
        "Kill switch must trigger at least one notify_operator action"
    )
    # Each notify must be in a terminal state — not silently swallowed
    for r in notify_results:
        assert r.get("status") in {"requested", "rate_limited"}, (
            f"notify_operator had unexpected status: {r}"
        )

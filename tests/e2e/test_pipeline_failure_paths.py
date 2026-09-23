"""External proposal rejection remains durable; kill switch suppresses control writes.

Checks symbol rejection, daily-loss rejection and kill-switch governance against
isolated runtime state. Risk decisions and rejected proposals remain queryable.
"""

from __future__ import annotations

from aletheia.config.loader import load_config, save_config


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
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.risk.symbol_filter_mode = "whitelist"
    cfg.risk.symbol_list = ["BTCUSDT", "ETHUSDT"]
    save_config(cfg)

    result = submit_proposal(
        symbol="SOL/USDT",
        market_type="spot",
        direction="long",
        quantity=0.5,
        price=200.0,
        execution_mode="bitget_demo",
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
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "bitget_demo"
    cfg.risk.demo.capital.base_source = "custom"
    cfg.risk.demo.capital.base_usdt = 1000.0
    cfg.risk.demo.loss.max_daily_loss_pct = 2.0   # daily limit = $20
    cfg.risk.symbol_filter_mode = "blacklist"
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
        execution_mode="bitget_demo",
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




"""Tests for the full §21.3 risk-check matrix (the checks added beyond the initial 6).

Covers: max_daily_loss (global + kill-switch trip), data_freshness (live),
account_mode_match, strategy_lifecycle_state (live), max_leverage (futures),
max_single_trade_risk (live, stop-based). Each new check must NOT break the
existing paper flow, which stays permissive.
"""

import sqlite3
from datetime import datetime, timezone, timedelta

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import TradeProposal, Position, MarketSnapshot
from aletheia.storage import repositories as repo
from aletheia.risk.engine import evaluate_proposal


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()


def _unlock_live_auto(tmp_path):
    """Set config to Auto + live_limited + exchange.mode=live so live checks are reachable."""
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.live.state = "live_limited"
    cfg.exchange.mode = "live"
    save_config(cfg)


# --- max_daily_loss (global brake) ---

def test_daily_loss_within_limit_approves(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    # No closed positions today → realized pnl 0 → paper proposal still approves.
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="paper_internal", quantity=0.001, price=60000)
    repo.insert_trade_proposal(p)
    assert evaluate_proposal(p).approved is True


def test_daily_loss_exceeded_blocks_and_trips_kill_switch(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.portfolio.pnl import close_position
    from aletheia.risk import kill_switch
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.kill_switch_enabled_demo = True  # default False; paper_internal is demo-mode
    save_config(cfg)

    # Capital base = max_live_limited_cap_usdt default 100; max_daily_loss_pct 1.0 → $1 limit.
    pos = Position(symbol="BTCUSDT", market_type="spot", direction="long",
                   quantity=1.0, entry_price=100.0, execution_mode="paper_internal")
    repo.insert_position(pos)
    close_position(pos.id, exit_price=90.0)  # realized -10, exceeds $1 limit

    p = TradeProposal(symbol="ETHUSDT", market_type="spot", direction="long",
                      execution_mode="paper_internal", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("max_daily_loss" in v for v in result.violations)
    # auto-tripped the global brake
    assert kill_switch.is_active() is True


# --- account_mode_match ---

def test_account_mode_mismatch_blocks_live_when_exchange_demo(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.live.state = "live_limited"
    cfg.exchange.mode = "demo"  # but trying a live order
    save_config(cfg)

    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("account_mode_mismatch" in v for v in result.violations)


# --- data_freshness (live) ---

def test_stale_market_data_blocks_live(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    # Insert a stale snapshot (2 hours old).
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000)
    snap.snapshot_at = stale
    repo.insert_market_snapshot(snap)

    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("stale_market_data" in v for v in result.violations)


def test_fresh_market_data_passes_freshness(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000)  # fresh now
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not any("stale_market_data" in v for v in result.violations)


# --- max_total_live_exposure (live) ---

def test_live_exposure_cap_blocks_new_order(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)

    # Existing live exposure already near limit.
    from aletheia.storage.models import Position
    repo.insert_position(Position(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        entry_price=10000.0,
        current_price=10000.0,
        execution_mode="bitget_live_limited",
    ))
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=10000)
    repo.insert_market_snapshot(snap)

    p = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="bitget_live_limited",
        quantity=0.0001,
        price=10000,
    )
    # current_exposure ~1, proposal adds ~1 -> cap is 5 (5% of 100).
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("max_total_live_exposure_exceeded" in v for v in result.violations)


def test_live_exposure_cap_allows_order_under_limit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)

    snap = MarketSnapshot(symbol="ETHUSDT", market_type="spot", price=2000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(
        symbol="ETHUSDT",
        market_type="spot",
        direction="long",
        execution_mode="bitget_live_limited",
        quantity=0.0001,
        price=2000,
        stop_price=1990,
    )
    # projected exposure ~0.2 + existing 0 exposure -> within 5.
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not any("max_total_live_exposure_exceeded" in v for v in result.violations)


# --- max_leverage (futures) ---

def test_demo_leverage_over_limit_blocks(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Paper"; save_config(cfg)
    # demo futures leverage limit default = 5
    p = TradeProposal(symbol="BTCUSDT", market_type="usdt_futures", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=60000)
    p.leverage = 10
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("exceeds_max_leverage" in v for v in result.violations)


def test_demo_leverage_within_limit_ok(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Paper"; save_config(cfg)
    p = TradeProposal(symbol="BTCUSDT", market_type="usdt_futures", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=60000)
    p.leverage = 3
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not any("leverage" in v for v in result.violations)


# --- max_single_trade_risk (live, stop-based) ---

def test_single_trade_risk_exceeded_blocks_live(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    # cap=100, single_trade_risk_pct=0.25 → max risk $0.25.
    # entry 60000, stop 59000 (dist 1000), qty 0.001 → risk = $1 > $0.25 → block.
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.001, price=60000)
    p.stop_price = 59000
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("exceeds_single_trade_risk" in v for v in result.violations)


def test_missing_stop_price_fails_single_trade_risk_check(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000)
    # no stop_price → this is a hard fail in strict live mode.
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("max_single_trade_risk_missing_stop_price" in v for v in result.violations)


# --- scanner-family live-eligibility gate (#14, Position C 2026-07-02) ---
# The strategy-FSM eligibility gate was replaced by an evidence gate on the
# proposal's scanner_family (family_direction_edge clean-window WR + samples).

def test_live_family_without_evidence_rejected(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    from aletheia.storage.models import Opportunity
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      scanner_family="momentum")
    repo.insert_opportunity(opp)
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000,
                      opportunity_id=opp.id)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    # family has zero settled outcomes → no_evidence → not live-eligible
    assert any("family_not_live_eligible" in v for v in result.violations)


def test_live_without_resolvable_family_rejected(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(
        symbol="BTCUSDT",
        market_type="spot",
        direction="long",
        execution_mode="bitget_live_limited",
        quantity=0.0001,
        price=60000,
    )
    # No opportunity_id → scanner_family can't be resolved → fail-closed reject.
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("family_not_live_eligible:unknown" in v for v in result.violations)


def test_live_family_with_clean_window_edge_passes_gate14(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    from datetime import datetime, timezone
    from aletheia.storage.models import Opportunity, UniverseForwardOutcome
    now = datetime.now(timezone.utc).isoformat()
    # 30 winning settled outcomes for (momentum, long) in the 7d clean window.
    for i in range(30):
        repo.insert_forward_outcome(UniverseForwardOutcome(
            member_id=f"m{i}", snapshot_id="snap1", symbol="BTC/USDT",
            scanner_family="momentum", direction="long", horizon="24h",
            entry_price_usd=100.0, due_at=now, settled_at=now,
            exit_price_usd=105.0, forward_return_pct=5.0, status="settled",
        ))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      scanner_family="momentum")
    repo.insert_opportunity(opp)
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000,
                      opportunity_id=opp.id, stop_price=59000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    # >=30 wins, WR=100% → check #14 passes (other live checks may still fire).
    assert not any("family_not_live_eligible" in v for v in result.violations), (
        f"family gate should pass with 30 winning outcomes, got: {result.violations}"
    )


# --- demo/paper bounds (§2.3: paper evidence must be built from bounded trades) ---

def test_demo_position_notional_bounded(tmp_path, monkeypatch):
    """A paper order above the demo notional cap is rejected (was unbounded)."""
    setup_env(tmp_path, monkeypatch)
    # default max_demo_position_notional_usdt = 200; 0.01 * 60000 = 600 > 200
    p = TradeProposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.01, price=60000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("exceeds_demo_position_notional" in v for v in result.violations)
    # Distinct token: it must NOT masquerade as the live notional violation.
    assert not any("exceeds_max_position_notional" in v for v in result.violations)


def test_demo_single_trade_risk_bounded(tmp_path, monkeypatch):
    """A paper order whose loss-at-stop exceeds the demo risk cap is rejected."""
    setup_env(tmp_path, monkeypatch)
    # demo cap = starting_capital(100) * 2% = $2. Stop 10% away on $150 notional
    # = 0.0025 * 60000 * 0.10 = $15 risk > $2. Notional 150 < 200 so only the
    # risk check should trip.
    p = TradeProposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.0025, price=60000,
        stop_price=54000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("exceeds_demo_single_trade_risk" in v for v in result.violations)
    assert not any("exceeds_single_trade_risk:" in v for v in result.violations)


def test_demo_bounds_within_limits_approve(tmp_path, monkeypatch):
    """A normal small paper trade with a sane stop is still approved."""
    setup_env(tmp_path, monkeypatch)
    p = TradeProposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.0004, price=60000,
        stop_price=58800,  # 2% stop -> risk = 0.0004*60000*0.02 = $0.48 < $2
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is True, result.violations


# --- kill-switch TOCTOU serialization (§2.3) ---

def test_evaluate_proposal_is_serialized(tmp_path, monkeypatch):
    """evaluate_proposal holds a lock so concurrent evaluations can't interleave
    around a kill-switch trip. We assert the lock is actually engaged during a call."""
    setup_env(tmp_path, monkeypatch)
    import threading
    from aletheia.risk import engine

    seen_locked = {"v": False}
    real_locked = engine._evaluate_proposal_locked

    def spy(proposal):
        # While inside the evaluation, the module lock must be held (non-acquirable).
        seen_locked["v"] = not engine._eval_lock.acquire(blocking=False)
        if not seen_locked["v"]:
            engine._eval_lock.release()
        return real_locked(proposal)

    monkeypatch.setattr(engine, "_evaluate_proposal_locked", spy)

    p = TradeProposal(
        symbol="BTCUSDT", market_type="spot", direction="long",
        execution_mode="paper_internal", quantity=0.0004, price=60000, stop_price=58800,
    )
    repo.insert_trade_proposal(p)
    engine.evaluate_proposal(p)
    assert seen_locked["v"] is True


def test_kill_switch_trip_blocks_subsequent_proposal(tmp_path, monkeypatch):
    """After one proposal trips the daily-loss kill switch, the NEXT proposal must
    see it engaged (config re-read inside the serialized evaluation)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.risk.engine import evaluate_proposal

    # Force a realized daily loss beyond the limit so the next eval trips the switch.
    # Seed a closed losing position large enough to breach max_daily_loss.
    cfg = load_config()
    cfg.risk.max_daily_loss_pct = 1.0  # 1% of cap(100) = $1 limit
    cfg.risk.kill_switch_enabled_demo = True  # default False; paper_internal is demo-mode
    save_config(cfg)
    repo.insert_position(Position(
        symbol="BTCUSDT", market_type="spot", direction="long",
        quantity=0.01, entry_price=60000, current_price=54000,
        execution_mode="paper_internal", status="closed", realized_pnl=-50.0,
        closed_at=datetime.now(timezone.utc).isoformat(),
    ))

    p1 = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                       execution_mode="paper_internal", quantity=0.0004, price=60000, stop_price=58800)
    repo.insert_trade_proposal(p1)
    r1 = evaluate_proposal(p1)
    assert any("max_daily_loss_exceeded" in v for v in r1.violations)

    # Kill switch is now persisted; a brand-new proposal must be rejected for it.
    p2 = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                       execution_mode="paper_internal", quantity=0.0004, price=60000, stop_price=58800)
    repo.insert_trade_proposal(p2)
    r2 = evaluate_proposal(p2)
    assert "kill_switch_active" in r2.violations

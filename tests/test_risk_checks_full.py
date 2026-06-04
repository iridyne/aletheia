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
    cfg.agent.permission_mode = "Auto"
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
    cfg.agent.permission_mode = "Auto"
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
    cfg = load_config(); cfg.agent.permission_mode = "PaperAuto"; save_config(cfg)
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
    cfg = load_config(); cfg.agent.permission_mode = "PaperAuto"; save_config(cfg)
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


# --- strategy_lifecycle_state (live) ---

def test_live_requires_live_eligible_strategy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    from aletheia.trading.strategy_lifecycle import create_strategy
    s = create_strategy("Draft Strat")  # state = draft, not live-eligible
    snap = MarketSnapshot(symbol="BTCUSDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTCUSDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000,
                      strategy_id=s.id)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("strategy_not_live_eligible" in v for v in result.violations)


def test_live_requires_strategy_id(tmp_path, monkeypatch):
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
    # No strategy_id on a live proposal is not eligible for risk approval.
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("strategy_not_live_eligible" in v for v in result.violations)

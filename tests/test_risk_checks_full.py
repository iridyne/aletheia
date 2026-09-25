"""Tests for the full §21.3 risk-check matrix (the checks added beyond the initial 6).

Covers: max_daily_loss (global + kill-switch trip), data_freshness (live),
account_mode_match, strategy_lifecycle_state (live), max_leverage (futures),
max_single_trade_risk (live, stop-based), and bounded external Demo trades.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aletheia.risk.engine import evaluate_proposal
from aletheia.storage import repositories as repo
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import (
    MarketSnapshot,
    TradeProposal,
)
from aletheia.storage.sqlite import reset_db


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
    cfg = loader.load_config()
    cfg.exchange.paper_execution_mode = "bitget_demo"
    loader.save_config(cfg)
    monkeypatch.setattr("aletheia.risk.engine._account_equity", lambda *_args: 100.0)


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
    # No closed positions today → realized pnl 0 → external Demo proposal still approves.
    from tests.risk_trade_fixture import approvable_risk_trade
    trade = approvable_risk_trade(
        monkeypatch, symbol="BTC/USDT", market_type="spot", direction="long", price=60000,
    )
    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=60000, **trade)
    repo.insert_trade_proposal(p)
    assert evaluate_proposal(p).approved is True


def test_daily_loss_exceeded_blocks_and_trips_kill_switch(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    from aletheia.risk import kill_switch
    cfg = load_config()
    cfg.risk.kill_switch_enabled_demo = True  # default False; bitget_demo is demo-mode
    cfg.risk.max_daily_loss_pct = 1.0  # off by default; this test covers the enabled check
    save_config(cfg)

    # The Demo denominator in this test is stubbed external account equity ($100); this
    # $10 loss is well beyond the 1% daily-loss limit configured above.
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    _insert_closed_position_via_producer(
        position_id="daily-loss-position",
        symbol="BTC/USDT",
        quantity=1.0,
        entry_price=100.0,
        market_type="spot", direction="long", execution_mode="bitget_demo",
        realized_pnl=-10.0, closed_at=datetime.now(timezone.utc).isoformat(),
    )

    p = TradeProposal(symbol="ETH/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=3000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is False
    assert any("max_daily_loss" in v for v in result.violations)
    # Auto-trip is scoped to the evaluated Demo policy.
    assert kill_switch.is_active("demo") is True
    assert kill_switch.is_active("global") is False


# --- account_mode_match ---

def test_account_mode_mismatch_blocks_live_when_exchange_demo(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Full"
    cfg.live.state = "live_limited"
    cfg.exchange.mode = "demo"  # but trying a live order
    save_config(cfg)

    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
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
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000, exchange="bitget")
    snap.snapshot_at = stale
    repo.insert_market_snapshot(snap)

    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("stale_market_data" in v for v in result.violations)


def test_fresh_market_data_passes_freshness(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000, exchange="bitget")  # fresh now
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not any("stale_market_data" in v for v in result.violations)


@pytest.mark.parametrize("mode,venue", [
    ("bitget_demo", "bitget"), ("bitget_live_limited", "bitget"),
    ("okx_demo", "okx"), ("okx_live_limited", "okx"),
    ("binance_testnet", "binance"), ("binance_live_limited", "binance"),
])
@pytest.mark.parametrize("exact", [False, True])
def test_risk_freshness_binds_proposal_venue_and_market(tmp_path, monkeypatch, mode, venue, exact):
    setup_env(tmp_path, monkeypatch)
    if mode.endswith("live_limited"):
        _unlock_live_auto(tmp_path)
    repo.insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT", market_type="spot", price=60000, exchange=venue,
    ))
    repo.insert_market_snapshot(MarketSnapshot(
        symbol="BTC/USDT", market_type="perp", price=60000,
        exchange=venue if exact else "other-venue",
    ))
    proposal = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                             execution_mode=mode, quantity=0.0001, price=60000, leverage=1)
    result = evaluate_proposal(proposal, decision_role="preflight")
    token = "stale_market_data:" if mode.endswith("live_limited") else "stale_market_data_demo:"
    assert any(v.startswith(token) for v in result.violations) is not exact


def test_future_market_data_fails_closed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000, exchange="bitget")
    snap.snapshot_at = future
    repo.insert_market_snapshot(snap)

    from aletheia.risk.engine import _market_data_age_seconds

    assert _market_data_age_seconds("BTC/USDT", "spot", venue="bitget") is None


# --- max_total_live_exposure (live) ---

def test_live_exposure_cap_blocks_new_order(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)

    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.risk.live.capital.base_source = "custom"
    cfg.risk.live.capital.base_usdt = 100.0
    save_config(cfg)

    # Existing live exposure already near limit.
    from aletheia.storage.models import Position
    repo.insert_position(Position(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        quantity=0.001,
        entry_price=10000.0,
        current_price=10000.0,
        execution_mode="bitget_live_limited",
    ))
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=10000)
    repo.insert_market_snapshot(snap)

    p = TradeProposal(
        symbol="BTC/USDT",
        market_type="spot",
        direction="long",
        execution_mode="bitget_live_limited",
        quantity=0.0001,
        price=10000,
    )
    # Existing exposure is $10 and the proposal adds $1, exceeding the $5 cap.
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not any(v.startswith("capital_base_unavailable:") for v in result.violations)
    assert any("max_total_live_exposure_exceeded" in v for v in result.violations)


def test_live_exposure_cap_allows_order_under_limit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)

    snap = MarketSnapshot(symbol="ETH/USDT", market_type="spot", price=2000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(
        symbol="ETH/USDT",
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
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.risk.max_futures_leverage_demo = 5
    save_config(cfg)
    p = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=60000)
    p.leverage = 10
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("exceeds_max_leverage" in v for v in result.violations)


def test_demo_leverage_within_limit_ok(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config(); cfg.agent.permission_mode = "Paper"; save_config(cfg)
    p = TradeProposal(symbol="BTC/USDT", market_type="usdt_futures", direction="long",
                      execution_mode="bitget_demo", quantity=0.001, price=60000)
    p.leverage = 3
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not any("leverage" in v for v in result.violations)


# --- max_single_trade_risk (live, stop-based) ---

def test_single_trade_risk_exceeded_blocks_live(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)

    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.risk.live.capital.base_source = "custom"
    cfg.risk.live.capital.base_usdt = 100.0
    save_config(cfg)

    # cap=100, single_trade_risk_pct=0.25 → max risk $0.25.
    # entry 60000, stop 59000 (dist 1000), qty 0.001 → risk = $1 > $0.25 → block.
    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.001, price=60000)
    p.stop_price = 59000
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert not any(v.startswith("capital_base_unavailable:") for v in result.violations)
    assert any("exceeds_single_trade_risk" in v for v in result.violations)


def test_missing_stop_price_fails_single_trade_risk_check(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
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
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000,
                      opportunity_id=opp.id)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    # family has zero settled outcomes → no_evidence → not live-eligible
    assert any("family_not_live_eligible" in v for v in result.violations)


def test_live_without_resolvable_family_rejected(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(
        symbol="BTC/USDT",
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


@pytest.mark.parametrize(
    ("edge_threshold", "cost_model_version", "raw_update", "expect_no_evidence"),
    (
        (0.05, "v1", None, False),
        (0.05, "legacy-v0", None, True),
        (None, "v1", None, True),
        (0.05, None, None, True),
        (0.05, "\t", None, True),
        (0.05, "\n", None, True),
        (0.05, "\N{NO-BREAK SPACE}", None, True),
        (0.05, "v1", ("net_return_pct", "malformed"), True),
        (0.05, "v1", ("net_return_pct", b"4.73"), True),
        (0.05, "v1", ("estimated_fee_pct", None), True),
        (0.05, "v1", ("estimated_spread_slippage_pct", None), True),
        (0.05, "v1", ("estimated_funding_pct", None), True),
        (0.05, "v1", ("estimated_fee_pct", -0.12), True),
        (0.05, "v1", ("estimated_spread_slippage_pct", -0.15), True),
        (0.05, "v1", ("estimated_funding_pct", float("inf")), True),
        (0.05, "v1", ("net_return_pct", 5.0), True),
    ),
)
def test_live_family_gate_requires_complete_edge_evidence(
    tmp_path, monkeypatch, edge_threshold, cost_model_version, raw_update,
    expect_no_evidence,
):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    from datetime import datetime, timezone

    from aletheia.storage.models import Opportunity, UniverseForwardOutcome
    now = datetime.now(timezone.utc).isoformat()
    # 30 winning settled outcomes for (momentum, long) in the 7d clean window.
    for i in range(30):
        repo.insert_forward_outcome(UniverseForwardOutcome(
            member_id=f"m{i}", snapshot_id="snap1", symbol="BTC/USDT",
            venue="bitget",
            scanner_family="momentum", direction="long", horizon="24h",
            entry_price_usd=100.0, due_at=now, settled_at=now,
            exit_price_usd=105.0, forward_return_pct=5.0,
            gross_return_pct=5.0, directional_return_pct=5.0,
            estimated_fee_pct=0.12, estimated_spread_slippage_pct=0.15,
            estimated_funding_pct=0.0, net_return_pct=4.73,
            min_tradable_edge_pct=edge_threshold, cost_model_version=cost_model_version,
            settlement_price_source="horizon_ohlcv", settlement_lag_sec=0.0,
            market_type="spot", status="settled",
        ))
    if raw_update is not None:
        from aletheia.storage.sqlite import get_db
        column, value = raw_update
        assert column in {
            "net_return_pct", "estimated_fee_pct",
            "estimated_spread_slippage_pct", "estimated_funding_pct",
        }
        get_db().execute(
            f"UPDATE universe_forward_outcomes SET {column}=? WHERE snapshot_id='snap1'",
            (value,),
        )
        get_db().commit()
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      scanner_family="momentum")
    repo.insert_opportunity(opp)
    snap = MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000)
    repo.insert_market_snapshot(snap)
    p = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                      execution_mode="bitget_live_limited", quantity=0.0001, price=60000,
                      opportunity_id=opp.id, stop_price=59000)
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    family_violations = [
        violation for violation in result.violations
        if "family_not_live_eligible" in violation
    ]
    if expect_no_evidence:
        assert family_violations == ["family_not_live_eligible:momentum:no_evidence"]
    else:
        assert family_violations == []


def test_live_family_edge_selects_matching_market_type(monkeypatch):
    from aletheia.risk.engine import _family_live_edge
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    calls = []

    def _edge_rows(**kwargs):
        calls.append(kwargs)
        return [
            {
                "scanner_family": "momentum", "direction": "long",
                "market_type": "spot", "cost_model_version": COST_MODEL_VERSION,
                "n": 30,
            },
            {
                "scanner_family": "momentum", "direction": "long",
                "market_type": "perp", "cost_model_version": COST_MODEL_VERSION,
                "n": 12,
            },
        ]

    monkeypatch.setattr(
        repo,
        "family_direction_edge",
        _edge_rows,
    )

    assert _family_live_edge("momentum", "long", "spot")["n"] == 30
    assert _family_live_edge("momentum", "long", "usdt_futures")["n"] == 12
    assert calls == [
        {"within_days": 7, "cost_model_version": COST_MODEL_VERSION},
        {"within_days": 7, "cost_model_version": COST_MODEL_VERSION},
    ]

    monkeypatch.setattr(
        repo,
        "family_direction_edge",
        lambda **_: [{
            "scanner_family": "momentum", "direction": "long",
            "market_type": "spot", "cost_model_version": "legacy-v0", "n": 30,
        }],
    )
    assert _family_live_edge("momentum", "long", "spot") is None


def test_live_family_gate_does_not_pool_old_cost_models(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _unlock_live_auto(tmp_path)
    from datetime import datetime, timezone

    from aletheia.storage.models import Opportunity, UniverseForwardOutcome
    from aletheia.universe.outcome_economics import COST_MODEL_VERSION

    now = datetime.now(timezone.utc).isoformat()

    def _outcome(index: int, version: str) -> UniverseForwardOutcome:
        return UniverseForwardOutcome(
            member_id=f"mixed-{version}-{index}", snapshot_id="mixed-models",
            symbol="BTC/USDT", venue="bitget", scanner_family="momentum", direction="long",
            horizon="24h", entry_price_usd=100.0, due_at=now, settled_at=now,
            exit_price_usd=105.0, forward_return_pct=5.0,
            gross_return_pct=5.0, directional_return_pct=5.0,
            estimated_fee_pct=0.12, estimated_spread_slippage_pct=0.15,
            estimated_funding_pct=0.0, net_return_pct=4.73,
            min_tradable_edge_pct=0.05, cost_model_version=version,
            settlement_price_source="horizon_ohlcv", settlement_lag_sec=0.0,
            market_type="spot", status="settled",
        )

    repo.insert_forward_outcomes_batch([
        *(_outcome(index, "legacy-v0") for index in range(30)),
        *(_outcome(index, COST_MODEL_VERSION) for index in range(29)),
    ])
    opp = Opportunity(
        symbol="BTC/USDT", market="spot", direction_bias="long",
        scanner_family="momentum",
    )
    repo.insert_opportunity(opp)
    repo.insert_market_snapshot(
        MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000)
    )

    def _proposal() -> TradeProposal:
        proposal = TradeProposal(
            symbol="BTC/USDT", market_type="spot", direction="long",
            execution_mode="bitget_live_limited", quantity=0.0001, price=60000,
            opportunity_id=opp.id, stop_price=59000,
        )
        repo.insert_trade_proposal(proposal)
        return proposal

    before = evaluate_proposal(_proposal())
    assert "family_not_live_eligible:momentum:samples=29<30" in before.violations

    repo.insert_forward_outcome(_outcome(29, COST_MODEL_VERSION))
    after = evaluate_proposal(_proposal())
    assert not any(
        "family_not_live_eligible" in violation for violation in after.violations
    )


# --- external Demo bounds ---

def test_demo_position_notional_bounded(tmp_path, monkeypatch):
    """An external Demo order above the demo margin cap is rejected (was unbounded)."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.max_demo_position_margin_usdt = 200.0
    save_config(cfg)
    # Configured margin cap $200; 0.01 * 60000 = $600 exceeds it.
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=0.01, price=60000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("exceeds_demo_position_margin" in v for v in result.violations)
    # Distinct token: it must NOT masquerade as the live margin violation.
    assert not any("exceeds_max_position_margin" in v for v in result.violations)


def test_demo_single_trade_risk_bounded(tmp_path, monkeypatch):
    """An external Demo order whose loss-at-stop exceeds the demo risk cap is rejected."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.risk.max_demo_single_trade_risk_pct = 2.0
    save_config(cfg)
    # demo cap = account_equity(100) * 2% = $2. Stop 10% away on $150 notional
    # = 0.0025 * 60000 * 0.10 = $15 risk > $2. Notional 150 < 200 so only the
    # risk check should trip.
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=0.0025, price=60000,
        stop_price=54000,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert any("exceeds_demo_single_trade_risk" in v for v in result.violations)
    assert not any("exceeds_single_trade_risk:" in v for v in result.violations)


def test_demo_bounds_within_limits_approve(tmp_path, monkeypatch):
    """A normal small external Demo trade with a sane stop is still approved."""
    setup_env(tmp_path, monkeypatch)
    from tests.risk_trade_fixture import approvable_risk_trade
    trade = approvable_risk_trade(
        monkeypatch, symbol="BTC/USDT", market_type="spot", direction="long", price=60000,
    )
    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=0.0004, price=60000,
        # 2% stop -> risk = 0.0004*60000*0.02 = $0.48 < $2
        **trade,
    )
    repo.insert_trade_proposal(p)
    result = evaluate_proposal(p)
    assert result.approved is True, result.violations


# --- kill-switch TOCTOU serialization (§2.3) ---

def test_evaluate_proposal_is_serialized(tmp_path, monkeypatch):
    """evaluate_proposal holds a lock so concurrent evaluations can't interleave
    around a kill-switch trip. We assert the lock is actually engaged during a call."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.risk import engine

    seen_locked = {"v": False}
    real_locked = engine._evaluate_proposal_locked

    def spy(proposal, **kwargs):
        # While inside the evaluation, the module lock must be held (non-acquirable).
        seen_locked["v"] = not engine._eval_lock.acquire(blocking=False)
        if not seen_locked["v"]:
            engine._eval_lock.release()
        return real_locked(proposal, **kwargs)

    monkeypatch.setattr(engine, "_evaluate_proposal_locked", spy)

    p = TradeProposal(
        symbol="BTC/USDT", market_type="spot", direction="long",
        execution_mode="bitget_demo", quantity=0.0004, price=60000, stop_price=58800,
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
    cfg.risk.kill_switch_enabled_demo = True  # default False; bitget_demo is demo-mode
    save_config(cfg)
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    _insert_closed_position_via_producer(
        position_id="kill-switch-loss-position",
        symbol="BTC/USDT",
        quantity=0.01,
        entry_price=60000,
        market_type="spot", direction="long", execution_mode="bitget_demo",
        realized_pnl=-60.0, closed_at=datetime.now(timezone.utc).isoformat(),
    )

    p1 = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                       execution_mode="bitget_demo", quantity=0.0004, price=60000, stop_price=58800)
    repo.insert_trade_proposal(p1)
    r1 = evaluate_proposal(p1)
    assert any("max_daily_loss_exceeded" in v for v in r1.violations)

    # The Demo brake is now persisted; a brand-new Demo proposal is rejected.
    p2 = TradeProposal(symbol="BTC/USDT", market_type="spot", direction="long",
                       execution_mode="bitget_demo", quantity=0.0004, price=60000, stop_price=58800)
    repo.insert_trade_proposal(p2)
    r2 = evaluate_proposal(p2)
    assert "demo_brake_active" in r2.violations

"""Tests for the strategy <-> trade causal chain (gap #2).

These lock in that a designed strategy actually becomes tradable and that its
strategy_id survives the full chain: strategy -> opportunity.linked_strategy_id
-> proposal -> order -> fill -> position. Before this wiring the chain was broken
in four places (no producer scheduled, strategy stuck in draft, observer never
set linked_strategy_id, parameters never persisted), so promotion gates counted
zero paper trades forever.

Governance: paper-only, never live. The designer auto-advances ONLY through the
ungated lifecycle states (stops at paper_running); live gates are untouched.
"""

import json
import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Opportunity, MarketSnapshot
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent
from aletheia.trading.strategy_lifecycle import create_strategy, check_promotion_gates


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes", "knowledge/strategies"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


def _set_mode(mode="Paper", kill=False, **_kwargs):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = mode
    cfg.runtime.kill_switch_active = kill
    save_config(cfg)


# --- create_strategy parameters persistence ---

def test_create_strategy_persists_parameters(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    params = {"symbol": "BTCUSDT", "timeframe": "1h", "direction_bias": "long"}
    s = create_strategy("Param Strategy", "desc", parameters=params)

    row = repo.get_strategy(s.id)
    assert row is not None
    assert json.loads(row["parameters"]) == params
    reset_db()


def test_create_strategy_parameters_default_empty(tmp_path, monkeypatch):
    """Backward compatibility: no parameters -> empty object, not crash."""
    setup_env(tmp_path, monkeypatch)
    s = create_strategy("No Params")
    row = repo.get_strategy(s.id)
    assert json.loads(row["parameters"]) == {}
    reset_db()


# --- designer auto-advances a strategy to paper_running ---

async def test_designer_creates_tradable_strategy(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")  # research_tool permitted; LLM unavailable -> graceful default

    result = await run_agent("strategy_designer", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")

    strategies = repo.list_strategies(limit=10)
    assert len(strategies) == 1
    s = strategies[0]
    # Auto-advanced through the ungated path to paper_running (tradable), no further.
    assert s["state"] == "paper_running"
    # Parameters snapshot persisted (not the empty default).
    assert json.loads(s["parameters"]) != {}
    reset_db()


# --- full causal chain: strategy_id survives end to end ---

async def test_strategy_id_flows_through_chain(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")

    # Producer: a tradable strategy exists.
    await run_agent("strategy_designer", trigger_type="manual")
    strat = repo.list_strategies(limit=10)[0]
    assert strat["state"] == "paper_running"

    # A market snapshot so the trader can price the order.
    # Use canonical format — market_observer and paper_trader both work in BTC/USDT.
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000.0))

    # Observer binds the opportunity to the active strategy. We assert the binding
    # directly (LLM is unavailable in tests, so drive a directional opportunity by
    # seeding the bound opportunity the way the observer now constructs it).
    await run_agent("market_observer", trigger_type="manual")
    # Filter to crypto opportunities only — xStocks (asset_class=equity) have no strategy link.
    observed = [
        o for o in repo.list_opportunities(limit=20)
        if o.get("source_agent") == "market_observer"
        and (o.get("asset_class") or "crypto") == "crypto"
    ]
    assert observed, "observer should create a crypto opportunity"
    assert observed[0]["linked_strategy_id"] == strat["id"], "opportunity must bind to the active strategy"

    # Seed a directional, medium-confidence opportunity bound to the strategy so the
    # paper trader actually trades (observer's own read is neutral without an LLM).
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      thesis="bound test", confidence="medium", source_agent="market_observer",
                      linked_strategy_id=strat["id"])
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")

    # The proposal that consumed our bound opportunity must carry the strategy_id.
    proposals = [p for p in repo.list_trade_proposals() if p.get("opportunity_id") == opp.id]
    assert len(proposals) == 1
    p = proposals[0]
    assert p["strategy_id"] == strat["id"]
    assert p["status"] == "executed"

    # And it must survive into the position row.
    positions = repo.list_positions(status="open")
    assert positions
    assert any(pos["strategy_id"] == strat["id"] for pos in positions)
    reset_db()


# --- promotion gate actually counts paper trades now (was always 0) ---

async def test_promotion_gate_counts_bound_paper_trades(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")

    await run_agent("strategy_designer", trigger_type="manual")
    strat = repo.list_strategies(limit=10)[0]
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000.0))

    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      thesis="bound", confidence="medium", source_agent="market_observer",
                      linked_strategy_id=strat["id"])
    repo.insert_opportunity(opp)
    await run_agent("paper_trader", trigger_type="manual")

    gates = check_promotion_gates(strat["id"])
    # The headline regression: paper_trades is no longer structurally stuck at 0.
    assert gates["metrics"]["paper_trades"] >= 1
    reset_db()

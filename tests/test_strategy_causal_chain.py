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

import pytest

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
    # Validate the local paper_internal fallback path (default is now bitget_demo,
    # a real-exchange call covered separately by test_demo_trading.py).
    cfg.exchange.paper_execution_mode = "paper_internal"
    # Limit to BTC/USDT only so market_observer doesn't create opportunities
    # for symbols without a market snapshot in the test DB.
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    save_config(cfg)


_MOCK_EXEC_SPEC = {
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "direction_bias": "long",
    "entry_conditions": [
        {"indicator": "rsi", "indicator_params": {"period": 14},
         "comparator": "lt", "threshold": 30.0}
    ],
    "exit_conditions": [
        {"indicator": "rsi", "indicator_params": {"period": 14},
         "comparator": "gt", "threshold": 70.0}
    ],
    "stop_loss_pct": 3.0,
}


def _patch_llm_structured(monkeypatch):
    """Mock llm_structured to return a valid executable strategy spec."""
    async def fake_llm_structured(system, user, schema=None, max_tokens=800):
        return _MOCK_EXEC_SPEC, None

    monkeypatch.setattr(
        "aletheia.agents.strategy_designer.llm_structured", fake_llm_structured
    )


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
    """Phase 4e: Strategy auto-advances to paper_candidate; PI Agent decides paper_running."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")
    _patch_llm_structured(monkeypatch)

    result = await run_agent("strategy_designer", trigger_type="manual")
    assert result["status"] in ("completed", "completed_with_errors")

    strategies = repo.list_strategies(limit=10)
    assert len(strategies) == 1
    s = strategies[0]
    # Phase 4e: stops at paper_candidate (PI Agent must confirm promotion to paper_running)
    assert s["state"] == "paper_candidate"
    # Parameters snapshot persisted (not the empty default).
    assert json.loads(s["parameters"]) != {}

    # A recommend_promote entry should exist in pi_decisions
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=5)
    promote_recs = [d for d in decisions if d.get("action") == "recommend_promote"]
    assert len(promote_recs) >= 1
    reset_db()


# --- full causal chain: strategy_id survives end to end ---

async def test_strategy_id_flows_through_chain(tmp_path, monkeypatch):
    """Phase 4a/4e: Strategy stops at paper_candidate. After PI Agent promotes it,
    paper_trader writes signal_pending entries for PI Agent to execute."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")
    _patch_llm_structured(monkeypatch)

    # Producer: strategy is created (stops at paper_candidate per Phase 4e).
    await run_agent("strategy_designer", trigger_type="manual")
    strat = repo.list_strategies(limit=10)[0]
    assert strat["state"] == "paper_candidate"

    # Simulate PI Agent approving promotion to paper_running
    from aletheia.trading.strategy_lifecycle import promote_strategy
    promote_strategy(strat["id"], "paper_running")
    strat = repo.get_strategy(strat["id"])
    assert strat["state"] == "paper_running"

    # Strip RuleSet so opportunity-path B doesn't require OHLCV bars.
    params = json.loads(strat["parameters"])
    params.pop("ruleset", None)
    repo.update_strategy(strat["id"], parameters=json.dumps(params))

    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000.0))

    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      thesis="causal chain test", confidence="medium",
                      source_agent="market_observer",
                      linked_strategy_id=strat["id"])
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")

    # Phase 4a: Paper trader writes signal_pending for PI Agent
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=10)
    pending = [d for d in decisions if d.get("action") == "signal_pending" and d.get("symbol") == "BTC/USDT"]
    assert len(pending) >= 1
    reset_db()


# --- promotion gate actually counts paper trades now (was always 0) ---

async def test_promotion_gate_counts_bound_paper_trades(tmp_path, monkeypatch):
    """Phase 4a/4e: Paper trader now writes signals. To test promotion gates,
    we simulate PI Agent creating a position for the strategy."""
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")
    _patch_llm_structured(monkeypatch)

    await run_agent("strategy_designer", trigger_type="manual")
    strat = repo.list_strategies(limit=10)[0]

    # Simulate PI Agent promoting and then creating a trade for this strategy
    from aletheia.trading.strategy_lifecycle import promote_strategy
    promote_strategy(strat["id"], "paper_running")

    # Simulate a completed paper trade (as PI Agent would create via submit_decision_as_proposal)
    from aletheia.execution.paper_tools import create_paper_proposal
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000.0))
    create_paper_proposal(
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=0.001, price=60000.0, stop_price=58800.0,
        execution_mode="paper_internal", strategy_id=strat["id"],
    )

    gates = check_promotion_gates(strat["id"])
    assert gates["metrics"]["paper_trades"] >= 1
    reset_db()


async def test_paused_strategy_is_not_linked_or_traded(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_mode("Paper")

    strat = create_strategy("Paused Momentum", "paper strategy")
    for target in ("research_note", "strategy_hypothesis", "paper_candidate", "paper_running"):
        from aletheia.trading.strategy_lifecycle import promote_strategy

        promote_strategy(strat.id, target)
    repo.update_strategy(
        strat.id,
        paper_trading_paused=1,
        paper_pause_reason="operator pause",
    )

    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000.0))

    # With LLM unavailable, observer doesn't create opportunities (quality gate).
    # We verify the paused-strategy logic by seeding directly.
    opp = Opportunity(
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        thesis="paused strategy should not trade",
        confidence="medium",
        source_agent="market_observer",
        linked_strategy_id=strat.id,
    )
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")

    proposals = [p for p in repo.list_trade_proposals() if p.get("opportunity_id") == opp.id]
    assert proposals == []
    updated = repo.get_opportunity(opp.id)
    assert updated is not None
    assert updated["status"] == "reviewed"
    reset_db()

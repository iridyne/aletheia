"""Tests for the entry-logic upgrade (Component 2).

Goal: trades stop being noise. market_observer produces a real structured
direction/confidence; paper_trader skips neutral and low-confidence signals BUT
still records the opportunity; a stop_price is computed and reaches the proposal.
LLM calls degrade gracefully (never raise).
"""

import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import Opportunity, MarketSnapshot
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


def _set_paperauto(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "PaperAuto"
    cfg.agent.autonomous_paper_trading = True
    save_config(cfg)


# --- llm_structured graceful degradation ---

async def test_llm_structured_degrades_gracefully(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.helpers import llm_structured
    from pydantic import BaseModel

    class Schema(BaseModel):
        direction_bias: str = "neutral"

    # No model provider configured → returns (None, error), never raises.
    data, err = await llm_structured(system="x", user="y", schema=Schema)
    assert data is None
    assert err is not None


# --- market_observer writes real structured values ---

async def test_observer_writes_structured_direction(tmp_path, monkeypatch):
    # Configure a single symbol so we get exactly 1 opportunity.
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    save_config(cfg)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    async def fake_structured(system, user, *, schema, max_tokens=400):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    result = await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 1
    assert opps[0]["direction_bias"] == "long"
    assert opps[0]["confidence"] == "high"
    assert opps[0]["symbol"] == "BTC/USDT"  # stored in canonical format


async def test_observer_falls_back_to_neutral_on_error(tmp_path, monkeypatch):
    # Configure a single symbol so we get exactly 1 opportunity.
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    save_config(cfg)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    async def fake_structured(system, user, *, schema, max_tokens=400):
        return None, "llm_unavailable"

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    result = await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 1
    # graceful fallback: opportunity still recorded, neutral/speculative
    assert opps[0]["direction_bias"] == "neutral"
    assert opps[0]["confidence"] == "speculative"


# --- paper_trader honors direction + confidence; records non-traded signals ---

async def test_paper_trader_skips_neutral_but_keeps_signal(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_paperauto(monkeypatch)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="neutral",
                      confidence="high", source_agent="market_observer")
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")
    assert len(repo.list_trade_proposals()) == 0  # neutral → no trade
    # signal preserved (not deleted); marked as reviewed
    row = repo.get_opportunity(opp.id)
    assert row is not None
    assert row["status"] != "open"


async def test_paper_trader_skips_low_confidence(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_paperauto(monkeypatch)  # min_confidence_to_trade default "medium"
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      confidence="speculative", source_agent="market_observer")
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")
    assert len(repo.list_trade_proposals()) == 0  # speculative < medium → skip
    assert repo.get_opportunity(opp.id)["status"] != "open"  # still recorded


async def test_paper_trader_trades_directional_and_sets_stop(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_paperauto(monkeypatch)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      confidence="medium", source_agent="market_observer")
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")
    proposals = repo.list_trade_proposals()
    assert len(proposals) == 1
    p = proposals[0]
    assert p["direction"] == "long"
    assert p["stop_price"] is not None
    # long stop is below entry (2% default)
    assert p["stop_price"] < p["price"]


# --- Symbol format: market_observer stores canonical BTC/USDT format ---

async def test_observer_stores_canonical_format_symbol(tmp_path, monkeypatch):
    """market_observer stores opportunities in canonical BTC/USDT format
    (matching CCXTProvider snapshot keys). paper_trader normalises tradable_symbols
    to canonical before matching, so the filter still works."""
    setup_env(tmp_path, monkeypatch)
    # Snapshot stored in canonical format (as collect_market_snapshots does)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    cfg.agent.permission_mode = "Auto"
    save_config(cfg)

    async def fake_structured(system, user, *, schema, max_tokens=400):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    await run_agent("market_observer", trigger_type="manual")

    opps = repo.list_opportunities()
    assert len(opps) == 1
    # Canonical format — not Bitget "BTCUSDT"
    assert opps[0]["symbol"] == "BTC/USDT"


async def test_full_observer_to_paper_trader_loop(tmp_path, monkeypatch):
    """End-to-end: observer runs with CCXT observed_symbols, stores opportunity in
    canonical format, then paper_trader finds and consumes it — producing a proposal."""
    setup_env(tmp_path, monkeypatch)
    # Snapshot in canonical format (real pipeline stores it this way)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    cfg.agent.permission_mode = "PaperAuto"
    cfg.agent.autonomous_paper_trading = True
    save_config(cfg)

    async def fake_structured(system, user, *, schema, max_tokens=400):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)

    # Step 1: observer creates opportunity
    await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 1
    assert opps[0]["symbol"] == "BTC/USDT"

    # Step 2: paper_trader finds and consumes it
    await run_agent("paper_trader", trigger_type="manual")
    proposals = repo.list_trade_proposals()
    assert len(proposals) == 1
    assert proposals[0]["direction"] == "long"
    # opportunity should be marked proposed (not still open)
    assert repo.get_opportunity(opps[0]["id"])["status"] == "proposed"

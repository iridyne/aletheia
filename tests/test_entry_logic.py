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
    cfg.agent.permission_mode = "Paper"
    # Local paper_internal fallback path (default is now bitget_demo / real exchange).
    cfg.exchange.paper_execution_mode = "paper_internal"
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
    # Spot-only for this test so a single symbol yields exactly one opportunity
    # (with usdt_futures enabled the observer also emits the perp — covered elsewhere).
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}
    save_config(cfg)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)
    result = await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 1
    assert opps[0]["direction_bias"] == "long"
    assert opps[0]["confidence"] == "high"
    assert opps[0]["symbol"] == "BTC/USDT"  # stored in canonical format


async def test_observer_skips_opportunity_on_llm_failure(tmp_path, monkeypatch):
    # LLM-skip behavior: when LLM is unavailable, market_observer does NOT
    # create neutral noise. Opportunities come from deterministic paths instead.
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    save_config(cfg)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        return None, "llm_unavailable"

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)
    result = await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 0  # no neutral noise created
    assert any("llm_unavailable" in e for e in result.get("errors", []))


async def test_observer_dedups_same_symbol_when_both_markets_enabled(tmp_path, monkeypatch):
    """With both spot+usdt_futures enabled, a single symbol yields ONE
    opportunity (market == preferred_market_type which defaults to "auto" → perp).

    Previously this emitted a redundant perp for every spot — running the LLM
    twice for the same ETH and risking duplicate positions downstream. Directional
    analysis is instrument-agnostic: the same ETH reads 76% long whether spot or
    perp (price ≈ identical). OD-S01/S03 lock the dedup behavior."""
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    save_config(cfg)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)
    await run_agent("market_observer", trigger_type="manual")

    opps = repo.list_opportunities()
    assert len(opps) == 1, f"expected 1 opp (deduped), got {len(opps)}: {[o['market'] for o in opps]}"
    assert opps[0]["market"] == "usdt_futures", f"expected perp (auto bias), got {opps[0]['market']}"
    assert opps[0]["symbol"] == "BTC/USDT"


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
    """Phase 4a: Paper trader Path B now writes signal_pending entries for PI Agent."""
    setup_env(tmp_path, monkeypatch)
    _set_paperauto(monkeypatch)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      confidence="medium", source_agent="market_observer")
    repo.insert_opportunity(opp)

    result = await run_agent("paper_trader", trigger_type="manual")
    # Phase 4a: Path B writes signal_pending instead of creating proposals directly
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=5)
    pending = [d for d in decisions if d.get("action") == "signal_pending"]
    assert len(pending) >= 1
    assert pending[0]["symbol"] == "BTC/USDT"


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
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}  # one symbol → one opp
    cfg.agent.permission_mode = "Full"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)
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
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": False}  # one symbol → one opp
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)

    # Step 1: observer creates opportunity
    await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 1
    assert opps[0]["symbol"] == "BTC/USDT"

    # Step 2: paper_trader writes signal_pending for PI Agent (Phase 4a)
    await run_agent("paper_trader", trigger_type="manual")
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=5)
    pending = [d for d in decisions if d.get("action") == "signal_pending"]
    assert len(pending) >= 1
    assert pending[0]["symbol"] == "BTC/USDT"

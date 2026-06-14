"""Tests for Bitget xStocks observation via market_observer.

xStocks (AAPLON/USDT etc.) are Bitget tokenized equity tokens:
- Fetched via CCXTProvider(exchange="bitget") — only Bitget lists ON-series
- asset_class="equity" → paper_trader MUST NOT trade them
- linked_strategy_id=None — no crypto strategy binding
"""

import asyncio
import sqlite3

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.trading.instruments import xstocks_instruments_from_config
from aletheia.data.symbols import InstrumentId


def _setup(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes", "knowledge/strategies",
                "knowledge/post_trade_reviews", "knowledge/dreaming_logs",
                "knowledge/daily_reviews"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


# --- Config helpers ---

def test_xstocks_instruments_from_config(tmp_path, monkeypatch):
    """xstocks_instruments_from_config returns bitget-exchange InstrumentIds."""
    _setup(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config
    cfg = load_config()
    # Default config has 4 xStocks
    xs = xstocks_instruments_from_config(cfg)
    assert len(xs) >= 1
    for x in xs:
        assert isinstance(x, InstrumentId)
        assert x.exchange == "bitget"
        assert "/" in x.symbol
        assert x.market_type == "spot"
    reset_db()


def test_xstocks_instruments_empty_when_not_configured(tmp_path, monkeypatch):
    """No crash when observed_xstocks is empty."""
    _setup(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_xstocks = []
    save_config(cfg)
    xs = xstocks_instruments_from_config(cfg)
    assert xs == []
    reset_db()


# --- Observer integration ---

def test_xstocks_opportunities_have_equity_asset_class(tmp_path, monkeypatch):
    """market_observer writes asset_class=equity for xStocks symbols."""
    _setup(tmp_path, monkeypatch)

    # Limit crypto symbols to none so only xStocks run.
    import aletheia.agents.market_observer as mo
    import aletheia.config.loader as loader

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = []
    cfg.market_data.observed_xstocks = ["AAPLON/USDT"]
    save_config(cfg)

    # Stub LLM — returns a directional read so opportunity is created.
    async def fake_llm(*a, **kw):
        return ({"direction_bias": "long", "confidence": "medium",
                 "confidence_score": 60, "rationale": "xstock test",
                 "key_observations": ["price above SMA20"]}, None)
    monkeypatch.setattr(mo, "llm_structured", fake_llm)

    # Stub build_market_context so no network needed.
    async def fake_builder(symbols, market_type="spot", provider=None, client=None):
        return ("AAPLON/USDT verified snapshot", {"fallback": False, "errors": [], "symbols": {}})
    import aletheia.trading.market_context as mc
    monkeypatch.setattr(mc, "build_market_context", fake_builder)

    # Stub the CCXTProvider so xStocks loop doesn't hit network.
    from aletheia.data.ccxt_provider import CCXTProvider
    class FakeBitgetProvider:
        async def get_ticker(self, sym):
            from aletheia.data.providers import Ticker
            return Ticker(symbol=sym, last_price=300.0, volume_24h=10000, change_pct_24h=0.5, provider="bitget")
        async def get_ohlcv(self, sym, timeframe, limit):
            return []
        async def close(self):
            pass
    monkeypatch.setattr(mo, "_analyze_symbol", mo._analyze_symbol)  # keep original
    # Patch CCXTProvider construction inside market_observer
    monkeypatch.setattr("aletheia.data.ccxt_provider.CCXTProvider",
                        lambda exchange, fallback_exchange: FakeBitgetProvider())

    from aletheia.agents.market_observer import run_market_observer
    out = asyncio.run(run_market_observer(permission_mode="Full", run_id="run-xs-1"))

    opps = repo.list_opportunities(limit=20)
    xstock_opps = [o for o in opps if o.get("symbol") == "AAPLON/USDT"]
    assert xstock_opps, f"Expected xStock opportunity, got: {[o['symbol'] for o in opps]}"
    opp = xstock_opps[0]
    assert opp["asset_class"] == "equity", f"asset_class should be equity, got {opp['asset_class']}"
    assert opp["linked_strategy_id"] is None, "xStocks should not be linked to crypto strategies"

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-xs-1")
    reset_db()


def test_paper_trader_ignores_xstocks_opportunities(tmp_path, monkeypatch):
    """paper_trader must skip asset_class=equity opportunities."""
    _setup(tmp_path, monkeypatch)

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    save_config(cfg)

    from aletheia.storage.models import Opportunity, MarketSnapshot
    # Insert an xStocks opportunity.
    opp = Opportunity(
        symbol="AAPLON/USDT",
        market="spot",
        direction_bias="long",
        thesis="xstock test",
        confidence="high",
        source_agent="market_observer",
        asset_class="equity",
    )
    repo.insert_opportunity(opp)
    # Also a price snapshot so paper_trader could price it if it tried.
    repo.insert_market_snapshot(MarketSnapshot(symbol="AAPLON/USDT", market_type="spot", price=309.0))

    from aletheia.agents.paper_trader import run_paper_trader
    out = asyncio.run(run_paper_trader(permission_mode="Full", run_id="run-xs-pt"))

    # paper_trader should find no ranked candidate (equity excluded from crypto filter).
    assert "no_ranked_candidate" in out["outputs"], f"Expected skip, got: {out}"
    proposals = repo.list_trade_proposals()
    assert not proposals, "paper_trader must not propose xStocks trades"
    reset_db()

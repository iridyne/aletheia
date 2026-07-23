"""Tests for Bitget xStocks observation and equity_observer integration.

xStocks (AAPLON/USDT etc.) are Bitget tokenized equity tokens:
- Fetched via equity_observer (not market_observer) since Phase 2 of equity integration
- asset_class="equity" → paper_trader MUST NOT trade them
- execution_eligible=False — observation-only; xStocks are the on-chain execution channel
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
    """No crash when both observed_xstocks and observed_stock_tokens are empty."""
    _setup(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_xstocks = []
    cfg.market_data.observed_stock_tokens = []
    save_config(cfg)
    xs = xstocks_instruments_from_config(cfg)
    assert xs == []
    reset_db()


# --- equity_observer integration ---

def test_equity_observer_creates_equity_opportunity(tmp_path, monkeypatch):
    """equity_observer writes asset_class=equity, execution_eligible=False for equity tickers."""
    _setup(tmp_path, monkeypatch)

    import aletheia.agents.equity_observer as eo
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_equities = ["AAPL"]
    cfg.market_data.observed_xstocks = []  # no xStocks cross-validation in this test
    cfg.market_data.observed_stock_tokens = []
    save_config(cfg)

    # Stub yfinance fetch so no network needed.
    def fake_fetch(ticker, _retry=True):
        return {
            "ticker": ticker, "last": 195.0, "bars": 65,
            "sma20": 190.0, "sma50": 185.0, "rsi14": 58.0, "atr14": 3.5,
            "bb_lower": 180.0, "bb_upper": 210.0, "bb_pct_b": 0.6,
            "momentum_10": 2.5, "volume_zscore": 0.8,
        }
    monkeypatch.setattr(eo, "_fetch_equity_indicators", fake_fetch)

    # Stub LLM.
    async def fake_llm(*a, **kw):
        return ({"direction_bias": "long", "confidence": "medium",
                 "confidence_score": 65, "rationale": "equity test",
                 "key_observations": ["price above SMA50"]}, None)
    monkeypatch.setattr(eo, "llm_structured", fake_llm)

    from aletheia.agents.equity_observer import run_equity_observer
    out = asyncio.run(run_equity_observer(permission_mode="Full", run_id="run-eq-1"))

    opps = repo.list_opportunities(limit=20)
    eq_opps = [o for o in opps if o.get("canonical_ticker") == "AAPL"]
    assert eq_opps, f"Expected AAPL equity opportunity, got: {[o.get('symbol') for o in opps]}"
    opp = eq_opps[0]
    assert opp["asset_class"] == "equity", f"asset_class should be equity, got {opp['asset_class']}"
    assert opp["asset_subclass"] == "equity_daily"
    assert opp["execution_eligible"] == 0, "equity opportunities must not be execution-eligible"
    assert opp["symbol"] == "AAPL/USDT"

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-eq-1")
    reset_db()


def test_equity_observer_stamps_attribution_fields_and_confidence_fallback(tmp_path, monkeypatch):
    """equity_observer must set scanner_family, valid_until, and confidence_score=0 on LLM failure (#183)."""
    _setup(tmp_path, monkeypatch)

    import aletheia.agents.equity_observer as eo
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_equities = ["MSFT"]
    cfg.market_data.observed_xstocks = []
    cfg.market_data.observed_stock_tokens = []
    save_config(cfg)

    def fake_fetch(ticker, _retry=True):
        return {
            "ticker": ticker, "last": 420.0, "bars": 65,
            "sma20": 410.0, "sma50": 400.0, "rsi14": 55.0, "atr14": 4.0,
            "bb_lower": 390.0, "bb_upper": 450.0, "bb_pct_b": 0.55,
            "momentum_10": 1.5, "volume_zscore": 0.5,
        }
    monkeypatch.setattr(eo, "_fetch_equity_indicators", fake_fetch)

    # LLM fails — structured read is unavailable
    async def fake_llm_fail(*a, **kw):
        return (None, "llm_timeout")
    monkeypatch.setattr(eo, "llm_structured", fake_llm_fail)

    from aletheia.agents.equity_observer import run_equity_observer
    asyncio.run(run_equity_observer(permission_mode="Full", run_id="run-eq-attr"))

    opps = repo.list_opportunities(limit=20)
    msft_opps = [o for o in opps if o.get("canonical_ticker") == "MSFT"]
    assert msft_opps, "Expected MSFT equity opportunity"
    opp = msft_opps[0]

    # scanner_family and valid_until must always be present
    assert opp["scanner_family"] == "equity_daily", (
        f"scanner_family should be 'equity_daily', got {opp['scanner_family']!r}"
    )
    assert opp["valid_until"] is not None, "valid_until must not be NULL"

    # When LLM fails, confidence_score must be 0 (not NULL)
    assert opp["confidence_score"] == 0, (
        f"confidence_score should be 0 on LLM failure, got {opp['confidence_score']!r}"
    )

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-eq-attr")
    reset_db()


def test_paper_trader_ignores_xstocks_opportunities(tmp_path, monkeypatch):
    """paper_trader must not trade asset_class=equity opportunities (shadow track only)."""
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

    # equity_shadow_track candidate appears in skip outputs but never becomes a trade proposal.
    skip_outputs = [o for o in out["outputs"] if o.startswith("skip:") and "equity_shadow_track" in o]
    assert skip_outputs, f"Expected equity_shadow_track skip output, got: {out}"
    proposals = repo.list_trade_proposals()
    assert not proposals, "paper_trader must not propose xStocks trades"
    reset_db()

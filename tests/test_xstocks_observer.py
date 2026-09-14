"""Tests for Bitget xStocks observation and equity_observer integration.

xStocks (AAPLON/USDT etc.) are Bitget tokenized equity tokens:
- Fetched via equity_observer (not market_observer) since Phase 2 of equity integration
- asset_class="equity" → paper_trader MUST NOT trade them
- execution_eligible=False — observation-only; xStocks are the on-chain execution channel
"""

import asyncio
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.trading.instruments import stock_tokens_from_config
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

def test_optional_equity_registration_has_no_core_facade(monkeypatch):
    import builtins
    import importlib
    import importlib.util
    from aletheia.agents import _AGENTS, get_agent_fn
    from extensions.observation.equity_observer import run_equity_observer

    composition = importlib.import_module("aletheia.server.app")
    monkeypatch.delitem(_AGENTS, "equity_observer", raising=False)
    original_import = builtins.__import__

    def absent(name, *args, **kwargs):
        if name.startswith("extensions.observation"):
            raise ModuleNotFoundError(name)
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as missing:
        missing.setattr(builtins, "__import__", absent)
        composition._register_observation_extension()
        with pytest.raises(ValueError, match="Unknown agent: equity_observer"):
            get_agent_fn("equity_observer")
    composition._register_observation_extension()
    assert get_agent_fn("equity_observer") is run_equity_observer
    assert importlib.util.find_spec("aletheia.agents.equity_observer") is None


def test_stock_tokens_from_config(tmp_path, monkeypatch):
    """stock_tokens_from_config returns bitget-exchange InstrumentIds."""
    _setup(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config
    cfg = load_config()
    # Default config has 4 xStocks
    xs = stock_tokens_from_config(cfg)
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
    cfg.market_data.observed_stock_tokens = []
    save_config(cfg)
    xs = stock_tokens_from_config(cfg)
    assert xs == []
    reset_db()


# --- equity_observer integration ---

@pytest.mark.parametrize("consumer", ["equity", "snapshots"])
def test_stock_token_consumers_use_pinned_factory(monkeypatch, consumer):
    from aletheia.data import ccxt_provider, venue_listing_cache
    from aletheia.data.providers import Ticker
    from extensions.observation import equity_observer
    from aletheia.trading import market_data

    instrument = InstrumentId.from_parts("bitget", "AAPLON/USDT", "spot")
    providers = []

    def build(**kwargs):
        assert kwargs == dict(exchange="bitget", native_ccxt=True, fallback_exchange="bitget")
        provider = AsyncMock()
        provider.get_ticker.return_value = Ticker(
            symbol=instrument.symbol, last_price=200.0, volume_24h=1000.0,
            change_pct_24h=0.0, provider="bitget",
        )
        providers.append(provider)
        return provider

    factory = MagicMock(side_effect=build)
    monkeypatch.setattr(ccxt_provider, "get_market_data_provider", factory)
    monkeypatch.setattr(ccxt_provider, "_native_provider_factory", MagicMock(
        side_effect=AssertionError("direct provider construction"),
    ))
    if consumer == "equity":
        assert asyncio.run(equity_observer._fetch_stock_token_price(instrument.symbol)) == 200.0
        assert factory.call_count == 1
    else:
        cfg = MagicMock()
        cfg.market_data.ccxt_exchange = "bitget"
        monkeypatch.setattr(market_data, "load_config", lambda: cfg)
        monkeypatch.setattr(market_data, "_build_dynamic_instruments", lambda cfg: [])
        monkeypatch.setattr(market_data, "stock_tokens_from_config", lambda cfg: [instrument])
        monkeypatch.setattr(venue_listing_cache, "get_venue_listing", AsyncMock(return_value=None))
        monkeypatch.setattr(venue_listing_cache, "filter_by_listing", lambda items, **kwargs: items)
        writer = MagicMock(return_value=1)
        monkeypatch.setattr(market_data, "insert_market_snapshot_batch", writer)
        result = asyncio.run(market_data.collect_market_snapshots())
        assert result["written"] == 1 and result["errors"] == []
        assert factory.call_count == 2
        writer.assert_called_once()
    for provider in providers:
        provider.close.assert_awaited_once()

@pytest.mark.parametrize("state", ["absent", "empty", "error"])
def test_equity_reader_failure_preserves_core_classification(monkeypatch, state):
    import threading
    from extensions.observation import equity_observer as eo
    from aletheia.data.models import EquityFetchError
    main_thread = threading.get_ident()
    def fetch(ticker):
        assert threading.get_ident() != main_thread
        return None if state == "empty" else EquityFetchError(reason="fixture_error")
    monkeypatch.setattr(eo, "_equity_indicators_fetch", None if state == "absent" else fetch)
    monkeypatch.setattr(eo, "log_tool", lambda *args, **kwargs: None)
    result = asyncio.run(eo._analyze_equity("AAPL", "read_only", None, None, []))
    expected = {"absent": "hard:equity_data_unavailable:AAPL",
                "empty": "soft:empty_data:AAPL", "error": "hard:fixture_error:AAPL"}
    assert result == (None, expected[state])
    assert not hasattr(eo, "_fetch_equity_indicators")
    assert not hasattr(eo, "_fetch_live_price")


def test_yahoo_equity_missing_sdk_and_bounded_retry(monkeypatch):
    import sys
    from types import SimpleNamespace
    from extensions.yahoo import market_data
    from aletheia.data.models import EquityFetchError
    monkeypatch.setitem(sys.modules, "yfinance", None)
    result = market_data.fetch_equity_indicators("AAPL")
    assert isinstance(result, EquityFetchError) and result.reason == "yfinance_not_installed"
    assert market_data.fetch_live_price("AAPL") is None
    calls, sleeps = [], []
    def download(ticker, **kwargs):
        calls.append((ticker, kwargs))
        return SimpleNamespace(empty=True)
    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=download))
    monkeypatch.setattr("time.sleep", sleeps.append)
    assert market_data.fetch_equity_indicators("AAPL") is None
    assert len(calls) == 2 and sleeps == [3]
    assert all(kwargs == dict(period="90d", interval="1d", auto_adjust=True, progress=False)
               for _, kwargs in calls)


@pytest.mark.parametrize("token_price", [None, 195.0, 201.0, 210.0])
def test_equity_observer_creates_equity_opportunity(tmp_path, monkeypatch, token_price):
    """equity_observer writes asset_class=equity, execution_eligible=False for equity tickers."""
    _setup(tmp_path, monkeypatch)

    import extensions.observation.equity_observer as eo
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_equities = ["AAPL"]
    cfg.market_data.observed_stock_tokens = ["AAPLON/USDT"] if token_price is not None else []
    save_config(cfg)

    # Stub yfinance fetch so no network needed.
    def fake_fetch(ticker, _retry=True):
        return {
            "ticker": ticker, "last": 195.0, "bars": 65,
            "sma20": 190.0, "sma50": 185.0, "rsi14": 58.0, "atr14": 3.5,
            "bb_lower": 180.0, "bb_upper": 210.0, "bb_pct_b": 0.6,
            "momentum_10": 2.5, "volume_zscore": 0.8,
        }
    monkeypatch.setattr(eo, "_equity_indicators_fetch", fake_fetch)
    monkeypatch.setattr(eo, "_equity_live_price_fetch", None)
    monkeypatch.setattr(eo, "_fetch_stock_token_price", AsyncMock(return_value=token_price))

    # Stub LLM.
    prompts = []
    async def fake_llm(*a, **kw):
        prompts.append(kw["user"])
        return ({"direction_bias": "long", "confidence": "medium",
                 "confidence_score": 65, "rationale": "equity test",
                 "key_observations": ["price above SMA50"]}, None)
    monkeypatch.setattr(eo, "llm_structured", fake_llm)

    from extensions.observation.equity_observer import run_equity_observer
    out = asyncio.run(run_equity_observer(permission_mode="Full", run_id="run-eq-1"))

    opps = repo.list_opportunities(limit=20)
    eq_opps = [o for o in opps if o.get("canonical_ticker") == "AAPL"]
    assert eq_opps, f"Expected AAPL equity opportunity, got: {[o.get('symbol') for o in opps]}"
    opp = eq_opps[0]
    assert opp["asset_class"] == "equity", f"asset_class should be equity, got {opp['asset_class']}"
    assert opp["asset_subclass"] == "equity_daily"
    assert opp["execution_eligible"] == 0, "equity opportunities must not be execution-eligible"
    assert opp["symbol"] == "AAPL/USDT"
    import json
    metadata = json.loads(opp["metadata"])
    assert "xstock" not in metadata
    if token_price is None:
        assert "stock_token" not in metadata
    else:
        assert metadata["stock_token"] == {
            "symbol": "AAPLON/USDT", "price": token_price, "exchange": "bitget",
        }
        deviation = abs(token_price - 195.0) / 195.0 * 100
        assert metadata["deviation_pct"] == round(deviation, 4)
        assert bool(metadata.get("premium_alert")) == (deviation > 2)
        assert bool(metadata.get("data_anomaly")) == (deviation > 5)
        assert ("Exchange token price" in prompts[0]) == (deviation <= 5)

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-eq-1")
    reset_db()


def test_equity_observer_stamps_attribution_fields_and_confidence_fallback(tmp_path, monkeypatch):
    """equity_observer must set scanner_family, valid_until, and confidence_score=0 on LLM failure (#183)."""
    _setup(tmp_path, monkeypatch)

    import extensions.observation.equity_observer as eo
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_equities = ["MSFT"]
    cfg.market_data.observed_stock_tokens = []
    save_config(cfg)

    def fake_fetch(ticker, _retry=True):
        return {
            "ticker": ticker, "last": 420.0, "bars": 65,
            "sma20": 410.0, "sma50": 400.0, "rsi14": 55.0, "atr14": 4.0,
            "bb_lower": 390.0, "bb_upper": 450.0, "bb_pct_b": 0.55,
            "momentum_10": 1.5, "volume_zscore": 0.5,
        }
    monkeypatch.setattr(eo, "_equity_indicators_fetch", fake_fetch)
    monkeypatch.setattr(eo, "_equity_live_price_fetch", None)

    # LLM fails — structured read is unavailable
    async def fake_llm_fail(*a, **kw):
        return (None, "llm_timeout")
    monkeypatch.setattr(eo, "llm_structured", fake_llm_fail)

    from extensions.observation.equity_observer import run_equity_observer
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

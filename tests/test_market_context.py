"""Tests for the new market context layer (indicators + builder + observer).

Covers:
  - Stdlib indicators (SMA / RSI / ATR / Bollinger / momentum / vol z-score / RS rank)
  - market_context.build_market_context (multi-TF render + fallback)
  - market_observer integration (structured output extraction, recorded steps,
    graceful fallback when LLM unavailable)
"""

import asyncio
import sqlite3
import math
from datetime import datetime, timedelta, timezone

from aletheia.trading import indicators


# === Indicators ===

def test_sma_basic():
    assert indicators.sma([1, 2, 3, 4, 5], 5) == 3
    assert indicators.sma([1, 2, 3, 4, 5], 3) == 4  # last 3 = (3+4+5)/3
    assert indicators.sma([1, 2], 5) is None


def test_ema_recovers_after_seed():
    # EMA must be defined once we have `period` samples.
    out = indicators.ema([1, 2, 3, 4, 5], 3)
    assert out is not None
    assert 3 < out < 5  # weighted toward recent values


def test_rsi_uptrend_above_50():
    closes = [float(i) for i in range(1, 30)]
    r = indicators.rsi(closes, 14)
    assert r is not None
    assert r > 70  # pure monotonic uptrend should saturate


def test_rsi_handles_no_loss():
    """Wilder div-by-zero guard: all gains, no losses."""
    closes = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    r = indicators.rsi(closes, 14)
    assert r == 100.0


def test_rsi_too_short_returns_none():
    assert indicators.rsi([1, 2, 3], 14) is None


def test_atr_returns_positive_volatility():
    highs = [10 + i * 0.5 for i in range(20)]
    lows = [9 + i * 0.5 for i in range(20)]
    closes = [9.5 + i * 0.5 for i in range(20)]
    a = indicators.atr(highs, lows, closes, 14)
    assert a is not None and a > 0


def test_bollinger_centers_on_sma():
    closes = [10.0] * 20
    band = indicators.bollinger(closes, 20, 2.0)
    assert band is not None
    lower, mid, upper = band
    # Constant series → zero stddev → all three equal.
    assert lower == mid == upper == 10.0


def test_momentum_pct_correct_sign():
    closes = [100.0, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
    m = indicators.momentum_pct(closes, 10)
    assert m is not None
    assert math.isclose(m, 10.0, rel_tol=1e-9)  # 100 → 110 over 10 bars


def test_zscore_last_high_volume_positive():
    vols = [10.0] * 19 + [100.0]
    z = indicators.zscore_last(vols, 20)
    assert z is not None
    assert z > 1.0  # last bar is a clear outlier high


def test_relative_strength_rank_orders_desc():
    ranked = indicators.relative_strength_rank(
        {"BTCUSDT": 0.5, "ETHUSDT": -0.2, "SOLUSDT": 1.0}
    )
    # SOL leads, BTC second, ETH last
    assert ranked == [("SOLUSDT", 1), ("BTCUSDT", 2), ("ETHUSDT", 3)]


def test_relative_strength_rank_handles_none_returns():
    ranked = indicators.relative_strength_rank({"BTC": 0.1, "ETH": None})
    # None is appended at end with rank = len(valid)+1
    assert ranked[0] == ("BTC", 1)
    assert ranked[1][0] == "ETH"


# === Market context builder ===

class _FakeCandle:
    def __init__(self, ts, o, h, l, c, v):
        self.timestamp = ts
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v


class _FakeProvider:
    """Mock MarketDataProvider — no network, deterministic uptrend candles."""

    def __init__(self, *, fail_after: int | None = None):
        self.fail_after = fail_after
        self.calls = 0

    async def get_ticker(self, symbol):
        from aletheia.data.providers import Ticker
        return Ticker(symbol=symbol, last_price=100.0, volume_24h=1e6,
                      change_pct_24h=0.5, provider="fake")

    async def get_ohlcv(self, symbol, timeframe, limit):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("simulated network failure")
        # Generate `limit` candles: monotonic uptrend, deterministic per symbol.
        seed = sum(ord(c) for c in symbol) % 100
        return [
            _FakeCandle(
                ts=1_700_000_000_000 + i * 60_000,
                o=100.0 + seed + i * 0.1,
                h=100.5 + seed + i * 0.1,
                l=99.5 + seed + i * 0.1,
                c=100.0 + seed + i * 0.1,
                v=1000.0 + i * 5,
            )
            for i in range(limit)
        ]

    async def close(self):
        pass


# Keep old name as alias so any other references still work
_FakeClient = _FakeProvider


def test_build_context_renders_markdown_tables():
    from aletheia.trading.market_context import build_market_context
    provider = _FakeProvider()
    ctx, debug = asyncio.run(build_market_context(["BTCUSDT", "ETHUSDT"], provider=provider))
    assert "Verified market data snapshot" in ctx
    assert "BTCUSDT" in ctx and "ETHUSDT" in ctx
    # Multi-timeframe: header row should appear once per symbol.
    assert ctx.count("| TF | Close | SMA20 |") == 2
    # Glossary always present.
    assert "Indicator glossary" in ctx
    # Cross-asset RS table.
    assert "Cross-asset relative strength" in ctx
    # Debug info populated.
    assert "BTCUSDT" in debug["symbols"]
    assert debug["fallback"] is False


def test_build_context_falls_back_on_total_failure(tmp_path, monkeypatch):
    """If every OHLCV call fails, we degrade to the legacy 1-line context."""
    from aletheia.trading.market_context import build_market_context
    from aletheia.data.cache import get_cache
    get_cache().invalidate()  # clear any state from previous tests

    provider = _FakeProvider(fail_after=0)  # fail immediately

    # Patch latest_snapshot to return a known shape — the fallback path uses it.
    monkeypatch.setattr(
        "aletheia.trading.market_data.latest_snapshot",
        lambda sym: {"price": 60000.0, "change_pct_24h": 0.05},
    )
    ctx, debug = asyncio.run(build_market_context(["BTCUSDT"], provider=provider))
    assert "price=60000" in ctx
    assert "Verified market data snapshot" not in ctx  # fallback skips the header
    assert debug["fallback"] is True
    assert len(debug["errors"]) > 0


def test_build_context_includes_indicators_in_debug():
    from aletheia.trading.market_context import build_market_context
    provider = _FakeProvider()
    ctx, debug = asyncio.run(build_market_context(["BTCUSDT"], provider=provider))
    btc = debug["symbols"]["BTCUSDT"]
    # 1h timeframe should be present with computed RSI.
    assert "1h" in btc
    assert btc["1h"]["bars"] == 60
    # RSI on a pure uptrend should be high (>70).
    assert btc["1h"]["rsi_14"] is not None
    assert btc["1h"]["rsi_14"] > 60


# === Observer integration ===

def _setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.storage.sqlite import reset_db
    from aletheia.storage.migrations import run_migrations
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes", "knowledge/strategies",
                "knowledge/post_trade_reviews", "knowledge/dreaming_logs",
                "knowledge/daily_reviews"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    init_tool_registry()


def test_observer_records_step_for_market_read(tmp_path, monkeypatch):
    """Even when LLM is unavailable, observer must record a step trace.

    This is the new visibility — the dashboard step trace shows the model
    DID consult market data, killing the "AI doing nothing" perception.
    """
    _setup_env(tmp_path, monkeypatch)

    # Force the structured-LLM path to fail.
    import aletheia.agents.market_observer as mo

    async def fake_llm_structured(*a, **kw):
        return None, "llm_unavailable: simulated"
    monkeypatch.setattr(mo, "llm_structured", fake_llm_structured)
    monkeypatch.setattr(mo, "_has_sufficient_signal", lambda ctx: True)

    # Stub build_market_context so observer doesn't need network.
    async def fake_builder(symbols, market_type="spot", **kw):
        return ("BTCUSDT verified snapshot here", {"fallback": False, "errors": [],
                                                    "symbols": {}})
    monkeypatch.setattr(mo, "build_market_context", fake_builder, raising=False)
    # Patch via the module import used inside the agent body.
    import aletheia.trading.market_context as mc
    monkeypatch.setattr(mc, "build_market_context", fake_builder)

    from aletheia.agents.market_observer import run_market_observer
    from aletheia.storage import repositories as repo
    out = asyncio.run(run_market_observer(permission_mode="Observe", run_id="run-mo-1"))
    # Observer should have completed without throwing, even though LLM failed.
    assert "llm_unavailable" in (out["errors"][0] if out["errors"] else "")
    steps = repo.list_run_steps("run-mo-1")
    actions = [s["action"] for s in steps]
    # Actions are now per-symbol: "market_read_input:BTC/USDT"
    assert any("market_read_input" in a for a in actions)
    # LLM-skip behavior: when LLM fails, records "market_read_skipped" not "market_read_output"
    assert any("market_read_skipped" in a for a in actions)

    # Cleanup the in-process step counter.
    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-mo-1")


def test_observer_extracts_confidence_score_when_provided(tmp_path, monkeypatch):
    """When LLM returns the new structured shape, observer must read all fields."""
    _setup_env(tmp_path, monkeypatch)
    import aletheia.agents.market_observer as mo

    async def fake_llm_structured(*a, **kw):
        return ({
            "direction_bias": "long",
            "confidence": "medium",
            "confidence_score": 65,
            "rationale": "Multi-tf agreement on BTC",
            "key_observations": [
                "BTC 1h RSI 68 + price > SMA20",
                "4h momentum positive",
            ],
        }, None)
    monkeypatch.setattr(mo, "llm_structured", fake_llm_structured)
    monkeypatch.setattr(mo, "_has_sufficient_signal", lambda ctx: True)

    async def fake_builder(symbols, market_type="spot", **kw):
        return ("snapshot", {"fallback": False, "errors": [], "symbols": {}})
    import aletheia.trading.market_context as mc
    monkeypatch.setattr(mc, "build_market_context", fake_builder)

    from aletheia.agents.market_observer import run_market_observer
    from aletheia.storage import repositories as repo
    out = asyncio.run(run_market_observer(permission_mode="Full", run_id="run-mo-2"))
    assert any(o.startswith("opportunity:") for o in out["outputs"])

    opps = repo.list_opportunities(limit=5)
    assert opps, "observer must have created an opportunity"
    opp = opps[0]
    assert opp["direction_bias"] == "long"
    assert opp["confidence"] == "medium"
    # The score is now persisted as a real column, not just embedded in thesis.
    assert opp["confidence_score"] == 65
    # Thesis must surface the score and observations (not just rationale).
    assert "score=65" in opp["thesis"]
    assert "Observations:" in opp["thesis"]

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-mo-2")


def test_prediction_market_lines_require_fresh_rows(tmp_path, monkeypatch):
    """Old prediction rows must not enter market_observer context as current 24h moves."""
    _setup_env(tmp_path, monkeypatch)
    import aletheia.agents.market_observer as mo
    from aletheia.storage.repositories import _insert

    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=14)).isoformat()
    fresh = now.isoformat()
    base = {
        "event_id": "ev-test",
        "source": "polymarket",
        "condition_id": None,
        "outcomes": "[]",
        "outcome_prices": "[]",
        "yes_probability": 0.7,
        "no_probability": 0.3,
        "probability_change_24h": 0.18,
        "volume_24h": 100000.0,
        "liquidity": 50000.0,
        "status": "active",
        "raw": "{}",
    }
    _insert("prediction_markets", {
        **base,
        "id": "pm-stale",
        "external_market_id": "pm-stale",
        "question": "Will Bitcoin reach 100k?",
        "updated_at": stale,
    })
    _insert("prediction_markets", {
        **base,
        "id": "pm-fresh",
        "external_market_id": "pm-fresh",
        "question": "Will Bitcoin rally this week?",
        "updated_at": fresh,
    })

    cutoff = (now - timedelta(hours=6)).isoformat()
    lines = mo._prediction_market_signal_lines("BTC/USDT", cutoff)
    joined = "\n".join(lines)
    assert "rally this week" in joined
    assert "reach 100k" not in joined


def test_research_prediction_context_requires_enabled_and_fresh_claims(tmp_path, monkeypatch):
    """Research agent should not reuse stale prediction-market claims when the module is off."""
    _setup_env(tmp_path, monkeypatch)
    from aletheia.agents.research_agent import _prediction_context_block
    from aletheia.config.loader import load_config, save_config
    from aletheia.storage.repositories import _insert

    cfg = load_config()
    cfg.market_data.prediction_enabled = False
    save_config(cfg)
    _insert("prediction_market_claims", {
        "id": "claim-stale",
        "market_id": "pm-stale",
        "claim_text": "Old probability momentum should not appear",
        "claim_type": "probability_momentum",
        "direction": "long",
        "confidence": 0.7,
        "source": "prediction_observer",
        "metadata": "{}",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    })
    assert _prediction_context_block() == ""

    cfg.market_data.prediction_enabled = True
    save_config(cfg)
    assert _prediction_context_block() == ""

    _insert("prediction_market_claims", {
        "id": "claim-fresh",
        "market_id": "pm-fresh",
        "claim_text": "Fresh probability momentum may appear",
        "claim_type": "probability_momentum",
        "direction": "long",
        "confidence": 0.7,
        "source": "prediction_observer",
        "metadata": "{}",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    assert "Fresh probability momentum may appear" in _prediction_context_block()


def test_observer_includes_fresh_perp_structure_context(tmp_path, monkeypatch):
    """Fresh perp-structure snapshots must reach the market_observer LLM prompt."""
    _setup_env(tmp_path, monkeypatch)
    import aletheia.agents.market_observer as mo
    from aletheia.storage import repositories as repo

    repo.insert_perp_structure_snapshot({
        "id": "perp-struct-observer-1",
        "symbol": "BTC/USDT",
        "venue": "hyperliquid",
        "source": "vergex",
        "as_of_time": datetime.now(timezone.utc).isoformat(),
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "data_quality": "complete",
        "poc": 62267.64,
        "value_area_low": 60000.0,
        "value_area_high": 65000.0,
        "short_liq_above_usd": 335_141_283.44,
        "long_liq_below_usd": 220_161_274.01,
        "top10_concentration": 0.21,
        "top50_concentration": 0.44,
    })

    captured = []

    async def fake_llm_structured(*, system, user, schema, max_tokens=1024, provider=None):
        captured.append(user)
        return ({
            "direction_bias": "neutral",
            "confidence": "speculative",
            "confidence_score": 50,
            "rationale": "structure context inspected",
            "key_observations": ["Perp structure block present"],
        }, None)

    monkeypatch.setattr(mo, "llm_structured", fake_llm_structured)
    monkeypatch.setattr(mo, "_has_sufficient_signal", lambda ctx: True)

    async def fake_builder(symbols, market_type="spot", **kw):
        return ("snapshot", {"fallback": False, "errors": [], "symbols": {}})

    import aletheia.trading.market_context as mc
    monkeypatch.setattr(mc, "build_market_context", fake_builder)

    from aletheia.agents.market_observer import run_market_observer
    asyncio.run(run_market_observer(permission_mode="Full", run_id="run-mo-struct"))

    btc_prompt = next(p for p in captured if "Market data for BTC/USDT" in p)
    assert "Perp structure" in btc_prompt
    assert "POC $62,268" in btc_prompt
    assert "short-liq above $335M vs long-liq below $220M" in btc_prompt

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-mo-struct")


def test_observer_thesis_fallback_avoids_raw_snapshot(tmp_path, monkeypatch):
    """When LLM returns empty rationale + observations, thesis must NOT dump
    the raw markdown snapshot (which used to leak through and look broken on
    the dashboard). Stub explanation instead."""
    _setup_env(tmp_path, monkeypatch)
    import aletheia.agents.market_observer as mo

    async def fake_llm_structured(*a, **kw):
        # Model returns valid shape but every narrative field is empty —
        # this is the codex_cli "JSON parse dropped fields" failure mode.
        return ({
            "direction_bias": "neutral",
            "confidence": "speculative",
            "rationale": "",
            "key_observations": [],
        }, None)
    monkeypatch.setattr(mo, "llm_structured", fake_llm_structured)
    monkeypatch.setattr(mo, "_has_sufficient_signal", lambda ctx: True)

    async def fake_builder(symbols, market_type="spot", **kw):
        # Big realistic snapshot — would be ugly if it leaked into thesis.
        return (
            "## Verified market data snapshot\n### BTCUSDT\n| TF | Close ...",
            {"fallback": False, "errors": [], "symbols": {}},
        )
    import aletheia.trading.market_context as mc
    monkeypatch.setattr(mc, "build_market_context", fake_builder)

    from aletheia.agents.market_observer import run_market_observer
    from aletheia.storage import repositories as repo
    asyncio.run(run_market_observer(permission_mode="Full", run_id="run-mo-fb"))
    opps = repo.list_opportunities(limit=5)
    assert opps
    opp = opps[0]
    # The raw markdown markers must NOT appear in thesis.
    assert "## Verified market data snapshot" not in opp["thesis"]
    assert "###" not in opp["thesis"]
    # When rationale + observations are empty, thesis uses a clean fallback
    # label instead of leaking raw snapshot content.
    assert "market read" in opp["thesis"]
    # confidence_score is null (not provided by the model).
    assert opp["confidence_score"] is None

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-mo-fb")


def test_observer_ignores_invalid_confidence_score(tmp_path, monkeypatch):
    """Out-of-range / non-numeric scores must be dropped, not crash."""
    _setup_env(tmp_path, monkeypatch)
    import aletheia.agents.market_observer as mo

    async def fake_llm_structured(*a, **kw):
        return ({
            "direction_bias": "neutral",
            "confidence": "speculative",
            "confidence_score": "not a number",
            "rationale": "",
            "key_observations": "should be a list but isn't",
        }, None)
    monkeypatch.setattr(mo, "llm_structured", fake_llm_structured)
    monkeypatch.setattr(mo, "_has_sufficient_signal", lambda ctx: True)

    async def fake_builder(symbols, market_type="spot", **kw):
        return ("snapshot", {"fallback": False, "errors": [], "symbols": {}})
    import aletheia.trading.market_context as mc
    monkeypatch.setattr(mc, "build_market_context", fake_builder)

    from aletheia.agents.market_observer import run_market_observer
    out = asyncio.run(run_market_observer(permission_mode="Full", run_id="run-mo-3"))
    # Must not raise — junk inputs are dropped silently.
    assert out["outputs"]  # opportunity still created (with neutral)

    from aletheia.agents.helpers import _release_run_counter
    _release_run_counter("run-mo-3")

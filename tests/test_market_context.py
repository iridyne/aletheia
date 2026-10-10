"""Tests for the new market context layer (indicators + builder).

Covers:
  - Stdlib indicators (SMA / RSI / ATR / Bollinger / momentum / vol z-score / RS rank)
  - market_context.build_market_context (multi-TF render + fallback)
"""

import asyncio
import math

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


def test_macd_matches_hand_computed_small_series():
    # fast=2, slow=3, signal=2 over [1, 2, 4, 8, 16], SMA-seeded EMAs:
    # EMA2 = 1.5, 19/6, 115/18, 691/54; EMA3 = 7/3, 31/6, 127/12
    # line = 5/6, 11/9, 239/108; signal = 37/36, then 589/324.
    out = indicators.macd([1, 2, 4, 8, 16], fast=2, slow=3, signal=2)
    assert [tuple(round(v, 9) for v in point) for point in out] == [
        (round(11 / 9, 9), round(37 / 36, 9), round(11 / 9 - 37 / 36, 9)),
        (round(239 / 108, 9), round(589 / 324, 9), round(239 / 108 - 589 / 324, 9)),
    ]


def test_macd_ramp_has_constant_lag_and_needs_34_closes():
    # A unit ramp lags EMA(n) by (n-1)/2 exactly: line = 12.5 - 5.5 = 7, hist = 0.
    line, signal, hist = indicators.macd([float(i) for i in range(100)])[-1]
    assert (line, round(signal, 9), round(hist, 9)) == (7.0, 7.0, 0.0)
    assert indicators.macd([float(i) for i in range(33)]) == []
    assert len(indicators.macd([float(i) for i in range(34)])) == 1
    assert len(indicators.macd([float(i) for i in range(35)])) == 2


def test_adx_matches_hand_computed_small_series():
    # period=2. TR = 2, 3, 4; +DM = 1, 1, 0; -DM = 0, 0, 2.
    # First DI: +DI = 1/2.5 = 40, -DI = 0 -> DX 100. Next: TR 3.25, +DM 0.5,
    # -DM 1 -> +DI 200/13, -DI 400/13, DX 100/3. ADX = mean(100, 100/3).
    out = indicators.adx([10, 11, 12, 11], [8, 9, 9, 7], [9, 10, 11, 8], period=2)
    assert out is not None
    assert [round(v, 9) for v in out] == [round(200 / 3, 9), round(200 / 13, 9), round(400 / 13, 9)]
    # One more bar (12, 10, 11): TR 4, +DM 1 -> TR 3.625, +DM 0.75, -DM 0.5,
    # +DI 600/29, -DI 400/29, DX 20; Wilder ADX = (200/3 + 20) / 2 = 130/3.
    out = indicators.adx([10, 11, 12, 11, 12], [8, 9, 9, 7, 10], [9, 10, 11, 8, 11], period=2)
    assert out is not None
    assert [round(v, 9) for v in out] == [round(130 / 3, 9), round(600 / 29, 9), round(400 / 29, 9)]


def test_adx_ramp_saturates_and_flat_or_short_series_is_unavailable():
    highs = [i + 1.0 for i in range(28)]
    lows = [float(i) for i in range(28)]
    assert indicators.adx(highs, lows, highs) == (100.0, 100.0, 0.0)
    assert indicators.adx(highs[:27], lows[:27], highs[:27]) is None
    assert indicators.adx([1.0] * 28, [1.0] * 28, [1.0] * 28) is None
    # Inside bars only: range exists but there is no directional movement (0/0).
    assert indicators.adx([2.0] * 28, [1.0] * 28, [1.5] * 28) is None


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


def test_indicator_snapshot_rejects_nonfinite_rsi_and_atr():
    from aletheia.trading.market_context import _compute_snap

    closes = [100.0] * 20
    highs = [101.0] * 19 + [float("inf")]
    lows = [99.0] * 20
    closes[-1] = float("nan")

    snapshot = _compute_snap(closes, highs, lows, [1.0] * 20, "1h")

    assert snapshot.rsi_14 is None
    assert snapshot.atr_14 is None


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
    is_closed = True

    def __init__(self, ts, o, h, l, c, v, exchange=None):
        self.timestamp = ts
        self.open = o
        self.high = h
        self.low = l
        self.close = c
        self.volume = v
        self.exchange = exchange


class _FakeProvider:
    """Mock MarketDataProvider — no network, deterministic uptrend candles."""

    def __init__(self, *, fail_after: int | None = None):
        self.fail_after = fail_after
        self.calls = 0
        self.ohlcv_symbols = []

    async def get_ticker(self, symbol):
        from aletheia.data.providers import Ticker
        return Ticker(symbol=symbol, last_price=100.0, volume_24h=1e6,
                      change_pct_24h=0.5, provider="fake")

    async def get_ohlcv(self, symbol, timeframe, limit):
        self.calls += 1
        self.ohlcv_symbols.append(symbol)
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("simulated network failure")
        # Generate `limit` candles: monotonic uptrend, deterministic per symbol.
        seed = sum(ord(c) for c in str(symbol)) % 100
        return [
            _FakeCandle(
                ts=1_700_000_000_000 + i * 60_000,
                o=100.0 + seed + i * 0.1,
                h=100.5 + seed + i * 0.1,
                l=99.5 + seed + i * 0.1,
                c=100.0 + seed + i * 0.1,
                v=1000.0 + i * 5,
                exchange=getattr(symbol, "exchange", None),
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
    # One normalized technical-context-v1 bundle per symbol.
    assert ctx.count("technical-context-v1") == 2
    assert "trend=" in ctx and "vwap=" in ctx and "donchian=" in ctx
    assert "last 10 1h closes" not in ctx
    # Daily history is intentionally too short for the 50-bar contract.
    assert "unavailable:warmup(30)" in ctx
    # Glossary always present.
    assert "Indicator glossary" in ctx
    # Cross-asset RS table.
    assert "Cross-asset relative strength" in ctx
    assert ctx.count("Cross-asset relative strength") == 1
    # Debug info populated.
    assert "BTCUSDT" in debug["symbols"]
    technical = debug["technical_context"]
    assert technical["adapter_id"] == "technical-context-v1"
    assert len(technical["artifacts"]) == 2
    assert all(
        item["stable_id"].startswith("technical-context-v1:")
        for item in technical["artifacts"]
    )
    assert all(item["observed_at"] for item in technical["artifacts"])
    assert debug["fallback"] is False


def test_build_context_rejects_consistent_foreign_candle_venue():
    from aletheia.data.cache import get_cache
    from aletheia.trading.market_context import build_market_context

    class FallbackVenueProvider(_FakeProvider):
        async def get_ohlcv(self, symbol, timeframe, limit):
            candles = await super().get_ohlcv(symbol, timeframe, limit)
            for candle in candles:
                candle.exchange = "okx"
            return candles

    get_cache().invalidate()
    ctx, debug = asyncio.run(build_market_context(
        ["BTC/USDT"], exchange="bitget", provider=FallbackVenueProvider(),
    ))

    assert debug["fallback"] is True
    assert debug["technical_context"]["artifacts"] == []
    assert any("foreign_timeframe_venue" in error for error in debug["errors"])
    assert "technical-context-v1 (okx" not in ctx


def test_build_context_fails_closed_on_inconsistent_timeframe_venues():
    from aletheia.data.cache import get_cache
    from aletheia.trading.market_context import build_market_context

    class MixedVenueProvider(_FakeProvider):
        async def get_ohlcv(self, symbol, timeframe, limit):
            candles = await super().get_ohlcv(symbol, timeframe, limit)
            venue = "okx" if timeframe == "4h" else "bitget"
            for candle in candles:
                candle.exchange = venue
            return candles

    get_cache().invalidate()
    ctx, debug = asyncio.run(build_market_context(
        ["BTC/USDT"], exchange="bitget", provider=MixedVenueProvider(),
    ))

    assert debug["fallback"] is True
    assert debug["technical_context"]["artifacts"] == []
    assert any("inconsistent_timeframe_venues" in error for error in debug["errors"])
    assert "technical-context-v1 (bitget" not in ctx


def test_build_context_marks_missing_timeframe_in_partial_bundle():
    from aletheia.data.cache import get_cache
    from aletheia.trading.market_context import build_market_context

    class MissingTimeframeProvider(_FakeProvider):
        async def get_ohlcv(self, symbol, timeframe, limit):
            if timeframe == "4h":
                return []
            return await super().get_ohlcv(symbol, timeframe, limit)

    get_cache().invalidate()
    ctx, debug = asyncio.run(build_market_context(
        ["BTC/USDT"], exchange="bitget", provider=MissingTimeframeProvider(),
    ))
    artifact = debug["technical_context"]["artifacts"][0]

    assert "- 4h q=unavailable:no_candles close=n/a" in ctx
    assert artifact["quality_state"] == "partial"
    assert artifact["timeframes"] == ["1h", "4h", "1d"]


def test_build_context_falls_back_on_total_failure(tmp_path, monkeypatch):
    """If every OHLCV call fails, we degrade to the legacy 1-line context."""
    from aletheia.trading.market_context import build_market_context
    from aletheia.data.cache import get_cache
    get_cache().invalidate()  # clear any state from previous tests

    provider = _FakeProvider(fail_after=0)  # fail immediately

    # Patch latest_snapshot to return a known shape — the fallback path uses it.
    monkeypatch.setattr(
        "aletheia.trading.market_data.latest_snapshot",
        lambda sym, market_type=None: {"price": 60000.0, "change_pct_24h": 0.05},
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
    assert btc["1h"]["atr_pct"] is not None
    assert btc["1h"]["atr_pct"] > 0


def test_build_context_preserves_instrument_identity_and_cache_scope():
    """Spot and perp reads for one symbol must never share a cache entry."""
    from aletheia.data.cache import get_cache
    from aletheia.data.symbols import InstrumentId
    from aletheia.trading.market_context import build_market_context

    get_cache().invalidate()
    provider = _FakeProvider()

    asyncio.run(build_market_context(
        ["CAP/USDT"], market_type="perp", exchange="okx", provider=provider,
    ))
    asyncio.run(build_market_context(
        ["CAP/USDT"], market_type="spot", exchange="okx", provider=provider,
    ))

    assert provider.calls == 6
    assert all(isinstance(symbol, InstrumentId) for symbol in provider.ohlcv_symbols)
    assert {symbol.market_type for symbol in provider.ohlcv_symbols[:3]} == {"perp"}
    assert {symbol.market_type for symbol in provider.ohlcv_symbols[3:]} == {"spot"}


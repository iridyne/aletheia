"""Unit tests for the 9-domain data collection layer.

All tests are offline (no network, no DB). We mock ccxt and httpx.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# microstructure.py
# ---------------------------------------------------------------------------

def test_parse_orderbook_basic():
    from aletheia.data.collectors.microstructure import _parse_orderbook

    ob = {
        "bids": [[50000.0, 1.0], [49990.0, 2.0]],
        "asks": [[50010.0, 1.0], [50020.0, 2.0]],
    }
    snap = _parse_orderbook("BTC/USDT", "binance", ob, latency_ms=5)
    assert snap is not None
    assert snap.symbol == "BTC/USDT"
    assert snap.exchange == "binance"
    assert snap.bid_price == 50000.0
    assert snap.ask_price == 50010.0
    # spread = (50010 - 50000) / 50005 * 10000 ≈ 2 bps
    assert snap.spread_bps == pytest.approx(2.0, abs=0.5)
    assert snap.fetch_latency_ms == 5


def test_parse_orderbook_empty():
    from aletheia.data.collectors.microstructure import _parse_orderbook

    snap = _parse_orderbook("BTC/USDT", "binance", {"bids": [], "asks": []}, 0)
    assert snap is None


def test_depth_within():
    from aletheia.data.collectors.microstructure import _depth_within

    levels = [[50000.0, 2.0], [49980.0, 3.0], [49950.0, 5.0]]
    # mid=50005, threshold=50005 * 0.001 = 50.005, so cutoff at 50005-50.005=49954.995
    # bid side: prices >= 49954.995 included → [50000.0×2.0, 49980.0×3.0] = 249940
    result = _depth_within(levels, price_threshold=49955.0, side="bid")
    assert result == pytest.approx(50000.0 * 2.0 + 49980.0 * 3.0)


# ---------------------------------------------------------------------------
# derivatives.py
# ---------------------------------------------------------------------------

def test_parse_funding_basic():
    from aletheia.data.collectors.derivatives import _parse_funding

    raw = {
        "fundingRate": 0.0001,
        "fundingDatetime": "2026-06-14T08:00:00Z",
        "markPrice": 50000.0,
    }
    snap = _parse_funding("BTC/USDT", "binance", raw)
    assert snap is not None
    assert snap.funding_rate == 0.0001
    assert snap.funding_rate_annualized == pytest.approx(0.0001 * 3 * 365, rel=1e-3)
    assert snap.mark_price == 50000.0


def test_parse_funding_missing_rate():
    from aletheia.data.collectors.derivatives import _parse_funding

    snap = _parse_funding("BTC/USDT", "binance", {})
    assert snap is None


def test_parse_oi_basic():
    from aletheia.data.collectors.derivatives import _parse_oi

    raw = {"openInterestValue": 1_000_000_000.0, "info": {"sumOpenInterest": "20000"}}
    snap = _parse_oi("BTC/USDT", "binance", raw)
    assert snap is not None
    assert snap.open_interest_usd == 1_000_000_000.0
    assert snap.open_interest_coin == 20000.0


# ---------------------------------------------------------------------------
# macro.py
# ---------------------------------------------------------------------------

def test_classify_risk_regime_risk_off():
    from aletheia.data.collectors.macro import _classify_risk_regime

    assert _classify_risk_regime(vix=30.0, cap_change=None) == "risk_off"
    assert _classify_risk_regime(vix=None, cap_change=-5.0) == "risk_off"


def test_classify_risk_regime_risk_on():
    from aletheia.data.collectors.macro import _classify_risk_regime

    assert _classify_risk_regime(vix=12.0, cap_change=1.5) == "risk_on"


def test_classify_risk_regime_neutral():
    from aletheia.data.collectors.macro import _classify_risk_regime

    assert _classify_risk_regime(vix=20.0, cap_change=0.5) == "neutral"
    assert _classify_risk_regime(vix=None, cap_change=None) == "neutral"


@pytest.mark.asyncio
async def test_fetch_fred_parses_response(monkeypatch):
    """FRED fetcher extracts latest non-'.' observation value."""
    import httpx
    from aletheia.data.collectors.macro import _fetch_fred

    # Mock a FRED-like JSON response
    def _mock_response(series_id):
        values = {
            "SP500": "5500.12",
            "VIXCLS": "18.50",
            "DTWEXBGS": "103.45",
            "NASDAQCOM": "17200.00",
        }
        return {
            "observations": [
                {"date": "2026-06-14", "value": "."},       # holiday → skip
                {"date": "2026-06-13", "value": values.get(series_id, "0")},
            ]
        }

    class FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def __init__(self, series_id): self._sid = series_id
        def json(self): return _mock_response(self._sid)

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, params=None):
            return FakeResponse(params["series_id"])

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient())

    spy, vix, dxy, nasdaq = await _fetch_fred("fake-key")
    assert spy == 5500.12
    assert vix == 18.50
    assert dxy == 103.45
    assert nasdaq == 17200.00


@pytest.mark.asyncio
async def test_fetch_fred_skips_dots(monkeypatch):
    """All '.' values → returns None for that series."""
    import httpx
    from aletheia.data.collectors.macro import _fetch_fred

    class FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"observations": [{"date": "2026-06-14", "value": "."}]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url, params=None): return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FakeClient())

    spy, vix, dxy, nasdaq = await _fetch_fred("fake-key")
    assert spy is None
    assert vix is None


# ---------------------------------------------------------------------------
# normalizer.py
# ---------------------------------------------------------------------------

def test_compute_tradability_high():
    from aletheia.data.normalizer import compute_tradability

    t = compute_tradability(
        symbol="BTC/USDT", exchange="binance",
        spread_bps=2.0, depth_10bps_usd=500_000, volume_24h_usd=200_000_000,
    )
    assert t.score > 60
    assert t.is_tradable is True
    assert t.execution_cost_estimate_bps < 5


def test_compute_tradability_low():
    from aletheia.data.normalizer import compute_tradability

    t = compute_tradability(
        symbol="UNKNOWN/USDT", exchange="binance",
        spread_bps=50.0, depth_10bps_usd=1000, volume_24h_usd=10_000,
    )
    assert t.score < 30
    assert t.is_tradable is False


def test_compute_leverage_state_crowded():
    from aletheia.data.normalizer import compute_leverage_state

    lv = compute_leverage_state(
        symbol="BTC/USDT",
        funding_rate=0.003,   # 0.3% per 8h, very high
        funding_annualized=0.003 * 3 * 365,
        open_interest_usd=5e9,
        oi_delta_1h_pct=10.0,
        liquidation_1h_usd=500_000,
    )
    assert lv.crowding_score > 50
    assert lv.funding_opportunity is True


def test_compute_leverage_state_normal():
    from aletheia.data.normalizer import compute_leverage_state

    lv = compute_leverage_state(
        symbol="BTC/USDT",
        funding_rate=0.0001,
        funding_annualized=0.0001 * 3 * 365,
    )
    assert lv.crowding_score < 10
    assert lv.funding_opportunity is False


# ---------------------------------------------------------------------------
# context_builder.py
# ---------------------------------------------------------------------------

def test_render_enriched_context_md_empty():
    from aletheia.data.context_builder import render_enriched_context_md

    result = render_enriched_context_md(["BTC/USDT"], {}, {}, None)
    assert result == ""


def test_render_enriched_context_md_with_data():
    from aletheia.data.context_builder import render_enriched_context_md
    from aletheia.data.normalizer import compute_tradability, compute_leverage_state
    from aletheia.data.models import MacroSnapshot

    t = compute_tradability("BTC/USDT", "binance", 2.5, 300_000, 100_000_000)
    lv = compute_leverage_state(
        "BTC/USDT", funding_rate=0.0002, funding_annualized=0.0002*3*365
    )
    macro = MacroSnapshot(
        btc_dominance=55.0, total_market_cap_usd=2e12,
        vix_close=18.0, risk_regime="neutral", observed_at="2026-06-14T00:00:00+00:00"
    )

    md = render_enriched_context_md(
        ["BTC/USDT"],
        {"BTC/USDT": t},
        {"BTC/USDT": lv},
        macro,
    )
    assert "Microstructure" in md
    assert "Derivatives" in md
    assert "Macro" in md
    assert "BTC/USDT" in md
    assert "neutral" in md


def test_build_market_context_snapshot_completeness():
    from aletheia.data.context_builder import build_market_context_snapshot
    from aletheia.data.normalizer import compute_tradability, compute_leverage_state

    symbols = ["BTC/USDT", "ETH/USDT"]
    t = {s: compute_tradability(s, "binance", 2.0, 200_000, 50_000_000) for s in symbols}
    lv = {s: compute_leverage_state(s, funding_rate=0.0001) for s in symbols}

    snap = build_market_context_snapshot(symbols, t, lv, macro=None)
    assert snap.data_completeness > 0.5
    assert "macro" in snap.stale_domains
    assert len(snap.tradability) == 2
    assert len(snap.leverage) == 2

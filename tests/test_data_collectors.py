"""Unit tests for the market data collection layer.

All tests are offline (no network, no DB). We mock ccxt and httpx.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# microstructure.py
# ---------------------------------------------------------------------------

def test_parse_orderbook_basic():
    from aletheia.data.collectors.microstructure import _parse_orderbook

    ob = {
        "bids": [[50000.0, 1.0], [49990.0, 2.0]],
        "asks": [[50010.0, 1.0], [50020.0, 2.0]],
    }
    snap = _parse_orderbook(
        "BTC/USDT", "binance", ob, latency_ms=5, market_type="perp"
    )
    assert snap is not None
    assert snap.symbol == "BTC/USDT"
    assert snap.exchange == "binance"
    assert snap.market_type == "perp"
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


def test_hyperliquid_oi_delta_uses_same_source_hour_baseline(monkeypatch):
    from aletheia.data.collectors.hyperliquid import _parse_oi
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(
        repo,
        "open_interest_near",
        lambda symbol, exchange, **kwargs: {
            "exchange": "hyperliquid",
            "open_interest_usd": 100.0,
        },
    )
    monkeypatch.setattr(
        repo,
        "latest_open_interest",
        lambda *args, **kwargs: {"open_interest_usd": 199.0},
    )

    snap = _parse_oi(
        "BTC/USDT",
        {"openInterest": "11", "markPx": "10"},
        datetime.now(timezone.utc).isoformat(),
    )

    assert snap is not None
    assert snap.oi_change_1h_pct == 10.0


def test_hyperliquid_oi_delta_over_300_percent_is_unavailable(monkeypatch):
    from aletheia.data.collectors.hyperliquid import _parse_oi
    from aletheia.storage import repositories as repo

    monkeypatch.setattr(
        repo,
        "open_interest_near",
        lambda *args, **kwargs: {"open_interest_usd": 1.0},
    )

    snap = _parse_oi(
        "BTC/USDT",
        {"openInterest": "40.1", "markPx": "10"},
        datetime.now(timezone.utc).isoformat(),
    )

    assert snap is not None
    assert snap.oi_change_1h_pct is None


@pytest.mark.parametrize(
    "ctx",
    [
        {"openInterest": "nan", "markPx": "10"},
        {"openInterest": "10", "markPx": "inf"},
        {"openInterest": "-1", "markPx": "10"},
    ],
)
def test_hyperliquid_oi_rejects_nonfinite_or_nonpositive_values(ctx):
    from aletheia.data.collectors.hyperliquid import _parse_oi

    assert _parse_oi(
        "BTC/USDT", ctx, datetime.now(timezone.utc).isoformat()
    ) is None


@pytest.mark.parametrize(
    "ctx",
    [
        {"funding": "nan", "markPx": "10"},
        {"funding": "0.001", "markPx": "inf"},
        {"funding": "0.001", "markPx": "0"},
    ],
)
def test_hyperliquid_funding_rejects_nonfinite_or_nonpositive_mark(ctx):
    from aletheia.data.collectors.hyperliquid import _parse_funding

    assert _parse_funding(
        "BTC/USDT", ctx, datetime.now(timezone.utc).isoformat()
    ) is None


@pytest.mark.asyncio
async def test_hyperliquid_predicted_funding_isolates_invalid_rows(monkeypatch):
    import aletheia.data.collectors.hyperliquid as mod

    class Response:
        def raise_for_status(self):
            return None

        @staticmethod
        def json():
            return [
                ["BTC", [["HlPerp", {"fundingRate": "nan", "fundingIntervalHours": 1}]]],
                ["ETH", [["HlPerp", {"fundingRate": "0.001", "fundingIntervalHours": 0}]]],
                ["SOL", [["HlPerp", {"fundingRate": "0.002", "fundingIntervalHours": 2}]]],
            ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(mod, "httpx_client", lambda **kwargs: Client())

    rows = await mod._fetch_predicted_fundings(
        None, datetime.now(timezone.utc).isoformat()
    )

    assert [row.symbol for row in rows] == ["SOL/USDT"]
    assert rows[0].funding_rate_annualized == pytest.approx(0.002 * 12 * 365)


@pytest.mark.asyncio
async def test_coinalyze_oi_delta_uses_hour_baseline_and_sanity_cap(monkeypatch):
    import aletheia.data.collectors.coinalyze as mod
    from aletheia.storage import repositories as repo

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return [
                {"symbol": "BTCUSDT_PERP.A", "open_interest_usd": 110.0},
                {"symbol": "ETHUSDT_PERP.A", "open_interest_usd": 401.0},
            ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(mod, "httpx_client", lambda **kwargs: Client())
    monkeypatch.setattr(
        repo,
        "open_interest_near",
        lambda symbol, exchange, **kwargs: {
            "open_interest_usd": 100.0 if symbol.startswith("BTC") else 1.0,
        },
    )

    rows = await mod._fetch_open_interest(
        "unused",
        ["BTC/USDT", "ETH/USDT"],
        ["BTCUSDT_PERP.A", "ETHUSDT_PERP.A"],
    )

    assert rows[0].oi_change_1h_pct == 10.0
    assert rows[1].oi_change_1h_pct is None


@pytest.mark.asyncio
async def test_coinalyze_skips_coin_only_and_nonfinite_oi(monkeypatch):
    import aletheia.data.collectors.coinalyze as mod

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return [
                {"symbol": "BTCUSDT_PERP.A", "open_interest": 123.0},
                {"symbol": "ETHUSDT_PERP.A", "open_interest_usd": "nan"},
                {"symbol": "SOLUSDT_PERP.A", "open_interest_usd": "inf"},
            ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(mod, "httpx_client", lambda **kwargs: Client())

    rows = await mod._fetch_open_interest(
        "unused",
        ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
        ["BTCUSDT_PERP.A", "ETHUSDT_PERP.A", "SOLUSDT_PERP.A"],
    )

    assert rows == []


@pytest.mark.asyncio
async def test_coinalyze_liquidations_isolate_invalid_rows(monkeypatch):
    import aletheia.data.collectors.coinalyze as mod

    now = datetime.now(timezone.utc)

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return [
                {"t": now.timestamp() * 1000, "lsl": "nan", "ssl": 0},
                {"t": now.timestamp() * 1000, "lsl": -1, "ssl": 0},
                {"t": (now.timestamp() + 3600) * 1000, "lsl": 1, "ssl": 0},
                {"t": now.timestamp() * 1000, "lsl": 25.0, "ssl": 10.0},
            ]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(mod, "httpx_client", lambda **kwargs: Client())

    rows = await mod._fetch_liquidations(
        "unused", ["BTC/USDT"], ["BTCUSDT_PERP.A"]
    )

    assert [(row.side, row.quantity_usd) for row in rows] == [
        ("long", 25.0),
        ("short", 10.0),
    ]


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


def test_render_enriched_context_md_with_perp_structure():
    from aletheia.data.context_builder import render_enriched_context_md

    md = render_enriched_context_md(
        ["BTC/USDT"],
        {},
        {},
        None,
        perp_structure={
            "BTC/USDT": {
                "source": "vergex",
                "venue": "hyperliquid",
                "data_quality": "complete",
                "as_of_time": "2026-07-04T14:16:53+00:00",
                "poc": 62267.64,
                "value_area_low": 60000.0,
                "value_area_high": 65000.0,
                "short_liq_above_usd": 335_141_283.44,
                "long_liq_below_usd": 220_161_274.01,
                "top10_concentration": 0.21,
                "top50_concentration": 0.44,
            }
        },
    )

    assert "Perp structure" in md
    assert "BTC/USDT" in md
    assert "POC $62,268" in md
    assert "Value Area $60,000-$65,000" in md
    assert "short-liq above $335M vs long-liq below $220M" in md
    assert "top10 21%" in md
    assert "complete" in md
    assert "as-of 2026-07-04T14:16:53+00:00" in md


@pytest.mark.asyncio
async def test_vergex_structure_collector_filters_rejected_fields():
    from aletheia.data.collectors.vergex_structure import VergexStructureCollector

    collector = VergexStructureCollector(fixture_payloads=[{
        "symbol": "BTC/USDT",
        "venue": "hyperliquid",
        "source": "vergex",
        "as_of_time": "2026-07-04T14:16:53+00:00",
        "data_quality": "complete",
        "poc": 62267.64,
        "value_area_low": 60000.0,
        "value_area_high": 65000.0,
        "short_liq_above_usd": 335_141_283.44,
        "long_liq_below_usd": 220_161_274.01,
        "top10_concentration": 0.21,
        "top50_concentration": 0.44,
        "wallet_addresses": ["0xabc"],
        "api_key": "must-not-survive",
        "system_prompt": "must-not-survive",
        "account_balance": 123.45,
        "unknown_nested": {"credential": "must-not-survive"},
    }])

    out = await collector.collect(["BTC/USDT"])

    assert out["errors"] == []
    assert len(out["perp_structure"]) == 1
    snap = out["perp_structure"][0]
    assert snap.symbol == "BTC/USDT"
    assert snap.poc == 62267.64
    assert snap.cascade_risk is None
    raw = snap.raw_json
    assert "wallet_addresses" not in raw
    assert "api_key" not in raw
    assert "system_prompt" not in raw
    assert "account_balance" not in raw
    assert "unknown_nested" not in raw


@pytest.mark.asyncio
async def test_vergex_structure_fixture_persists_btc_eth_sol():
    from aletheia.data.collectors.vergex_structure import VergexStructureCollector
    from aletheia.storage import repositories as repo

    collector = VergexStructureCollector(
        fixture_payloads=VergexStructureCollector.sample_fixture_payloads(
            as_of_time="2026-07-04T14:16:53+00:00",
        )
    )

    out = await collector.collect(["BTC/USDT", "ETH/USDT", "SOL/USDT"])

    assert out["errors"] == []
    assert [s.symbol for s in out["perp_structure"]] == ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    for snap in out["perp_structure"]:
        repo.insert_perp_structure_snapshot(snap.model_dump())

    for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        latest = repo.latest_perp_structure(sym)
        assert latest is not None
        assert latest["source"] == "vergex"
        assert latest["venue"] == "hyperliquid"


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


def test_perp_structure_repository_roundtrip_and_prune():
    from datetime import datetime, timedelta, timezone
    from aletheia.storage import repositories as repo

    old_ts = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    fresh_ts = datetime.now(timezone.utc).isoformat()
    repo.insert_perp_structure_snapshot({
        "id": "ps-old",
        "symbol": "BTC/USDT",
        "venue": "hyperliquid",
        "source": "vergex",
        "as_of_time": old_ts,
        "observed_at": old_ts,
        "data_quality": "complete",
        "poc": 60000.0,
        "raw_json": '{"wallets":["must-not-be-required"]}',
    })
    repo.insert_perp_structure_snapshot({
        "id": "ps-fresh",
        "symbol": "BTC/USDT",
        "venue": "hyperliquid",
        "source": "vergex",
        "as_of_time": fresh_ts,
        "observed_at": fresh_ts,
        "data_quality": "degraded",
        "poc": 62267.64,
        "value_area_low": 60000.0,
        "value_area_high": 65000.0,
        "short_liq_above_usd": 335_141_283.44,
        "long_liq_below_usd": 220_161_274.01,
        "top10_concentration": 0.21,
        "top50_concentration": 0.44,
        "raw_json": "{}",
    })

    latest = repo.latest_perp_structure("BTC/USDT")
    assert latest is not None
    assert latest["id"] == "ps-fresh"
    assert latest["data_quality"] == "degraded"
    assert latest["poc"] == 62267.64

    assert repo.prune_perp_structure_snapshots(days=30) == 1
    assert repo.latest_perp_structure("BTC/USDT")["id"] == "ps-fresh"



def test_extract_position_x_signal_payload_into_snapshot():
    from aletheia.data.position_x import extract_position_x_snapshot

    snap = extract_position_x_snapshot(
        {
            "symbol": "SNDK/USDT",
            "data": {
                "bias": "bearish",
                "structureRead": "Supply-led with overhead resistance.",
                "dimensions": [
                    {
                        "key": "cgo",
                        "direction": "bearish",
                        "strength": "medium",
                        "percentile": 13.333333333333334,
                        "detail": "support below $5.69M vs overhead above $30.44M",
                    },
                    {
                        "key": "lfa",
                        "direction": "bearish",
                        "strength": "flat",
                        "percentile": 63.33333333333333,
                        "detail": "short above $5.91M vs long below $12.58M",
                    },
                    {
                        "key": "convexity",
                        "direction": "neutral",
                        "detail": "23% of band fuel at densest cluster",
                    },
                    {
                        "key": "cascade",
                        "direction": "neutral",
                        "detail": "0.4% of OI within ±5% of liquidation",
                    },
                ],
                "levels": {
                    "poc": 1960.2759,
                    "support": 1811.4711,
                    "resistance": 1875.2446,
                    "valueAreaLow": 1811.4711,
                    "valueAreaHigh": 2002.7916,
                },
                "metrics": {
                    "shortLiqAbove": 5_909_524.00,
                    "longLiqBelow": 12_584_565.05,
                    "longOverhangPnl": -64_987.32,
                    "shortOverhangPnl": 1_548_924.16,
                    "cascadeVulnPct": 0.0042413245,
                    "top10Pct": 0.4843475373,
                    "convexity": 0.2279036709,
                    "includedPositions": 2249,
                    "state": "complete",
                },
                "compositeZ": -0.9,
                "rank": 25,
                "universeSize": 30,
            },
            "wallets": ["must-not-survive"],
            "credential": "must-not-survive",
        }
    )

    assert snap.symbol == "SNDK/USDT"
    assert snap.structure_bias == "bearish"
    assert snap.composite_z == -0.9
    assert snap.structure_rank == 25
    assert snap.universe_size == 30
    assert snap.value_area_low == 1811.4711
    assert snap.value_area_high == 2002.7916
    assert snap.top10_concentration == 0.4843475373
    assert '"wallets"' not in snap.raw_json
    assert '"credential"' not in snap.raw_json

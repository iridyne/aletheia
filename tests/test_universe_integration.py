"""Integration test — full universe pipeline with mocked CCXT."""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from aletheia.config.schema import UniverseConfig
from aletheia.universe.service import UniverseService
from aletheia.universe.registry import sync_registry
from aletheia.universe.filters import apply_tradability_filters
from aletheia.universe.scanners import run_all_scanners

# Authoritative registered-family set — derived from run_all_scanners itself so
# these assertions track the code instead of a hardcoded list that silently goes
# stale (this test previously hardcoded 4 families, omitting 'event' and later
# 'short_bias'). all_scores carries every registered family regardless of score.
_VALID_FAMILIES = set(run_all_scanners({}).get("all_scores", {}).keys())


def _make_cfg():
    cfg = MagicMock()
    cfg.universe = UniverseConfig(
        enabled=True,
        max_candidates=5,
        core_pool_size=3,
        min_volume_24h_usdt=1_000_000,
        max_spread_bps=30.0,
        explore_ratio=0.2,
        family_quota={"momentum": 0.30, "mean_reversion": 0.30, "volatility": 0.20, "derivatives": 0.20},
        blacklist=["SCAM/USDT"],
    )
    cfg.market_data = MagicMock()
    cfg.market_data.ccxt_exchange = "bitget"
    return cfg


@pytest.mark.asyncio
async def test_registry_routes_reviewed_stock_linked_perps_and_blocks_unknowns(monkeypatch):
    """Reviewed products are supported narrowly; unknown products stay closed."""
    def market(base, *, contract_type, underlying_type, subtype):
        return {
            "id": f"{base}USDT",
            "symbol": f"{base}/USDT:USDT",
            "base": base,
            "quote": "USDT",
            "active": True,
            "swap": True,
            "info": {
                "contractType": contract_type,
                "underlyingType": underlying_type,
                "underlyingSubType": [subtype],
            },
        }

    markets = {
        "BTC/USDT:USDT": market(
            "BTC", contract_type="PERPETUAL", underlying_type="COIN", subtype="PoW"
        ),
        "SOXL/USDT:USDT": market(
            "SOXL", contract_type="TRADIFI_PERPETUAL",
            underlying_type="EQUITY", subtype="TradFi",
        ),
        "SOXS/USDT:USDT": market(
            "SOXS", contract_type="TRADIFI_PERPETUAL",
            underlying_type="EQUITY", subtype="TradFi",
        ),
        "GOLD/USDT:USDT": market(
            "GOLD", contract_type="TRADIFI_PERPETUAL",
            underlying_type="COMMODITY", subtype="TradFi",
        ),
        "MYSTERY/USDT:USDT": {
            **market(
                "MYSTERY", contract_type="PERPETUAL",
                underlying_type="", subtype="",
            ),
            "info": {"contractType": "PERPETUAL"},
        },
    }

    class FakeExchange:
        async def load_markets(self):
            self.markets = markets

    captured = {}

    def fake_sync(assets, **_kwargs):
        captured["assets"] = assets
        return len(assets), 0, 0

    cfg = MagicMock()
    cfg.market_data.ccxt_exchange = "binance"
    cfg.market_data.ccxt_fallback = None
    cfg.exchange.paper_execution_mode = "binance_testnet"
    cfg.exchange.enabled_markets = {"usdt_futures": True}
    cfg.universe.blacklist = []

    monkeypatch.setattr("extensions.data.ccxt_provider.CCXTProvider._ensure_primary", lambda self: FakeExchange())
    monkeypatch.setattr(
        "aletheia.universe.registry.repo.sync_universe_assets", fake_sync
    )

    assert await sync_registry(cfg) == 5
    assets = {asset.symbol: asset for asset in captured["assets"]}

    btc_background = json.loads(assets["BTC/USDT"].metadata)[
        "instrument_background"
    ]
    assert btc_background["asset_class"] == "crypto"
    assert btc_background["reference_asset"] == "BTC"
    assert assets["BTC/USDT"].is_blacklisted is False

    for symbol, multiple in (("SOXL/USDT", 3.0), ("SOXS/USDT", -3.0)):
        asset = assets[symbol]
        background = json.loads(asset.metadata)["instrument_background"]
        assert asset.is_blacklisted is False
        assert asset.blacklist_reason is None
        assert background["asset_class"] == "equity"
        assert background["product_kind"] == "stock_linked_perp"
        assert background["physical_market_type"] == "perp"
        assert background["reference_asset"] == symbol.split("/")[0]
        assert background["embedded_product"] == {
            "kind": "leveraged_etf",
            "benchmark_name": "NYSE Semiconductor Index",
            "benchmark_id": "ICESEMIT",
            "embedded_daily_multiplier": multiple,
            "reset_horizon": "1d",
            "source_refs": [
                "https://www.direxion.com/product/"
                "daily-semiconductor-bull-bear-3x-etfs"
            ],
        }
    assert assets["GOLD/USDT"].blacklist_reason == (
        "unsupported_analysis_context:tradifi_perpetual"
    )
    assert json.loads(assets["GOLD/USDT"].metadata)["instrument_background"][
        "reference_asset"
    ] == "GOLD"
    assert assets["MYSTERY/USDT"].blacklist_reason == (
        "instrument_background_unresolved"
    )


@pytest.mark.asyncio
async def test_okx_demo_registry_sync_does_not_fetch_sandbox_catalog(monkeypatch):
    """Registry sync consumes normalized listings without a mode-specific catalog read."""
    class FakeExchange:
        async def load_markets(self):
            self.markets = {
                "BTC/USDT": {
                    "id": "BTC-USDT",
                    "symbol": "BTC/USDT",
                    "base": "BTC",
                    "quote": "USDT",
                    "active": True,
                    "spot": True,
                    "info": {},
                },
            }

    captured = {}

    def fake_sync(assets, **_kwargs):
        captured["assets"] = assets
        return len(assets), 0, 0

    cfg = MagicMock()
    cfg.market_data.ccxt_exchange = "okx"
    cfg.market_data.ccxt_fallback = None
    cfg.exchange.paper_execution_mode = "okx_demo"
    cfg.exchange.enabled_markets = {"usdt_futures": False}
    cfg.universe.blacklist = []

    monkeypatch.setattr(
        "extensions.data.ccxt_provider.CCXTProvider._ensure_primary",
        lambda self: FakeExchange(),
    )
    monkeypatch.setattr(
        "extensions.data.ccxt_provider.CCXTProvider.get_public_market_capabilities",
        lambda *args, **kwargs: pytest.fail("mode-specific sandbox catalog must not be read"),
        raising=False,
    )
    monkeypatch.setattr("aletheia.universe.registry.repo.sync_universe_assets", fake_sync)

    assert await sync_registry(cfg) == 1
    assert [asset.symbol for asset in captured["assets"]] == ["BTC/USDT"]


def test_background_preserves_bitget_stock_token_boundary():
    from aletheia.data.symbols import build_instrument_background

    background = build_instrument_background(
        symbol="RNVDA/USDT",
        venue="bitget",
        market_type="spot",
        market_info={
            "symbol": "RNVDA/USDT",
            "base": "RNVDA",
            "quote": "USDT",
            "info": {"baseCoin": "rNVDA"},
        },
    )

    assert background["asset_class"] == "equity"
    assert background["reference_asset"] == "NVDA"
    assert background["status"] == "complete"


def _fake_features():
    """Simulate features that would come from collect_features."""
    return [
        {
            "asset_id": "a1", "symbol": "DOGE/USDT",
            "quote_volume_24h": 50_000_000, "spread_bps": 5.0,
            "data_freshness_sec": 120, "volume_1h": 3_000_000,
            "rsi_14": 62.0, "atr_pct": 4.5,
            "return_1h": 3.0, "return_4h": 5.5, "return_24h": 8.0,
            "funding_rate": 0.0003, "open_interest": 100_000_000,
            "oi_delta_1h": 5.0,
        },
        {
            "asset_id": "a2", "symbol": "PEPE/USDT",
            "quote_volume_24h": 30_000_000, "spread_bps": 8.0,
            "data_freshness_sec": 200, "volume_1h": 2_000_000,
            "rsi_14": 28.0, "atr_pct": 7.0,
            "return_1h": -4.0, "return_4h": -7.5, "return_24h": -12.0,
            "funding_rate": -0.01, "open_interest": 50_000_000,
            "oi_delta_1h": -8.0,
        },
        {
            "asset_id": "a3", "symbol": "SOL/USDT",
            "quote_volume_24h": 80_000_000, "spread_bps": 3.0,
            "data_freshness_sec": 60, "volume_1h": 5_000_000,
            "rsi_14": 55.0, "atr_pct": 3.0,
            "return_1h": 1.0, "return_4h": 2.0, "return_24h": 4.0,
            "funding_rate": 0.0001, "open_interest": 200_000_000,
            "oi_delta_1h": 2.0,
        },
        {
            "asset_id": "a4", "symbol": "SCAM/USDT",
            "quote_volume_24h": 20_000_000, "spread_bps": 4.0,
            "data_freshness_sec": 150, "volume_1h": 1_500_000,
            "rsi_14": 50.0, "atr_pct": 2.5,
            "return_1h": 0.5, "return_4h": 1.0, "return_24h": 2.0,
            "funding_rate": None, "open_interest": None,
            "oi_delta_1h": None,
        },
        {
            "asset_id": "a5", "symbol": "LOWVOL/USDT",
            "quote_volume_24h": 500_000, "spread_bps": 2.0,
            "data_freshness_sec": 100, "volume_1h": 50_000,
            "rsi_14": 50.0, "atr_pct": 1.0,
            "return_1h": 0.1, "return_4h": 0.2, "return_24h": 0.3,
            "funding_rate": None, "open_interest": None,
            "oi_delta_1h": None,
        },
    ]


def test_pipeline_all_blacklisted_returns_empty():
    cfg = _make_cfg()
    cfg.universe.blacklist = ["DOGE/USDT", "PEPE/USDT", "SOL/USDT", "SCAM/USDT", "LOWVOL/USDT"]
    features = _fake_features()
    filtered = apply_tradability_filters(features, cfg.universe)
    assert filtered == []


def test_pipeline_scorer_assigns_families():
    features = _fake_features()
    cfg = _make_cfg()
    filtered = apply_tradability_filters(features, cfg.universe)

    families_seen = set()
    for feat in filtered:
        result = run_all_scanners(feat)
        families_seen.add(result["scanner_family"])

    # At least 1 family assigned (may be all momentum if data skews that way)
    assert len(families_seen) >= 1
    assert all(f in _VALID_FAMILIES for f in families_seen)


def test_market_observer_universe_integration():
    """Verify market_observer correctly reads universe members."""
    cfg = _make_cfg()
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    mock_members = [
        {"symbol": "DOGE/USDT", "scanner_family": "momentum", "total_score": 75,
         "snapshot_id": "snap_1", "id": "m1", "execution_eligible": 1,
         "expires_at": future, "rank": 1},
        {"symbol": "SOL/USDT", "scanner_family": "volatility", "total_score": 60,
         "snapshot_id": "snap_1", "id": "m2", "execution_eligible": 1,
         "expires_at": future, "rank": 2},
    ]
    snap = {"id": "snap_1", "status": "active"}

    svc = UniverseService()
    with patch("aletheia.universe.service.load_config", return_value=cfg):
        with patch("aletheia.universe.service.repo.get_active_universe_snapshot", return_value=snap):
            with patch("aletheia.universe.service.repo.list_universe_members", return_value=mock_members):
                members = svc.get_current_universe(limit=5)

    assert len(members) == 2
    # Market observer would consume these as symbol + metadata
    for m in members:
        assert "symbol" in m
        assert "scanner_family" in m
        assert "snapshot_id" in m


def test_opportunity_metadata_fields_exist():
    """Verify Opportunity model accepts universe fields."""
    from aletheia.storage.models import Opportunity
    opp = Opportunity(
        symbol="DOGE/USDT",
        market="spot",
        direction_bias="long",
        thesis="test",
        confidence="medium",
        source_agent="market_observer",
        universe_snapshot_id="snap_123",
        universe_member_id="m_456",
        scanner_family="momentum",
    )
    assert opp.universe_snapshot_id == "snap_123"
    assert opp.universe_member_id == "m_456"
    assert opp.scanner_family == "momentum"

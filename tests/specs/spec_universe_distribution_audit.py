"""Spec: post-fix Universe distribution audit.

Locks the integrated 2026-07-03 Universe fixes as one refresh-level behavior:
family quota, preferred market dedupe, regime propagation, and derivatives
context all survive a real UniverseService.refresh() write path.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(
    SpecClaim(
        "UP-DIST-01",
        "universe_pipeline",
        "§post-fix distribution audit",
        "L2b",
        "A full UniverseService.refresh() on an isolated DB produces a diversified "
        "member pool, keeps preferred perp over higher-scoring spot duplicates, "
        "persists regime_tag, and emits derivatives context into opportunities.",
        status="implemented",
    ),
)
def test_universe_refresh_post_fix_distribution(tmp_path, monkeypatch):
    import asyncio
    import sqlite3

    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    from aletheia.config.schema import AletheiaConfig
    from aletheia.storage.migrations import run_migrations
    from aletheia.storage.models import UniverseAsset
    from aletheia.storage import repositories as repo
    from aletheia.universe.service import UniverseService

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "app.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    sq._db_conn = conn
    run_migrations()

    cfg = AletheiaConfig()
    cfg.universe.enabled = True
    cfg.universe.max_candidates = 10
    cfg.universe.explore_ratio = 0.0
    cfg.regime.enabled = True
    cfg.regime.short_enabled = True
    cfg.market_data.ccxt_exchange = "okx"
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "auto"

    monkeypatch.setattr("aletheia.universe.service.load_config", lambda: cfg)

    async def fake_sync_registry(_cfg):
        assets = [
            UniverseAsset(venue="okx", market_type="spot", symbol="BTC/USDT"),
            UniverseAsset(venue="okx", market_type="perp", symbol="BTC/USDT"),
            UniverseAsset(venue="okx", market_type="spot", symbol="ETH/USDT"),
            UniverseAsset(venue="okx", market_type="perp", symbol="SOL/USDT"),
            UniverseAsset(venue="okx", market_type="perp", symbol="XRP/USDT"),
            UniverseAsset(venue="okx", market_type="perp", symbol="DOGE/USDT"),
            UniverseAsset(venue="okx", market_type="perp", symbol="ADA/USDT"),
        ]
        for asset in assets:
            repo.upsert_universe_asset(asset)
        return len(assets)

    def _feature(asset: dict, family: str, opp_score: float, direction: str) -> dict:
        return {
            "asset_id": asset["id"],
            "symbol": asset["symbol"],
            "market_type": asset["market_type"],
            "quote_volume_24h": 100_000_000.0,
            "spread_bps": 2.0,
            "data_freshness_sec": 60,
            "last_price": 100.0,
            "synthetic_family": family,
            "synthetic_score": opp_score,
            "synthetic_direction": direction,
            "funding_rate": 0.0007 if asset["market_type"] == "perp" else None,
            "open_interest": 12_500_000.0 if asset["market_type"] == "perp" else None,
            "oi_delta_1h": 8.5 if asset["market_type"] == "perp" else None,
        }

    async def fake_collect_features(assets, _cfg):
        by_key = {
            ("BTC/USDT", "spot"): ("momentum", 100.0, "long"),
            # Lower score than spot, but auto preference must keep this perp row.
            ("BTC/USDT", "perp"): ("momentum", 65.0, "long"),
            ("ETH/USDT", "spot"): ("mean_reversion", 95.0, "short"),
            ("SOL/USDT", "perp"): ("volatility", 95.0, "long"),
            ("XRP/USDT", "perp"): ("derivatives", 95.0, "long"),
            ("DOGE/USDT", "perp"): ("event", 95.0, "long"),
            ("ADA/USDT", "perp"): ("short_bias", 95.0, "short"),
        }
        return [
            _feature(a, *by_key[(a["symbol"], a["market_type"])])
            for a in assets
            if (a["symbol"], a["market_type"]) in by_key
        ]

    def fake_run_all_scanners(feat):
        family = feat["synthetic_family"]
        direction = feat["synthetic_direction"]
        score = feat["synthetic_score"]
        return {
            "scanner_family": family,
            "opportunity_score": score,
            "reasons": [f"synthetic {family}"],
            "scanner_direction": direction,
            "all_scores": {
                "momentum": score if family == "momentum" else 0.0,
                "mean_reversion": score if family == "mean_reversion" else 0.0,
                "volatility": score if family == "volatility" else 0.0,
                "derivatives": score if family == "derivatives" else 0.0,
                "event": score if family == "event" else 0.0,
                "short_bias": score if family == "short_bias" else 0.0,
            },
        }

    async def fake_regime_inputs():
        return {"dummy": True}

    def fake_detect(**_kwargs):
        return SimpleNamespace(
            state="sideways",
            confidence=0.9,
            long_bias=0.8,
            short_bias=0.8,
            signals={},
            detected_at="2026-01-01T00:00:00+00:00",
        )

    monkeypatch.setattr("aletheia.universe.registry.sync_registry", fake_sync_registry)
    monkeypatch.setattr("aletheia.universe.features.collect_features", fake_collect_features)
    monkeypatch.setattr("aletheia.universe.scanners.run_all_scanners", fake_run_all_scanners)
    monkeypatch.setattr("aletheia.regime.collector.collect_regime_inputs", fake_regime_inputs)
    monkeypatch.setattr("aletheia.regime.detector.debounced_detect", fake_detect)
    monkeypatch.setattr("aletheia.universe.outcome_recorder.settle_due", lambda: None)
    monkeypatch.setattr("aletheia.universe.outcome_recorder.register_pending", lambda *a, **k: None)
    monkeypatch.setattr(
        "aletheia.universe.signal_emitter.lookup_factor_signal",
        lambda _ctx: {"status": "watch"},
    )

    snapshot_id = asyncio.run(UniverseService().refresh())
    assert snapshot_id is not None

    members = repo.list_universe_members(snapshot_id, limit=50)
    assert len(members) == 6, f"7 raw instruments should dedupe to 6 members, got {len(members)}"

    btc_members = [m for m in members if m["symbol"] == "BTC/USDT"]
    assert len(btc_members) == 1, f"same-symbol spot/perp should dedupe: {btc_members}"
    assert btc_members[0]["market_type"] == "perp", (
        "preferred_market_type=auto should keep perp even when spot has a higher score"
    )

    family_counts: dict[str, int] = {}
    for member in members:
        family_counts[member["scanner_family"]] = family_counts.get(member["scanner_family"], 0) + 1
    assert family_counts == {
        "momentum": 1,
        "mean_reversion": 1,
        "volatility": 1,
        "derivatives": 1,
        "event": 1,
        "short_bias": 1,
    }, family_counts
    assert all(m["regime_tag"] == "sideways" for m in members), (
        f"regime_tag not propagated to every member: {members}"
    )

    opps = repo.list_opportunities(limit=50)
    assert len(opps) == 6, f"each selected member should emit one opportunity, got {len(opps)}"
    perp_opps = [o for o in opps if o["market"] == "usdt_futures"]
    assert perp_opps, f"expected perp opportunities, got {opps}"
    assert any(
        o["funding_rate"] is not None
        and o["open_interest"] is not None
        and o["oi_delta_1h"] is not None
        for o in perp_opps
    ), f"perp opportunities lost derivatives context: {perp_opps}"

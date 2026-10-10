"""Spec: Cross-Market-Type Opportunity Deduplication.

The original 2026-07-02 planning note was not retained. This executable spec is
the durable regression evidence for the behavior.

When ``exchange.enabled_markets`` has both spot and usdt_futures enabled, the
legacy path in ``observed_instruments_from_config`` must NOT emit two instruments
per canonical symbol (two instruments per symbol mean duplicate directional
analysis of the same asset, e.g. redundant 76%-vs-74% spot-vs-perp reads).

Design principle (first-principles, confirmed across Hummingbot + 12 academic
papers): directional analysis is instrument-agnostic. The same ETH reads ~76%
long whether the instrument is spot or perp. Separating by market_type is only
valid for funding-rate / basis arbitrage, which is an independent strategy
(FundingRateHarvester), not the directional observer pipeline.

Reuses ``data_layer_contract`` — no new trust boundary. Behavioral claims are
offlined (don't need LLM).
"""

from tests.helpers.repo_test_helpers import find_opportunity_by_symbol_snapshot

import inspect

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# OD-S01 — L2b Behavioral: legacy path emits ≤1 instrument per symbol
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("OD-S01", "data_layer_contract", "§3 legacy entry dedup", "L2b",
              "observed_instruments_from_config emits AT MOST one instrument per "
              "canonical symbol when both spot and usdt_futures are enabled and "
              "preferred_market_type is not 'spot'"),
)
def test_legacy_path_single_instrument_per_symbol():
    from aletheia.trading.instruments import observed_instruments_from_config
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.market_data.observed_symbols = ["BTC/USDT", "ETH/USDT"]
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "auto"

    insts = observed_instruments_from_config(cfg)
    by_symbol: dict[str, list[str]] = {}
    for i in insts:
        by_symbol.setdefault(i.symbol, []).append(i.market_type)

    assert len(insts) == 2, f"expected 2 instruments (one per symbol), got {len(insts)}"
    for sym, mts in by_symbol.items():
        assert len(mts) == 1, f"{sym} emitted {len(mts)} instruments: {mts}"
        assert mts[0] == "perp", (
            f"{sym} got market_type={mts[0]}; expected perp (auto bias)"
        )


@spec_claim(
    SpecClaim("OD-S01b", "data_layer_contract", "§3 perp-only legacy path", "L2b",
              "USDT perpetuals are the only product: even with preferred_market_type='spot' "
              "observed_instruments_from_config emits perp, never spot"),
)
def test_legacy_path_is_perp_only():
    from aletheia.trading.instruments import observed_instruments_from_config
    from aletheia.config.schema import AletheiaConfig

    cfg = AletheiaConfig()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "spot"

    insts = observed_instruments_from_config(cfg)
    assert len(insts) == 1
    assert insts[0].market_type == "perp"


# ---------------------------------------------------------------------------
# OD-S02 — L2a Structural: repo function accepts market kwarg
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim("OD-S02", "data_layer_contract", "§3 DB dedup filter", "L2a",
              "find_open_opportunity_by_symbol_snapshot accepts an optional market "
              "kwarg; when provided, the query also filters on opportunities.market "
              "so a same-symbol opp from a different market (spot vs perp) does not "
              "collide. market=None preserves prior behavior."),
)
def test_repo_accepts_market_kwarg():
    from tests.helpers.repo_test_helpers import find_open_opportunity_by_symbol_snapshot

    sig = inspect.signature(find_open_opportunity_by_symbol_snapshot)
    params = sig.parameters
    assert "market" in params, "must accept market kwarg"
    # Default None = existing callers work unchanged.
    assert params["market"].default is None, "market must default to None"


@spec_claim(
    SpecClaim(
        "OD-S02e", "data_layer_contract", "§3 lifecycle identity dedup", "L2b",
        "Opportunity identity lookup for one universe snapshot includes reviewed rows, so a "
        "later observer pass cannot create a second opportunity or rewrite terminal history.",
        status="implemented",
    ),
)
def test_lifecycle_lookup_reuses_reviewed_opportunity(tmp_path, monkeypatch):
    from tests.test_entry_logic import setup_env

    setup_env(tmp_path, monkeypatch)
    from aletheia.storage.models import Opportunity
    from aletheia.storage import repositories

    original = Opportunity(
        symbol="WLD/USDT",
        market="usdt_futures",
        direction_bias="long",
        confidence="medium",
        status="reviewed",
        universe_snapshot_id="snapshot-reviewed",
        universe_member_id="member-reviewed",
        scanner_family="momentum",
    )
    repositories.insert_opportunity(original)
    duplicate = Opportunity(
        symbol=original.symbol,
        market=original.market,
        direction_bias="long",
        confidence="medium",
        status="open",
        universe_snapshot_id=original.universe_snapshot_id,
        universe_member_id=original.universe_member_id,
        scanner_family=original.scanner_family,
    )
    repositories.insert_opportunity(duplicate)

    found = find_opportunity_by_symbol_snapshot(
        original.symbol,
        original.universe_snapshot_id,
        market=original.market,
    )

    assert found is not None
    assert found["id"] == original.id
    assert found["status"] == "reviewed"




@spec_claim(
    SpecClaim("OD-S02d", "data_layer_contract", "§3 universe preferred market", "L2b",
              "UniverseService's same-symbol member dedup keeps the configured "
              "preferred market instrument (auto→perp, spot→spot) instead of "
              "letting the highest-scoring spot row crowd out perp rows"),
)
def test_universe_member_dedup_respects_preferred_market():
    from aletheia.config.schema import AletheiaConfig
    from aletheia.universe.service import _dedupe_by_symbol_preferred_market

    allocated = [
        {"symbol": "BTC/USDT", "market_type": "spot", "total_score": 90.0},
        {"symbol": "BTC/USDT", "market_type": "perp", "total_score": 50.0},
        {"symbol": "ETH/USDT", "market_type": "spot", "total_score": 60.0},
    ]

    cfg = AletheiaConfig()
    cfg.exchange.enabled_markets = {"spot": True, "usdt_futures": True}
    cfg.exchange.preferred_market_type = "auto"

    deduped = _dedupe_by_symbol_preferred_market([dict(x) for x in allocated], cfg)
    by_symbol = {x["symbol"]: x for x in deduped}
    assert by_symbol["BTC/USDT"]["market_type"] == "perp", (
        "auto preference should keep perp even when the spot duplicate has a higher score"
    )

    cfg.exchange.preferred_market_type = "spot"
    deduped = _dedupe_by_symbol_preferred_market([dict(x) for x in allocated], cfg)
    by_symbol = {x["symbol"]: x for x in deduped}
    assert by_symbol["BTC/USDT"]["market_type"] == "spot"

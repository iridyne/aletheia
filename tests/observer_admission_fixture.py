"""Minimal deterministic universe-opportunity admission fixture."""

import json
from datetime import datetime, timedelta, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    Opportunity,
    OpportunityGenerationV2,
    OpportunityInstrumentIdentity,
)


def crypto_background(
    symbol: str = "BTC/USDT",
    market_type: str = "spot",
    venue: str = "fixture",
) -> dict:
    return {
        "version": "instrument-background/v1",
        "status": "complete",
        "venue": venue,
        "symbol": symbol,
        "market_type": market_type,
        "physical_market_type": market_type,
        "asset_class": "crypto",
        "product_kind": (
            "crypto_perpetual" if market_type == "perp" else "crypto_spot"
        ),
        "reference_asset": symbol.split("/")[0],
        "relationship": (
            "tracks_reference_price" if market_type == "perp" else "direct_asset"
        ),
        "classification": {"source": "test_fixture"},
    }


def insert_universe_opportunity_generation(
    opportunity: Opportunity,
    *,
    venue: str = "fixture",
    market_type: str = "spot",
    signal_mark: float = 100.0,
) -> Opportunity:
    detected_at = datetime.now(timezone.utc)
    valid_until = (detected_at + timedelta(hours=1)).isoformat()
    stored = opportunity.model_copy(
        update={
            "valid_until": opportunity.valid_until or valid_until,
            "entry_zone": json.dumps(
                [signal_mark, signal_mark],
                separators=(",", ":"),
            ),
            "signal_mark": str(signal_mark),
            "scanner_family": opportunity.scanner_family or "momentum",
            "setup_id": opportunity.setup_id or f"test-setup-{opportunity.id}",
        }
    )
    generation = OpportunityGenerationV2(
        instrument_identity=OpportunityInstrumentIdentity(
            venue=venue,
            symbol=stored.symbol,
            market_type=market_type,
        ),
        family=stored.scanner_family or "momentum",
        archetype="continuation",
        direction=(
            stored.direction_bias
            if stored.direction_bias in {"long", "short"}
            else "long"
        ),
        detected_at=detected_at.isoformat(),
        signal_mark=signal_mark,
        motive_evidence={"source": "test_fixture"},
        entry_trigger={"type": "test_fixture"},
        entry_zone=(signal_mark, signal_mark),
        invalidation={"type": "test_fixture"},
        horizon_hours=4.0,
        cost_snapshot={
            "cost_model_version": "test-fixture",
            "spread_bps": 1.0,
            "estimated_slippage_bps": 1.0,
            "funding": {"status": "not_applicable"},
            "depth_10bps_usd": 1_000_000.0,
            "target_notional_usd": None,
            "capacity_ratio": None,
        },
        material_fingerprint="a" * 64,
        valid_until=stored.valid_until or valid_until,
    )
    return repo.insert_opportunity_generation(stored, generation)

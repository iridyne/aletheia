"""Minimal deterministic admission fixture for Market Observer tests."""

import json

from aletheia.analytics.filter_evidence import record_filter_decision
from aletheia.config.loader import load_config
from aletheia.data.symbols import InstrumentId, instrument_analysis_context
from aletheia.storage import repositories as repo
from aletheia.storage.models import Opportunity
from aletheia.trading.instruments import instrument_market_to_db_market


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


def admit_observer_instrument(
    monkeypatch,
    observer,
    *,
    symbol: str = "BTC/USDT",
    market_type: str = "spot",
) -> Opportunity:
    """Seed one v2 emitter-attested owner and route Observer to it."""
    snapshot_id = "observer-test-snapshot"
    member_id = f"observer-test-member-{market_type}"
    opportunity = Opportunity(
        id=f"observer-test-opportunity-{market_type}",
        symbol=symbol,
        market=instrument_market_to_db_market(market_type),
        direction_bias="long",
        confidence="medium",
        confidence_score=60,
        source_agent="universe_scanner",
        universe_snapshot_id=snapshot_id,
        universe_member_id=member_id,
        scanner_family="momentum",
        metadata=json.dumps({
            "instrument_background": (
                background := crypto_background(symbol, market_type)
            ),
            "analysis_context": instrument_analysis_context(background),
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v4",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": snapshot_id,
                    "universe_member_id": member_id,
                    "symbol": symbol,
                    "market_type": market_type,
                    "venue": "fixture",
                    "direction": "long",
                },
            },
        }),
    )
    repo.insert_opportunity(opportunity)
    record_filter_decision(
        candidate_key=f"universe_member:{member_id}",
        candidate_kind="universe_member",
        filter_name="opportunity_emission",
        stage="signal_emitter",
        decision="passed",
        reason_code="opportunity_emitted",
        symbol=symbol,
        market_type=market_type,
        venue="fixture",
        direction="long",
        opportunity_id=opportunity.id,
        universe_snapshot_id=snapshot_id,
        universe_member_id=member_id,
    )
    cfg = load_config()
    instrument = InstrumentId.from_parts(
        cfg.market_data.ccxt_exchange,
        symbol,
        market_type,
    )
    member = {
        "id": member_id,
        "scanner_family": "momentum",
        "scanner_direction": "long",
    }
    monkeypatch.setattr(
        observer,
        "_admitted_observer_workset",
        lambda _cfg: ([instrument], {str(instrument): member}, snapshot_id, []),
    )
    return opportunity

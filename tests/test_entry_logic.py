"""Structured-model failure behavior plus the universe-admission seed helper.

`llm_structured` must degrade gracefully (return an error, never raise) when no
model provider is configured. `_seed_admitted_opportunity` builds one
emitter-attested universe opportunity for tests that need an admitted owner.
"""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import (
    Opportunity,
    UniverseAsset,
    UniverseMember,
    UniverseSnapshot,
)
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from tests.observer_admission_fixture import (
    crypto_background,
    insert_universe_opportunity_generation,
)


def setup_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sq
    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    for sub in ("logs", "knowledge/research_notes"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    reset_db()
    sq._db_conn = None
    conn = sqlite3.connect(str(tmp_path / "test.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    sq._db_conn = conn
    run_migrations()
    init_tool_registry()


def _seed_admitted_opportunity(
    *,
    symbol: str = "BTC/USDT",
    market_type: str = "spot",
    direction: str = "long",
    confidence: str = "high",
) -> Opportunity:
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.config.loader import load_config, save_config

    cfg = load_config()
    cfg.universe.enabled = True
    cfg.exchange.enabled_markets = {
        "spot": market_type == "spot",
        "usdt_futures": market_type == "perp",
    }
    cfg.exchange.preferred_market_type = market_type
    save_config(cfg)

    snapshot_id = "entry-logic-universe"
    member_id = f"entry-logic-member-{market_type}"
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    repo.upsert_universe_asset(UniverseAsset(
        id=f"entry-logic-asset-{market_type}",
        venue=cfg.market_data.ccxt_exchange,
        market_type=market_type,
        symbol=symbol,
    ))
    repo._insert("universe_snapshots", UniverseSnapshot(
        id=snapshot_id,
        market_type="crypto",
        status="active",
        expires_at=expires_at,
    ).model_dump())
    repo._insert("universe_members", UniverseMember(
        id=member_id,
        snapshot_id=snapshot_id,
        asset_id=f"entry-logic-asset-{market_type}",
        symbol=symbol,
        market_type=market_type,
        rank=1,
        total_score=60.0,
        scanner_family="momentum",
        scanner_direction=direction,
        expires_at=expires_at,
        entry_price_usd=60_000.0,
    ).model_dump())
    from aletheia.data.symbols import instrument_analysis_context
    background = crypto_background(
        symbol, market_type, cfg.market_data.ccxt_exchange
    )
    opportunity = Opportunity(
        id=f"entry-logic-opportunity-{market_type}",
        symbol=symbol,
        market="spot" if market_type == "spot" else "usdt_futures",
        direction_bias=direction,
        confidence=confidence,
        confidence_score=60,
        source_agent="universe_scanner",
        universe_snapshot_id=snapshot_id,
        universe_member_id=member_id,
        scanner_family="momentum",
        valid_until=expires_at,
        metadata=json.dumps({
            "instrument_background": background,
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
                    "venue": cfg.market_data.ccxt_exchange,
                    "direction": direction,
                },
            },
        }),
    )
    insert_universe_opportunity_generation(
        opportunity,
        venue=cfg.market_data.ccxt_exchange,
        market_type=market_type,
        signal_mark=60_000.0,
    )
    record_filter_decision(
        candidate_key=f"universe_member:{member_id}",
        candidate_kind="universe_member",
        filter_name="opportunity_emission",
        stage="signal_emitter",
        decision="passed",
        reason_code="opportunity_emitted",
        symbol=symbol,
        market_type=market_type,
        venue=cfg.market_data.ccxt_exchange,
        direction=direction,
        opportunity_id=opportunity.id,
        universe_snapshot_id=snapshot_id,
        universe_member_id=member_id,
    )
    return opportunity


# --- llm_structured graceful degradation ---

async def test_llm_structured_degrades_gracefully(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.agents.helpers import llm_structured
    from pydantic import BaseModel

    class Schema(BaseModel):
        direction_bias: str = "neutral"

    # No model provider configured → returns (None, error), never raises.
    data, err = await llm_structured(system="x", user="y", schema=Schema)
    assert data is None
    assert err is not None

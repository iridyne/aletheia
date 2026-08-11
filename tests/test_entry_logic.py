"""Tests for the entry-logic upgrade (Component 2).

Goal: trades stop being noise. The deterministic emitter owns Opportunity
admission; market_observer adds structured advisory context; paper_trader skips
neutral and low-confidence signals while preserving lifecycle evidence.
"""

from datetime import datetime, timedelta, timezone
import json
import sqlite3

from aletheia.storage.sqlite import reset_db
from aletheia.storage.migrations import run_migrations
from aletheia.storage.models import (
    MarketSnapshot,
    Opportunity,
    UniverseAsset,
    UniverseMember,
    UniverseSnapshot,
)
from aletheia.storage import repositories as repo
from aletheia.agent_runtime.tool_registry import init_tool_registry
from aletheia.agent_runtime.orchestrator import run_agent


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


def _set_paperauto(monkeypatch):
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    # Local paper_internal fallback path (default is now bitget_demo / real exchange).
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)


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
    repo.insert_universe_snapshot(UniverseSnapshot(
        id=snapshot_id,
        market_type="crypto",
        status="active",
        expires_at=expires_at,
    ))
    repo.insert_universe_member(UniverseMember(
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
    ))
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
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v2",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": snapshot_id,
                    "universe_member_id": member_id,
                    "symbol": symbol,
                    "market_type": market_type,
                    "direction": direction,
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


# --- market_observer enriches an admitted deterministic owner ---

async def test_observer_writes_structured_direction(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    owner = _seed_admitted_opportunity()

    structured_kwargs = {}

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        structured_kwargs.update(kw)
        return {
            "direction_bias": "long",
            "confidence": "high",
            "confidence_score": 88,
            "rationale": "uptrend",
        }, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)
    result = await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 1
    assert opps[0]["id"] == owner.id
    assert opps[0]["source_agent"] == "universe_scanner"
    assert opps[0]["direction_bias"] == "long"
    assert opps[0]["confidence"] == "high"
    assert opps[0]["symbol"] == "BTC/USDT"  # stored in canonical format
    assessment = json.loads(opps[0]["metadata"])["observer_assessment"]
    assert assessment["direction_bias"] == "long"
    assert assessment["confidence_score"] == 88
    assert result["errors"] == []
    assert structured_kwargs["reasoning_effort"] == "none"


async def test_observer_skips_opportunity_on_llm_failure(tmp_path, monkeypatch):
    # Without an admitted deterministic owner, Observer must stop before LLM.
    setup_env(tmp_path, monkeypatch)
    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.market_data.observed_symbols = ["BTC/USDT"]
    cfg.market_data.observed_xstocks = []
    save_config(cfg)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    async def fake_structured(*_args, **_kwargs):
        raise AssertionError("Observer must not call the LLM without an admitted owner")

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)
    result = await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert opps == []
    assert result["errors"] == []


# --- paper_trader honors direction + confidence; records non-traded signals ---

async def test_paper_trader_skips_neutral_but_keeps_signal(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_paperauto(monkeypatch)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="neutral",
                      confidence="high", source_agent="market_observer")
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")
    assert len(repo.list_trade_proposals()) == 0  # neutral → no trade
    # signal preserved (not deleted); marked as reviewed
    row = repo.get_opportunity(opp.id)
    assert row is not None
    assert row["status"] != "open"


async def test_paper_trader_skips_low_confidence(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    _set_paperauto(monkeypatch)  # min_confidence_to_trade default "medium"
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      confidence="speculative", source_agent="market_observer")
    repo.insert_opportunity(opp)

    await run_agent("paper_trader", trigger_type="manual")
    assert len(repo.list_trade_proposals()) == 0  # speculative < medium → skip
    assert repo.get_opportunity(opp.id)["status"] != "open"  # still recorded


async def test_paper_trader_trades_directional_and_sets_stop(tmp_path, monkeypatch):
    """Phase 4a: Paper trader Path B now writes signal_pending entries for System Operator."""
    setup_env(tmp_path, monkeypatch)
    _set_paperauto(monkeypatch)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))
    opp = Opportunity(symbol="BTC/USDT", market="spot", direction_bias="long",
                      confidence="medium", source_agent="divergence_scanner")
    repo.insert_opportunity(opp)

    result = await run_agent("paper_trader", trigger_type="manual")
    # Phase 4a: Path B writes signal_pending instead of creating proposals directly
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=5)
    pending = [d for d in decisions if d.get("action") == "signal_pending"]
    assert len(pending) >= 1
    assert pending[0]["symbol"] == "BTC/USDT"


async def test_full_observer_to_paper_trader_loop(tmp_path, monkeypatch):
    """Emitter owner → Observer enrichment → Ranker pending preserves identity."""
    setup_env(tmp_path, monkeypatch)
    repo.insert_market_snapshot(MarketSnapshot(symbol="BTC/USDT", market_type="spot", price=60000))

    from aletheia.config.loader import load_config, save_config
    cfg = load_config()
    cfg.agent.permission_mode = "Paper"
    cfg.exchange.paper_execution_mode = "paper_internal"
    save_config(cfg)
    owner = _seed_admitted_opportunity()

    async def fake_structured(system, user, *, schema, max_tokens=400, **kw):
        return {"direction_bias": "long", "confidence": "high", "rationale": "uptrend"}, None

    monkeypatch.setattr("aletheia.agents.market_observer.llm_structured", fake_structured)
    monkeypatch.setattr("aletheia.agents.market_observer._has_sufficient_signal", lambda ctx: True)

    # Step 1: Observer enriches, but does not create or relabel the owner.
    await run_agent("market_observer", trigger_type="manual")
    opps = repo.list_opportunities()
    assert len(opps) == 1
    assert opps[0]["id"] == owner.id
    assert opps[0]["source_agent"] == "universe_scanner"
    assert opps[0]["symbol"] == "BTC/USDT"

    # Step 2: paper_trader writes signal_pending for System Operator (Phase 4a)
    await run_agent("paper_trader", trigger_type="manual")
    from aletheia.storage.repositories import list_pi_decisions
    decisions = list_pi_decisions(limit=5)
    pending = [d for d in decisions if d.get("action") == "signal_pending"]
    assert len(pending) >= 1
    assert pending[0]["symbol"] == "BTC/USDT"

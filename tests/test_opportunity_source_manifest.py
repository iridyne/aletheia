import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aletheia.data.symbols import instrument_analysis_context
from tests.observer_admission_fixture import (
    crypto_background,
    insert_universe_opportunity_generation,
)


def _prepare_db(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    import aletheia.storage.sqlite as sqlite
    from aletheia.storage.migrations import run_migrations

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite.reset_db()
    run_migrations()




@pytest.mark.asyncio
async def test_observer_does_not_analyze_or_emit_without_deterministic_owner(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from extensions.research import market_observer as observer
    from aletheia.storage.repositories import list_opportunities

    init_tool_registry()

    async def unexpected_llm(*_args, **_kwargs):
        raise AssertionError("Observer must reject before LLM analysis")

    monkeypatch.setattr(observer, "llm_structured", unexpected_llm)
    monkeypatch.setattr(observer, "record_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(observer, "log_tool", lambda *_args, **_kwargs: None)
    prefetched = observer._SymbolPrefetch(
        context_str="LOW/USDT technical context",
        debug={},
        pre_filter={
            "passed": True,
            "rsi": 75.0,
            "atr_pct": 5.0,
            "keyword_hits": [],
            "matched_rules": ["rsi_deviation"],
        },
        signal_score=1.0,
        funding_rate_val=None,
        open_interest_val=None,
        oi_delta_val=None,
    )

    opportunity_id, error = await observer._analyze_symbol(
        symbol="LOW/USDT",
        market_type="perp",
        permission_mode="Observe",
        run_id="observer-no-owner",
        linked_strategy_id=None,
        memory_block="",
        universe_snapshot_id="snapshot-low",
        universe_member_id="member-low",
        scanner_family="momentum",
        scanner_direction="long",
        force_full=True,
        _prefetched=prefetched,
    )

    assert opportunity_id is None
    assert error is None
    assert list_opportunities(limit=10) == []






















@pytest.mark.asyncio
async def test_observer_t1_names_only_sources_rendered_for_the_exact_instrument(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from extensions.research import market_observer as observer
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO macro_snapshots (id, risk_regime, observed_at) "
        "VALUES ('macro-rendered', 'neutral', ?)",
        (now,),
    )
    db.commit()
    cache = observer._DbCache(
        perp_structures={
            "BTC/USDT": {
                "id": "position-x-rendered",
                "symbol": "BTC/USDT",
                "source": "owner_aggregate",
                "as_of_time": now,
                "observed_at": now,
                "data_quality": "complete",
            }
        },
        funding_hl={
            "BTC/USDT": {
                "id": "funding-rendered",
                "exchange": "hyperliquid",
                "funding_rate_annualized": 0.2,
                "observed_at": now,
            }
        },
        funding_hl_pred={},
        open_interest_hl={
            "BTC/USDT": {
                "id": "oi-rendered",
                "exchange": "hyperliquid",
                "open_interest_usd": 1_000_000,
                "observed_at": now,
            }
        },
        news={
            "BTC/USDT": [{
                "id": "news-rendered",
                "title": "Bitcoin demand rises",
                "source": "fixture",
                "published_at": now,
            }]
        },
        onchain_rows=[{
            "id": "onchain-rendered",
            "source": "defillama",
            "metric_type": "chain_tvl",
            "total_usd": 1_000_000,
            "observed_at": now,
        }],
        options_structures={
            "BTC": {
                "id": "options-rendered",
                "symbol": "BTC",
                "source": "deribit_public",
                "gex_convention": "v1_retail_call_minus_put",
                "gamma_regime": "positive",
                "net_gex_estimate": 1_000_000.0,
                "observed_at": now,
            }
        },
    )

    async def technical_context(*_args, **_kwargs):
        return "technical", {
            "symbols": {"BTC/USDT": {"1h": {"rsi_14": 70.0, "atr_pct": 1.0}}}
        }

    snapshot = SimpleNamespace(
        tradability={}, leverage={}, macro={"risk_regime": "neutral", "observed_at": now}
    )
    with patch(
        "aletheia.trading.market_context.build_market_context",
        side_effect=technical_context,
    ), patch(
        "aletheia.data.context_builder.build_market_context_from_db",
        return_value=snapshot,
    ), patch.object(
        observer,
        "_prediction_market_signal_context",
        return_value=(["- prediction"], [{"id": "prediction-rendered", "updated_at": now}]),
    ):
        prefetched = await observer._prefetch_symbol(
            SimpleNamespace(symbol="BTC/USDT", market_type="perp"),
            venue="okx",
            provider=None,
            prediction_context_enabled=True,
            prediction_context_cutoff="2026-07-18T00:00:00Z",
            db_cache=cache,
        )

    source_ids = {
        kind: [source["id"] for source in sources]
        for kind, sources in prefetched.rendered_sources.items()
    }
    assert source_ids == {
        "news": ["news-rendered"],
        "macro": ["macro-rendered"],
        "onchain": ["onchain-rendered"],
        "prediction": ["prediction-rendered"],
        "hyperliquid": ["funding-rendered", "oi-rendered"],
        "position_x": ["position-x-rendered"],
        "options_structure": ["options-rendered"],
        "technical_context": [],
    }

    manifest = observer._opportunity_evidence_manifest(
        metadata={
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v1",
                "t0": {"status": "available", "feature_snapshot_id": "feature-t0"},
            }
        },
        rendered_sources=prefetched.rendered_sources,
        run_id="observer-run",
        universe_snapshot_id="snapshot-1",
        universe_member_id="member-1",
        scanner_family="momentum",
        pre_filter=prefetched.pre_filter,
        pre_filter_evidence_id="filter-t0",
    )
    assert manifest["policy_epoch"] == "opportunity_information_integrity/v1"
    assert manifest["t0"]["feature_snapshot_id"] == "feature-t0"
    assert manifest["t1"]["sources"] == prefetched.rendered_sources
    assert manifest["t1"]["technical_context"] == {"status": "unavailable"}










@pytest.mark.asyncio
async def test_observer_enrichment_failure_does_not_claim_unrendered_source_ids(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from extensions.research import market_observer as observer
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    db = get_db()
    db.execute(
        "INSERT INTO macro_snapshots (id, risk_regime, observed_at) "
        "VALUES ('macro-never-rendered', 'neutral', ?)",
        (now,),
    )
    db.commit()
    cache = observer._DbCache(
        perp_structures={},
        funding_hl={
            "BTC/USDT": {
                "id": "funding-never-rendered",
                "exchange": "hyperliquid",
                "funding_rate_annualized": 0.2,
                "observed_at": now,
            }
        },
        funding_hl_pred={},
        news={},
        onchain_rows=[{
            "id": "onchain-never-rendered",
            "source": "defillama",
            "metric_type": "chain_tvl",
            "total_usd": 1_000_000,
            "observed_at": now,
        }],
    )

    async def technical_context(*_args, **_kwargs):
        return "technical", {
            "symbols": {"BTC/USDT": {"1h": {"rsi_14": 70.0, "atr_pct": 1.0}}}
        }

    snapshot = SimpleNamespace(
        tradability={}, leverage={}, macro={"risk_regime": "neutral", "observed_at": now}
    )
    with patch(
        "aletheia.trading.market_context.build_market_context",
        side_effect=technical_context,
    ), patch(
        "aletheia.data.context_builder.build_market_context_from_db",
        return_value=snapshot,
    ), patch(
        "aletheia.data.context_builder.render_enriched_context_md",
        side_effect=ValueError("render failed"),
    ):
        prefetched = await observer._prefetch_symbol(
            SimpleNamespace(symbol="BTC/USDT", market_type="perp"),
            venue="okx",
            provider=None,
            prediction_context_enabled=False,
            prediction_context_cutoff=None,
            db_cache=cache,
        )

    assert prefetched.context_str == "technical"
    assert prefetched.rendered_sources == observer._empty_rendered_sources()


@pytest.mark.asyncio
async def test_observer_cannot_rewrite_scanner_direction_or_score_semantics(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from extensions.research import market_observer as observer
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity
    from aletheia.analytics.filter_evidence import record_filter_decision

    init_tool_registry()
    original = Opportunity(
        id="scanner-owned-opportunity",
        symbol="SLX/USDT",
        market="usdt_futures",
        direction_bias="short",
        thesis="[mean_reversion] overbought extension",
        confidence="medium",
        confidence_score=49,
        source_agent="universe_scanner",
        execution_eligible=True,
        universe_snapshot_id="scanner-snapshot",
        universe_member_id="scanner-member",
        scanner_family="mean_reversion",
        metadata=json.dumps({
            "instrument_background": crypto_background("SLX/USDT", "perp"),
            "analysis_context": instrument_analysis_context(
                crypto_background("SLX/USDT", "perp")
            ),
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v4",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": "scanner-snapshot",
                    "universe_member_id": "scanner-member",
                    "symbol": "SLX/USDT",
                    "market_type": "perp",
                    "venue": "fixture",
                    "direction": "short",
                    "total_score": 49.105,
                },
            },
        }),
    )
    insert_universe_opportunity_generation(
        original,
        venue="fixture",
        market_type="perp",
    )
    record_filter_decision(
        candidate_key="universe_member:scanner-member",
        candidate_kind="universe_member",
        parent_candidate_key="universe_snapshot:scanner-snapshot",
        filter_name="opportunity_emission",
        stage="signal_emitter",
        decision="passed",
        reason_code="opportunity_emitted",
        symbol=original.symbol,
        market_type="perp",
        venue="fixture",
        direction="short",
        opportunity_id=original.id,
        universe_snapshot_id=original.universe_snapshot_id,
        universe_member_id=original.universe_member_id,
    )

    async def contrary_read(*_args, **_kwargs):
        return ({
            "direction_bias": "long",
            "confidence": "high",
            "confidence_score": 70,
            "rationale": "volume confirms a bullish breakout",
            "key_observations": ["4h breakout above the upper band"],
        }, None)

    monkeypatch.setattr(observer, "llm_structured", contrary_read)
    monkeypatch.setattr(observer, "get_provider_for_tier", lambda _tier: object())
    monkeypatch.setattr(observer, "record_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(observer, "log_tool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "extensions.learning.prompt_bundles.authorize_observer_learning_bundle",
        lambda *_args, **_kwargs: ("", None),
    )
    prefetched = observer._SymbolPrefetch(
        context_str="SLX/USDT technical context",
        debug={},
        pre_filter={
            "passed": True,
            "rsi": 75.0,
            "atr_pct": 5.0,
            "keyword_hits": [],
            "matched_rules": ["rsi_deviation"],
        },
        signal_score=1.0,
        funding_rate_val=0.01,
        open_interest_val=1_000_000.0,
        oi_delta_val=1.0,
    )

    opportunity_id, error = await observer._analyze_symbol(
        symbol=original.symbol,
        market_type="perp",
        permission_mode="Observe",
        run_id="observer-direction-conflict",
        linked_strategy_id=None,
        memory_block="",
        universe_snapshot_id=original.universe_snapshot_id,
        universe_member_id=original.universe_member_id,
        scanner_family=original.scanner_family,
        scanner_direction=original.direction_bias,
        force_full=True,
        _prefetched=prefetched,
    )

    assert error is None
    assert opportunity_id == original.id
    stored = get_opportunity(original.id)
    assert stored["direction_bias"] == "short"
    assert stored["confidence"] == "medium"
    assert stored["confidence_score"] == 49
    assert stored["thesis"] == "[mean_reversion] overbought extension"
    assert stored["key_observations"] == ""
    assessment = json.loads(stored["metadata"])["observer_assessment"]
    assert assessment == {
        "version": "market-observer-assessment/v1",
        "direction_bias": "long",
        "confidence": "high",
        "confidence_score": 70,
        "rationale": "volume confirms a bullish breakout",
        "key_observations": ["4h breakout above the upper band"],
        "scanner_direction": "short",
        "direction_conflict": True,
    }




def test_scanner_manifest_without_persisted_emitter_fact_fails_closed(
    tmp_path, monkeypatch,
):
    _prepare_db(tmp_path, monkeypatch)
    from aletheia.analytics.filter_evidence import verify_signal_emitter_admission
    from aletheia.storage.models import Opportunity

    opportunity = Opportunity(
        id="scanner-missing-emitter-fact",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        source_agent="universe_scanner",
        universe_snapshot_id="snapshot-missing-fact",
        universe_member_id="member-missing-fact",
        metadata=json.dumps({
            "instrument_background": crypto_background(),
            "analysis_context": instrument_analysis_context(crypto_background()),
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v4",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": "snapshot-missing-fact",
                    "universe_member_id": "member-missing-fact",
                    "symbol": "BTC/USDT",
                    "market_type": "spot",
                    "venue": "fixture",
                    "direction": "long",
                },
            },
        }),
    )
    insert_universe_opportunity_generation(
        opportunity,
        venue="fixture",
        market_type="spot",
    )

    assert verify_signal_emitter_admission(
        opportunity.model_dump()
    ) == (False, "producer_eligibility_fact_missing")



def test_scanner_carrier_without_background_fails_closed_before_fact_lookup():
    from aletheia.analytics.filter_evidence import verify_signal_emitter_admission
    from aletheia.storage.models import Opportunity

    opportunity = Opportunity(
        id="pre-background-carrier",
        symbol="SOXL/USDT",
        market="usdt_futures",
        direction_bias="long",
        source_agent="universe_scanner",
        universe_snapshot_id="pre-background-snapshot",
        universe_member_id="pre-background-member",
        metadata=json.dumps({
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v3",
                "t0": {"status": "available"},
            },
        }),
    )

    assert verify_signal_emitter_admission(
        opportunity.model_dump()
    ) == (False, "instrument_background_missing")



def test_signal_emitter_attestation_db_failure_is_retryable(monkeypatch):
    from aletheia.analytics.filter_evidence import verify_signal_emitter_admission
    from aletheia.storage.models import Opportunity

    opportunity = Opportunity(
        id="opp-attestation-unavailable",
        source_agent="universe_scanner",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        universe_snapshot_id="snapshot-attestation-unavailable",
        universe_member_id="member-attestation-unavailable",
        metadata=json.dumps({
            "instrument_background": crypto_background(),
            "analysis_context": instrument_analysis_context(crypto_background()),
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v4",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": "snapshot-attestation-unavailable",
                    "universe_member_id": "member-attestation-unavailable",
                    "symbol": "BTC/USDT",
                    "market_type": "spot",
                    "venue": "fixture",
                    "direction": "long",
                },
            },
        }),
    )

    class _UnavailableDb:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr("aletheia.storage.sqlite.get_db", lambda: _UnavailableDb())

    assert verify_signal_emitter_admission(
        opportunity.model_dump()
    ) == (False, "producer_attestation_unavailable")



def test_pre_v2_scanner_carrier_fails_closed_before_fact_lookup():
    from aletheia.analytics.filter_evidence import verify_signal_emitter_admission
    from aletheia.storage.models import Opportunity

    opportunity = Opportunity(
        id="pre-v2-scanner-carrier",
        source_agent="universe_scanner",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        universe_snapshot_id="pre-v2-snapshot",
        universe_member_id="pre-v2-member",
        metadata=json.dumps({
            "instrument_background": crypto_background(),
            "analysis_context": instrument_analysis_context(crypto_background()),
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v1",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": "pre-v2-snapshot",
                    "universe_member_id": "pre-v2-member",
                    "symbol": "BTC/USDT",
                    "market_type": "spot",
                    "venue": "fixture",
                    "direction": "long",
                },
            },
        }),
    )

    assert verify_signal_emitter_admission(
        opportunity.model_dump()
    ) == (False, "producer_eligibility_epoch_mismatch")



def test_observer_workset_keeps_spot_and_perp_owners_distinct(monkeypatch):
    from extensions.research import market_observer as observer

    members = [
        {
            "id": "member-spot",
            "snapshot_id": "snapshot-both",
            "symbol": "BTC/USDT",
            "market_type": "spot",
        },
        {
            "id": "member-perp",
            "snapshot_id": "snapshot-both",
            "symbol": "BTC/USDT",
            "market_type": "perp",
        },
    ]
    monkeypatch.setattr(
        "aletheia.universe.service.UniverseService.get_current_universe",
        lambda *_args, **_kwargs: members,
    )

    def existing(_symbol, _snapshot_id, *, market):
        if market == "perp":
            return None
        return {
            "id": f"opportunity-{market}",
            "status": "open",
            "universe_member_id": (
                "member-perp" if market == "usdt_futures" else "member-spot"
            ),
        }

    monkeypatch.setattr(observer, "find_opportunity_by_symbol_snapshot", existing)
    monkeypatch.setattr(
        "aletheia.analytics.filter_evidence.verify_signal_emitter_admission",
        lambda _opportunity: (True, "signal_emitter_admission_verified"),
    )
    cfg = SimpleNamespace(
        universe=SimpleNamespace(enabled=True, max_candidates=2),
        market_data=SimpleNamespace(ccxt_exchange="fixture"),
    )

    instruments, owners, snapshot_id, errors = observer._admitted_observer_workset(cfg)

    assert snapshot_id == "snapshot-both"
    assert errors == []
    assert [str(instrument) for instrument in instruments] == [
        "fixture:BTC/USDT:spot",
        "fixture:BTC/USDT:perp",
    ]
    assert owners["fixture:BTC/USDT:spot"]["id"] == "member-spot"
    assert owners["fixture:BTC/USDT:perp"]["id"] == "member-perp"

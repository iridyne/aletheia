"""Market Observer must preserve the deterministic producer's expiry."""

from __future__ import annotations

import json

import pytest

from tests.observer_admission_fixture import (
    crypto_background,
    insert_universe_opportunity_generation,
)


@pytest.mark.asyncio
async def test_observer_preserves_scanner_valid_until(tmp_path, monkeypatch):
    from tests.test_opportunity_source_manifest import _prepare_db

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from extensions.research import market_observer as observer
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity

    init_tool_registry()
    from aletheia.data.symbols import instrument_analysis_context
    expiry = "2099-07-22T23:00:00+00:00"
    background = crypto_background("BTC/USDT", "perp")
    opportunity = Opportunity(
        id="scanner-expiry-owner",
        symbol="BTC/USDT",
        market="usdt_futures",
        direction_bias="long",
        confidence="medium",
        confidence_score=55,
        thesis="scanner thesis",
        source_agent="universe_scanner",
        universe_snapshot_id="snapshot-expiry",
        universe_member_id="member-expiry",
        scanner_family="momentum",
        valid_until=expiry,
        metadata=json.dumps({
            "instrument_background": background,
            "analysis_context": instrument_analysis_context(background),
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v4",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": "snapshot-expiry",
                    "universe_member_id": "member-expiry",
                    "symbol": "BTC/USDT",
                    "market_type": "perp",
                    "venue": "fixture",
                    "direction": "long",
                },
            },
        }),
    )
    insert_universe_opportunity_generation(
        opportunity,
        venue="fixture",
        market_type="perp",
    )
    record_filter_decision(
        candidate_key="universe_member:member-expiry",
        candidate_kind="universe_member",
        filter_name="opportunity_emission",
        stage="signal_emitter",
        decision="passed",
        reason_code="opportunity_emitted",
        symbol=opportunity.symbol,
        market_type="perp",
        venue="fixture",
        direction="long",
        opportunity_id=opportunity.id,
        universe_snapshot_id=opportunity.universe_snapshot_id,
        universe_member_id=opportunity.universe_member_id,
    )

    async def read(*_args, **_kwargs):
        return ({
            "direction_bias": "long",
            "confidence": "high",
            "confidence_score": 70,
            "rationale": "observer assessment",
            "key_observations": ["RSI 68"],
        }, None)

    monkeypatch.setattr(observer, "llm_structured", read)
    from aletheia.config.schema import ModelSelection
    monkeypatch.setattr(observer, "get_provider_for_tier",
                        lambda _tier: ModelSelection(endpoint_id="test", model_id="test-model"))
    monkeypatch.setattr(observer, "record_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(observer, "log_tool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "extensions.learning.prompt_bundles.authorize_observer_learning_bundle",
        lambda *_args, **_kwargs: ("", None),
    )
    prefetched = observer._SymbolPrefetch(
        context_str="BTC/USDT technical context",
        debug={},
        pre_filter={
            "passed": True,
            "rsi": 68.0,
            "atr_pct": 2.0,
            "keyword_hits": [],
            "matched_rules": ["rsi_deviation"],
        },
        signal_score=1.0,
        funding_rate_val=None,
        open_interest_val=None,
        oi_delta_val=None,
    )

    opportunity_id, error = await observer._analyze_symbol(
        symbol=opportunity.symbol,
        market_type="perp",
        permission_mode="Observe",
        run_id="observer-expiry",
        linked_strategy_id=None,
        memory_block="",
        universe_snapshot_id=opportunity.universe_snapshot_id,
        universe_member_id=opportunity.universe_member_id,
        scanner_family=opportunity.scanner_family,
        scanner_direction="long",
        member_expires_at="2099-01-01T00:00:00+00:00",
        force_full=True,
        _prefetched=prefetched,
    )

    assert error is None
    assert opportunity_id == opportunity.id
    assert get_opportunity(opportunity.id)["valid_until"] == expiry


@pytest.mark.asyncio
async def test_stock_linked_owner_skips_crypto_observer_context(tmp_path, monkeypatch):
    from tests.test_opportunity_source_manifest import _prepare_db

    _prepare_db(tmp_path, monkeypatch)
    from extensions.research import market_observer as observer
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.data.symbols import (
        build_instrument_background,
        instrument_analysis_context,
    )
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity

    background = build_instrument_background(
        symbol="SOXL/USDT:USDT",
        venue="binance",
        market_type="perp",
        market_info={
            "symbol": "SOXL/USDT:USDT",
            "base": "SOXL",
            "quote": "USDT",
            "info": {"underlyingType": "EQUITY"},
        },
    )
    metadata = json.dumps({
        "instrument_background": background,
        "analysis_context": instrument_analysis_context(background),
        "evidence_manifest": {
            "version": "opportunity-information-integrity-v1",
            "policy_epoch": "opportunity_information_integrity/v4",
            "t0": {
                "status": "available",
                "trigger_type": "universe_scanner",
                "universe_snapshot_id": "snapshot-soxl",
                "universe_member_id": "member-soxl",
                "symbol": "SOXL/USDT",
                "market_type": "perp",
                "venue": "binance",
                "direction": "long",
            },
        },
    })
    opportunity = Opportunity(
        id="scanner-soxl-owner",
        symbol="SOXL/USDT",
        market="perp",
        direction_bias="long",
        source_agent="universe_scanner",
        universe_snapshot_id="snapshot-soxl",
        universe_member_id="member-soxl",
        scanner_family="momentum",
        asset_class="equity",
        asset_subclass="stock_linked_perp",
        execution_eligible=True,
        metadata=metadata,
    )
    insert_universe_opportunity_generation(
        opportunity,
        venue="binance",
        market_type="perp",
    )
    record_filter_decision(
        candidate_key="universe_member:member-soxl",
        candidate_kind="universe_member",
        filter_name="opportunity_emission",
        stage="signal_emitter",
        decision="passed",
        reason_code="opportunity_emitted",
        symbol=opportunity.symbol,
        market_type="perp",
        venue="binance",
        direction="long",
        opportunity_id=opportunity.id,
        universe_snapshot_id=opportunity.universe_snapshot_id,
        universe_member_id=opportunity.universe_member_id,
    )
    steps = []
    monkeypatch.setattr(
        observer,
        "llm_structured",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("mixed crypto Observer context must not run")
        ),
    )
    monkeypatch.setattr(
        observer,
        "record_step",
        lambda *_args, **kwargs: steps.append(kwargs.get("output_data")),
    )

    opportunity_id, error = await observer._analyze_symbol(
        symbol=opportunity.symbol,
        market_type="perp",
        permission_mode="Observe",
        run_id="observer-soxl",
        linked_strategy_id=None,
        memory_block="",
        universe_snapshot_id=opportunity.universe_snapshot_id,
        universe_member_id=opportunity.universe_member_id,
        scanner_family=opportunity.scanner_family,
        scanner_direction="long",
    )

    assert error is None
    assert opportunity_id == opportunity.id
    assert steps == ["stock_linked_perp_uses_operator_instrument_context"]
    assert get_opportunity(opportunity.id)["metadata"] == metadata

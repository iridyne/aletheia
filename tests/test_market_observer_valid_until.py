"""Market Observer must preserve the deterministic producer's expiry."""

from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_observer_preserves_scanner_valid_until(tmp_path, monkeypatch):
    from tests.test_opportunity_source_manifest import _prepare_db

    _prepare_db(tmp_path, monkeypatch)
    from aletheia.agent_runtime.tool_registry import init_tool_registry
    from aletheia.agents import market_observer as observer
    from aletheia.analytics.filter_evidence import record_filter_decision
    from aletheia.storage.models import Opportunity
    from aletheia.storage.repositories import get_opportunity, insert_opportunity

    init_tool_registry()
    expiry = "2026-07-22T23:00:00+00:00"
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
            "evidence_manifest": {
                "version": "opportunity-information-integrity-v1",
                "policy_epoch": "opportunity_information_integrity/v2",
                "t0": {
                    "status": "available",
                    "trigger_type": "universe_scanner",
                    "universe_snapshot_id": "snapshot-expiry",
                    "universe_member_id": "member-expiry",
                    "symbol": "BTC/USDT",
                    "market_type": "perp",
                    "direction": "long",
                },
            },
        }),
    )
    insert_opportunity(opportunity)
    record_filter_decision(
        candidate_key="universe_member:member-expiry",
        candidate_kind="universe_member",
        filter_name="opportunity_emission",
        stage="signal_emitter",
        decision="passed",
        reason_code="opportunity_emitted",
        symbol=opportunity.symbol,
        market_type="perp",
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
    monkeypatch.setattr(observer, "record_step", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(observer, "log_tool", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "aletheia.learning.prompt_bundles.authorize_observer_learning_bundle",
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

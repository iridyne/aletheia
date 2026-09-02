from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aletheia.trading.entry_analysis import (
    EntryAnalysisContextV1,
    EntryAnchorV1,
    EntryHypothesisV1,
    validate_hypothesis_references,
)


def _anchor(kind: str, price: float, source: str) -> EntryAnchorV1:
    return EntryAnchorV1(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind=kind,
        price=price, directional_role="both", known_at_ms=1_700_000_000_000,
        source_fact_refs=(source,), source_fingerprint=source,
    )


def _context(anchors: tuple[EntryAnchorV1, ...]) -> EntryAnalysisContextV1:
    return EntryAnalysisContextV1(
        analysis_cutoff_at="2026-09-02T00:00:00Z",
        instrument={"venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": "long"},
        planning_generation={"id": "g1", "planner_contract_version": "entry-planner/v3", "candidate_id": "c1", "candidate_family": "momentum"},
        candidate={"fact_refs": ["candidate:c1"]},
        candle_windows={key: {"availability": "unavailable", "bars": []} for key in ("4h", "1h", "15m")},
        market_structure={"fact_refs": []}, quant_context={"fact_refs": []},
        portfolio_context={"availability": "unavailable"},
        execution_context={"availability": "available"}, anchors=anchors,
        entry_profiles=({"id": "anchor-resting-maker", "version": "v1"},),
        learning_context={"availability": "unavailable"},
    )


def test_anchor_and_context_hashes_are_stable_and_aliases_do_not_vote_twice():
    first = _anchor("candidate_reference", 100.0, "candidate:c1")
    aliased = first.model_copy(update={"aliases": ("SMC", "PA", "SMC")})
    rebuilt = EntryAnchorV1.model_validate(aliased.model_dump())
    assert rebuilt.anchor_id == first.anchor_id
    assert rebuilt.aliases == ("PA", "SMC")
    assert _context((first,)).context_fingerprint == _context((first,)).context_fingerprint


def test_hypothesis_references_are_strict_and_no_plan_is_canonical():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    context = _context((primary, invalidation))
    hypothesis = EntryHypothesisV1(
        decision="ENTRY_PLAN", reason_code="OK", thesis="bounded", scenario="continuation",
        setup_archetype="continuation", primary_anchor_ref=primary.anchor_id,
        invalidation_anchor_ref=invalidation.anchor_id, entry_profile_id="anchor-resting-maker",
        entry_profile_version="v1", execution_urgency="PASSIVE", risk_tier="LOW",
        horizon="INTRADAY", supporting_fact_refs=(primary.anchor_id,),
    )
    validate_hypothesis_references(hypothesis, context)
    no_plan = EntryHypothesisV1(
        decision="NO_PLAN", reason_code="CONFLICTING_SIGNALS", thesis="", scenario="",
        primary_anchor_ref=primary.anchor_id, entry_profile_id="anchor-resting-maker",
        entry_profile_version="v1", execution_urgency="PASSIVE", risk_tier="LOW",
        horizon="SWING", supporting_fact_refs=(),
    )
    assert no_plan.primary_anchor_ref is None
    assert no_plan.risk_tier == no_plan.horizon == "NONE"


def test_entry_hypothesis_requires_roles_urgency_and_nonempty_text():
    primary = EntryAnchorV1.model_validate({
        **_anchor("candidate_reference", 100.0, "candidate:c1").model_dump(),
        "directional_role": "invalidation_candidate", "anchor_id": "",
    })
    invalidation = EntryAnchorV1.model_validate({
        **_anchor("confirmed_swing", 95.0, "swing:s1").model_dump(),
        "directional_role": "entry_candidate", "anchor_id": "",
    })
    hypothesis = EntryHypothesisV1(
        decision="ENTRY_PLAN", reason_code="OK", thesis=" bounded ", scenario=" continuation ",
        setup_archetype="continuation", primary_anchor_ref=primary.anchor_id,
        invalidation_anchor_ref=invalidation.anchor_id, entry_profile_id="anchor-retrace-reclaim",
        entry_profile_version="v1", execution_urgency="PASSIVE", risk_tier="LOW",
        horizon="INTRADAY", supporting_fact_refs=(primary.anchor_id,),
    )
    assert (hypothesis.thesis, hypothesis.scenario) == ("bounded", "continuation")
    with pytest.raises(ValueError, match="hypothesis_primary_anchor_role_invalid"):
        validate_hypothesis_references(hypothesis, _context((primary, invalidation)))
    with pytest.raises(ValueError, match="entry_thesis_scenario_required"):
        EntryHypothesisV1.model_validate({
            **hypothesis.model_dump(), "thesis": "  ",
        })
    with pytest.raises(ValueError, match="entry_execution_urgency_required"):
        EntryHypothesisV1.model_validate({
            **hypothesis.model_dump(), "execution_urgency": None,
        })


def test_context_uses_snapshot_time_and_normalized_point_in_time_facts():
    from aletheia.trading.entry_analysis import build_entry_analysis_context

    bar_ms = 15 * 60 * 1000
    cutoff_ms = 96 * bar_ms
    instant = lambda value: datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    bars = []
    for index in range(96):
        breakout = index == 95
        bars.append({
            "ts": index * bar_ms, "open": 99.0, "high": 102.0 if breakout else 100.0,
            "low": 98.0, "close": 101.0 if breakout else 99.0, "volume": 10.0,
            "provider": "fixture", "exchange": "bitget", "market_type": "perp",
            "first_persisted_at": instant(0), "last_mutated_at": instant(0),
        })
    generation = {
        "id": "g1", "last_candidate_id": "c1", "venue": "bitget", "symbol": "BTC/USDT",
        "market_type": "perp", "candidate_family": "momentum", "direction": "long",
        "planner_contract_version": "entry-planner/v3",
    }
    candidate = {
        "id": "c1", "snapshot_id": "s1", "asset_id": "asset-btc", "venue": "bitget",
        "symbol": "BTC/USDT", "market_type": "perp", "scanner_family": "momentum",
        "scanner_direction": "long", "entry_price_usd": 101.5, "estimated_cost": 0.1,
    }
    feature = {
        "id": "f1", "asset_id": "asset-btc", "ts": instant(95 * bar_ms),
        "atr_pct": 2.0, "raw": "must-not-leak", "technical_snapshot_manifest": "{}",
    }
    context = build_entry_analysis_context(
        generation, candidate, feature, {"4h": [], "1h": [], "15m": bars},
        cutoff_at=datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc),
        candidate_detected_at=instant(2), positions=[],
    )
    assert context.candidate["detected_at"] == instant(2)
    assert "feature_snapshot" not in context.quant_context
    assert "raw" not in str(context.quant_context)
    assert context.quant_context["values"] == {"atr_pct": 2.0}
    breakout = next(anchor for anchor in context.anchors if anchor.kind == "causal_breakout")
    assert breakout.known_at_ms == cutoff_ms
    assert context.market_structure["15m"]["provenance"]["instrument_id"] == "bitget:BTC/USDT:perp"

    future = build_entry_analysis_context(
        generation, candidate, {**feature, "ts": instant(cutoff_ms + 1)},
        {"4h": [], "1h": [], "15m": bars},
        cutoff_at=datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc),
        candidate_detected_at=instant(2), positions=[],
    )
    assert future.quant_context["availability"] == "unavailable"

from __future__ import annotations

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
        candle_windows={key: {"availability": "available", "bars": []} for key in ("4h", "1h", "15m")},
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

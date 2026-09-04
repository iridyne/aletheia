from __future__ import annotations
import asyncio
import json

from datetime import datetime, timezone

import pytest

from aletheia.trading.entry_analysis import (
    EntryAnalysisContextV1,
    EntryAnalysisContextV2,
    EntryAnchorV1,
    EntryHypothesisV1,
    EntryHypothesisV2,
    _candidate_opportunity_evidence_refs,
    canonical_json,
    entry_model_view,
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


def _context_v2(anchors: tuple[EntryAnchorV1, ...]) -> EntryAnalysisContextV2:
    from aletheia.learning.entry_context import empty_entry_learning_context

    base = _context(anchors).model_dump(exclude={"context_version", "context_fingerprint"})
    base.update(
        planning_generation={
            "id": "g1", "planner_contract_version": "entry-planner/v4",
            "candidate_id": "c1", "candidate_family": "mean_reversion",
        },
        candle_windows={
            "4h": {"availability": "unavailable", "bars": []},
            "1h": {"availability": "unavailable", "bars": []},
            "15m": {"availability": "available", "bars": [{} for _ in range(96)]},
        },
        market_structure={
            "15m": {"availability": "available"},
            "fact_refs": [anchors[-1].anchor_id],
        },
        entry_profiles=({"id": "grounded-anchor-resting-maker", "version": "v1"},),
        learning_context=empty_entry_learning_context(
            as_of_at="2026-09-02T00:00:00Z", mechanism="reversal", direction="long",
            market_type="perp", policy="candidate_selection_utility_v1", policy_epoch="epoch-1",
        ).model_copy(update={
            "status": "observational", "evidence_refs": ("learning:1",),
            "source_fingerprint": "e" * 64, "blocking_reasons": (),
        }),
        candidate_opportunity_context={
            "opportunity": {
                "primary_mechanism": "reversal", "direction": "long",
            },
        },
        inference_identity={
            "caller": "agent_shell.entry_planner",
            "prompt_fingerprint": "f" * 64,
            "model_provider": "test",
        },
    )
    return EntryAnalysisContextV2(**base)


def _maximal_context() -> EntryAnalysisContextV2:
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    data = _context_v2((primary, invalidation)).model_dump(
        mode="json", exclude={"context_fingerprint"}
    )
    for timeframe, size in (("4h", 30), ("1h", 48), ("15m", 96)):
        data["candle_windows"][timeframe] = {
            "availability": "available",
            "source_fingerprint": f"candles:{timeframe}",
            "bars": [
                {"ts": index, "open": 1, "high": 2, "low": 0.5, "close": 1.5,
                 "volume": 10, "provider": "test", "provenance": "raw" * 20}
                for index in range(size)
            ],
        }
        data["market_structure"][timeframe] = {
            "availability": "available", "state": "uptrend", "atr14": 2.0,
            "geometry_fingerprint": f"geometry:{timeframe}",
            "fact_refs": [invalidation.anchor_id],
            "swings": [{"id": f"swing:{index}", "raw": "x" * 200} for index in range(30)],
            "events": [{"id": f"event:{index}", "raw": "y" * 200} for index in range(30)],
            "provenance": {"raw": "z" * 5000},
        }
    data["entry_profiles"] = ({
        "id": "grounded-anchor-resting-maker", "version": "v1",
        "supported_mechanisms": ("reversal",),
        "required_fact_sections": ("market_structure", "execution_context"),
        "primary_anchor_kinds": ("candidate_reference",),
        "invalidation_anchor_kinds": ("confirmed_swing",),
        "supported_timeframes": ("15m",),
    },)
    return EntryAnalysisContextV2.model_validate(data)


def test_entry_model_view_excludes_raw_evidence_and_retains_grounding_identities():
    context = _maximal_context()
    view = entry_model_view(context)

    assert all("bars" not in window for window in view["candle_windows"].values())
    assert all(
        not {"swings", "events", "provenance"} & section.keys()
        for name, section in view["market_structure"].items()
        if name != "fact_refs"
    )
    assert view["planning_generation"] == {
        "id": "g1", "candidate_id": "c1", "candidate_family": "mean_reversion",
    }
    assert view["candidate"]["fact_refs"] == ["candidate:c1"]
    assert view["market_structure"]["fact_refs"] == list(context.market_structure["fact_refs"])
    assert {item["anchor_id"] for item in view["anchors"]} == {
        item.anchor_id for item in context.anchors
    }
    assert view["entry_profiles"][0]["id"] == "grounded-anchor-resting-maker"


def test_entry_model_view_is_deterministic_bounded_and_does_not_mutate_context():
    context = _maximal_context()
    frozen_before = canonical_json(context.model_dump(mode="json"))

    first = canonical_json(entry_model_view(context))
    second = canonical_json(entry_model_view(context))

    assert first == second
    assert len(first.encode()) < 24 * 1024
    assert len(frozen_before.encode()) > 24 * 1024
    assert canonical_json(context.model_dump(mode="json")) == frozen_before


def test_system_operator_prompt_uses_projection_while_full_context_stays_valid(monkeypatch):
    from aletheia.agent_shell.entry_planner import system_operator_entry_planner

    context = _maximal_context()
    captured = {}

    async def structured(_system, user, **kwargs):
        captured.update({"user": user, **kwargs})
        return {
            "decision": "NO_PLAN", "reason_code": "CONFLICTING_SIGNALS",
            "mechanism": "reversal", "direction": "long",
            "risk_tier": "NONE", "horizon": "NONE",
        }, None

    monkeypatch.setattr("aletheia.agents.helpers.llm_structured", structured)

    result = asyncio.run(system_operator_entry_planner(context.model_dump(mode="json"), "test"))
    prompt = json.loads(captured["user"])

    assert result.decision == "NO_PLAN"
    assert prompt == entry_model_view(context)
    assert all("bars" not in window for window in prompt["candle_windows"].values())
    assert captured["context_artifacts"][0]["rendered_chars"] == len(captured["user"])
    assert context.candle_windows["15m"]["bars"]
    semantic_validator = captured["semantic_validator"]
    assert semantic_validator({
        **result.model_dump(mode="json"),
        "supporting_fact_refs": ["missing:fact"],
    }) == "hypothesis_fact_ref_not_supplied"
    assert semantic_validator({
        "decision": "ENTRY_PLAN", "reason_code": "OK",
        "mechanism": "reversal", "direction": "long",
        "primary_anchor_ref": context.anchors[0].anchor_id,
        "invalidation_anchor_ref": context.anchors[1].anchor_id,
        "entry_profile_id": "missing-profile", "entry_profile_version": "v1",
        "execution_urgency": "PASSIVE", "risk_tier": "LOW", "horizon": "INTRADAY",
        "supporting_fact_refs": [context.anchors[1].anchor_id],
    }) == "entry_profile_not_registered"
    assert semantic_validator(result.model_dump(mode="json")) is None


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


def test_v2_hypothesis_binds_mechanism_profile_and_current_market_fact():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    context = _context_v2((primary, invalidation))
    hypothesis = EntryHypothesisV2(
        decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="grounded-anchor-resting-maker", entry_profile_version="v1",
        execution_urgency="PASSIVE", risk_tier="LOW", horizon="INTRADAY",
        supporting_fact_refs=(invalidation.anchor_id,), learning_evidence_refs=("learning:1",),
    )
    validate_hypothesis_references(hypothesis, context)

    with pytest.raises(ValueError, match="hypothesis_opportunity_identity_mismatch"):
        validate_hypothesis_references(
            hypothesis.model_copy(update={"mechanism": "continuation"}), context,
        )
    with pytest.raises(ValueError, match="hypothesis_current_market_fact_required"):
        validate_hypothesis_references(
            hypothesis.model_copy(update={"supporting_fact_refs": (primary.anchor_id,)}), context,
        )


def test_v2_candidate_facts_include_bounded_primary_and_secondary_opportunity_evidence():
    refs = _candidate_opportunity_evidence_refs({
        "primary_evidence_refs": ["opportunity:primary"],
        "secondary_mechanisms": [
            {"evidence_refs": ["opportunity:secondary", "opportunity:primary"]},
        ],
    })

    assert refs == ("opportunity:primary", "opportunity:secondary")


def test_unavailable_treatment_learning_is_replaced_by_empty_baseline():
    from types import SimpleNamespace

    from aletheia.learning.entry_context import empty_entry_learning_context
    from aletheia.trading.entry_analysis import _bind_experimental_entry_learning_context

    scope = {
        "as_of_at": "2026-09-02T00:00:00Z", "mechanism": "reversal",
        "direction": "long", "market_type": "perp",
        "policy": "candidate_selection_utility_v1", "policy_epoch": "epoch-1",
    }
    unavailable = empty_entry_learning_context(**scope).model_copy(update={
        "evidence_refs": ("must-not-reach-model",),
        "metrics": {"intent_to_treat_net_utility_pct": 99.0},
    })

    result = _bind_experimental_entry_learning_context(
        unavailable,
        SimpleNamespace(
            observation_id="obs", generation=1, manifest_hash="hash",
            receipt_id="receipt", assignment="treatment",
        ),
        scope,
    )

    assert result.status == "unavailable"
    assert result.evidence_refs == ()
    assert result.metrics["intent_to_treat_net_utility_pct"] is None


def test_v2_resting_maker_cannot_claim_bounded_urgent_execution():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")

    with pytest.raises(ValueError, match="resting_maker_must_be_passive"):
        EntryHypothesisV2(
            decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
            primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
            entry_profile_id="grounded-anchor-resting-maker", entry_profile_version="v1",
            execution_urgency="BOUNDED_URGENT", risk_tier="LOW", horizon="INTRADAY",
            supporting_fact_refs=(invalidation.anchor_id,),
        )


def test_v2_no_plan_still_binds_opportunity_identity_and_explanatory_refs():
    context = _context_v2((_anchor("candidate_reference", 100.0, "candidate:c1"),))
    no_plan = EntryHypothesisV2(
        decision="NO_PLAN", reason_code="CONFLICTING_SIGNALS",
        mechanism="continuation", direction="long", risk_tier="NONE", horizon="NONE",
    )

    with pytest.raises(ValueError, match="hypothesis_opportunity_identity_mismatch"):
        validate_hypothesis_references(no_plan, context)
    with pytest.raises(ValueError, match="hypothesis_fact_ref_not_supplied"):
        validate_hypothesis_references(
            no_plan.model_copy(update={
                "mechanism": "reversal", "supporting_fact_refs": ("missing:fact",),
            }),
            context,
        )
    with pytest.raises(ValueError, match="hypothesis_learning_ref_not_supplied"):
        validate_hypothesis_references(
            no_plan.model_copy(update={
                "mechanism": "reversal", "learning_evidence_refs": ("missing:learning",),
            }),
            context,
        )


@pytest.mark.parametrize(
    "mechanism",
    ("continuation", "reversal", "positioning_forced_flow", "information_catalyst"),
)
def test_v2_schema_represents_every_mechanism_without_fabricating_a_plan(mechanism):
    hypothesis = EntryHypothesisV2(
        decision="NO_PLAN",
        reason_code="NO_VALID_ENTRY_PROFILE",
        mechanism=mechanism,
        direction="long",
        risk_tier="NONE",
        horizon="NONE",
    )

    assert hypothesis.mechanism == mechanism
    assert hypothesis.primary_anchor_ref is None
    with pytest.raises(ValueError, match="extra_forbidden"):
        EntryHypothesisV2.model_validate({**hypothesis.model_dump(), "entry_price": 100.0})


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
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContextV1

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
        "entry_behavior_fingerprint": "a" * 64, "schema_fingerprint": "b" * 64,
        "opportunity_mechanism": "continuation", "candidate_selection_policy": "candidate_selection_utility_v1",
        "candidate_selection_policy_epoch": "epoch-1", "mechanism_version": "continuation/v1",
    }
    opportunity = CandidateOpportunityContextV1(
        candidate_id="c1",
        candidate_selection={"policy": "candidate_selection_utility_v1", "policy_epoch": "epoch-1", "source_record_hash": "a" * 64},
        source={"detected_at": instant(2), "source_cutoff_at": instant(2), "feature_snapshot_id": "f1"},
        opportunity={"primary_mechanism": "continuation", "mechanism_version": "continuation/v1", "mechanism_source": "target_authority", "source_component_id": "momentum", "direction": "long", "horizon": "4h", "economic_utility_pct": 1.0, "capability_status": "supported", "capability_gaps": (), "primary_evidence_refs": (), "secondary_mechanisms": ()},
        context={},
    )
    candidate = {
        "id": "c1", "snapshot_id": "s1", "asset_id": "asset-btc", "venue": "bitget",
        "symbol": "BTC/USDT", "market_type": "perp", "scanner_family": "momentum",
        "scanner_direction": "long", "entry_price_usd": 101.5, "estimated_cost": 0.1,
        "candidate_opportunity_context_json": opportunity.model_dump_json(),
        "candidate_opportunity_context_hash": opportunity.context_hash,
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

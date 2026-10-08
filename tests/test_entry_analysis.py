from __future__ import annotations
import asyncio
import json

from datetime import datetime, timezone
from typing import Any

import pytest

from aletheia.trading.entry_analysis import (
    EntryAnalysisContext,
    EntryAnchor,
    EntryHypothesis,
    _candidate_opportunity_evidence_refs,
    canonical_json,
    entry_model_view,
    validate_hypothesis_references,
)
from aletheia.trading.risk_intent import RiskIntent


def _anchor(kind: str, price: float, source: str) -> EntryAnchor:
    return EntryAnchor(
        venue="bitget", symbol="BTC/USDT", market_type="perp", kind=kind,
        price=price, directional_role="both", known_at_ms=1_700_000_000_000,
        source_fact_refs=(source,), source_fingerprint=source,
    )


def _risk_intent(*refs: str) -> RiskIntent:
    return RiskIntent(posture="BASELINE", reason="fixture baseline posture", evidence_refs=refs)


def _context(anchors: tuple[EntryAnchor, ...]) -> EntryAnalysisContext:
    from aletheia.trading.entry_learning import empty_entry_learning_context

    return EntryAnalysisContext(
        analysis_cutoff_at="2026-09-02T00:00:00Z",
        instrument={"venue": "bitget", "symbol": "BTC/USDT", "market_type": "perp", "direction": "long"},
        planning_generation={"id": "g1", "candidate_id": "c1", "candidate_family": "momentum"},
        candidate={"fact_refs": ["candidate:c1"]},
        candle_windows={key: {"availability": "unavailable", "bars": []} for key in ("4h", "1h", "15m")},
        market_structure={"fact_refs": []}, quant_context={"fact_refs": []},
        portfolio_context={"availability": "unavailable"},
        execution_context={"availability": "available"}, anchors=anchors,
        entry_profiles=({"id": "grounded-anchor-direct"},),
        learning_context=empty_entry_learning_context(
            as_of_at="2026-09-02T00:00:00Z", mechanism="continuation", direction="long",
            market_type="perp", policy="candidate_selection_utility_v1", policy_epoch="epoch-1",
        ),
        candidate_opportunity_context={"opportunity": {"primary_mechanism": "continuation", "direction": "long"}},
        inference_identity={"caller": "agent_shell.entry_planner", "prompt_fingerprint": "f" * 64, "model_provider": "test"},
    )


def _context_v2(anchors: tuple[EntryAnchor, ...]) -> EntryAnalysisContext:
    from aletheia.trading.entry_learning import empty_entry_learning_context

    base = _context(anchors).model_dump(exclude={"context_fingerprint"})
    base.update(
        planning_generation={
            "id": "g1",
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
        entry_profiles=({"id": "grounded-anchor-direct"},),
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
    return EntryAnalysisContext(**base)


def _maximal_context() -> EntryAnalysisContext:
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    data = _context_v2((primary, invalidation)).model_dump(
        mode="json", exclude={"context_fingerprint"}
    )
    for timeframe, size in (("4h", 35), ("1h", 50), ("15m", 96)):
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
        "id": "grounded-anchor-direct", "version": "v1",
        "supported_mechanisms": ("reversal",),
        "required_fact_sections": ("market_structure", "execution_context"),
        "primary_anchor_kinds": ("candidate_reference",),
        "invalidation_anchor_kinds": ("confirmed_swing",),
        "supported_timeframes": ("15m",),
    },)
    return EntryAnalysisContext.model_validate(data)


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
    assert view["entry_profiles"][0]["id"] == "grounded-anchor-direct"


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
        "entry_profile_id": "missing-profile",
        "capture_priority": "PRICE_FIRST", "capture_reason": "current structure", "capture_evidence_refs": [context.anchors[1].anchor_id], "risk_tier": "LOW", "horizon": "INTRADAY",
        "supporting_fact_refs": [context.anchors[1].anchor_id],
        "risk_intent": {"posture": "BASELINE", "reason": "fixture baseline posture", "evidence_refs": [context.anchors[1].anchor_id]},
    }) == "entry_profile_not_registered"
    assert semantic_validator(result.model_dump(mode="json")) is None


def test_anchor_and_context_hashes_are_stable_and_aliases_do_not_vote_twice():
    first = _anchor("candidate_reference", 100.0, "candidate:c1")
    aliased = first.model_copy(update={"aliases": ("SMC", "PA", "SMC")})
    rebuilt = EntryAnchor.model_validate(aliased.model_dump())
    assert rebuilt.anchor_id == first.anchor_id
    assert rebuilt.aliases == ("PA", "SMC")
    assert _context((first,)).context_fingerprint == _context((first,)).context_fingerprint


def test_v2_hypothesis_binds_mechanism_profile_and_current_market_fact():
    primary = _anchor("candidate_reference", 100.0, "candidate:c1")
    invalidation = _anchor("confirmed_swing", 95.0, "swing:s1")
    context = _context_v2((primary, invalidation))
    hypothesis = EntryHypothesis(
        decision="ENTRY_PLAN", reason_code="OK", mechanism="reversal", direction="long",
        primary_anchor_ref=primary.anchor_id, invalidation_anchor_ref=invalidation.anchor_id,
        entry_profile_id="grounded-anchor-direct",
        capture_priority="PRICE_FIRST", capture_reason="fixture current evidence", capture_evidence_refs=(invalidation.anchor_id,), risk_tier="LOW", horizon="INTRADAY",
        supporting_fact_refs=(invalidation.anchor_id,), learning_evidence_refs=("learning:1",),
        risk_intent=_risk_intent(invalidation.anchor_id),
    )
    validate_hypothesis_references(hypothesis, context)
    contract = entry_model_view(context)["reference_contract"]
    assert contract == {
        "citable_fact_refs": sorted(["candidate:c1", primary.anchor_id, invalidation.anchor_id]),
        "current_market_fact_refs": [invalidation.anchor_id],
    }
    for ref in contract["citable_fact_refs"]:
        validate_hypothesis_references(hypothesis.model_copy(update={"opposing_fact_refs": (ref,)}), context)
    with pytest.raises(ValueError, match="hypothesis_fact_ref_not_supplied"):
        validate_hypothesis_references(
            hypothesis.model_copy(update={"opposing_fact_refs": ("swing:s1",)}), context,
        )

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

    from aletheia.trading.entry_learning import empty_entry_learning_context
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




def test_v2_no_plan_still_binds_opportunity_identity_and_explanatory_refs():
    context = _context_v2((_anchor("candidate_reference", 100.0, "candidate:c1"),))
    no_plan = EntryHypothesis(
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
    hypothesis = EntryHypothesis(
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
        EntryHypothesis.model_validate({**hypothesis.model_dump(), "entry_price": 100.0})


def _planner_inputs(instant) -> tuple[dict, dict]:
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContext

    generation = {
        "id": "g1", "last_candidate_id": "c1", "venue": "bitget", "symbol": "BTC/USDT",
        "market_type": "perp", "candidate_family": "momentum", "direction": "long",
        "entry_behavior_fingerprint": "a" * 64, "schema_fingerprint": "b" * 64,
        "opportunity_mechanism": "continuation", "candidate_selection_policy": "candidate_selection_utility_v1",
        "candidate_selection_policy_epoch": "epoch-1", "mechanism_version": "continuation/v1",
    }
    opportunity = CandidateOpportunityContext(
        candidate_id="c1",
        candidate_selection={"policy": "candidate_selection_utility_v1", "policy_epoch": "epoch-1", "source_record_hash": "a" * 64},
        source={"detected_at": instant(2), "source_cutoff_at": instant(2), "feature_snapshot_id": "f1"},
        opportunity={"primary_mechanism": "continuation", "mechanism_version": "continuation/v1", "mechanism_source": "target_authority", "source_component_id": "momentum", "direction": "long", "horizon": "4h", "economic_utility_pct": 1.0, "capability_status": "supported", "capability_gaps": (), "primary_evidence_refs": (), "secondary_mechanisms": ()},
        context={},
    )
    generation["candidate_id"] = opportunity.candidate_id
    generation["requalification_source_json"] = json.dumps({"candidate_opportunity_context": opportunity.model_dump(mode="json")})
    candidate = {
        "id": "c1", "snapshot_id": "s1", "asset_id": "asset-btc", "venue": "bitget",
        "symbol": "BTC/USDT", "market_type": "perp", "scanner_family": "momentum",
        "scanner_direction": "long", "entry_price_usd": 101.5, "estimated_cost": 0.1,
        "candidate_opportunity_context_json": opportunity.model_dump_json(),
        "candidate_opportunity_context_hash": opportunity.context_hash,
    }
    return generation, candidate


def test_context_uses_snapshot_time_and_normalized_point_in_time_facts():
    from aletheia.trading.entry_analysis import build_entry_analysis_context
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContext

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
    generation, candidate = _planner_inputs(instant)
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


def _trend_bars(count: int, interval_ms: int, cutoff_ms: int, instant) -> list[dict]:
    """Closed, contiguous, persisted-before-cutoff bars on a wavy uptrend."""
    bars = []
    for index in range(count):
        close = 100.0 + 0.5 * index + (1.5 if index % 3 == 0 else -0.5)
        bars.append({
            "ts": cutoff_ms - (count - index) * interval_ms, "open": close - 0.25,
            "high": close + 1.0, "low": close - 1.0, "close": close, "volume": 10.0 + index % 4,
            "provider": "fixture", "exchange": "bitget", "market_type": "perp",
            "first_persisted_at": instant(0), "last_mutated_at": instant(0),
        })
    return bars


def _technical_context(windows: dict, *, imbalance: float | None, feature_extra: dict):
    from aletheia.trading.entry_analysis import build_entry_analysis_context

    cutoff_ms = 1_000 * 4 * 3600 * 1000
    instant = lambda value: datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    cutoff = datetime.fromtimestamp(cutoff_ms / 1000, tz=timezone.utc)
    interval = {"4h": 4 * 3600 * 1000, "1h": 3600 * 1000, "15m": 15 * 60 * 1000}
    generation, candidate = _planner_inputs(instant)
    feature = {
        "id": "f1", "asset_id": "asset-btc", "ts": instant(cutoff_ms - 1000),
        "technical_snapshot_manifest": "{}", **feature_extra,
    }
    orderbook = {
        "id": "ob1", "bid_price": 100.0, "ask_price": 100.1, "spread_bps": 10.0,
        "total_depth_10bps_usd": 50_000.0, "observed_at": instant(cutoff_ms - 500),
        "imbalance_ratio": imbalance,
    }
    return build_entry_analysis_context(
        generation, candidate, feature,
        {tf: _trend_bars(count, interval[tf], cutoff_ms, instant) for tf, count in windows.items()},
        cutoff_at=cutoff, candidate_detected_at=instant(2), positions=[], orderbook=orderbook,
    )


def test_model_view_exposes_technical_labels_and_imbalance_when_inputs_suffice():
    from aletheia.trading.entry_analysis import ENTRY_WINDOW_BARS, technical_fields

    context = _technical_context(
        ENTRY_WINDOW_BARS, imbalance=0.25,
        feature_extra={"rsi_14": 72.0, "rsi_15m": 30.0, "rsi_4h": 50.0, "return_1h": 1.0, "oi_delta_1h": -2.0},
    )
    view: dict[str, Any] = entry_model_view(context)
    quant = view["quant_context"]

    assert quant["values"]["rsi_14"] == 72.0
    assert {key: quant["values"][key] for key in ("rsi_zone_15m", "rsi_zone_1h", "rsi_zone_4h", "oi_price_quadrant")} == {
        "rsi_zone_15m": "oversold", "rsi_zone_1h": "overbought", "rsi_zone_4h": "neutral",
        "oi_price_quadrant": "price_up_oi_down",
    }
    for timeframe in ("15m", "1h", "4h"):
        section = quant["technical"][timeframe]
        assert set(section["values"]) == set(technical_fields(timeframe)), timeframe
        assert section["unavailable_fields"] == []
        ref = f"technical:{timeframe}:{context.candle_windows[timeframe]['source_fingerprint']}"
        assert section["fact_refs"] == [ref]
        assert ref in view["reference_contract"]["current_market_fact_refs"]
    assert "ema_50" not in technical_fields("4h") and "trend_regime" in technical_fields("1h")
    one_hour = quant["technical"]["1h"]["values"]
    assert one_hour["ma_alignment"] == "bullish"
    assert one_hour["macd_line"] > 0 and one_hour["trend_regime"] == "trend_up"
    from aletheia.trading.indicators import macd

    series = macd([bar["close"] for bar in context.candle_windows["1h"]["bars"]])
    assert (one_hour["macd_hist"], one_hour["macd_hist_delta"]) == (series[-1][2], series[-1][2] - series[-2][2])
    assert one_hour["plus_di_14"] > one_hour["minus_di_14"]
    assert view["execution_context"]["imbalance_ratio"] == 0.25
    assert view["execution_context"]["unavailable_fields"] == []


def test_insufficient_bars_and_missing_book_imbalance_are_listed_not_defaulted():
    from aletheia.trading.entry_analysis import technical_fields

    context = _technical_context({"15m": 96}, imbalance=None, feature_extra={"rsi_15m": 50.0})
    view: dict[str, Any] = entry_model_view(context)
    quant = view["quant_context"]

    for timeframe in ("1h", "4h"):
        section = quant["technical"][timeframe]
        assert section["values"] == {} and section["fact_refs"] == []
        assert section["unavailable_fields"] == sorted(technical_fields(timeframe))
    assert {"rsi_14", "rsi_zone_1h", "rsi_zone_4h", "oi_price_quadrant"} <= set(quant["unavailable_fields"])
    assert not {"rsi_zone_1h", "oi_price_quadrant"} & set(quant["values"])
    assert quant["values"]["rsi_zone_15m"] == "neutral"
    assert "imbalance_ratio" not in view["execution_context"]
    assert view["execution_context"]["unavailable_fields"] == ["imbalance_ratio"]


def test_technical_section_marks_flat_window_unavailable_instead_of_neutral():
    from aletheia.trading.entry_analysis import technical_section

    bars = [{"ts": i, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0.0} for i in range(96)]
    section = technical_section("15m", {"availability": "available", "bars": bars, "source_fingerprint": "x"})
    # Zero-width band, zero volume, 0/0 DI and a zero histogram carry no reading.
    for field in ("bb_pct_b", "obv_normalized", "vwap_dev_pct", "adx_14", "macd_state", "trend_regime"):
        assert field in section["unavailable_fields"], field
        assert field not in section["values"]


@pytest.mark.parametrize(("value", "zone"), [
    (30.0, "oversold"), (30.01, "neutral"), (69.99, "neutral"), (70.0, "overbought"), (None, None),
])
def test_rsi_zone_thresholds(value, zone):
    from aletheia.trading.entry_analysis import rsi_zone

    assert rsi_zone(value) == zone


@pytest.mark.parametrize(("ret", "oi", "label"), [
    (0.2, 0.5, "price_up_oi_up"), (0.19, 5.0, "flat"), (5.0, -0.49, "flat"),
    (-0.2, 0.5, "price_down_oi_up"), (0.3, -0.6, "price_up_oi_down"), (-0.3, -0.6, "price_down_oi_down"),
    (None, 1.0, None),
])
def test_oi_price_quadrant_thresholds(ret, oi, label):
    from aletheia.trading.entry_analysis import oi_price_quadrant

    assert oi_price_quadrant(ret, oi) == label


def test_macd_ma_and_trend_labels_at_their_boundaries():
    from aletheia.trading.entry_analysis import ma_alignment, macd_state, trend_regime

    assert [macd_state(*args) for args in ((1, 0.1), (1, 0), (-1, -0.1), (-1, 0), (0, 1))] == [
        "bull_expanding", "bull_contracting", "bear_expanding", "bear_contracting", None,
    ]
    assert [ma_alignment(*args) for args in ((3, 2, 1), (1, 2, 3), (2, 3, 1), (2, 2, 1))] == [
        "bullish", "bearish", "mixed", "mixed",
    ]
    assert trend_regime(25, 30, 10, "bullish") == "trend_up"
    assert trend_regime(24.99, 30, 10, "bullish") == "transition"
    assert trend_regime(25, 10, 30, "bearish") == "trend_down"
    assert trend_regime(30, 30, 10, "mixed") == "transition"
    assert trend_regime(19.99, 30, 10, "bullish") == "range"
    assert trend_regime(20, 10, 30, "bullish") == "transition"

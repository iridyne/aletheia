"""Regression tests for the read-only pre-competition Universe integrity view."""

from aletheia.universe.baseline_integrity import (
    BASELINE_INTEGRITY_CONTRACT_VERSION,
    audit_universe_baseline,
    build_lossless_family_evidence,
    family_semantics,
)
from aletheia.universe.scanners import run_all_scanners


def _feature(**overrides):
    feature = {
        "market_type": "perp",
        "analysis_context": {"kind": "crypto"},
        "return_1h": 0.0,
        "return_4h": 0.0,
        "return_24h": 0.0,
        "return_15m": 0.0,
        "last_candle_range_atr_15m": 0.5,
        "relative_strength_24h": None,
        "relative_strength_4h": None,
        "rs_beta_adj_24h": None,
        "rsi_14": 50.0,
        "rsi_4h": 50.0,
        "atr_pct": 2.0,
        "atr_4h": 2.0,
        "bb_width_pct": 5.0,
        "bb_width_4h": 5.0,
        "bb_squeeze": False,
        "vol_ratio": 1.0,
        "taker_buy_ratio": None,
        "funding_rate": None,
        "oi_delta_1h": None,
        "oi_price_divergence": None,
    }
    feature.update(overrides)
    return feature


def test_audit_preserves_authority_output_exactly():
    feature = _feature(
        relative_strength_24h=7.0,
        relative_strength_4h=2.0,
        return_1h=5.0,
        return_4h=5.0,
        rsi_14=60.0,
        vol_ratio=3.0,
    )
    authority = run_all_scanners(dict(feature))
    audited = audit_universe_baseline(feature)

    assert audited["contract_version"] == BASELINE_INTEGRITY_CONTRACT_VERSION
    assert audited["authority"]["scanner_family"] == authority["scanner_family"]
    assert audited["authority"]["scanner_direction"] == authority["scanner_direction"]
    assert audited["authority"]["opportunity_score"] == authority["opportunity_score"]
    assert audited["authority"]["all_scores"] == authority["all_scores"]
    assert audited["frozen_control"]["selection_behavior_changed_by_audit"] is False


def test_non_winner_family_direction_and_reasons_are_not_lost():
    feature = _feature(
        relative_strength_24h=7.0,
        relative_strength_4h=2.0,
        return_1h=5.0,
        return_4h=5.0,
        rsi_14=60.0,
        vol_ratio=3.0,
    )
    audited = audit_universe_baseline(feature)

    assert audited["authority"]["scanner_family"] == "momentum"
    event = audited["family_evidence"]["event"]
    assert event["score"] > 0.0
    assert event["direction"] == "long"
    assert event["reasons"]
    assert audited["integrity"]["non_winner_direction_and_reasons_retained"] is True


def test_event_is_truthfully_marked_as_market_shock_proxy():
    semantics = family_semantics()["event"]
    evidence = build_lossless_family_evidence(
        _feature(return_1h=6.0, return_4h=6.0, vol_ratio=3.0)
    )["event"]

    assert semantics["display_name"] == "Market Shock / Event Proxy"
    assert semantics["semantic_role"] == "market_shock_proxy"
    assert semantics["true_external_event_evidence"] is False
    assert evidence["true_external_event_evidence"] is False
    assert {
        "news_provenance",
        "announcement_provenance",
        "social_attention_provenance",
    }.issubset(set(evidence["capability_gaps"]))


def test_derivatives_v1_capability_gaps_are_explicit():
    derivatives = family_semantics()["derivatives"]

    assert derivatives["semantic_role"] == "positioning_evidence_domain_proxy"
    assert set(derivatives["capability_gaps"]) == {
        "basis",
        "liquidation",
        "cross_venue_positioning",
    }


def test_family_semantics_expose_mixed_conceptual_layers():
    semantics = family_semantics()

    assert semantics["momentum"]["semantic_role"] == "directional_return_archetype"
    assert semantics["mean_reversion"]["semantic_role"] == "directional_return_archetype"
    assert semantics["volatility"]["semantic_role"] == "market_state_conditioned_directional_proxy"
    assert semantics["derivatives"]["semantic_role"] == "positioning_evidence_domain_proxy"
    assert semantics["event"]["semantic_role"] == "market_shock_proxy"
    assert semantics["short_bias"]["semantic_role"] == "directional_specialization"


def test_spot_derivatives_are_explicitly_not_applicable():
    evidence = build_lossless_family_evidence(
        _feature(
            market_type="spot",
            funding_rate=0.02,
            oi_delta_1h=25.0,
            return_1h=3.0,
        )
    )["derivatives"]

    assert evidence["applicability"] == "not_applicable"
    assert evidence["score"] == 0.0
    assert evidence["direction"] is None
    assert evidence["reasons"] == []


def test_stock_linked_short_bias_is_explicitly_not_applicable():
    evidence = build_lossless_family_evidence(
        _feature(
            analysis_context={"kind": "stock_linked_perp"},
            return_4h=-4.0,
            return_24h=-6.0,
            relative_strength_24h=-5.0,
            funding_rate=0.005,
            rsi_14=40.0,
        )
    )["short_bias"]

    assert evidence["applicability"] == "not_applicable"
    assert evidence["score"] == 0.0
    assert evidence["direction"] is None
    assert evidence["reasons"] == []


def test_strategy_upgrades_remain_unresolved_competition_questions():
    audited = audit_universe_baseline(_feature())

    assert audited["frozen_control"]["absolute_step_score_authority"] is True
    assert audited["frozen_control"]["relative_rank_authority"] is False
    assert audited["competition_questions"] == {
        "relative_rank_better_than_step_score": "unresolved",
        "complex_universe_beats_simple_momentum": "unresolved",
        "basis_liquidation_cross_venue_add_derivatives_lift": "unresolved",
    }

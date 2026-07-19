"""Versioned attribution for the Opportunity information-integrity repair."""

from __future__ import annotations

from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


POLICY_EPOCH_LABEL = "opportunity_information_integrity/v1"
_EPOCH_KIND = "opportunity_information_integrity"
_EFFECTIVE_VALUES = {
    "version": "v1",
    "observer_prefilter": "structured_1h_rsi_atr_pct",
    "oi_baseline_minutes": 60,
    "oi_baseline_band_minutes": [45, 75],
    "coinalyze_oi_unit": "usd_required",
    "nonfinite_market_values": "unavailable",
    "spot_derivatives_context": False,
    "regime_market_cap_window_days": 7,
    "regime_atr": "completed_daily_true_range",
    "prediction_question_polarity": "polarity-v1",
    "prediction_asset_identity": "exact-single-asset",
    "news_entity_matching": "exact-ticker-or-asset-name-v1",
    "news_decision_gate": "analyzed_relevance_gte_0_6",
    "news_publication_time": "known-not-future",
    "opportunity_evidence_manifest": "opportunity-information-integrity-v1",
    "operator_evidence_manifest": "operator-candidate-v3",
    "manifest_sources": "rendered-only",
    "serenity_exact_carrier_context": True,
    "serenity_partial_theme_retry": True,
    "review_complete_rationale": True,
    "epoch_activation": "affected_producer_entry",
}
_AFFECTED_PATHS = (
    "market_observer_pre_filter",
    "cross_venue_oi_delta",
    "spot_derivatives_context",
    "universe_regime_detection",
    "prediction_divergence_direction",
    "news_evidence_quality",
    "opportunity_source_manifest",
    "operator_decision_evidence_manifest",
    "serenity_pending_context",
    "serenity_promotion_continuity",
    "post_trade_rationale_context",
)


def ensure_opportunity_information_policy_epoch() -> str:
    """Return the durable epoch id, rotating only when effective behavior changes."""
    epoch = build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=False,
    )
    return persist_policy_epoch(epoch)


def opportunity_information_policy_context() -> dict:
    """Return the exact non-secret effective values used by this repair."""
    return dict(_EFFECTIVE_VALUES)

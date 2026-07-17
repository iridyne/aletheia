"""Policy epoch for the VergeX/Hyperliquid evidence utilization change."""

from __future__ import annotations

from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


_EPOCH_KIND = "system_operator_vergex_utilization"
_EFFECTIVE_VALUES = {
    "version": "v1",
    "funding_units": "raw_fraction_0.0001_equals_0.01_percent",
    "candidate_timeframes": ["15m", "1h", "4h"],
    "atr_no_chase_ratio": 1.5,
    "hyperliquid_context": ["funding", "predicted_funding", "open_interest"],
    "position_x_authority": "observation_only",
    "position_x_min_shadow_window_days": 14,
}
_AFFECTED_PATHS = (
    "universe_scanner_funding_semantics",
    "universe_atr_chase_guard",
    "market_observer_cross_venue_derivatives_context",
    "system_operator_candidate_technical_context",
)


def ensure_vergex_utilization_policy_epoch() -> str:
    return persist_policy_epoch(build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=False,
    ))

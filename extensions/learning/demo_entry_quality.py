"""Policy epoch for the exchange-demo entry quality brake."""

from __future__ import annotations

POLICY_EPOCH_LABEL = "demo_entry_quality/v5"
_EPOCH_KIND = "demo_entry_quality"
_EFFECTIVE_VALUES = {
    "version": "v4",
    "applies_to": ["bitget_demo", "okx_demo", "binance_testnet"],
    "live_family_gate_unchanged": True,
    "open_positions_unchanged": True,
    "min_regime_confidence_to_enter": 0.60,
    "sideways_standdown": True,
    "mean_reversion_sideways_standdown": True,
    "family_min_samples": 20,
    "family_min_expectancy": 0.0,
    "insufficient_family_evidence_fail_open": True,
    "authority_read_failure_fail_closed": True,
    "reverse_after_stop_hours": 24,
    # `sideways_stop_mult` was removed with the fixed stop floor it described
    # (#3777): the demo entry-quality brake no longer scales a stop distance.
}
_AFFECTED_PATHS = (
    "paper_ranker_demo_entry_quality",
    "risk_engine_demo_entry_quality",
)


def ensure_demo_entry_quality_policy_epoch() -> str:
    """Return the durable epoch id, rotating when the policy definition changes."""
    from extensions.learning.policy_epochs import build_policy_epoch, persist_policy_epoch

    epoch = build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=True,
    )
    return persist_policy_epoch(epoch)


def demo_entry_quality_policy_context() -> dict:
    return dict(_EFFECTIVE_VALUES)

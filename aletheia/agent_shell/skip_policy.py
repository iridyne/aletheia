"""Versioned B1 policy attribution for System Operator skip remediation."""

from __future__ import annotations

from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


_EPOCH_KIND = "system_operator_skip_remediation"
_EFFECTIVE_VALUES = {
    "version": "b1",
    "aggregate_signal_health_candidate_veto": False,
    "zero_pending_heartbeat_trading_evaluation": False,
    "aggregate_signal_health_config_tightening": False,
}
_AFFECTED_PATHS = (
    "system_operator_candidate_context",
    "system_operator_heartbeat_evaluation",
    "operator_loop_aggregate_signal_diagnosis",
)


def ensure_skip_remediation_policy_epoch() -> str:
    """Return the durable B1 policy epoch, rotating only if its policy changes."""
    epoch = build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=False,
    )
    return persist_policy_epoch(epoch)

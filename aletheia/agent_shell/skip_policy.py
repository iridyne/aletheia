"""Versioned B1 policy attribution for System Operator skip remediation."""

from __future__ import annotations

from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


_EPOCH_KIND = "system_operator_skip_remediation"
_EFFECTIVE_VALUES = {
    "version": "b1.2",
    "aggregate_signal_health_candidate_veto": False,
    "zero_pending_heartbeat_trading_evaluation": False,
    "aggregate_signal_health_config_tightening": False,
    # ``medium`` is the neutral, risk-aware candidate supply tier.  It still
    # excludes speculative reads and still passes every deterministic guard,
    # System Operator conviction, and Risk Engine check.  ``high`` remains a
    # useful quality label/priority, but must not strand the pipeline when an
    # old automatic tightening made it a universal hard gate.
    "ranker_neutral_confidence_floor": "medium",
    "auto_tightened_confidence_supply_recovery": True,
    "manual_high_confidence_preserved": True,
    # B1.2: a direct model skip is no longer an asymmetric one-pass veto.
    # One bounded counterfactual confirmation must provide typed,
    # candidate-specific adverse evidence. Neutral insufficiency defers.
    "direct_skip_confirmation_required": True,
    "insufficient_evidence_terminal_skip": False,
    "rank_priority_is_terminal_veto": False,
    "unchanged_context_defer_window_minutes": 120,
}
_AFFECTED_PATHS = (
    "system_operator_candidate_context",
    "system_operator_heartbeat_evaluation",
    "operator_loop_aggregate_signal_diagnosis",
    "paper_ranker_confidence_gate",
    "operator_loop_candidate_supply_recovery",
)


def ensure_skip_remediation_policy_epoch() -> str:
    """Return the durable B1.2 policy epoch, rotating only if policy changes."""
    epoch = build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=False,
    )
    return persist_policy_epoch(epoch)


def skip_policy_context() -> dict:
    """Return the exact non-secret effective values used by the decision path."""
    return dict(_EFFECTIVE_VALUES)

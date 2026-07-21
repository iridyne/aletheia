"""Versioned attribution for post-trade review coverage semantics."""

from __future__ import annotations

from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


POLICY_EPOCH_LABEL = "post_trade_review_coverage/v2"
_EPOCH_KIND = "post_trade_review_coverage"
_EFFECTIVE_VALUES = {
    "version": "v2",
    "outcome_cohort": "eligible_strategy_outcomes",
    "coverage_identity": "strategy_id_and_position_id_tag",
    "review_lifecycle": "active_only",
    "api_operator_truth": "shared_coverage_read_model",
    "epoch_activation": "post_trade_reviewer_entry",
}
_AFFECTED_PATHS = (
    "post_trade_reviewer_idempotency",
    "post_trade_review_backlog",
    "operator_review_delegation",
    "post_trade_review_learning_input",
)


def ensure_post_trade_review_policy_epoch() -> str:
    """Return the durable v2 epoch id, rotating only on effective behavior changes."""
    epoch = build_policy_epoch(
        _EPOCH_KIND,
        _EFFECTIVE_VALUES,
        affected_path_ids=_AFFECTED_PATHS,
        safety_required=False,
    )
    return persist_policy_epoch(epoch)


def post_trade_review_policy_context() -> dict:
    """Return the exact non-secret effective values used by this repair."""
    return dict(_EFFECTIVE_VALUES)

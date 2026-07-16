"""B2 policy epoch and context attachment for candidate evidence alignment."""

from __future__ import annotations

from aletheia.analytics.pi_evidence import build_b2_evidence_summary
from aletheia.learning.policy_epochs import build_policy_epoch, persist_policy_epoch


_EPOCH_KIND = "system_operator_b2_candidate_evidence"
_EFFECTIVE_VALUES = {
    "version": "b2",
    "source": "same_epoch_operator_outcomes",
    "selection_tier": "ranker_enqueued",
    "score_buckets": ["low_score", "medium_score", "high_score"],
    "horizon": "24h",
    "min_net_samples": 20,
    "min_outcome_coverage": 0.70,
    "shrinkage_prior_mean_pct": 0.0,
    "shrinkage_k": 20.0,
    "insufficient_evidence": "neutral_not_directional_veto",
    "shared_roles": ["assessment", "bull", "bear"],
}
_AFFECTED_PATHS = (
    "system_operator_assessment_evidence",
    "system_operator_bull_debate_evidence",
    "system_operator_bear_debate_evidence",
)


def ensure_b2_candidate_evidence_policy_epoch() -> str:
    epoch = build_policy_epoch(
        _EPOCH_KIND, _EFFECTIVE_VALUES, affected_path_ids=_AFFECTED_PATHS, safety_required=False,
    )
    return persist_policy_epoch(epoch)


def attach_b2_candidate_evidence(ctx: dict, *, policy_epoch_id: str) -> None:
    """Attach one cohort summary to each queued candidate, without changing gates."""
    layer2 = ctx.get("layer2") or {}
    for candidate in layer2.get("pending_signals") or []:
        candidate["b2_evidence"] = build_b2_evidence_summary(
            candidate, policy_epoch_id=policy_epoch_id,
        )
    ctx["b2_evidence_policy_epoch_id"] = policy_epoch_id

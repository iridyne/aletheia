"""Prospective assignment and fail-closed numeric Learning influence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from uuid import uuid4

from aletheia.config.loader import load_config
from aletheia.learning.hypotheses import hypothesis_fingerprint
from aletheia.learning.ledger import (
    get_record,
    get_hypothesis_by_fingerprint,
    get_exposure_by_dedup_key,
    get_open_experiment,
    insert_experiment,
    insert_exposure,
    insert_hypothesis,
)
from aletheia.learning.models import LearningExperiment, LearningExposure, LearningHypothesis
from aletheia.learning.registry import get_learning_path
from aletheia.learning.policy_epochs import build_policy_epoch


@dataclass(frozen=True)
class InfluenceDecision:
    value: float
    applied: bool
    assignment: str
    reason: str
    hypothesis_id: str | None = None
    experiment_id: str | None = None
    exposure_id: str | None = None


def stable_assignment(experiment: LearningExperiment, candidate_key: str) -> str:
    raw = f"{experiment.experiment_id}:{experiment.assignment_salt}:{candidate_key}"
    fraction = int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16], 16) / float(0xFFFFFFFFFFFFFFFF)
    return "treatment" if fraction < experiment.allocation_pct else "control"


def _learning_gate_reason() -> str | None:
    from aletheia.learning.control import learning_influence_gate_reason
    return learning_influence_gate_reason()


def _ensure_experiment(
    *, path_id: str, scope: dict, action: dict, source_cohort: str,
    target_horizon: str, consumer: str,
) -> tuple[LearningHypothesis, LearningExperiment]:
    path = get_learning_path(path_id)
    fingerprint = hypothesis_fingerprint(
        path_id=path_id, scope=scope, action=action,
        target_horizon=target_horizon, source_cohort=source_cohort,
    )
    row = get_hypothesis_by_fingerprint(fingerprint)
    if row is None:
        hypothesis = LearningHypothesis(
            fingerprint=fingerprint,
            cohort_id=hashlib.sha256(source_cohort.encode("utf-8")).hexdigest()[:16],
            path_id=path_id,
            overlap_group=path.overlap_group,
            source_type=source_cohort,
            scope_json=json.dumps(scope, sort_keys=True),
            action_json=json.dumps(action, sort_keys=True),
            consumer=consumer,
            target_horizon=target_horizon,
            status="shadow",
            harm_budget_json=json.dumps({"max_negative_delta": 0.0}),
        )
        insert_hypothesis(hypothesis)
        row = get_hypothesis_by_fingerprint(fingerprint)
    hypothesis = LearningHypothesis(**row)

    experiment_row = get_open_experiment(hypothesis.hypothesis_id)
    if experiment_row is None:
        experiment = LearningExperiment(
            hypothesis_id=hypothesis.hypothesis_id,
            assignment_salt=uuid4().hex,
            allocation_pct=0.5,
            min_samples_per_arm=50,
            min_market_days=7,
            promotion_gate_json=json.dumps({"prospective_only": True, "net_of_costs": True}),
            harm_gate_json=json.dumps({"negative_holdout_refreeze": True}),
        )
        insert_experiment(experiment)
        experiment_row = get_open_experiment(hypothesis.hypothesis_id)
    return hypothesis, LearningExperiment(**experiment_row)


def _verdict_authorizes(experiment: LearningExperiment, policy_epoch: str) -> bool:
    """Compatibility shim for callers; all authority lives in one resolver."""
    from aletheia.learning.authority import resolve_learning_authority
    hypothesis = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis is None:
        return False
    return resolve_learning_authority(
        str(hypothesis.get("path_id") or ""), experiment, policy_epoch,
    ).authorized


def _code_fingerprint() -> str | None:
    """Commit the running process booted on (cached module global — no per-call
    subprocess, safe in the per-candidate scoring loop). None in tests / CLI where
    the boot commit was never captured."""
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit
        return get_boot_commit()
    except Exception:
        return None


def _policy_epoch(path_id: str) -> str:
    try:
        cfg = load_config()
        values = {
            "autonomous_learning": cfg.agent.autonomous_learning,
            "permission_mode": cfg.agent.permission_mode,
            "scanner_weights": cfg.universe.scanner_weights,
            "scanner_regime_scoring": cfg.universe.scanner_regime_scoring,
            "risk": cfg.risk.model_dump(),
            "trading": cfg.trading.model_dump(),
            # P1-1: rotate the epoch when the running code changes (captured on
            # restart), so a verdict from an earlier build cannot authorize the new one
            # (P0-4 requires an exact epoch match). None in tests → stable.
            "code_commit": _code_fingerprint(),
            # Lifecycle and paper-influence settings change the effective
            # consumer policy, so a successor cannot inherit authority across
            # a settings change.
            "learning": cfg.learning.model_dump(),
        }
        # P1-1: universe scoring/allocation config materially changes which candidates
        # win — it is path-relevant behavior the epoch previously ignored, so a change
        # to it must rotate the universe epoch (and invalidate prior universe verdicts).
        if path_id == "universe_family_calibration":
            u = cfg.universe
            values["universe_behavior"] = {
                "family_quota": u.family_quota,
                "family_quota_by_regime": u.family_quota_by_regime,
                "explore_ratio": u.explore_ratio,
                "max_candidates": u.max_candidates,
                "core_pool_size": u.core_pool_size,
                "min_volume_24h_usdt": u.min_volume_24h_usdt,
                "max_spread_bps": u.max_spread_bps,
                "min_depth_10bps_usd": u.min_depth_10bps_usd,
            }
    except Exception:
        values = {"config": "unavailable"}
    from aletheia.learning.policy_epochs import persist_policy_epoch
    epoch = build_policy_epoch(
        f"decision_policy:{path_id}", values, affected_path_ids=(path_id,)
    )
    return persist_policy_epoch(epoch)


def apply_numeric_treatment(
    *, path_id: str, scope: dict, action: dict, source_cohort: str,
    target_horizon: str, candidate_key: str | None, baseline: float,
    treatment: float, consumer: str, consumer_stage: str,
    outcome_candidate_key: str | None = None, opportunity_id: str | None = None,
    manifest: dict | None = None,
    assignment_key: str | None = None,
) -> InfluenceDecision:
    """Record prospective exposure and apply only an authorized paper treatment."""
    gate_reason = _learning_gate_reason()
    if gate_reason is not None:
        return InfluenceDecision(float(baseline), False, "disabled", gate_reason)
    if not candidate_key:
        return InfluenceDecision(float(baseline), False, "unassigned", "missing_candidate_key")

    hypothesis, experiment = _ensure_experiment(
        path_id=path_id, scope=scope, action=action, source_cohort=source_cohort,
        target_horizon=target_horizon, consumer=consumer,
    )
    assignment = stable_assignment(experiment, assignment_key or candidate_key)
    policy_epoch = _policy_epoch(path_id)
    from aletheia.learning.authority import resolve_learning_authority
    authority = resolve_learning_authority(path_id, experiment, policy_epoch)
    authorized = authority.authorized
    applied = authorized and assignment == "treatment"
    value = float(treatment if applied else baseline)
    stage = experiment.stage if authorized else "shadow_refrozen"
    dedup_key = f"{experiment.experiment_id}:{candidate_key}:{stage}:{policy_epoch}"
    exposure = LearningExposure(
        dedup_key=dedup_key,
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        assignment=assignment,
        consumer=consumer,
        consumer_stage=f"{consumer_stage}:{stage}" if consumer_stage else stage,
        path_id=path_id,
        overlap_group=get_learning_path(path_id).overlap_group,
        candidate_key=candidate_key,
        opportunity_id=opportunity_id,
        before_json=json.dumps({"value": float(baseline)}, sort_keys=True),
        after_json=json.dumps({"value": value}, sort_keys=True),
        manifest_json=json.dumps({
            **(manifest or {}),
            "proposed_value": float(treatment),
            "authority_applied": applied,
            "authority_reason": authority.reason,
        }, sort_keys=True),
        outcome_candidate_key=outcome_candidate_key,
        outcome_source="fixed_horizon_candidate" if outcome_candidate_key else None,
        experiment_stage=experiment.stage,
        concurrent_policy_epoch=policy_epoch,
        # P1-1: attribute the exposure to the running build so evidence produced by
        # different code versions is distinguishable (the fingerprint is also folded
        # into policy_epoch above, so a rebuild rotates the epoch on restart).
        code_commit=_code_fingerprint(),
    )
    inserted = insert_exposure(exposure)
    if not inserted:
        stored = get_exposure_by_dedup_key(dedup_key)
        if stored:
            exposure = LearningExposure(**stored)
    return InfluenceDecision(
        value, applied, assignment,
        authority.reason if applied else f"baseline:{authority.reason}",
        hypothesis.hypothesis_id, experiment.experiment_id, exposure.exposure_id,
    )


def apply_boolean_treatment(
    *, path_id: str, scope: dict, action: dict, source_cohort: str,
    target_horizon: str, candidate_key: str | None, consumer: str,
    consumer_stage: str, outcome_candidate_key: str | None = None,
    opportunity_id: str | None = None, manifest: dict | None = None,
) -> InfluenceDecision:
    """Apply one default-off boolean treatment through the numeric authority seam.

    Prompt injection and learned proposal filters are binary effects, but they must
    use the same prospective assignment, exact-path allowlist, policy epoch,
    authority resolver, and immutable exposure ledger as numeric treatments.  A
    control or refrozen candidate therefore remains ``0.0``/baseline; only an
    authorized treatment candidate becomes ``1.0``/applied.
    """
    return apply_numeric_treatment(
        path_id=path_id,
        scope=scope,
        action=action,
        source_cohort=source_cohort,
        target_horizon=target_horizon,
        candidate_key=candidate_key,
        baseline=0.0,
        treatment=1.0,
        consumer=consumer,
        consumer_stage=consumer_stage,
        outcome_candidate_key=outcome_candidate_key,
        opportunity_id=opportunity_id,
        manifest=manifest,
    )

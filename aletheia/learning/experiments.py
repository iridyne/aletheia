"""Prospective assignment and fail-closed numeric Learning influence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from aletheia.config.loader import load_config
from aletheia.learning.hypotheses import hypothesis_fingerprint
from aletheia.learning.ledger import (
    get_hypothesis_by_fingerprint,
    get_exposure_by_dedup_key,
    get_latest_verdict,
    linked_settled_exposure_counts,
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


def _learning_enabled() -> bool:
    try:
        return bool(load_config().agent.autonomous_learning)
    except Exception:
        return False


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
    if experiment.status != "active" or not experiment.stage.startswith("paper"):
        return False
    verdict = get_latest_verdict(experiment.experiment_id)
    if not verdict or verdict.get("verdict") != "promote":
        return False
    if min(int(verdict.get("unique_treatment_n") or 0), int(verdict.get("unique_control_n") or 0)) < experiment.min_samples_per_arm:
        return False
    linked = linked_settled_exposure_counts(
        experiment.experiment_id, policy_epoch=policy_epoch,
    )
    if min(linked["treatment"], linked["control"]) < experiment.min_samples_per_arm:
        return False
    try:
        start = datetime.fromisoformat(str(verdict["window_start"]).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(verdict["window_end"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return False
    if (end - start).days < experiment.min_market_days:
        return False
    try:
        harm = json.loads(verdict.get("harm_metrics_json") or "{}")
    except (TypeError, ValueError):
        return False
    return not bool(harm.get("breached"))


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
) -> InfluenceDecision:
    """Record prospective exposure and apply only an authorized paper treatment."""
    if not _learning_enabled():
        return InfluenceDecision(float(baseline), False, "disabled", "autonomous_learning_disabled")
    if not candidate_key:
        return InfluenceDecision(float(baseline), False, "unassigned", "missing_candidate_key")

    hypothesis, experiment = _ensure_experiment(
        path_id=path_id, scope=scope, action=action, source_cohort=source_cohort,
        target_horizon=target_horizon, consumer=consumer,
    )
    assignment = stable_assignment(experiment, candidate_key)
    policy_epoch = _policy_epoch(path_id)
    authorized = _verdict_authorizes(experiment, policy_epoch)
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
            "authority_reason": "prospective_verdict" if authorized else "refrozen_without_prospective_verdict",
        }, sort_keys=True),
        outcome_candidate_key=outcome_candidate_key,
        outcome_source="fixed_horizon_candidate" if outcome_candidate_key else None,
        concurrent_policy_epoch=policy_epoch,
    )
    inserted = insert_exposure(exposure)
    if not inserted:
        stored = get_exposure_by_dedup_key(dedup_key)
        if stored:
            exposure = LearningExposure(**stored)
    return InfluenceDecision(
        value, applied, assignment,
        "prospective_verdict" if applied else "baseline_refreeze",
        hypothesis.hypothesis_id, experiment.experiment_id, exposure.exposure_id,
    )

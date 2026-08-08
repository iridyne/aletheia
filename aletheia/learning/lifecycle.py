"""Durable, idempotent lifecycle transitions for AT-01 measurements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any

from aletheia.config.loader import load_config
from aletheia.learning.authority import (
    MEASUREMENT_VERDICTS,
    measurement_harm_block_reason,
    measurement_identity_reason,
    measurement_intervention_block_reason,
    resolve_learning_authority,
)
from aletheia.learning.cohorts import SUPPORTED_PATH_ID, SUPPORTED_PATH_IDS
from aletheia.learning.behavior_contracts import current_behavior_contract_hash
from aletheia.learning.control import global_learning_refreeze_reason
from aletheia.learning.ledger import (
    count_unmirrored_lifecycle_events,
    get_latest_measurement_verdict,
    get_lifecycle_event_by_dedup_key,
    get_record,
    insert_lifecycle_event,
    insert_experiment,
    list_unmirrored_lifecycle_events,
    list_open_experiments,
    list_lifecycle_events,
    update_lifecycle_audit_mirror,
)
from aletheia.learning.models import (
    LearningExperiment,
    LearningHypothesis,
    LearningLifecycleEvent,
)
from aletheia.learning.experiments import _policy_epoch, current_policy_epoch
from aletheia.learning.verdicts import METRIC_VERSION
from aletheia.learning.fixed_look import (
    InferenceContractError,
    get_consumed_fixed_look_verdict,
    inference_authority_reason,
    register_fixed_look_decision,
)
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db

logger = logging.getLogger("aletheia.learning.lifecycle")


STAGE_SUCCESSOR = {
    "shadow": "paper_10",
    "paper_10": "paper_25",
    "paper_25": "paper_50",
    "paper_50": "paper_active",
}
STAGE_ALLOCATION = {
    "paper_10": 0.10,
    "paper_25": 0.25,
    "paper_50": 0.50,
}


def _lifecycle_audit_payload(event: LearningLifecycleEvent) -> dict[str, Any]:
    return {
        "id": f"learning-lifecycle:{event.event_id}",
        "actor": event.audit_actor,
        "source": event.audit_source,
        "event_type": "learning_lifecycle_transition",
        "entity_type": "learning_experiment",
        "entity_id": event.experiment_id,
        "state_change": event.action,
        "message": event.reason,
        "metadata": event.model_dump(
            mode="json",
            exclude={
                "audit_mirror_status",
                "audit_mirrored_at",
                "audit_failure_reason",
            },
        ),
        "timestamp": event.created_at,
    }
LIFECYCLE_ACTIONS = frozenset({
    "admit_trial", "promote_stage", "hold", "refreeze", "invalidate",
})


@dataclass(frozen=True)
class LifecycleResult:
    action: str
    recommended_action: str
    reason: str
    experiment_id: str
    successor_experiment_id: str | None = None
    verdict_id: str | None = None


def measurement_to_lifecycle_action(verdict: str) -> str:
    mapping = {
        "positive": "promote_stage",
        "neutral": "hold",
        "insufficient_data": "hold",
        "negative": "refreeze",
        "invalid": "invalidate",
    }
    try:
        return mapping[verdict]
    except KeyError as exc:
        raise ValueError(f"unsupported measurement verdict: {verdict!r}") from exc


def process_lifecycle(experiment_id: str, *, current_policy_epoch: str) -> LifecycleResult:
    """Apply at most one durable lifecycle action for one current experiment.

    The database transition is transactional and event-deduplicated.  A caller
    may retry after a crash at any point without manufacturing a successor.
    """
    experiment_row = get_record("learning_experiments", experiment_id)
    if experiment_row is None:
        return LifecycleResult("hold", "hold", "experiment_not_found", experiment_id)
    experiment = LearningExperiment(**experiment_row)
    hypothesis_row = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis_row is None:
        return LifecycleResult("hold", "hold", "hypothesis_not_found", experiment_id)
    hypothesis = LearningHypothesis(**hypothesis_row)
    if hypothesis.path_id not in SUPPORTED_PATH_IDS:
        return LifecycleResult("hold", "hold", "unsupported_learning_path", experiment_id)
    try:
        expected_behavior_contract_hash = current_behavior_contract_hash(
            path_id=hypothesis.path_id,
            policy_epoch=current_policy_epoch,
            target_horizon=hypothesis.target_horizon,
            outcome_contract_version="v1",
            metric_version=METRIC_VERSION,
        )
    except (KeyError, ValueError):
        return LifecycleResult(
            "hold", "hold", "behavior_contract_unavailable", experiment_id,
        )
    verdict = get_latest_measurement_verdict(
        experiment.experiment_id,
        policy_epoch=current_policy_epoch,
        stage=experiment.stage,
        hypothesis_id=hypothesis.hypothesis_id,
        target_horizon=hypothesis.target_horizon,
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        behavior_contract_hash=expected_behavior_contract_hash,
        inference_role="monitoring",
    )
    if verdict is None:
        verdict = get_latest_measurement_verdict(
            experiment.experiment_id,
            inference_role="monitoring",
        )
    if verdict is None:
        return LifecycleResult("hold", "hold", "no_current_stage_measurement", experiment_id)
    if experiment.stage.startswith("paper_"):
        latest_harm_reason = measurement_harm_block_reason(verdict)
        latest_measurement = str(verdict.get("verdict") or "")
        if (
            latest_measurement not in {"negative", "invalid"}
            and latest_harm_reason is None
        ):
            consumed = get_consumed_fixed_look_verdict(experiment.experiment_id)
            if consumed is not None:
                verdict = consumed
    measurement = str(verdict.get("verdict") or "")
    if measurement not in MEASUREMENT_VERDICTS:
        return LifecycleResult("hold", "hold", "unknown_measurement_verdict", experiment_id)
    recommended = measurement_to_lifecycle_action(measurement)
    harm_reason = measurement_harm_block_reason(verdict)
    if harm_reason == "harm_gate_breached" and recommended != "invalidate":
        recommended = "refreeze"
    identity_reason = measurement_identity_reason(
        verdict, experiment.stage, current_policy_epoch,
        expected_horizon=hypothesis.target_horizon,
        expected_behavior_contract_hash=expected_behavior_contract_hash,
        expected_hypothesis_id=hypothesis.hypothesis_id,
    )
    stale_safety_reasons = {
        "stale_policy_epoch",
        "horizon_mismatch",
        "outcome_contract_mismatch",
        "metric_version_mismatch",
        "behavior_contract_mismatch",
    }
    if identity_reason and not (
        recommended in {"refreeze", "invalidate"}
        and identity_reason in stale_safety_reasons
    ):
        return LifecycleResult("hold", recommended, identity_reason, experiment_id, verdict_id=verdict["verdict_id"])

    # A harm breach must never suppress the safety action it is evidence for.
    # It blocks every path that grants or extends authority, and nothing else:
    # holding a refreeze/invalidate because the result was harmful exempted the
    # worst experiments from being stopped at all.
    if recommended not in {"refreeze", "invalidate"}:
        if harm_reason:
            return LifecycleResult(
                "hold", recommended, harm_reason, experiment_id,
                verdict_id=verdict["verdict_id"],
            )

    # A no-intervention shadow can only recommend a separate owner action. The
    # scheduled controller must never cross the shadow -> paper authority boundary.
    if (
        experiment.stage == "shadow"
        and measurement == "insufficient_data"
        and str(verdict.get("reason") or "") == "no_actual_intervention"
    ):
        return LifecycleResult(
            "hold", "admit_trial", "shadow_requires_explicit_trial_admission",
            experiment_id, verdict_id=str(verdict["verdict_id"]),
        )

    if recommended == "hold":
        return LifecycleResult("hold", "hold", f"measurement_{measurement}", experiment_id, verdict_id=verdict["verdict_id"])
    if recommended in {"refreeze", "invalidate"}:
        return _apply_safety_action(
            experiment,
            hypothesis,
            verdict,
            recommended,
            current_policy_epoch,
            reason=("stale_safety_measurement" if identity_reason else None),
        )
    if experiment.stage == "shadow":
        return LifecycleResult(
            "hold", "admit_trial", "shadow_requires_explicit_trial_admission",
            experiment_id, verdict_id=verdict["verdict_id"],
        )
    if experiment.stage not in STAGE_SUCCESSOR:
        return LifecycleResult("hold", "promote_stage", "terminal_stage", experiment_id, verdict_id=verdict["verdict_id"])
    intervention_reason = measurement_intervention_block_reason(
        verdict,
        required_per_arm=experiment.min_samples_per_arm,
    )
    if intervention_reason:
        return LifecycleResult(
            "hold",
            "promote_stage",
            intervention_reason,
            experiment_id,
            verdict_id=verdict["verdict_id"],
        )
    inference_reason = inference_authority_reason(verdict)
    if inference_reason:
        return LifecycleResult(
            "hold",
            "promote_stage",
            inference_reason,
            experiment_id,
            verdict_id=verdict["verdict_id"],
        )

    gate_reason = _promotion_gate_reason(hypothesis.path_id)
    if gate_reason == "lifecycle_automation_disabled":
        return LifecycleResult("hold", "promote_stage", gate_reason, experiment_id, verdict_id=verdict["verdict_id"])
    if gate_reason is not None:
        return _record_blocked_recommendation(experiment, hypothesis, verdict, current_policy_epoch, gate_reason)
    return _promote_one_stage(experiment, hypothesis, verdict, current_policy_epoch)


def admit_shadow_trial(
    experiment_id: str,
    *,
    verdict_id: str,
    current_policy_epoch: str,
    admitted_by: str,
    reason: str,
) -> LifecycleResult:
    """Remain fail closed until a real authenticated owner mutation route exists."""
    del current_policy_epoch, admitted_by, reason
    return LifecycleResult(
        "hold",
        "admit_trial",
        "authenticated_owner_admission_unavailable",
        experiment_id,
        verdict_id=verdict_id,
    )


def _execute_trial_admission(
    experiment_id: str,
    *,
    verdict_id: str,
    expected_policy_epoch: str,
    reason: str,
) -> LifecycleResult:
    """Shadow-to-paper_10 transition callable only from the protected owner route.

    admit_shadow_trial() remains fail-closed for all other callers (spec LE-L04).
    The current epoch is derived server-side; caller input is optimistic
    concurrency evidence only and can never define measurement identity.
    """
    experiment_row = get_record("learning_experiments", experiment_id)
    if experiment_row is None:
        return LifecycleResult("hold", "admit_trial", "experiment_not_found", experiment_id, verdict_id=verdict_id)
    experiment = LearningExperiment(**experiment_row)
    hypothesis_row = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis_row is None:
        return LifecycleResult("hold", "admit_trial", "hypothesis_not_found", experiment_id, verdict_id=verdict_id)
    hypothesis = LearningHypothesis(**hypothesis_row)
    if hypothesis.path_id not in SUPPORTED_PATH_IDS:
        return LifecycleResult("hold", "admit_trial", "unsupported_learning_path", experiment_id, verdict_id=verdict_id)

    dedup_key = f"{verdict_id}:shadow:admit_trial"
    existing = get_lifecycle_event_by_dedup_key(dedup_key)
    if existing is not None:
        if str(existing.get("experiment_id") or "") != experiment_id:
            return LifecycleResult(
                "hold",
                "admit_trial",
                "verdict_experiment_mismatch",
                experiment_id,
                verdict_id=verdict_id,
            )
        return LifecycleResult(
            "admit_trial",
            "admit_trial",
            "already_transitioned",
            experiment.experiment_id,
            existing.get("successor_experiment_id"),
            verdict_id,
        )
    if experiment.stage != "shadow":
        return LifecycleResult("hold", "admit_trial", "experiment_not_shadow_stage", experiment_id, verdict_id=verdict_id)
    if experiment.status != "active":
        return LifecycleResult("hold", "admit_trial", "experiment_not_active", experiment_id, verdict_id=verdict_id)

    current_policy_epoch = _policy_epoch(hypothesis.path_id)
    if expected_policy_epoch != current_policy_epoch:
        return LifecycleResult(
            "hold",
            "admit_trial",
            "policy_epoch_changed",
            experiment_id,
            verdict_id=verdict_id,
        )
    gate_reason = trial_admission_gate_reason(hypothesis.path_id)
    if gate_reason is not None:
        return LifecycleResult("hold", "admit_trial", gate_reason, experiment_id, verdict_id=verdict_id)

    try:
        expected_behavior_contract_hash = current_behavior_contract_hash(
            path_id=hypothesis.path_id,
            policy_epoch=current_policy_epoch,
            target_horizon=hypothesis.target_horizon,
            outcome_contract_version="v1",
            metric_version=METRIC_VERSION,
        )
    except (KeyError, ValueError):
        return LifecycleResult(
            "hold", "admit_trial", "behavior_contract_unavailable",
            experiment_id, verdict_id=verdict_id,
        )

    verdict = get_latest_measurement_verdict(
        experiment_id,
        policy_epoch=current_policy_epoch,
        stage="shadow",
        hypothesis_id=hypothesis.hypothesis_id,
        target_horizon=hypothesis.target_horizon,
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        behavior_contract_hash=expected_behavior_contract_hash,
        inference_role="monitoring",
    )
    if verdict is None or str(verdict.get("verdict_id") or "") != verdict_id:
        return LifecycleResult("hold", "admit_trial", "verdict_not_found_or_mismatch", experiment_id, verdict_id=verdict_id)
    if str(verdict.get("verdict") or "") != "insufficient_data":
        return LifecycleResult("hold", "admit_trial", "verdict_not_insufficient_data", experiment_id, verdict_id=verdict_id)
    if str(verdict.get("reason") or "") != "no_actual_intervention":
        return LifecycleResult("hold", "admit_trial", "verdict_not_no_actual_intervention", experiment_id, verdict_id=verdict_id)

    identity_reason = measurement_identity_reason(
        verdict, "shadow", current_policy_epoch,
        expected_horizon=hypothesis.target_horizon,
        expected_behavior_contract_hash=expected_behavior_contract_hash,
        expected_hypothesis_id=hypothesis.hypothesis_id,
    ) or measurement_harm_block_reason(verdict)
    if identity_reason:
        return LifecycleResult("hold", "admit_trial", identity_reason, experiment_id, verdict_id=verdict_id)

    allocation = _stage_allocation("paper_10")
    now = _now()
    successor = LearningExperiment(
        hypothesis_id=experiment.hypothesis_id,
        stage="paper_10",
        assignment_salt=_fresh_salt(),
        allocation_pct=allocation,
        min_samples_per_arm=experiment.min_samples_per_arm,
        min_market_days=experiment.min_market_days,
        primary_metric=experiment.primary_metric,
        promotion_gate_json=experiment.promotion_gate_json,
        harm_gate_json=experiment.harm_gate_json,
        status="active",
        parent_experiment_id=experiment.experiment_id,
        activation_verdict_id=verdict_id,
        stage_generation=int(experiment.stage_generation) + 1,
        started_at=now,
    )
    admitted_reason = f"owner_admitted:{reason[:200]}"
    event = LearningLifecycleEvent(
        dedup_key=dedup_key,
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
        successor_experiment_id=successor.experiment_id,
        verdict_id=verdict_id,
        from_stage="shadow",
        to_stage="paper_10",
        action="admit_trial",
        reason=admitted_reason,
        policy_epoch=current_policy_epoch,
        audit_actor="human",
        audit_source="dashboard",
        created_at=now,
    )
    try:
        with transaction():
            if not insert_lifecycle_event(event):
                existing = get_lifecycle_event_by_dedup_key(dedup_key)
                return LifecycleResult(
                    "admit_trial", "admit_trial", "already_transitioned", experiment.experiment_id,
                    (existing or {}).get("successor_experiment_id"), verdict_id,
                )
            db = get_db()
            db.execute(
                "UPDATE learning_experiments SET status='closed', ended_at=? "
                "WHERE experiment_id=? AND ended_at IS NULL",
                (now, experiment.experiment_id),
            )
            insert_experiment(successor)
            register_fixed_look_decision(
                successor,
                hypothesis,
                policy_epoch=current_policy_epoch,
                behavior_contract_hash=expected_behavior_contract_hash,
            )
    except InferenceContractError:
        return LifecycleResult(
            "hold",
            "admit_trial",
            "inference_contract_registration_failed",
            experiment_id,
            verdict_id=verdict_id,
        )
    _write_lifecycle_audit(event)
    return LifecycleResult(
        "admit_trial", "admit_trial", admitted_reason,
        experiment.experiment_id, successor.experiment_id, verdict_id,
    )


def _promotion_gate_reason(
    path_id: str, *, persist_config_migrations: bool = True,
) -> str | None:
    try:
        if global_learning_refreeze_reason() is not None:
            return "reconciliation_integrity_incident"
        cfg = (
            load_config()
            if persist_config_migrations
            else load_config(persist_migrations=False)
        )
        learning = cfg.learning
    except Exception:
        return "learning_config_unavailable"
    if not bool(cfg.agent.autonomous_learning):
        return "autonomous_learning_disabled"
    if bool(cfg.runtime.paused):
        return "runtime_paused"
    if not bool(learning.lifecycle_automation_enabled):
        return "lifecycle_automation_disabled"
    if not bool(learning.paper_influence_enabled):
        return "paper_influence_disabled"
    if path_id not in set(learning.paper_influence_paths or []):
        return "path_not_allowlisted"
    return None


def trial_admission_gate_reason(
    path_id: str, *, persist_config_migrations: bool = True,
) -> str | None:
    """Return the first default-closed gate blocking owner trial admission."""
    gate_reason = _promotion_gate_reason(
        path_id,
        persist_config_migrations=persist_config_migrations,
    )
    if gate_reason is not None:
        return gate_reason
    try:
        cfg = (
            load_config()
            if persist_config_migrations
            else load_config(persist_migrations=False)
        )
        if not bool(cfg.learning.contribution_evaluation_enabled):
            return "contribution_evaluation_disabled"
    except Exception:
        return "learning_config_unavailable"
    return None


def _safety_action_gate_reason() -> str | None:
    """Gate for safety actions (refreeze/invalidate) — narrower than the promotion gate.

    lifecycle_automation_disabled and paper_influence_disabled block promotions but
    must never block a negative-verdict refreeze or invalidation.  Only the global
    kill switch, a runtime pause, and a reconciliation integrity incident justify
    holding a safety action.
    """
    try:
        if global_learning_refreeze_reason() is not None:
            return "reconciliation_integrity_incident"
        cfg = load_config()
    except Exception:
        return "learning_config_unavailable"
    if not bool(cfg.agent.autonomous_learning):
        return "autonomous_learning_disabled"
    if bool(cfg.runtime.paused):
        return "runtime_paused"
    return None


def _promote_one_stage(
    experiment: LearningExperiment,
    hypothesis: LearningHypothesis,
    verdict: dict[str, Any],
    policy_epoch: str,
) -> LifecycleResult:
    to_stage = STAGE_SUCCESSOR[experiment.stage]
    allocation = _stage_allocation(to_stage)
    now = _now()
    successor = LearningExperiment(
        hypothesis_id=experiment.hypothesis_id,
        stage=to_stage,
        assignment_salt=_fresh_salt(),
        allocation_pct=allocation,
        min_samples_per_arm=experiment.min_samples_per_arm,
        min_market_days=experiment.min_market_days,
        primary_metric=experiment.primary_metric,
        promotion_gate_json=experiment.promotion_gate_json,
        harm_gate_json=experiment.harm_gate_json,
        status="active",
        parent_experiment_id=experiment.experiment_id,
        activation_verdict_id=str(verdict["verdict_id"]),
        stage_generation=int(experiment.stage_generation) + 1,
        started_at=now,
    )
    dedup_key = f"{verdict['verdict_id']}:{experiment.stage}:promote_stage"
    event = LearningLifecycleEvent(
        dedup_key=dedup_key,
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
        successor_experiment_id=successor.experiment_id,
        verdict_id=str(verdict["verdict_id"]),
        from_stage=experiment.stage,
        to_stage=to_stage,
        action="promote_stage",
        reason="positive_current_stage_measurement",
        policy_epoch=policy_epoch,
        created_at=now,
    )
    try:
        with transaction():
            if not insert_lifecycle_event(event):
                existing = get_lifecycle_event_by_dedup_key(dedup_key)
                return LifecycleResult(
                    "promote_stage", "promote_stage", "already_transitioned", experiment.experiment_id,
                    (existing or {}).get("successor_experiment_id"), str(verdict["verdict_id"]),
                )
            db = get_db()
            db.execute(
                "UPDATE learning_experiments SET status='closed', ended_at=? "
                "WHERE experiment_id=? AND ended_at IS NULL",
                (now, experiment.experiment_id),
            )
            insert_experiment(successor)
            register_fixed_look_decision(
                successor,
                hypothesis,
                policy_epoch=policy_epoch,
                behavior_contract_hash=str(verdict["behavior_contract_hash"]),
            )
    except InferenceContractError:
        return LifecycleResult(
            "hold",
            "promote_stage",
            "inference_contract_registration_failed",
            experiment.experiment_id,
            verdict_id=str(verdict["verdict_id"]),
        )
    _write_lifecycle_audit(event)
    return LifecycleResult(
        "promote_stage", "promote_stage", "promoted_one_stage", experiment.experiment_id,
        successor.experiment_id, str(verdict["verdict_id"]),
    )


def _apply_safety_action(
    experiment: LearningExperiment,
    hypothesis: LearningHypothesis,
    verdict: dict[str, Any],
    action: str,
    policy_epoch: str,
    *,
    reason: str | None = None,
) -> LifecycleResult:
    try:
        gate_reason = _safety_action_gate_reason()
        if gate_reason is not None:
            return LifecycleResult(
                "hold", action, gate_reason, experiment.experiment_id,
                verdict_id=verdict["verdict_id"],
            )
        cfg = load_config()
        if not bool(cfg.learning.auto_refreeze):
            return LifecycleResult("hold", action, "auto_refreeze_disabled", experiment.experiment_id, verdict_id=verdict["verdict_id"])
    except Exception:
        return LifecycleResult("hold", action, "learning_config_unavailable", experiment.experiment_id, verdict_id=verdict["verdict_id"])
    dedup_key = f"{verdict['verdict_id']}:{experiment.stage}:{action}"
    now = _now()
    event = LearningLifecycleEvent(
        dedup_key=dedup_key,
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
        verdict_id=str(verdict["verdict_id"]),
        from_stage=experiment.stage,
        action=action,
        reason=reason or (
            "invalid_measurement_refreeze"
            if action == "invalidate"
            else "negative_measurement_refreeze"
        ),
        policy_epoch=policy_epoch,
        created_at=now,
    )
    with transaction():
        if not insert_lifecycle_event(event):
            return LifecycleResult(action, action, "already_transitioned", experiment.experiment_id, verdict_id=verdict["verdict_id"])
        db = get_db()
        status = "invalid" if action == "invalidate" else "refrozen"
        db.execute(
            "UPDATE learning_experiments SET status=?, ended_at=? "
            "WHERE experiment_id=? AND ended_at IS NULL",
            (status, now, experiment.experiment_id),
        )
        if action == "invalidate":
            db.execute(
                "UPDATE learning_hypotheses SET status='refrozen', updated_at=? WHERE hypothesis_id=?",
                (now, hypothesis.hypothesis_id),
            )
    _write_lifecycle_audit(event)
    return LifecycleResult(action, action, event.reason, experiment.experiment_id, verdict_id=verdict["verdict_id"])


def _record_blocked_recommendation(
    experiment: LearningExperiment,
    hypothesis: LearningHypothesis,
    verdict: dict[str, Any],
    policy_epoch: str,
    reason: str,
) -> LifecycleResult:
    dedup_key = f"{verdict['verdict_id']}:{experiment.stage}:hold"
    event = LearningLifecycleEvent(
        dedup_key=dedup_key,
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
        verdict_id=str(verdict["verdict_id"]),
        from_stage=experiment.stage,
        action="hold",
        reason=f"recommended_action=promote_stage; blocked={reason}",
        policy_epoch=policy_epoch,
    )
    if insert_lifecycle_event(event):
        _write_lifecycle_audit(event)
    return LifecycleResult("hold", "promote_stage", reason, experiment.experiment_id, verdict_id=verdict["verdict_id"])


def inspect_learning_experiment(experiment_id: str, *, current_policy_epoch: str) -> dict[str, Any]:
    """Return a read-only lifecycle/operator view for one experiment."""
    experiment_row = get_record("learning_experiments", experiment_id)
    if experiment_row is None:
        return {"experiment_id": experiment_id, "status": "not_found"}
    experiment = LearningExperiment(**experiment_row)
    hypothesis_row = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis_row is None:
        return {"experiment_id": experiment_id, "status": "orphaned_hypothesis"}
    hypothesis = LearningHypothesis(**hypothesis_row)
    try:
        expected_behavior_contract_hash = current_behavior_contract_hash(
            path_id=hypothesis.path_id,
            policy_epoch=current_policy_epoch,
            target_horizon=hypothesis.target_horizon,
            outcome_contract_version="v1",
            metric_version=METRIC_VERSION,
        )
    except (KeyError, ValueError):
        expected_behavior_contract_hash = None
    verdict = get_latest_measurement_verdict(
        experiment_id,
        policy_epoch=current_policy_epoch,
        stage=experiment.stage,
        hypothesis_id=hypothesis.hypothesis_id,
        target_horizon=hypothesis.target_horizon,
        outcome_contract_version="v1",
        metric_version=METRIC_VERSION,
        behavior_contract_hash=expected_behavior_contract_hash,
        inference_role="monitoring",
    )
    if verdict is not None and measurement_identity_reason(
        verdict,
        experiment.stage,
        current_policy_epoch,
        expected_horizon=hypothesis.target_horizon,
        expected_behavior_contract_hash=expected_behavior_contract_hash,
        expected_hypothesis_id=hypothesis.hypothesis_id,
    ):
        verdict = None
    recommended = "hold"
    if verdict is not None and str(verdict.get("verdict")) in MEASUREMENT_VERDICTS:
        recommended = measurement_to_lifecycle_action(str(verdict["verdict"]))
        if experiment.stage == "shadow" and (
            recommended == "promote_stage"
            or (
                verdict.get("verdict") == "insufficient_data"
                and verdict.get("reason") == "no_actual_intervention"
            )
        ):
            recommended = "admit_trial"
    authority = resolve_learning_authority(
        hypothesis.path_id,
        experiment,
        current_policy_epoch,
        persist_config_migrations=False,
    )
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.learning.control import get_learning_job_health, job_health_view
    cohorts = [] if expected_behavior_contract_hash is None else [
        cohort
        for cohort in build_experiment_cohorts(experiment.experiment_id)
        if cohort.key.policy_epoch == current_policy_epoch
        and cohort.key.horizon == hypothesis.target_horizon
        and cohort.key.outcome_contract_version == "v1"
        and cohort.key.behavior_contract_hash == expected_behavior_contract_hash
    ]
    evaluator_health = get_learning_job_health("learning_contribution_evaluator")
    return {
        "path_id": hypothesis.path_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "current_experiment_id": experiment.experiment_id,
        "current_stage": experiment.stage,
        "current_status": experiment.status,
        "policy_epoch": current_policy_epoch,
        "target_horizon": hypothesis.target_horizon,
        "allocation_pct": experiment.allocation_pct,
        "latest_measurement_verdict": (verdict or {}).get("verdict"),
        "recommended_lifecycle_action": recommended,
        "authority_enabled": authority.authorized,
        "authority_block_reason": None if authority.authorized else authority.reason,
        "readiness": {
            "ready_for_paper_influence": authority.authorized,
            "reason": authority.reason,
        },
        "activation_verdict_id": experiment.activation_verdict_id,
        "current_verdict_id": authority.current_verdict_id,
        "treatment_control_progress": {
            "treatment": sum(len(cohort.treatment_samples) for cohort in cohorts),
            "assigned_treatment": sum(
                len(cohort.treatment_samples) for cohort in cohorts
            ),
            "authority_applied_treatment": sum(
                len(cohort.authority_applied_treatment_samples)
                for cohort in cohorts
            ),
            "actual_intervention_treatment": sum(
                len(cohort.actual_treatment_samples) for cohort in cohorts
            ),
            "baseline_treatment": sum(
                len(cohort.baseline_treatment_samples) for cohort in cohorts
            ),
            "control": sum(len(cohort.control_samples) for cohort in cohorts),
            "required_per_arm": experiment.min_samples_per_arm,
            "cohorts": [cohort.inspection() for cohort in cohorts],
        },
        "next_evaluation_at": evaluator_health.next_due_at,
        "job_health": {
            "evaluator": job_health_view(evaluator_health),
            "controller": job_health_view(
                get_learning_job_health("learning_lifecycle_controller")
            ),
        },
        "events": list_lifecycle_events(experiment_id),
    }


def run_contribution_evaluator() -> dict[str, int | str]:
    """Run AT-01 measurement for supported open experiments only.

    This is intentionally outside the autonomous-behaviour job gate: it writes
    measurement facts but never stages, authorizes, or refreezes a consumer.
    """
    from aletheia.learning.control import (
        record_measurement_job_failure,
        record_measurement_job_partial,
        record_measurement_job_skipped,
        record_measurement_job_started,
        record_measurement_job_success,
    )
    job_id = "learning_contribution_evaluator"
    try:
        if not bool(load_config().learning.contribution_evaluation_enabled):
            record_measurement_job_skipped(job_id, "contribution_evaluation_disabled")
            return {"status": "disabled", "experiments": 0, "verdicts": 0}
        record_measurement_job_started(job_id)
        from aletheia.learning.contribution_runner import evaluate_experiment
        seen: set[str] = set()
        experiment_rows = []
        for pid in SUPPORTED_PATH_IDS:
            for row in list_open_experiments(path_id=pid):
                if row["experiment_id"] not in seen:
                    seen.add(row["experiment_id"])
                    experiment_rows.append(row)
        # Per-experiment isolation: a single dirty experiment row must not abort
        # the whole scheduled cycle. The previous ``sum(... for row in rows)`` let
        # the first failing experiment propagate to the outer handler, which marked
        # the job failed and skipped every remaining experiment; the next cycle then
        # hit the same first-failing row and crashed again — permanent verdict
        # starvation. Mirror the per-experiment ``try`` that run_verdict_cycle
        # already uses so a bad row is recorded and skipped, not fatal. (issue #160)
        verdicts = 0
        failed_experiments = 0
        for row in experiment_rows:
            experiment_id = str(row["experiment_id"])
            try:
                verdicts += len(evaluate_experiment(experiment_id))
            except Exception:
                failed_experiments += 1
                logger.warning(
                    "contribution_evaluator: experiment %s failed to evaluate; "
                    "skipping and continuing the cycle",
                    experiment_id, exc_info=True,
                )
        status = "healthy"
        if failed_experiments:
            status = "degraded"
            record_measurement_job_partial(
                job_id, f"experiment_evaluation_failed:{failed_experiments}"
            )
        else:
            record_measurement_job_success(job_id)
        return {
            "status": status,
            "experiments": len(experiment_rows),
            "verdicts": verdicts,
            "failed_experiments": failed_experiments,
        }
    except Exception as exc:
        record_measurement_job_failure(job_id, str(exc))
        raise


def run_lifecycle_controller() -> dict[str, int]:
    """Evaluate current-stage actions; scheduler gating supplies autonomy safety."""
    results = []
    failed_experiments = 0
    seen: set[str] = set()
    for pid in SUPPORTED_PATH_IDS:
        for row in list_open_experiments(path_id=pid):
            experiment_id = str(row["experiment_id"])
            if experiment_id in seen:
                continue
            seen.add(experiment_id)
            # Per-experiment isolation: this loop had no exception guard at all, so a
            # single experiment whose lifecycle evaluation raised aborted the entire
            # controller cycle and left every subsequent experiment unprocessed.
            # Contain the failure to the offending experiment. (issue #160)
            try:
                epoch = current_policy_epoch(pid) or ""
                results.append(process_lifecycle(experiment_id, current_policy_epoch=epoch))
            except Exception:
                failed_experiments += 1
                logger.warning(
                    "lifecycle_controller: experiment %s failed; skipping and "
                    "continuing the cycle",
                    experiment_id, exc_info=True,
                )
    return {
        "experiments": len(results),
        "promoted": sum(result.action == "promote_stage" for result in results),
        "refrozen": sum(result.action in {"refreeze", "invalidate"} for result in results),
        "admitted": sum(result.action == "admit_trial" for result in results),
        "failed_experiments": failed_experiments,
    }


def _stage_allocation(stage: str) -> float:
    if stage == "paper_active":
        try:
            value = float(load_config().learning.paper_active_allocation_pct)
        except Exception:
            value = 0.80
        return min(0.90, max(0.01, value))
    return STAGE_ALLOCATION[stage]


def _fresh_salt() -> str:
    from uuid import uuid4
    return uuid4().hex


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _attempt_lifecycle_audit(event: LearningLifecycleEvent) -> bool:
    audit_id = f"learning-lifecycle:{event.event_id}"
    try:
        from aletheia.files.jsonl import write_audit
        succeeded = write_audit(
            _lifecycle_audit_payload(event),
            mirror_db=False,
            dedup_id=audit_id,
        )
    except Exception:
        succeeded = False
        logger.warning(
            "learning lifecycle audit append raised for event %s",
            event.event_id,
            exc_info=True,
        )
    update_lifecycle_audit_mirror(
        event.event_id,
        status="mirrored" if succeeded else "failed",
        mirrored_at=_now() if succeeded else None,
        failure_reason=None if succeeded else "jsonl_append_failed",
    )
    return succeeded


def _write_lifecycle_audit(event: LearningLifecycleEvent) -> bool:
    """Append the JSONL mirror without rolling back the authoritative DB event."""
    succeeded = _attempt_lifecycle_audit(event)
    if not succeeded:
        from aletheia.learning.control import record_lifecycle_audit_result

        record_lifecycle_audit_result(
            pending=count_unmirrored_lifecycle_events(),
            failed=1,
        )
    return succeeded


def reconcile_lifecycle_audits(
    *, limit: int = 100, event_ids: list[str] | None = None,
) -> dict[str, int]:
    """Retry pending JSONL mirrors by stable event id; never replay transitions."""
    rows = list_unmirrored_lifecycle_events(limit=limit, event_ids=event_ids)
    events = [LearningLifecycleEvent(**row) for row in rows]
    try:
        from aletheia.files.jsonl import write_audits_deduplicated

        mirrored_ids = write_audits_deduplicated([
            _lifecycle_audit_payload(event) for event in events
        ])
    except Exception:
        mirrored_ids = set()
        logger.warning("learning lifecycle audit reconciliation failed", exc_info=True)
    mirrored = 0
    for event in events:
        audit_id = f"learning-lifecycle:{event.event_id}"
        succeeded = audit_id in mirrored_ids
        update_lifecycle_audit_mirror(
            event.event_id,
            status="mirrored" if succeeded else "failed",
            mirrored_at=_now() if succeeded else None,
            failure_reason=None if succeeded else "jsonl_append_failed",
        )
        mirrored += int(succeeded)
    failed = len(events) - mirrored
    pending = count_unmirrored_lifecycle_events()

    from aletheia.learning.control import record_lifecycle_audit_result

    record_lifecycle_audit_result(pending=pending, failed=failed)
    return {
        "attempted": len(rows),
        "mirrored": mirrored,
        "failed": failed,
        "pending": pending,
    }

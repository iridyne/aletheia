"""Durable, idempotent lifecycle transitions for AT-01 measurements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from aletheia.config.loader import load_config
from aletheia.learning.authority import (
    MEASUREMENT_VERDICTS,
    measurement_identity_reason,
    resolve_learning_authority,
)
from aletheia.learning.cohorts import SUPPORTED_PATH_ID
from aletheia.learning.control import global_learning_refreeze_reason
from aletheia.learning.ledger import (
    get_latest_measurement_verdict,
    get_lifecycle_event_by_dedup_key,
    get_record,
    insert_lifecycle_event,
    insert_experiment,
    list_open_experiments,
    list_lifecycle_events,
)
from aletheia.learning.models import (
    LearningExperiment,
    LearningHypothesis,
    LearningLifecycleEvent,
)
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


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
    if hypothesis.path_id != SUPPORTED_PATH_ID:
        return LifecycleResult("hold", "hold", "unsupported_learning_path", experiment_id)
    verdict = get_latest_measurement_verdict(experiment.experiment_id)
    if verdict is None:
        return LifecycleResult("hold", "hold", "no_current_stage_measurement", experiment_id)
    measurement = str(verdict.get("verdict") or "")
    if measurement not in MEASUREMENT_VERDICTS:
        return LifecycleResult("hold", "hold", "unknown_measurement_verdict", experiment_id)
    recommended = measurement_to_lifecycle_action(measurement)
    identity_reason = measurement_identity_reason(
        verdict, experiment.stage, current_policy_epoch,
        expected_horizon=hypothesis.target_horizon,
    )
    if identity_reason and measurement not in {"negative", "invalid"}:
        return LifecycleResult("hold", recommended, identity_reason, experiment_id, verdict_id=verdict["verdict_id"])

    if recommended == "hold":
        return LifecycleResult("hold", "hold", f"measurement_{measurement}", experiment_id, verdict_id=verdict["verdict_id"])
    if recommended in {"refreeze", "invalidate"}:
        return _apply_safety_action(experiment, hypothesis, verdict, recommended, current_policy_epoch)
    if experiment.stage == "shadow":
        return LifecycleResult(
            "hold", "admit_trial", "shadow_requires_explicit_trial_admission",
            experiment_id, verdict_id=verdict["verdict_id"],
        )
    if experiment.stage not in STAGE_SUCCESSOR:
        return LifecycleResult("hold", "promote_stage", "terminal_stage", experiment_id, verdict_id=verdict["verdict_id"])

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
    """Explicitly admit a measured shadow cohort into a bounded paper_10 trial.

    This seam is never called by the lifecycle scheduler. It requires a complete
    no-intervention shadow measurement and the same default-closed paper gates used
    by later lifecycle promotions.
    """
    experiment_row = get_record("learning_experiments", experiment_id)
    if experiment_row is None:
        return LifecycleResult("hold", "admit_trial", "experiment_not_found", experiment_id)
    experiment = LearningExperiment(**experiment_row)
    hypothesis_row = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis_row is None:
        return LifecycleResult("hold", "admit_trial", "hypothesis_not_found", experiment_id)
    hypothesis = LearningHypothesis(**hypothesis_row)
    verdict = get_record("learning_contribution_verdicts", verdict_id)
    if verdict is None or str(verdict.get("experiment_id")) != experiment_id:
        return LifecycleResult("hold", "admit_trial", "trial_measurement_not_found", experiment_id)
    dedup_key = f"{verdict_id}:shadow:admit_trial"
    existing = get_lifecycle_event_by_dedup_key(dedup_key)
    if existing is not None:
        return LifecycleResult(
            "admit_trial", "admit_trial", "already_admitted", experiment_id,
            existing.get("successor_experiment_id"), verdict_id,
        )
    if experiment.stage != "shadow" or experiment.status != "active" or experiment.ended_at is not None:
        return LifecycleResult("hold", "admit_trial", "experiment_not_active_shadow", experiment_id, verdict_id=verdict_id)
    latest = get_latest_measurement_verdict(experiment_id)
    if latest is None or str(latest.get("verdict_id")) != verdict_id:
        return LifecycleResult("hold", "admit_trial", "trial_measurement_not_current", experiment_id, verdict_id=verdict_id)
    if verdict.get("verdict") != "insufficient_data" or verdict.get("reason") != "no_actual_intervention":
        return LifecycleResult("hold", "admit_trial", "shadow_not_trial_ready", experiment_id, verdict_id=verdict_id)
    identity_reason = measurement_identity_reason(
        verdict,
        "shadow",
        current_policy_epoch,
        expected_horizon=hypothesis.target_horizon,
    )
    if identity_reason:
        return LifecycleResult("hold", "admit_trial", identity_reason, experiment_id, verdict_id=verdict_id)
    if min(
        int(verdict.get("unique_treatment_n") or 0),
        int(verdict.get("unique_control_n") or 0),
    ) < experiment.min_samples_per_arm:
        return LifecycleResult("hold", "admit_trial", "trial_samples_insufficient", experiment_id, verdict_id=verdict_id)
    gate_reason = _promotion_gate_reason(hypothesis.path_id)
    if gate_reason is not None:
        return LifecycleResult("hold", "admit_trial", gate_reason, experiment_id, verdict_id=verdict_id)
    actor = admitted_by.strip()
    admission_reason = reason.strip()
    if not actor or not admission_reason:
        return LifecycleResult("hold", "admit_trial", "admission_identity_required", experiment_id, verdict_id=verdict_id)

    now = _now()
    successor = LearningExperiment(
        hypothesis_id=experiment.hypothesis_id,
        stage="paper_10",
        assignment_salt=_fresh_salt(),
        allocation_pct=STAGE_ALLOCATION["paper_10"],
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
    event = LearningLifecycleEvent(
        dedup_key=dedup_key,
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_id=experiment.experiment_id,
        successor_experiment_id=successor.experiment_id,
        verdict_id=verdict_id,
        from_stage="shadow",
        to_stage="paper_10",
        action="admit_trial",
        reason=f"admitted_by={actor}; reason={admission_reason}",
        policy_epoch=current_policy_epoch,
        created_at=now,
    )
    with transaction():
        if not insert_lifecycle_event(event):
            existing = get_lifecycle_event_by_dedup_key(dedup_key)
            return LifecycleResult(
                "admit_trial", "admit_trial", "already_admitted", experiment_id,
                (existing or {}).get("successor_experiment_id"), verdict_id,
            )
        db = get_db()
        db.execute(
            "UPDATE learning_experiments SET status='closed', ended_at=? "
            "WHERE experiment_id=? AND ended_at IS NULL",
            (now, experiment.experiment_id),
        )
        insert_experiment(successor)
    _write_lifecycle_audit(event)
    return LifecycleResult(
        "admit_trial", "admit_trial", "admitted_to_paper_10", experiment_id,
        successor.experiment_id, verdict_id,
    )


def _promotion_gate_reason(path_id: str) -> str | None:
    try:
        if global_learning_refreeze_reason() is not None:
            return "reconciliation_integrity_incident"
        cfg = load_config()
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
) -> LifecycleResult:
    try:
        cfg = load_config()
        gate_reason = _promotion_gate_reason(hypothesis.path_id)
        if gate_reason is not None:
            return LifecycleResult(
                "hold", action, gate_reason, experiment.experiment_id,
                verdict_id=verdict["verdict_id"],
            )
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
        reason=("invalid_measurement_refreeze" if action == "invalidate" else "negative_measurement_refreeze"),
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
    hypothesis = LearningHypothesis(**get_record("learning_hypotheses", experiment.hypothesis_id))
    verdict = get_latest_measurement_verdict(experiment_id)
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
    authority = resolve_learning_authority(hypothesis.path_id, experiment, current_policy_epoch)
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.learning.control import get_learning_job_health
    cohorts = build_experiment_cohorts(experiment.experiment_id)
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
            "control": sum(len(cohort.control_samples) for cohort in cohorts),
            "required_per_arm": experiment.min_samples_per_arm,
            "cohorts": [cohort.inspection() for cohort in cohorts],
        },
        "next_evaluation_at": evaluator_health.next_due_at,
        "job_health": {
            "evaluator": evaluator_health.model_dump(),
            "controller": get_learning_job_health("learning_lifecycle_controller").model_dump(),
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
        experiment_rows = list_open_experiments(path_id=SUPPORTED_PATH_ID)
        verdicts = sum(len(evaluate_experiment(row["experiment_id"])) for row in experiment_rows)
        record_measurement_job_success(job_id)
        return {"status": "healthy", "experiments": len(experiment_rows), "verdicts": verdicts}
    except Exception as exc:
        record_measurement_job_failure(job_id, str(exc))
        raise


def run_lifecycle_controller() -> dict[str, int]:
    """Evaluate current-stage actions; scheduler gating supplies autonomy safety."""
    results = []
    for row in list_open_experiments(path_id=SUPPORTED_PATH_ID):
        experiment_id = str(row["experiment_id"])
        epoch = _latest_exposure_policy_epoch(experiment_id)
        results.append(process_lifecycle(experiment_id, current_policy_epoch=epoch))
    return {
        "experiments": len(results),
        "promoted": sum(result.action == "promote_stage" for result in results),
        "refrozen": sum(result.action in {"refreeze", "invalidate"} for result in results),
    }


def _latest_exposure_policy_epoch(experiment_id: str) -> str:
    row = get_db().execute(
        "SELECT concurrent_policy_epoch FROM learning_exposures WHERE experiment_id=? "
        "ORDER BY datetime(exposed_at) DESC, exposure_id DESC LIMIT 1",
        (experiment_id,),
    ).fetchone()
    return str(row[0]) if row and row[0] else ""


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


def _write_lifecycle_audit(event: LearningLifecycleEvent) -> None:
    try:
        from aletheia.files.jsonl import write_audit
        write_audit({
            "actor": "learning_lifecycle_controller",
            "source": "learning_lifecycle",
            "event_type": "learning_lifecycle_transition",
            "entity_type": "learning_experiment",
            "entity_id": event.experiment_id,
            "state_change": event.action,
            "message": event.reason,
            "metadata": event.model_dump(mode="json"),
        })
    except Exception:
        # The durable DB event remains authoritative; audit append failures must
        # not roll back a completed, safety-preserving state transition.
        pass

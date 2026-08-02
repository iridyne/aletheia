"""Bounded, read-only owner projection for measured Learning contribution."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from aletheia.learning.cohorts import SUPPORTED_PATH_IDS
from aletheia.learning.behavior_contracts import current_behavior_contract_hash
from aletheia.learning.experiments import current_policy_epoch
from aletheia.learning.ledger import get_latest_measurement_verdict
from aletheia.learning.registry import LEARNING_PATHS
from aletheia.learning.verdicts import METRIC_VERSION
from aletheia.storage.sqlite import get_db


SCHEMA_VERSION = "lco-v1"
_OUTCOME_CONTRACT_VERSION = "v1"
_MEASUREMENT_STATES = {
    "insufficient_data",
    "neutral",
    "positive",
    "negative",
    "invalid",
}


def build_learning_contribution_projection(*, limit: int = 20) -> dict[str, Any]:
    """Project persisted current facts without evaluating or mutating Learning."""
    bounded_limit = min(max(int(limit), 1), 100)
    generated_at = datetime.now(timezone.utc).isoformat()

    from aletheia.agent_runtime.build_info import get_boot_commit
    from aletheia.learning.control import (
        get_learning_job_health,
        job_health_view,
        measurement_catchup_due,
    )

    from aletheia.config.loader import load_config

    cfg = load_config(persist_migrations=False)
    stale_after_sec = max(
        1, int(cfg.learning.evaluation_interval_minutes),
    ) * 2 * 60
    boot_commit = get_boot_commit()
    rows = _current_experiment_rows(bounded_limit, cfg.learning.paper_influence_paths)
    path_states = {
        path_id: {
            "path_id": path_id,
            "availability": "available",
            "unavailable_reason": None,
            "state": "empty",
            "data_freshness": _freshness(
                None, generated_at, stale_after_sec=stale_after_sec,
            ),
        }
        for path_id in sorted(SUPPORTED_PATH_IDS)
    }
    items: list[dict[str, Any]] = []
    for row in rows:
        path_id = str(row.get("path_id") or "")
        try:
            item = _project_experiment(
                row,
                boot_commit=boot_commit,
                generated_at=generated_at,
                stale_after_sec=stale_after_sec,
            )
            items.append(item)
            if path_id in path_states and path_states[path_id]["state"] == "empty":
                path_states[path_id] = {
                    "path_id": path_id,
                    "availability": item["availability"],
                    "unavailable_reason": (
                        item["lifecycle"]["authority_block_reason"]
                        if item["lifecycle"]["authority_block_reason"]
                        == "authority_projection_failed"
                        else "conflict_provenance_truncated"
                        if item["availability"] == "partial"
                        else None
                    ),
                    "state": "active",
                    "data_freshness": item["data_freshness"],
                }
        except Exception:
            if path_id in path_states and path_states[path_id]["state"] == "empty":
                path_states[path_id] = {
                    "path_id": path_id,
                    "availability": "unavailable",
                    "unavailable_reason": "path_projection_failed",
                    "state": "unavailable",
                    "data_freshness": _freshness(
                        None, generated_at, stale_after_sec=stale_after_sec,
                    ),
                }
    operations_errors: dict[str, str] = {}
    evaluator_view = None
    lifecycle_audit_view = None
    try:
        evaluator = get_learning_job_health("learning_contribution_evaluator")
        freshness = _freshness(
            evaluator.last_succeeded_at,
            generated_at,
            stale_after_sec=stale_after_sec,
        )
        evaluator_view = _project_health(
            job_health_view(evaluator),
            catchup_due=False,
        )
        try:
            evaluator_view["catchup_due"] = measurement_catchup_due(
                "learning_contribution_evaluator",
                persist_config_migrations=False,
            )
        except Exception:
            evaluator_view["catchup_due"] = None
            operations_errors["evaluator_catchup"] = "read_failed"
    except Exception:
        operations_errors["evaluator"] = "read_failed"
        freshness = {
            **_freshness(None, generated_at, stale_after_sec=stale_after_sec),
            "state": "unavailable",
        }
    try:
        lifecycle_audit = get_learning_job_health("learning_lifecycle_audit_reconciler")
        lifecycle_audit_view = _project_health(
            job_health_view(lifecycle_audit),
            catchup_due=False,
        )
    except Exception:
        operations_errors["lifecycle_audit"] = "read_failed"

    operations = {
        "availability": "partial" if operations_errors else "available",
        "query_errors": operations_errors,
        "config": {
            "contribution_evaluation_enabled": bool(
                cfg.learning.contribution_evaluation_enabled
            ),
            "autonomous_learning_enabled": bool(cfg.agent.autonomous_learning),
            "lifecycle_automation_enabled": bool(
                cfg.learning.lifecycle_automation_enabled
            ),
            "paper_influence_enabled": bool(
                cfg.learning.paper_influence_enabled
            ),
        },
        "evaluator": evaluator_view,
        "lifecycle_audit": lifecycle_audit_view,
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "availability": (
            "partial"
            if any(
                state["availability"] in {"partial", "unavailable"}
                for state in path_states.values()
            )
            or bool(operations_errors)
            else "available"
        ),
        "unavailable_reason": (
            "operations_projection_failed" if operations_errors else None
        ),
        "data_freshness": freshness,
        "paths": [_project_path(path) for path in LEARNING_PATHS],
        "path_states": list(path_states.values()),
        "items": items,
        "operations": operations,
    }


def configured_learning_stale_after_sec() -> int:
    """Return the configured freshness threshold without persisting migrations."""
    from aletheia.config.loader import load_config

    cfg = load_config(persist_migrations=False)
    return max(1, int(cfg.learning.evaluation_interval_minutes)) * 2 * 60


def unavailable_learning_contribution_projection(
    reason: str,
    *,
    stale_after_sec: int | None = None,
) -> dict[str, Any]:
    """Return an honest unavailable envelope without leaking exception text."""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "availability": "unavailable",
        "unavailable_reason": reason,
        "data_freshness": {
            "last_evaluated_at": None,
            "age_sec": None,
            "stale_after_sec": stale_after_sec,
            "state": "unavailable",
        },
        "paths": [],
        "path_states": [],
        "items": [],
        "operations": None,
    }


def _current_experiment_rows(
    limit: int, priority_paths: list[str] | None = None
) -> list[dict[str, Any]]:
    base_sql = (
        "SELECT e.*, h.path_id, h.target_horizon, h.effect_threshold, "
        "v.verdict_id, v.verdict, v.reason AS verdict_reason, "
        "v.unique_treatment_n, v.unique_control_n, v.baseline, v.observed, "
        "v.delta, v.confidence_low, v.confidence_high, "
        "v.harm_metrics_json, v.created_at AS verdict_created_at, "
        "v.window_start, v.window_end, v.policy_epoch AS verdict_policy_epoch, "
        "v.stage AS verdict_stage, v.target_horizon AS verdict_target_horizon, "
        "v.outcome_contract_version AS verdict_outcome_contract_version, "
        "v.metric_version AS verdict_metric_version, "
        "v.code_commit AS verdict_code_commit, "
        "v.behavior_contract_hash AS verdict_behavior_contract_hash, "
        "v.hypothesis_id AS verdict_hypothesis_id "
        "FROM learning_experiments e "
        "JOIN learning_hypotheses h ON h.hypothesis_id=e.hypothesis_id "
        "LEFT JOIN learning_contribution_verdicts v ON v.verdict_id=("
        " SELECT v2.verdict_id FROM learning_contribution_verdicts v2 "
        " WHERE v2.experiment_id=e.experiment_id "
        # Evaluation order, not observation-window order: the 1970-01-01 sentinel
        # window of a sample-less cohort sorts after every real window, so
        # window_end ordering showed a superseded verdict as the latest one.
        # Matches get_latest_measurement_verdict so the owner surface and the
        # lifecycle controller cannot disagree about which row is authoritative.
        " ORDER BY datetime(v2.created_at) DESC, datetime(v2.window_end) DESC, "
        " v2.verdict_id DESC LIMIT 1"
        ") "
        "WHERE e.ended_at IS NULL "
    )
    influence = [
        path for path in dict.fromkeys(priority_paths or ())
        if path in SUPPORTED_PATH_IDS
    ]
    ordered_paths = influence + [
        path for path in sorted(SUPPORTED_PATH_IDS) if path not in set(influence)
    ]
    db = get_db()
    rows: list[dict[str, Any]] = []
    for path_id in ordered_paths[:limit]:
        row = db.execute(
            base_sql
            + "AND h.path_id=? "
            + "ORDER BY datetime(e.created_at) DESC, e.experiment_id DESC LIMIT 1",
            (path_id,),
        ).fetchone()
        if row is not None:
            rows.append(dict(row))
    remaining = limit - len(rows)
    if remaining <= 0:
        return rows

    measured = [
        path for path in sorted(SUPPORTED_PATH_IDS) if path not in set(influence)
    ]
    cases: list[str] = []
    case_params: list[Any] = []
    for rank, paths in ((0, influence), (1, measured)):
        if paths:
            cases.append(f"WHEN h.path_id IN ({', '.join('?' * len(paths))}) THEN {rank}")
            case_params.extend(paths)
    exclusions = [str(row["experiment_id"]) for row in rows]
    exclusion_sql = ""
    params: list[Any] = []
    if exclusions:
        exclusion_sql = f"AND e.experiment_id NOT IN ({', '.join('?' * len(exclusions))}) "
        params.extend(exclusions)
    eligible_paths = sorted(SUPPORTED_PATH_IDS)
    path_sql = f"AND h.path_id IN ({', '.join('?' * len(eligible_paths))}) "
    params.extend(eligible_paths)
    params.extend(case_params)
    tier_sql = f"CASE {' '.join(cases)} ELSE 2 END ASC, " if cases else ""
    params.append(remaining)
    filler_rows = db.execute(
        base_sql
        + exclusion_sql
        + path_sql
        + f"ORDER BY {tier_sql}datetime(e.created_at) DESC, "
        + "e.experiment_id DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    rows.extend(dict(row) for row in filler_rows)
    return rows


def _project_experiment(
    row: dict[str, Any],
    *,
    boot_commit: str | None,
    generated_at: str,
    stale_after_sec: int,
) -> dict[str, Any]:
    path_id = str(row.get("path_id") or "")
    try:
        policy_epoch = current_policy_epoch(
            path_id,
            persist_config_migrations=False,
        )
        policy_reason = (
            None if policy_epoch is not None else "current_policy_epoch_unavailable"
        )
    except Exception:
        policy_epoch = None
        policy_reason = "current_policy_epoch_unavailable"

    behavior_contract_hash = None
    behavior_reason = None
    if policy_epoch is not None:
        try:
            behavior_contract_hash = current_behavior_contract_hash(
                path_id=path_id,
                policy_epoch=policy_epoch,
                target_horizon=str(row.get("target_horizon") or ""),
                outcome_contract_version=_OUTCOME_CONTRACT_VERSION,
                metric_version=METRIC_VERSION,
            )
        except (KeyError, ValueError):
            behavior_reason = "behavior_contract_unavailable"

    identity_precondition_reason = policy_reason or behavior_reason
    current_verdict = None
    if identity_precondition_reason is None:
        current_verdict = get_latest_measurement_verdict(
            str(row.get("experiment_id") or ""),
            policy_epoch=policy_epoch,
            stage=str(row.get("stage") or ""),
            hypothesis_id=str(row.get("hypothesis_id") or ""),
            target_horizon=str(row.get("target_horizon") or ""),
            outcome_contract_version=_OUTCOME_CONTRACT_VERSION,
            metric_version=METRIC_VERSION,
            behavior_contract_hash=behavior_contract_hash,
        )
    historical_mismatch = identity_precondition_reason or _identity_mismatch(
        row,
        policy_epoch=policy_epoch,
        behavior_contract_hash=behavior_contract_hash,
    )
    current = current_verdict is not None
    mismatch = None if current else historical_mismatch
    verdict_metrics = _safe_object(row.get("harm_metrics_json"))
    current_metrics = _safe_object(
        (current_verdict or {}).get("harm_metrics_json")
    )
    # The current-facing blocks must describe the running cohort and nothing else.
    # ``measurement.*`` was already gated on identity, but ``samples.*`` and
    # ``integrity.*`` were not: a stale verdict had its per-arm counts zeroed here
    # while its aggregates were published unchanged, so one item could report
    # ``state=not_measured`` with ``arms 0/0`` beside ``harm_breached=true`` and
    # ``raw_exposures=41`` and no consumer could tell which cohort the breach
    # belonged to.  Nothing is dropped — the stale facts move to
    # ``latest_historical_measurement``, where they are labelled as not current.
    metrics = current_metrics if current else {}
    measurement_state = (
        str((current_verdict or {}).get("verdict"))
        if current and str((current_verdict or {}).get("verdict")) in _MEASUREMENT_STATES
        else "not_measured"
    )
    measurement_reason = (
        str((current_verdict or {}).get("reason") or "") or None
        if current
        else mismatch
    )
    recommended = _recommended_action(
        measurement_state,
        measurement_reason,
        stage=str(row.get("stage") or ""),
    )
    mutation_reason = "admission_not_recommended"
    mutation_available = False
    if recommended == "admit_trial" and current_verdict is not None:
        from aletheia.learning.authority import (
            measurement_harm_block_reason,
            measurement_identity_reason,
        )
        from aletheia.learning.lifecycle import trial_admission_gate_reason

        mutation_reason = trial_admission_gate_reason(
            path_id,
            persist_config_migrations=False,
        )
        if mutation_reason is None:
            mutation_reason = measurement_identity_reason(
                current_verdict,
                str(row.get("stage") or ""),
                str(policy_epoch or ""),
                expected_horizon=str(row.get("target_horizon") or ""),
                expected_behavior_contract_hash=behavior_contract_hash,
                expected_hypothesis_id=str(row.get("hypothesis_id") or ""),
            ) or measurement_harm_block_reason(current_verdict)
        mutation_available = mutation_reason is None

    authority_enabled = False
    authority_projection_failed = False
    authority_block_reason = identity_precondition_reason
    if policy_epoch is not None:
        try:
            from aletheia.learning.authority import resolve_learning_authority
            from aletheia.learning.models import LearningExperiment

            experiment = LearningExperiment.model_validate({
                field: row[field]
                for field in LearningExperiment.model_fields
                if field in row
            })
            authority = resolve_learning_authority(
                path_id,
                experiment,
                policy_epoch,
                persist_config_migrations=False,
            )
            authority_enabled = authority.authorized
            authority_block_reason = None if authority.authorized else authority.reason
        except Exception:
            authority_projection_failed = True
            authority_block_reason = "authority_projection_failed"

    source_counts = _safe_int_dict(metrics.get("outcome_source_member_counts"))
    allocation_sources = _safe_int_dict(
        metrics.get("allocation_selection_source_member_counts")
    )
    invalid_reasons = _safe_int_dict(metrics.get("invalid_reasons"))
    conflict_fingerprints = metrics.get("evidence_conflict_fingerprints")
    if not isinstance(conflict_fingerprints, list):
        conflict_fingerprints = []
    evidence_conflicts = [
        value
        for value in metrics.get("evidence_conflicts", [])
        if isinstance(value, dict)
    ]
    historical_conflicts = [
        value
        for value in verdict_metrics.get("evidence_conflicts", [])
        if isinstance(value, dict)
    ]

    return {
        "path_id": path_id,
        "availability": (
            "partial"
            if (
                bool(metrics.get("evidence_conflict_provenance_truncated"))
                or authority_projection_failed
            )
            else "available"
        ),
        "data_freshness": _freshness(
            (current_verdict or {}).get("created_at") if current else None,
            generated_at,
            stale_after_sec=stale_after_sec,
        ),
        "hypothesis_id": row.get("hypothesis_id"),
        "experiment_id": row.get("experiment_id"),
        "stage": row.get("stage"),
        "experiment_status": row.get("status"),
        "allocation_pct": row.get("allocation_pct"),
        "current_identity": {
            "policy_epoch": policy_epoch,
            "target_horizon": row.get("target_horizon"),
            "outcome_contract_version": _OUTCOME_CONTRACT_VERSION,
            "metric_version": METRIC_VERSION,
            "behavior_contract_hash": behavior_contract_hash,
            "code_commit": boot_commit,
            "matches_running_cohort": current,
            "mismatch_reason": mismatch,
        },
        "measurement": {
            "state": measurement_state,
            "reason": measurement_reason,
            "baseline": (current_verdict or {}).get("baseline") if current else None,
            "observed": (current_verdict or {}).get("observed") if current else None,
            "delta": (current_verdict or {}).get("delta") if current else None,
            "confidence_low": (
                (current_verdict or {}).get("confidence_low") if current else None
            ),
            "confidence_high": (
                (current_verdict or {}).get("confidence_high") if current else None
            ),
            "evaluated_at": (
                (current_verdict or {}).get("created_at") if current else None
            ),
            "window_start": (
                (current_verdict or {}).get("window_start") if current else None
            ),
            "window_end": (
                (current_verdict or {}).get("window_end") if current else None
            ),
        },
        "samples": {
            "raw_exposures": _safe_int(metrics.get("raw_exposure_n")),
            "treatment_assignment_units": (
                _safe_int((current_verdict or {}).get("unique_treatment_n"))
                if current else 0
            ),
            "control_assignment_units": (
                _safe_int((current_verdict or {}).get("unique_control_n"))
                if current else 0
            ),
            "required_per_arm": _safe_int(row.get("min_samples_per_arm")),
            "complete_assignment_units": _safe_int(
                metrics.get("unique_assignment_unit_n")
            ),
            "cluster_members": _safe_int(metrics.get("cluster_member_n")),
            "selected_members": _safe_int(
                metrics.get("allocation_selected_member_n")
            ),
        },
        "integrity": {
            "source_code_commits": [
                str(value)
                for value in metrics.get("source_code_commits", [])
                if isinstance(value, str)
            ],
            "source_code_commit_unknown_n": _safe_int(
                metrics.get("source_code_commit_unknown_n")
            ),
            "code_commit_provenance_complete": bool(
                metrics.get("code_commit_provenance_complete")
            ),
            "outcome_source_member_counts": source_counts,
            "allocation_selection_source_member_counts": allocation_sources,
            "matching_mirror_rows": _safe_int(
                metrics.get("mirrored_universe_row_n")
            ),
            "exclusion_reasons": _safe_int_dict(metrics.get("exclusion_reasons")),
            "invalid_reasons": invalid_reasons,
            "conflict_count": len(conflict_fingerprints),
            "evidence_conflicts": evidence_conflicts,
            "evidence_conflict_provenance_truncated": bool(
                metrics.get("evidence_conflict_provenance_truncated")
            ),
            "harm_state": str(metrics.get("harm_state") or "unknown"),
            "harm_indeterminate_reason": metrics.get("harm_indeterminate_reason"),
            "harm_breached": bool(metrics.get("breached")),
            "breach_reasons": [
                str(value)
                for value in metrics.get("breach_reasons", [])
                if isinstance(value, str)
            ],
        },
        "lifecycle": {
            "recommended_action": recommended,
            "recommendation_reason": measurement_reason,
            "authority_enabled": authority_enabled,
            "authority_block_reason": authority_block_reason,
            "mutation_available": mutation_available,
            "mutation_unavailable_reason": mutation_reason,
        },
        "latest_historical_measurement": {
            "verdict_id": row.get("verdict_id"),
            "state": row.get("verdict"),
            "reason": row.get("verdict_reason"),
            "policy_epoch": row.get("verdict_policy_epoch"),
            "stage": row.get("verdict_stage"),
            "target_horizon": row.get("verdict_target_horizon"),
            "outcome_contract_version": row.get(
                "verdict_outcome_contract_version"
            ),
            "metric_version": row.get("verdict_metric_version"),
            "behavior_contract_hash": row.get(
                "verdict_behavior_contract_hash"
            ),
            "code_commit": row.get("verdict_code_commit"),
            "source_code_commits": [
                str(value)
                for value in verdict_metrics.get("source_code_commits", [])
                if isinstance(value, str)
            ],
            "source_code_commit_unknown_n": _safe_int(
                verdict_metrics.get("source_code_commit_unknown_n")
            ),
            "code_commit_provenance_complete": bool(
                verdict_metrics.get("code_commit_provenance_complete")
            ),
            "current": (
                current
                and row.get("verdict_id") == (current_verdict or {}).get("verdict_id")
            ),
            "mismatch_reason": (
                None
                if current
                and row.get("verdict_id") == (current_verdict or {}).get("verdict_id")
                else historical_mismatch
            ),
            # Ungated on purpose: this block is explicitly labelled not-current, so
            # it is the honest home for the numbers the current-facing blocks must
            # not carry.  A harm breach measured under superseded identity stays
            # visible here instead of disappearing.
            "evaluated_at": row.get("verdict_created_at"),
            "window_start": row.get("window_start"),
            "window_end": row.get("window_end"),
            "treatment_assignment_units": _safe_int(row.get("unique_treatment_n")),
            "control_assignment_units": _safe_int(row.get("unique_control_n")),
            "complete_assignment_units": _safe_int(
                verdict_metrics.get("unique_assignment_unit_n")
            ),
            "raw_exposures": _safe_int(verdict_metrics.get("raw_exposure_n")),
            "harm_state": str(verdict_metrics.get("harm_state") or "unknown"),
            "harm_indeterminate_reason": verdict_metrics.get(
                "harm_indeterminate_reason"
            ),
            "harm_breached": bool(verdict_metrics.get("breached")),
            "breach_reasons": [
                str(value)
                for value in verdict_metrics.get("breach_reasons", [])
                if isinstance(value, str)
            ],
            "evidence_conflicts": historical_conflicts,
            "evidence_conflict_provenance_truncated": bool(
                verdict_metrics.get("evidence_conflict_provenance_truncated")
            ),
        } if row.get("verdict_id") else None,
    }


def _project_path(path: Any) -> dict[str, Any]:
    return {
        "path_id": path.path_id,
        "producer": path.producer,
        "consumer": path.consumer,
        "consumer_wired": bool(path.consumer_wired),
        "settings_supported": bool(path.settings_supported),
        "measurement_supported": bool(path.measurement_supported),
        "authority": path.authority,
        "allowed_effects": list(path.allowed_effects),
        "fallback": path.fallback,
        "outcome_source": path.outcome_source,
    }


def _identity_mismatch(
    row: dict[str, Any],
    *,
    policy_epoch: str | None,
    behavior_contract_hash: str | None,
) -> str | None:
    if not row.get("verdict_id"):
        return "no_persisted_measurement"
    if behavior_contract_hash is None:
        return "behavior_contract_unavailable"
    checks = (
        ("hypothesis_mismatch", row.get("verdict_hypothesis_id"), row.get("hypothesis_id")),
        ("stage_mismatch", row.get("verdict_stage"), row.get("stage")),
        ("policy_epoch_mismatch", row.get("verdict_policy_epoch"), policy_epoch),
        ("target_horizon_mismatch", row.get("verdict_target_horizon"), row.get("target_horizon")),
        (
            "outcome_contract_version_mismatch",
            row.get("verdict_outcome_contract_version"),
            _OUTCOME_CONTRACT_VERSION,
        ),
        ("metric_version_mismatch", row.get("verdict_metric_version"), METRIC_VERSION),
        (
            "behavior_contract_mismatch",
            row.get("verdict_behavior_contract_hash"),
            behavior_contract_hash,
        ),
    )
    for reason, actual, expected in checks:
        if actual != expected:
            return reason
    return None


def _recommended_action(state: str, reason: str | None, *, stage: str) -> str:
    if stage == "shadow" and state == "insufficient_data" and reason == "no_actual_intervention":
        return "admit_trial"
    return {
        "positive": "promote_stage" if stage != "shadow" else "admit_trial",
        "negative": "refreeze",
        "invalid": "invalidate",
    }.get(state, "hold")


def _project_health(row: dict[str, Any], *, catchup_due: bool) -> dict[str, Any]:
    return {
        "job_id": row.get("job_id"),
        "current_status": row.get("status") or "unknown",
        "last_started_at": row.get("last_started_at"),
        "last_succeeded_at": row.get("last_succeeded_at"),
        "next_due_at": row.get("next_due_at"),
        "input_watermark": row.get("input_watermark"),
        "backlog_count": _safe_int(row.get("backlog_count")),
        "oldest_backlog_at": row.get("oldest_backlog_at"),
        "consecutive_failures": _safe_int(row.get("consecutive_failures")),
        "overdue": bool(row.get("overdue")),
        "overdue_seconds": row.get("overdue_seconds"),
        "last_failed_at": row.get("last_failed_at"),
        "last_failure_code": _failure_code(
            row.get("last_failure_reason") or row.get("last_error")
        ),
        "catchup_due": bool(catchup_due),
    }


def _failure_code(value: Any) -> str | None:
    if not value:
        return None
    lowered = str(value).lower()
    if "audit_append_failed" in lowered:
        return "audit_append_failed"
    if "audit_backlog_pending" in lowered:
        return "audit_backlog_pending"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    if "database" in lowered or "sqlite" in lowered:
        return "database_unavailable"
    if "invalid" in lowered:
        return "invalid_result"
    return "internal_failure"


def _freshness(
    last_evaluated_at: str | None,
    generated_at: str,
    *,
    stale_after_sec: int,
) -> dict[str, Any]:
    age_sec = None
    state = "not_available"
    if last_evaluated_at:
        try:
            evaluated = datetime.fromisoformat(
                str(last_evaluated_at).replace("Z", "+00:00")
            )
            if evaluated.tzinfo is None:
                evaluated = evaluated.replace(tzinfo=timezone.utc)
            generated = datetime.fromisoformat(generated_at)
            age_sec = max(0, int((generated - evaluated).total_seconds()))
            state = "fresh" if age_sec <= stale_after_sec else "stale"
        except (TypeError, ValueError):
            state = "unavailable"
    return {
        "last_evaluated_at": last_evaluated_at,
        "age_sec": age_sec,
        "stale_after_sec": stale_after_sec,
        "state": state,
    }


def _safe_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_int_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): _safe_int(raw)
        for key, raw in value.items()
    }


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

"""Bounded, read-only owner projection for measured Learning contribution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from extensions.learning.cohorts import SUPPORTED_PATH_IDS
from extensions.learning.registry import LEARNING_PATHS

SCHEMA_VERSION = "lco-v1"
_MEASUREMENT_STATES = {
    "insufficient_data",
    "neutral",
    "positive",
    "negative",
    "invalid",
}


def build_learning_contribution_projection(*, limit: int = 20) -> dict[str, Any]:
    """Project persisted current facts without evaluating or mutating Learning."""
    from extensions.observation_lab.canonical import get_authority_source

    try:
        authority = get_authority_source()
    except Exception:
        return unavailable_learning_contribution_projection("experiment_authority_unavailable")
    if authority.source != "lab":
        return unavailable_learning_contribution_projection("experiment_authority_unavailable")
    return _build_lab_learning_contribution_projection(limit=limit)


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


def _build_lab_learning_contribution_projection(*, limit: int) -> dict[str, Any]:
    """Project immutable Lab results for Learning without recreating authority."""
    from extensions.observation_lab.db import readonly_lab_db

    bounded_limit = min(max(int(limit), 1), 100)
    generated_at = datetime.now(timezone.utc).isoformat()
    try:
        stale_after_sec = configured_learning_stale_after_sec()
    except Exception:
        stale_after_sec = 7200
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                return unavailable_learning_contribution_projection(
                    "canonical_lab_authority", stale_after_sec=stale_after_sec,
                )
            windows = lab.execute(
                "SELECT * FROM observation_windows ORDER BY started_at DESC, observation_id, generation DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
            items: list[dict[str, Any]] = []
            for raw_window in windows:
                window = dict(raw_window)
                observation_id = str(window["observation_id"])
                generation = int(window["generation"])
                contract_row = lab.execute(
                    "SELECT * FROM observation_generation_contracts "
                    "WHERE observation_id=? AND generation=?",
                    (observation_id, generation),
                ).fetchone()
                contract = dict(contract_row) if contract_row else {}
                spec = _safe_object(window.get("spec_json"))
                result_row = lab.execute(
                    "SELECT r.*, e.evaluation_kind FROM observation_results r "
                    "LEFT JOIN observation_evaluations e ON e.evaluation_id=r.evaluation_id "
                    "WHERE r.observation_id=? AND r.generation=? "
                    "ORDER BY r.created_at DESC, r.result_id DESC LIMIT 1",
                    (observation_id, generation),
                ).fetchone()
                result = dict(result_row) if result_row else None
                result_payload = _safe_object(result.get("result_json")) if result else {}
                domain_result = _safe_object(result_payload.get("domain_result"))
                exposures = lab.execute(
                    "SELECT COUNT(*) AS raw_n, "
                    "COUNT(DISTINCT CASE WHEN assignment='treatment' THEN assignment_unit_key END) AS treatment_n, "
                    "COUNT(DISTINCT CASE WHEN assignment='control' THEN assignment_unit_key END) AS control_n, "
                    "COUNT(DISTINCT outcome_candidate_key) AS outcome_n, "
                    "COUNT(DISTINCT CASE WHEN assignment='treatment' AND applied=1 "
                    "THEN assignment_unit_key END) AS applied_treatment_n, "
                    "COUNT(DISTINCT CASE WHEN assignment='treatment' AND behavior_changed=1 "
                    "THEN assignment_unit_key END) AS intervention_treatment_n "
                    "FROM observation_exposures WHERE observation_id=? AND generation=?",
                    (observation_id, generation),
                ).fetchone()
                lifecycle = lab.execute(
                    "SELECT event_id, action, reason, actor, source_receipt_id, to_status, created_at "
                    "FROM observation_lifecycle_events WHERE observation_id=? AND generation=? "
                    "ORDER BY created_at DESC, event_id DESC LIMIT 1",
                    (observation_id, generation),
                ).fetchone()
                plan = lab.execute(
                    "SELECT inference_contract_version, status, plan_hash, cutoff_fingerprint, consumed_evaluation_id "
                    "FROM observation_inference_plans WHERE observation_id=? AND generation=?",
                    (observation_id, generation),
                ).fetchone()
                path_id = str(spec.get("path_id") or window["kind"])
                stage = str(contract.get("stage") or spec.get("stage") or window["kind"])
                verdict = str(result.get("verdict")) if result else "not_measured"
                if verdict not in _MEASUREMENT_STATES:
                    verdict = "not_measured"
                latest_at = result.get("created_at") if result else window.get("started_at")
                lifecycle_action = str(lifecycle["action"]) if lifecycle else None
                items.append({
                    "path_id": path_id,
                    "availability": "available",
                    "data_freshness": _freshness(
                        latest_at, generated_at, stale_after_sec=stale_after_sec,
                    ),
                    "hypothesis_id": None,
                    "experiment_id": f"{observation_id}:g{generation}",
                    "stage": stage,
                    "experiment_status": window.get("status"),
                    "allocation_pct": contract.get("allocation_pct", spec.get("allocation_pct")),
                    "current_identity": {
                        "policy_epoch": contract.get("behavior_policy_epoch_ref"),
                        "target_horizon": contract.get("target_horizon") or spec.get("horizon"),
                        "outcome_contract_version": contract.get("outcome_contract_version"),
                        "metric_version": contract.get("metric_version"),
                        "statistical_unit": spec.get("statistical_unit"),
                        "behavior_contract_hash": contract.get("behavior_contract_hash"),
                        "code_commit": window.get("producer_commit"),
                        # Lab has no authoritative view of the currently
                        # running Main cohort in this projection.  Preserve
                        # that missing comparison instead of claiming a match.
                        "matches_running_cohort": None,
                        "mismatch_reason": "running_cohort_identity_unavailable",
                    },
                    "measurement": {
                        "state": verdict,
                        "reason": domain_result.get("reason") or (result.get("disposition") if result else "no_persisted_measurement"),
                        "baseline": domain_result.get("baseline"),
                        "observed": domain_result.get("observed"),
                        "delta": domain_result.get("delta"),
                        "confidence_low": domain_result.get("confidence_low"),
                        "confidence_high": domain_result.get("confidence_high"),
                        "evaluated_at": latest_at,
                        "window_start": window.get("started_at"),
                        "window_end": window.get("collect_until"),
                        "inference_role": (
                            result.get("evaluation_kind")
                            if result and result.get("evaluation_kind") in {"monitoring", "confirmatory"}
                            else "monitoring"
                        ),
                        "authority_eligible": False,
                    },
                    "inference": {
                        "contract_version": plan["inference_contract_version"] if plan else None,
                        "status": plan["status"] if plan else "not_registered",
                        "plan_hash": plan["plan_hash"] if plan else None,
                        "cutoff_fingerprint": plan["cutoff_fingerprint"] if plan else None,
                        "consumed_verdict_id": plan["consumed_evaluation_id"] if plan else None,
                        "authority_block_reason": "owner_admission_required",
                    },
                    "samples": {
                        "raw_exposures": int(exposures["raw_n"] or 0),
                        "exposed_treatment_units": int(exposures["treatment_n"] or 0),
                        "exposed_authority_applied_treatment_units": int(exposures["applied_treatment_n"] or 0),
                        "exposed_actual_intervention_treatment_units": int(exposures["intervention_treatment_n"] or 0),
                        "exposed_control_units": int(exposures["control_n"] or 0),
                        "treatment_assignment_units": int(exposures["treatment_n"] or 0),
                        "control_assignment_units": int(exposures["control_n"] or 0),
                        "complete_assignment_units": int(exposures["treatment_n"] or 0) + int(exposures["control_n"] or 0),
                        "required_per_arm": None,
                    },
                    "integrity": {
                        "source_cutoff": result.get("source_cutoff") if result else None,
                        "result_id": result.get("result_id") if result else None,
                        "result_fingerprint": result.get("result_fingerprint") if result else None,
                        "evaluation_id": result.get("evaluation_id") if result else None,
                        "archive_id": result.get("archive_id") if result else None,
                        "outcome_count": int(exposures["outcome_n"] or 0),
                        "harm_state": str(domain_result.get("harm_state") or "unknown"),
                        "harm_breached": bool(domain_result.get("breached")),
                        "exclusion_reasons": {},
                        "invalid_reasons": {},
                        "evidence_conflicts": [],
                        "evidence_conflict_provenance_truncated": False,
                    },
                    "lifecycle": {
                        "recommended_action": "hold",
                        "recommendation_reason": "lab_result_requires_owner_adoption",
                        "authority_enabled": False,
                        "authority_block_reason": "owner_admission_required",
                        "mutation_available": False,
                        "mutation_unavailable_reason": "canonical_lab_authority",
                        "latest_event": dict(lifecycle) if lifecycle else None,
                        "latest_action": lifecycle_action,
                    },
                    "latest_historical_measurement": (
                        {
                            "result_id": result["result_id"],
                            "state": verdict,
                            "reason": domain_result.get("reason") or result.get("disposition"),
                            "evaluated_at": result.get("created_at"),
                            "source_cutoff": result.get("source_cutoff"),
                            "result_fingerprint": result.get("result_fingerprint"),
                            "current": True,
                        }
                        if result else None
                    ),
                })
            job_health = {
                str(row["job_id"]): {
                    "status": row["status"],
                    "input_watermark": row["input_watermark"],
                    "backlog_count": row["backlog_count"],
                    "last_succeeded_at": row["last_succeeded_at"],
                    "last_failed_at": row["last_failed_at"],
                    "consecutive_failures": row["consecutive_failures"],
                }
                for row in lab.execute(
                    "SELECT job_id, status, input_watermark, backlog_count, last_succeeded_at, "
                    "last_failed_at, consecutive_failures FROM observation_job_health ORDER BY job_id"
                ).fetchall()
            }
    except Exception:
        return unavailable_learning_contribution_projection(
            "canonical_lab_projection_failed", stale_after_sec=stale_after_sec,
        )

    path_states = {
        path_id: {
            "path_id": path_id,
            "availability": "available",
            "unavailable_reason": None,
            "state": "empty",
            "data_freshness": _freshness(None, generated_at, stale_after_sec=stale_after_sec),
        }
        for path_id in sorted(SUPPORTED_PATH_IDS)
    }
    for item in items:
        state = path_states.get(item["path_id"])
        if state:
            if item.get("experiment_status") == "active":
                state.update({
                    "state": "active",
                    "data_freshness": item["data_freshness"],
                })
            elif state["state"] == "empty":
                state.update({
                    "state": "terminal",
                    "data_freshness": item["data_freshness"],
                })
    latest = [item["data_freshness"].get("last_evaluated_at") for item in items if item["data_freshness"].get("last_evaluated_at")]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "availability": "available",
        "unavailable_reason": None,
        "data_freshness": _freshness(
            max(latest) if latest else None, generated_at, stale_after_sec=stale_after_sec,
        ),
        "paths": [_project_path(path) for path in LEARNING_PATHS],
        "path_states": list(path_states.values()),
        "items": items,
        "operations": {
            "availability": "available",
            "query_errors": {},
            "config": None,
            "evaluator": None,
            "lifecycle_audit": None,
            "job_health": job_health,
        },
    }


def _project_path(path: Any) -> dict[str, Any]:
    return {
        "path_id": path.path_id,
        "producer": path.producer,
        "consumer": path.consumer,
        "supported": path.measurement_supported,
        "consumer_wired": bool(path.consumer_wired),
        "settings_supported": bool(path.settings_supported),
        "measurement_supported": bool(path.measurement_supported),
        "paper_authority_supported": bool(path.paper_authority_supported),
        "authority": path.authority,
        "allowed_effects": list(path.allowed_effects),
        "fallback": path.fallback,
        "outcome_source": path.outcome_source,
        "behavior_contract_version": path.behavior_contract_version,
        "outcome_contract_version": path.outcome_contract_version,
        "metric_version": path.metric_version,
        "statistical_unit": path.statistical_unit,
    }


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

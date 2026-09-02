"""Lab-owned evaluation over canonical exposures.

The statistical implementation remains the existing measurement contract in
``learning.cohorts``/``learning.verdicts``.  This module supplies those pure
calculations with an immutable Lab exposure snapshot and records only the Lab
evaluation/result rows.  It never writes or consults the legacy Experiment
ledger.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
from typing import Any, Mapping

from aletheia.observation_lab.db import readonly_lab_db


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _integer(*values: Any, default: int) -> int:
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed >= 0:
            return parsed
    return default


def _finite(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _optional_finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _strict_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return None


def _gate_integer(
    gates: Mapping[str, Any],
    spec: Mapping[str, Any],
    names: tuple[str, ...],
    *,
    default: int,
) -> int | None:
    for source in (gates, spec):
        for name in names:
            if name not in source:
                continue
            value = source[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return None
            return value
    return default


def _path_id(
    *,
    spec: Mapping[str, Any],
    window_kind: str,
    contract: Mapping[str, Any],
    manifests: list[Mapping[str, Any]],
) -> str | None:
    path = spec.get("path_id")
    if isinstance(path, str) and path.strip():
        return path.strip()
    for row in manifests:
        population = _object(row.get("eligible_population_json"))
        path = population.get("path_id")
        if isinstance(path, str) and path.strip():
            return path.strip()
    outcome_contract = str(contract.get("outcome_contract_version") or "")
    if outcome_contract == "ranker-case-filter-candidate/v1":
        # A missing path is still ambiguous between the two candidate-level
        # adapters; do not invent authority from a storage shape alone.
        return None
    if window_kind in {"universe_family_calibration", "momentum_entry_geometry", "ranker_case_score"}:
        return window_kind
    return None


def _models(
    *,
    observation_id: str,
    generation: int,
    root: Mapping[str, Any],
    contract: Mapping[str, Any],
    spec: Mapping[str, Any],
    manifests: list[Mapping[str, Any]],
):
    from aletheia.learning.models import LearningExperiment, LearningHypothesis
    from aletheia.learning.registry import get_learning_path

    path_id = _path_id(
        spec=spec,
        window_kind=str(root.get("kind") or ""),
        contract=contract,
        manifests=manifests,
    )
    if path_id is None:
        return None
    try:
        path = get_learning_path(path_id)
    except KeyError:
        return None
    if not path.measurement_supported:
        return None
    gates = _object(contract.get("gates_json"))
    harm_budget = _object(contract.get("harm_budget_json"))
    experiment_id = f"{observation_id}:g{int(generation)}"
    hypothesis_id = f"{experiment_id}:hypothesis"
    stage = str(contract.get("stage") or spec.get("stage") or "shadow")
    target_horizon = str(
        contract.get("target_horizon") or spec.get("horizon") or ""
    )
    if not target_horizon:
        return None
    raw_effect_threshold = spec.get("effect_threshold")
    if raw_effect_threshold is None:
        raw_effect_threshold = gates.get("effect_threshold", 0.0)
    effect_threshold = _optional_finite(raw_effect_threshold)
    if effect_threshold is None:
        return None
    for key in ("max_loss_rate_increase", "max_mae_p95_worsening_pp"):
        if key in harm_budget:
            value = _optional_finite(harm_budget[key])
            if value is None or value < 0.0:
                return None
    min_samples_per_arm = _gate_integer(
        gates, spec,
        ("min_samples_per_arm", "minimum_assignment_units_per_arm", "min_samples"),
        default=50,
    )
    min_market_days = _gate_integer(
        gates, spec,
        ("min_market_days", "minimum_consecutive_market_days"),
        default=7,
    )
    if min_samples_per_arm is None or min_market_days is None:
        return None
    started_at = str(root.get("started_at") or contract.get("started_at") or "")
    if _parse_time(started_at) is None:
        return None
    hypothesis = LearningHypothesis(
        hypothesis_id=hypothesis_id,
        fingerprint=str(root.get("spec_hash") or contract.get("contract_hash") or ""),
        behavior_contract_version=path.behavior_contract_version,
        fingerprint_schema_version=1,
        cohort_id=str(contract.get("assignment_namespace_key") or observation_id),
        path_id=path_id,
        overlap_group=path.overlap_group,
        source_type="observation_lab",
        scope_json=_json(spec.get("scope") or {}),
        action_json=_json(spec.get("action") or {}),
        consumer=str(spec.get("consumer") or path.consumer),
        target_horizon=target_horizon,
        status="active" if str(root.get("status")) == "active" else "terminal",
        effect_threshold=effect_threshold,
        harm_budget_json=_json(harm_budget),
        evidence_summary_json="{}",
        created_at=started_at,
        updated_at=started_at,
    )
    experiment = LearningExperiment(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        stage=stage,
        assignment_salt=str(contract.get("assignment_salt") or ""),
        allocation_pct=_finite(contract.get("allocation_pct"), default=0.0),
        min_samples_per_arm=max(1, min_samples_per_arm),
        min_market_days=min_market_days,
        primary_metric=str(contract.get("metric_version") or path.metric_version),
        promotion_gate_json=_json(gates),
        harm_gate_json=_json(harm_budget),
        status="active" if str(root.get("status")) == "active" else str(root.get("status") or "terminal"),
        stage_generation=int(generation),
        started_at=started_at,
        ended_at=(str(root.get("terminal_at")) if root.get("terminal_at") else None),
        created_at=started_at,
    )
    return experiment, hypothesis, path_id


def _source_rows(
    *,
    exposures: list[Mapping[str, Any]],
    manifests: list[Mapping[str, Any]],
    experiment_id: str,
    hypothesis_id: str,
    stage: str,
    outcome_contract_version: str,
    policy_epoch: str | None,
) -> list[dict[str, Any]]:
    manifests_by_identity = {
        str(row.get("manifest_hash") or row.get("manifest_id")): row
        for row in manifests
    }
    rows: list[dict[str, Any]] = []
    for raw in exposures:
        row = dict(raw)
        manifest = manifests_by_identity.get(
            str(row.get("manifest_hash") or row.get("manifest_id")),
            {},
        )
        effect_mode = str(manifest.get("effect_mode") or "observe_only")
        assignment = str(row.get("assignment") or "unassigned")
        applied = _strict_bool(row.get("applied"))
        behavior_changed = _strict_bool(row.get("behavior_changed"))
        if applied is None or behavior_changed is None:
            raise ValueError("malformed_exposure_boolean")
        authority_applied = effect_mode == "paper_treatment" and applied and assignment == "treatment"
        behavior_reason = (
            "effective_treatment_delta"
            if behavior_changed
            else "no_effective_treatment_delta"
            if authority_applied
            else "control_assignment"
            if assignment == "control"
            else "authority_not_applied"
        )
        row.update({
            "experiment_id": experiment_id,
            "hypothesis_id": hypothesis_id,
            "path_id": str(row.get("path_id") or ""),
            "overlap_group": str(row.get("overlap_group") or ""),
            "assignment": assignment,
            "assignment_unit_key": row.get("assignment_unit_key"),
            "candidate_key": row.get("candidate_key"),
            "before_json": row.get("baseline_payload_json") or "{}",
            "after_json": row.get("applied_payload_json") or "{}",
            "manifest_json": _json({
                "manifest_id": row.get("manifest_id"),
                "manifest_hash": row.get("manifest_hash"),
                "effect_mode": effect_mode,
                "authority_applied": authority_applied,
                "authority_reason": (
                    "experiment_stage_not_paper"
                    if effect_mode != "paper_treatment" and stage == "shadow"
                    else str(row.get("resolution_reason") or "authority_not_applied")
                ),
                "behavior_changed": behavior_changed,
                "behavior_change_reason": behavior_reason,
            }),
            "outcome_contract_version": outcome_contract_version,
            "experiment_stage": stage,
            "concurrent_policy_epoch": row.get("behavior_policy_epoch_ref") or "",
            "code_commit": row.get("producer_commit"),
            "outcome_source": row.get("outcome_source"),
            "exposed_at": row.get("exposed_at"),
        })
        rows.append(row)
    return rows


def _plan_cutoff(
    plan: Mapping[str, Any] | None,
    *,
    observation_id: str,
    generation: int,
    evaluation_kind: str,
) -> tuple[
    frozenset[str] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
    str | None,
]:
    if evaluation_kind != "confirmatory":
        return None, None, None, None
    if plan is None or str(plan.get("status")) not in {"frozen", "consumed"}:
        return None, None, None, "inference_plan_not_frozen"
    plan_payload = _object(plan.get("plan_json"))
    try:
        plan_valid = bool(plan_payload) and _hash({
            "observation_id": observation_id,
            "generation": int(generation),
            "inference_contract_version": plan.get("inference_contract_version"),
            "plan_json": _json(plan_payload),
        }) == str(plan.get("plan_hash") or "")
    except (TypeError, ValueError):
        plan_valid = False
    if not plan_valid:
        return None, None, None, "inference_plan_invalid"
    cutoff = _object(plan.get("cutoff_json"))
    keys = cutoff.get("assignment_unit_keys")
    if (
        not isinstance(keys, list)
        or not keys
        or not all(isinstance(key, str) and key for key in keys)
        or len(set(keys)) != len(keys)
    ):
        return None, None, plan_payload, "inference_cutoff_unavailable"
    try:
        cutoff_valid = _hash(cutoff) == str(plan.get("cutoff_fingerprint") or "")
    except (TypeError, ValueError):
        cutoff_valid = False
    if not cutoff_valid:
        return None, None, plan_payload, "inference_cutoff_invalid"
    return frozenset(keys), cutoff, plan_payload, None


def _evaluation_payload(cohort: Any, evaluation: Any) -> dict[str, Any]:
    return {
        "baseline": evaluation.baseline,
        "observed": evaluation.observed,
        "delta": evaluation.delta,
        "confidence_low": evaluation.confidence_low,
        "confidence_high": evaluation.confidence_high,
        "harm_metrics": evaluation.harm_metrics,
        "verdict": evaluation.verdict,
        "reason": evaluation.reason,
        "unique_treatment_n": evaluation.unique_treatment_n,
        "unique_control_n": evaluation.unique_control_n,
        "cohort": cohort.inspection(),
    }


def _evaluate_observation(
    observation_id: str,
    generation: int | None = None,
    *,
    evaluation_kind: str = "monitoring",
    as_of_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Evaluate one Lab Generation and persist immutable Lab result rows.

    Confirmatory evaluation is accepted only with a frozen Lab inference plan;
    the plan's assignment-unit cutoff is applied before exact outcomes resolve.
    """
    if evaluation_kind not in {"monitoring", "confirmatory"}:
        raise ValueError("invalid_evaluation_kind")
    cutoff_at = _parse_time(as_of_at) if as_of_at is not None else _now()
    if cutoff_at is None:
        raise ValueError("as_of_at_must_be_utc")
    with readonly_lab_db(allow_missing=True) as lab:
        if lab is None:
            return {"available": False, "reason": "lab_unavailable"}
        if generation is None:
            root = lab.execute(
                "SELECT * FROM observation_windows WHERE observation_id=? "
                "ORDER BY generation DESC LIMIT 1",
                (observation_id,),
            ).fetchone()
        else:
            root = lab.execute(
                "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
                (observation_id, int(generation)),
            ).fetchone()
        if root is None:
            return {"available": False, "reason": "observation_generation_unavailable"}
        root = dict(root)
        generation = int(root["generation"])
        contract_row = lab.execute(
            "SELECT * FROM observation_generation_contracts WHERE observation_id=? AND generation=?",
            (observation_id, generation),
        ).fetchone()
        if contract_row is None:
            return {"available": False, "reason": "generation_contract_unavailable"}
        contract = dict(contract_row)
        spec = _object(root.get("spec_json"))
        manifest_rows = [dict(row) for row in lab.execute(
            "SELECT * FROM execution_manifests WHERE observation_id=? AND generation=? ORDER BY manifest_id",
            (observation_id, generation),
        ).fetchall()]
        exposures = [dict(row) for row in lab.execute(
            "SELECT * FROM observation_exposures WHERE observation_id=? AND generation=? "
            "ORDER BY exposed_at, source_receipt_id",
            (observation_id, generation),
        ).fetchall()]
        plan_row = lab.execute(
            "SELECT * FROM observation_inference_plans WHERE observation_id=? AND generation=?",
            (observation_id, generation),
        ).fetchone()
        plan = dict(plan_row) if plan_row else None
        consumed_evaluation = None
        consumed_result = None
        if (
            evaluation_kind == "confirmatory"
            and plan is not None
            and str(plan.get("status")) == "consumed"
        ):
            consumed_id = plan.get("consumed_evaluation_id")
            if consumed_id:
                consumed_evaluation_row = lab.execute(
                    "SELECT * FROM observation_evaluations WHERE evaluation_id=?",
                    (consumed_id,),
                ).fetchone()
                if consumed_evaluation_row is not None:
                    consumed_evaluation = dict(consumed_evaluation_row)
                    consumed_result_row = lab.execute(
                        "SELECT * FROM observation_results WHERE observation_id=? AND generation=? "
                        "AND evaluation_id=? ORDER BY created_at DESC LIMIT 1",
                        (observation_id, generation, consumed_id),
                    ).fetchone()
                    consumed_result = (
                        dict(consumed_result_row)
                        if consumed_result_row is not None else None
                    )
        cursor = lab.execute(
            "SELECT contiguous_sequence, source_checkpoint_fingerprint FROM observation_import_cursors "
            "WHERE source_name='main'"
        ).fetchone()
        source_cutoff = {
            "source_name": "main",
            "main_import_contiguous_sequence": int(cursor[0]) if cursor else 0,
            "source_checkpoint_fingerprint": str(cursor[1]) if cursor and cursor[1] else None,
            "collect_until": root.get("collect_until"),
        }
    models = _models(
        observation_id=observation_id,
        generation=generation,
        root=root,
        contract=contract,
        spec=spec,
        manifests=manifest_rows,
    )
    if models is None:
        return {"available": False, "reason": "measurement_path_unavailable"}
    experiment, hypothesis, path_id = models
    expected_policy_epoch = str(contract.get("behavior_policy_epoch_ref") or "").strip()
    policy_epochs = {
        str(row.get("behavior_policy_epoch_ref") or "").strip()
        for row in exposures
    }
    if exposures and (
        (
            expected_policy_epoch
            and any(epoch != expected_policy_epoch for epoch in policy_epochs)
        )
        or (
            not expected_policy_epoch
            and (
                not policy_epochs
                or "" in policy_epochs
                or len(policy_epochs) != 1
            )
        )
    ):
        return {
            "available": False,
            "reason": (
                "policy_epoch_contract_mismatch"
                if expected_policy_epoch
                else "policy_epoch_unavailable"
            ),
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
        }
    assignment_units, cutoff, plan_payload, plan_reason = _plan_cutoff(
        plan,
        observation_id=observation_id,
        generation=generation,
        evaluation_kind=evaluation_kind,
    )
    if plan_reason is not None:
        return {
            "available": False,
            "reason": plan_reason,
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
        }
    if evaluation_kind == "confirmatory" and str(plan.get("status")) == "consumed":
        if (
            consumed_evaluation is None
            or str(consumed_evaluation.get("evaluation_kind")) != "confirmatory"
            or str(consumed_evaluation.get("observation_id")) != observation_id
            or _integer(consumed_evaluation.get("generation"), default=0) != int(generation)
        ):
            return {
                "available": False,
                "reason": "inference_consumption_unavailable",
                "observation_id": observation_id,
                "generation": generation,
                "evaluation_kind": evaluation_kind,
            }
        payload = _object(consumed_evaluation.get("result_json"))
        if not payload:
            return {
                "available": False,
                "reason": "inference_consumption_unavailable",
                "observation_id": observation_id,
                "generation": generation,
                "evaluation_kind": evaluation_kind,
            }
        return {
            "available": True,
            "observation_id": observation_id,
            "generation": generation,
            "path_id": path_id,
            "evaluation_kind": evaluation_kind,
            "source_cutoff": _object(consumed_evaluation.get("source_cutoff")),
            "evaluations": [{
                "evaluation_id": consumed_evaluation["evaluation_id"],
                "result_id": consumed_result.get("result_id") if consumed_result else None,
                **payload,
            }],
            "verdict": consumed_evaluation["verdict"],
            "reason": payload.get("reason") or consumed_evaluation["verdict"],
        }
    authority_alpha = None
    if evaluation_kind == "confirmatory":
        source_cutoff.update({
            "inference_plan_hash": str(plan["plan_hash"]),
            "inference_cutoff_fingerprint": str(plan["cutoff_fingerprint"]),
        })
        authority_alpha = _finite(
            (plan_payload or {}).get("authority_alpha"), default=0.025,
        )
        if not 0.0 < authority_alpha < 1.0:
            return {
                "available": False,
                "reason": "inference_plan_invalid",
                "observation_id": observation_id,
                "generation": generation,
                "evaluation_kind": evaluation_kind,
            }
    policy_epoch = contract.get("behavior_policy_epoch_ref")
    from aletheia.learning.registry import (
        get_learning_path,
        outcome_contract_version_for_path,
    )
    expected_outcome_contract = outcome_contract_version_for_path(path_id)
    if str(contract.get("outcome_contract_version") or "") != expected_outcome_contract:
        return {
            "available": False,
            "reason": "outcome_contract_mismatch",
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
        }
    if str(contract.get("metric_version") or "") != get_learning_path(path_id).metric_version:
        return {
            "available": False,
            "reason": "metric_contract_mismatch",
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
        }

    rows = _source_rows(
        exposures=exposures,
        manifests=manifest_rows,
        experiment_id=experiment.experiment_id,
        hypothesis_id=hypothesis.hypothesis_id,
        stage=experiment.stage,
        outcome_contract_version=expected_outcome_contract,
        policy_epoch=str(policy_epoch) if policy_epoch else None,
    )
    # The Lab source identity is authoritative for Lab rows.  A missing epoch
    # remains a visible invalid cohort; it is never replaced by Main config.
    for row in rows:
        if not row.get("path_id"):
            row["path_id"] = path_id
        if not row.get("overlap_group"):
            row["overlap_group"] = get_learning_path(path_id).overlap_group
    from aletheia.learning.cohorts import build_experiment_cohorts
    from aletheia.learning.verdicts import evaluate_cohort

    cohorts = build_experiment_cohorts(
        experiment.experiment_id,
        assignment_unit_keys=assignment_units,
        as_of_at=cutoff_at,
        _source_rows=rows,
        _source_experiment=experiment,
        _source_hypothesis=hypothesis,
        _source_valid_policy_epochs={
            str(row.get("concurrent_policy_epoch"))
            for row in rows
            if row.get("concurrent_policy_epoch")
        },
    )
    if not cohorts:
        return {
            "available": False,
            "reason": "cohort_unavailable",
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
        }
    from aletheia.observation_lab.canonical import (
        _hash as canonical_hash,
        record_observation_evaluation,
        record_observation_result,
    )

    exposure_fingerprint = _hash([
        {
            key: row.get(key)
            for key in (
                "source_receipt_id", "receipt_sequence", "assignment_unit_key",
                "candidate_key", "assignment", "applied", "behavior_changed",
                "outcome_candidate_key", "outcome_source", "exposed_at",
                "baseline_payload_hash", "applied_payload_hash",
            )
        }
        for row in rows
    ])
    evaluation_rows: list[dict[str, Any]] = []
    for cohort in cohorts:
        evaluated = evaluate_cohort(cohort, authority_alpha=authority_alpha)
        payload = _evaluation_payload(cohort, evaluated)
        outcome_fingerprint = _hash([
            asdict(sample) for sample in cohort.samples
        ] + [
            dict(cohort.exclusion_reasons), dict(cohort.invalid_reasons),
        ])
        input_fingerprint = canonical_hash({
            "observation_id": observation_id,
            "generation": generation,
            "spec_hash": root["spec_hash"],
            "contract_hash": contract["contract_hash"],
            "evaluation_kind": evaluation_kind,
            "inference_plan_hash": plan.get("plan_hash") if plan else None,
            "inference_cutoff_fingerprint": plan.get("cutoff_fingerprint") if plan else None,
            "source_cutoff": source_cutoff,
            "assignment_unit_keys": sorted(assignment_units) if assignment_units is not None else None,
            "cohort": cohort.inspection(),
        })
        evaluation_id = record_observation_evaluation(
            observation_id=observation_id,
            generation=generation,
            evaluation_kind=evaluation_kind,
            source_cutoff=_json(source_cutoff),
            input_fingerprint=input_fingerprint,
            exposure_fingerprint=exposure_fingerprint,
            outcome_fingerprint=outcome_fingerprint,
            result=payload,
            verdict=evaluated.verdict,
            disposition="evaluated",
        )
        result_id = record_observation_result(
            observation_id=observation_id,
            generation=generation,
            spec_hash=str(root["spec_hash"]),
            source_cutoff=_json(source_cutoff),
            verdict=evaluated.verdict,
            disposition="evaluated",
            result=payload,
            evaluation_id=evaluation_id,
        )
        evaluation_rows.append({
            "evaluation_id": evaluation_id,
            "result_id": result_id,
            **payload,
        })
    verdicts = {str(row["verdict"]) for row in evaluation_rows}
    overall_verdict = next(iter(verdicts)) if len(verdicts) == 1 else "invalid"
    return {
        "available": True,
        "observation_id": observation_id,
        "generation": generation,
        "path_id": path_id,
        "evaluation_kind": evaluation_kind,
        "source_cutoff": source_cutoff,
        "evaluations": evaluation_rows,
        "verdict": overall_verdict,
        "reason": (
            evaluation_rows[0]["reason"]
            if len(evaluation_rows) == 1
            else "multiple_cohort_identities"
        ),
        "exposure_fingerprint": exposure_fingerprint,
    }


def evaluate_observation(
    observation_id: str,
    generation: int | None = None,
    *,
    evaluation_kind: str = "monitoring",
    as_of_at: str | datetime | None = None,
) -> dict[str, Any]:
    if evaluation_kind not in {"monitoring", "confirmatory"}:
        raise ValueError("invalid_evaluation_kind")
    if as_of_at is not None and _parse_time(as_of_at) is None:
        raise ValueError("as_of_at_must_be_utc")
    try:
        return _evaluate_observation(
            observation_id,
            generation,
            evaluation_kind=evaluation_kind,
            as_of_at=as_of_at,
        )
    except (
        FileNotFoundError,
        OSError,
        sqlite3.Error,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:
        return {
            "available": False,
            "reason": "evaluation_projection_unavailable",
            "observation_id": observation_id,
            "generation": generation,
            "evaluation_kind": evaluation_kind,
            "error": type(exc).__name__,
        }


def evaluate_active_observations(*, limit: int = 10) -> dict[str, Any]:
    """Run one bounded monitoring pass for active measurement generations."""
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                return {"available": False, "reason": "lab_unavailable", "evaluated": 0}
            rows = lab.execute(
                "SELECT observation_id, generation FROM observation_windows "
                "WHERE status='active' ORDER BY started_at, observation_id, generation LIMIT ?",
                (max(0, min(int(limit), 100)),),
            ).fetchall()
    except Exception as exc:
        return {"available": False, "reason": "lab_projection_unavailable", "evaluated": 0, "error": type(exc).__name__}
    results: dict[str, Any] = {}
    for row in rows:
        key = f"{row['observation_id']}:{int(row['generation'])}"
        try:
            results[key] = evaluate_observation(str(row["observation_id"]), int(row["generation"]))
        except Exception as exc:
            results[key] = {"available": False, "reason": "evaluation_failed", "error": type(exc).__name__}
    return {
        "available": True,
        "evaluated": len(results),
        "results": results,
    }


__all__ = ["evaluate_active_observations", "evaluate_observation"]

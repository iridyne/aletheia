"""Prospective, once-consumed fixed-look inference for paper Learning.

Hourly measurements remain descriptive.  Only a paper experiment registered
before its first exposure can freeze one outcome-blind assignment-unit cutoff
and consume one matching confirmatory verdict.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from aletheia.learning.cohorts import _exposure_behavior_change, _manifest_authority
from aletheia.learning.ledger import get_record, insert_record
from aletheia.learning.models import (
    LearningContributionVerdict,
    LearningExperiment,
    LearningHypothesis,
    LearningInferenceDecision,
)
from aletheia.learning.verdicts import METRIC_VERSION
from aletheia.storage.repositories import transaction
from aletheia.storage.sqlite import get_db


INFERENCE_CONTRACT_VERSION = "ufc-fixed-look/v1"
AUTHORITY_ALPHA = 0.025
_PAPER_STAGES = frozenset({"paper_10", "paper_25", "paper_50", "paper_active"})


class InferenceContractError(RuntimeError):
    """A prospective inference invariant cannot be satisfied."""


@dataclass(frozen=True)
class _AssignmentUnit:
    key: str
    assignment: str
    exposed_at: str
    exposed_time: datetime
    actual_intervention: bool


def register_fixed_look_decision(
    experiment: LearningExperiment,
    hypothesis: LearningHypothesis,
    *,
    policy_epoch: str,
    behavior_contract_hash: str,
) -> LearningInferenceDecision:
    """Persist one immutable plan before a paper experiment gathers exposure."""

    if experiment.stage not in _PAPER_STAGES:
        raise InferenceContractError("fixed-look inference requires a paper stage")
    if hypothesis.path_id != "universe_family_calibration":
        raise InferenceContractError("fixed-look inference path is unsupported")
    if experiment.hypothesis_id != hypothesis.hypothesis_id:
        raise InferenceContractError("inference experiment hypothesis mismatch")
    if not policy_epoch or not behavior_contract_hash:
        raise InferenceContractError("inference identity is incomplete")

    family_payload = {
        "path_id": hypothesis.path_id,
        "policy_epoch": policy_epoch,
        "behavior_contract_hash": behavior_contract_hash,
        "metric_version": METRIC_VERSION,
    }
    family_id = _hash_json(family_payload)[:24]
    plan = {
        "inference_contract_version": INFERENCE_CONTRACT_VERSION,
        "inference_family_id": family_id,
        "experiment_id": experiment.experiment_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "stage": experiment.stage,
        "stage_generation": int(experiment.stage_generation),
        "path_id": hypothesis.path_id,
        "policy_epoch": policy_epoch,
        "target_horizon": hypothesis.target_horizon,
        "outcome_contract_version": "v1",
        "metric_version": METRIC_VERSION,
        "behavior_contract_hash": behavior_contract_hash,
        "minimum_assignment_units_per_arm": max(
            1, int(experiment.min_samples_per_arm)
        ),
        "minimum_actual_treatment_interventions": max(
            1, int(experiment.min_samples_per_arm)
        ),
        "minimum_consecutive_market_days": max(
            0, int(experiment.min_market_days)
        ),
        "authority_alpha": AUTHORITY_ALPHA,
        "estimand": "randomized_assignment_intent_to_treat",
        "cutoff_order": ["exposed_at", "assignment_unit_key"],
        "decision_looks": 1,
        "post_cutoff_samples": "excluded",
    }
    plan_json = _canonical_json(plan)
    plan_hash = hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
    now = _now()
    decision = LearningInferenceDecision(
        experiment_id=experiment.experiment_id,
        inference_family_id=family_id,
        inference_contract_version=INFERENCE_CONTRACT_VERSION,
        plan_json=plan_json,
        plan_hash=plan_hash,
        authority_alpha=AUTHORITY_ALPHA,
        created_at=now,
        updated_at=now,
    )

    with transaction(immediate=True):
        existing = get_fixed_look_decision(experiment.experiment_id)
        if existing is not None:
            if existing.plan_hash != plan_hash:
                raise InferenceContractError("inference plan already differs")
            return existing
        competing = get_db().execute(
            "SELECT d.experiment_id FROM learning_inference_decisions d "
            "JOIN learning_experiments e ON e.experiment_id=d.experiment_id "
            "WHERE d.inference_family_id=? AND e.ended_at IS NULL "
            "AND d.experiment_id!=? LIMIT 1",
            (family_id, experiment.experiment_id),
        ).fetchone()
        if competing is not None:
            raise InferenceContractError("inference_family_already_active")
        if get_db().execute(
            "SELECT 1 FROM learning_exposures WHERE experiment_id=? LIMIT 1",
            (experiment.experiment_id,),
        ).fetchone() is not None:
            raise InferenceContractError("inference_plan_not_prospective")
        if not insert_record("learning_inference_decisions", decision):
            raise InferenceContractError("inference plan insert failed")
    return decision


def get_fixed_look_decision(experiment_id: str) -> LearningInferenceDecision | None:
    row = get_record("learning_inference_decisions", experiment_id)
    return LearningInferenceDecision.model_validate(row) if row is not None else None


def freeze_fixed_look_cutoff(experiment_id: str) -> LearningInferenceDecision:
    """Freeze the earliest qualifying exposure prefix exactly once."""

    decision = get_fixed_look_decision(experiment_id)
    if decision is None:
        raise InferenceContractError("inference_contract_unavailable")
    if decision.status != "collecting":
        return decision
    plan = _validated_plan(decision)
    units = _prospective_assignment_units(experiment_id, plan)
    selected = _earliest_qualifying_prefix(units, plan)
    if selected is None:
        return decision

    cutoff = {
        "assignment_unit_keys": [unit.key for unit in selected],
        "cutoff_exposed_at": selected[-1].exposed_at,
        "cutoff_assignment_unit_key": selected[-1].key,
        "window_start_date": selected[0].exposed_time.date().isoformat(),
        "window_end_date": selected[-1].exposed_time.date().isoformat(),
        "treatment_n": sum(unit.assignment == "treatment" for unit in selected),
        "control_n": sum(unit.assignment == "control" for unit in selected),
        "actual_intervention_treatment_n": sum(
            unit.assignment == "treatment" and unit.actual_intervention
            for unit in selected
        ),
        "selection_basis": "outcome_blind_exposure_prefix",
    }
    cutoff_json = _canonical_json(cutoff)
    cutoff_fingerprint = hashlib.sha256(cutoff_json.encode("utf-8")).hexdigest()
    now = _now()
    with transaction(immediate=True):
        cursor = get_db().execute(
            "UPDATE learning_inference_decisions SET status='cutoff_frozen', "
            "cutoff_json=?, cutoff_fingerprint=?, cutoff_frozen_at=?, updated_at=? "
            "WHERE experiment_id=? AND status='collecting' AND plan_hash=?",
            (
                cutoff_json,
                cutoff_fingerprint,
                now,
                now,
                experiment_id,
                decision.plan_hash,
            ),
        )
        if cursor.rowcount != 1:
            persisted = get_fixed_look_decision(experiment_id)
            if persisted is None:
                raise InferenceContractError("inference decision disappeared")
            return persisted
    persisted = get_fixed_look_decision(experiment_id)
    if persisted is None:
        raise InferenceContractError("frozen inference decision unavailable")
    return persisted


def cutoff_assignment_unit_keys(decision: LearningInferenceDecision) -> tuple[str, ...]:
    if decision.status not in {"cutoff_frozen", "consumed"}:
        return ()
    try:
        payload = json.loads(decision.cutoff_json or "")
        values = payload["assignment_unit_keys"]
    except (TypeError, ValueError, KeyError):
        raise InferenceContractError("inference cutoff is malformed") from None
    if (
        not isinstance(payload, dict)
        or not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value for value in values)
        or len(set(values)) != len(values)
    ):
        raise InferenceContractError("inference cutoff is malformed")
    return tuple(values)


def consume_fixed_look_verdict(verdict: LearningContributionVerdict) -> bool:
    """Atomically bind the first exact confirmatory verdict to its cutoff."""

    if inference_authority_shape_reason(verdict.model_dump()) is not None:
        raise InferenceContractError("confirmatory verdict inference is malformed")
    with transaction(immediate=True):
        decision = get_fixed_look_decision(verdict.experiment_id)
        if decision is None or decision.status != "cutoff_frozen":
            return False
        if (
            verdict.inference_contract_version != decision.inference_contract_version
            or verdict.inference_plan_hash != decision.plan_hash
            or verdict.inference_cutoff_fingerprint != decision.cutoff_fingerprint
        ):
            return False
        insert_record(
            "learning_contribution_verdicts",
            verdict,
            ignore_duplicates=True,
        )
        now = _now()
        cursor = get_db().execute(
            "UPDATE learning_inference_decisions SET status='consumed', "
            "consumed_verdict_id=?, consumed_at=?, updated_at=? "
            "WHERE experiment_id=? AND status='cutoff_frozen' "
            "AND consumed_verdict_id IS NULL AND plan_hash=? "
            "AND cutoff_fingerprint=?",
            (
                verdict.verdict_id,
                now,
                now,
                verdict.experiment_id,
                decision.plan_hash,
                decision.cutoff_fingerprint,
            ),
        )
        return cursor.rowcount == 1


def inference_authority_shape_reason(verdict: dict[str, Any]) -> str | None:
    if str(verdict.get("inference_role") or "") != "confirmatory":
        return "inference_role_not_confirmatory"
    if verdict.get("authority_eligible") not in {1, True}:
        return "inference_verdict_not_authority_eligible"
    if str(verdict.get("inference_contract_version") or "") != INFERENCE_CONTRACT_VERSION:
        return "inference_contract_mismatch"
    for field in ("inference_plan_hash", "inference_cutoff_fingerprint"):
        value = verdict.get(field)
        if (
            not isinstance(value, str)
            or len(value) != 64
            or value.lower() != value
            or any(character not in "0123456789abcdef" for character in value)
        ):
            return "inference_evidence_unavailable"
    return None


def inference_authority_reason(verdict: dict[str, Any]) -> str | None:
    """Require a consumed durable decision before a positive can grant authority."""

    shape_reason = inference_authority_shape_reason(verdict)
    if shape_reason is not None:
        return shape_reason
    experiment_id = str(verdict.get("experiment_id") or "")
    verdict_id = str(verdict.get("verdict_id") or "")
    decision = get_fixed_look_decision(experiment_id)
    if decision is None:
        return "inference_contract_unavailable"
    if decision.status != "consumed":
        return "inference_decision_not_consumed"
    if decision.consumed_verdict_id != verdict_id:
        return "inference_consumed_verdict_mismatch"
    if (
        verdict.get("inference_contract_version") != decision.inference_contract_version
        or verdict.get("inference_plan_hash") != decision.plan_hash
        or verdict.get("inference_cutoff_fingerprint") != decision.cutoff_fingerprint
    ):
        return "inference_identity_mismatch"
    return None


def inference_collection_reason(
    experiment: LearningExperiment,
    *,
    policy_epoch: str,
    behavior_contract_hash: str,
    target_horizon: str,
) -> str | None:
    """Require the current Paper stage to own an exact prospective plan."""

    decision = get_fixed_look_decision(experiment.experiment_id)
    if decision is None:
        return "inference_contract_unavailable"
    if decision.status == "invalidated":
        return "inference_decision_invalidated"
    try:
        plan = _validated_plan(decision)
    except InferenceContractError:
        return "inference_plan_invalid"
    expected = {
        "experiment_id": experiment.experiment_id,
        "hypothesis_id": experiment.hypothesis_id,
        "stage": experiment.stage,
        "stage_generation": int(experiment.stage_generation),
        "path_id": "universe_family_calibration",
        "policy_epoch": policy_epoch,
        "target_horizon": target_horizon,
        "outcome_contract_version": "v1",
        "metric_version": METRIC_VERSION,
        "behavior_contract_hash": behavior_contract_hash,
        "inference_family_id": decision.inference_family_id,
        "authority_alpha": float(decision.authority_alpha),
    }
    if any(plan.get(field) != value for field, value in expected.items()):
        return "inference_plan_identity_mismatch"
    return None


def get_consumed_fixed_look_verdict(experiment_id: str) -> dict[str, Any] | None:
    decision = get_fixed_look_decision(experiment_id)
    if decision is None or decision.status != "consumed" or not decision.consumed_verdict_id:
        return None
    row = get_record("learning_contribution_verdicts", decision.consumed_verdict_id)
    if row is None or inference_authority_reason(row) is not None:
        return None
    return row


def _validated_plan(decision: LearningInferenceDecision) -> dict[str, Any]:
    try:
        plan = json.loads(decision.plan_json)
    except (TypeError, ValueError):
        raise InferenceContractError("inference plan is malformed") from None
    if not isinstance(plan, dict) or _hash_json(plan) != decision.plan_hash:
        raise InferenceContractError("inference plan hash mismatch")
    if plan.get("inference_contract_version") != INFERENCE_CONTRACT_VERSION:
        raise InferenceContractError("inference contract mismatch")
    return plan


def _prospective_assignment_units(
    experiment_id: str,
    plan: dict[str, Any],
) -> list[_AssignmentUnit]:
    rows = get_db().execute(
        "SELECT * FROM learning_exposures WHERE experiment_id=? "
        "AND hypothesis_id=? AND path_id=? AND concurrent_policy_epoch=? "
        "AND experiment_stage=? AND outcome_contract_version=? "
        "AND behavior_contract_hash=? "
        "ORDER BY datetime(exposed_at), assignment_unit_key, exposure_id",
        (
            experiment_id,
            str(plan["hypothesis_id"]),
            str(plan["path_id"]),
            str(plan["policy_epoch"]),
            str(plan["stage"]),
            str(plan["outcome_contract_version"]),
            str(plan["behavior_contract_hash"]),
        ),
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        key = str(row.get("assignment_unit_key") or "")
        if not key:
            raise InferenceContractError("assignment_unit_key_missing")
        grouped[key].append(row)

    units: list[_AssignmentUnit] = []
    for key, members in grouped.items():
        assignments = {str(member.get("assignment") or "") for member in members}
        if len(assignments) != 1 or not assignments <= {"treatment", "control"}:
            raise InferenceContractError("assignment_unit_assignment_invalid")
        assignment = next(iter(assignments))
        authority_values: set[bool] = set()
        changed_values: list[bool] = []
        times: list[datetime] = []
        for member in members:
            authority_applied, _authority_reason = _manifest_authority(
                member.get("manifest_json")
            )
            behavior_changed, behavior_reason = _exposure_behavior_change(
                member,
                assignment=assignment,
                authority_applied=authority_applied,
            )
            if behavior_changed is None or behavior_reason is None:
                raise InferenceContractError("invalid_behavior_change_provenance")
            exposed_time = _parse_utc(member.get("exposed_at"))
            if exposed_time is None:
                raise InferenceContractError("exposure_time_invalid")
            authority_values.add(authority_applied)
            changed_values.append(behavior_changed)
            times.append(exposed_time)
        if len(authority_values) != 1:
            raise InferenceContractError("assignment_unit_authority_conflict")
        exposed_time = min(times)
        units.append(_AssignmentUnit(
            key=key,
            assignment=assignment,
            exposed_at=exposed_time.isoformat(),
            exposed_time=exposed_time,
            actual_intervention=(
                assignment == "treatment"
                and next(iter(authority_values))
                and any(changed_values)
            ),
        ))
    return sorted(units, key=lambda unit: (unit.exposed_time, unit.key))


def _earliest_qualifying_prefix(
    units: list[_AssignmentUnit],
    plan: dict[str, Any],
) -> list[_AssignmentUnit] | None:
    if not units:
        return None
    observed_days = sorted({unit.exposed_time.date() for unit in units})
    runs: list[list[Any]] = []
    for day in observed_days:
        if not runs or day != runs[-1][-1] + timedelta(days=1):
            runs.append([day])
        else:
            runs[-1].append(day)
    current_days = set(runs[-1])
    current = [unit for unit in units if unit.exposed_time.date() in current_days]
    required_per_arm = int(plan["minimum_assignment_units_per_arm"])
    required_actual = int(plan["minimum_actual_treatment_interventions"])
    required_days = int(plan["minimum_consecutive_market_days"])
    treatment_n = control_n = actual_n = 0
    seen_days: set[Any] = set()
    for index, unit in enumerate(current):
        seen_days.add(unit.exposed_time.date())
        if unit.assignment == "treatment":
            treatment_n += 1
            actual_n += int(unit.actual_intervention)
        else:
            control_n += 1
        if (
            treatment_n >= required_per_arm
            and control_n >= required_per_arm
            and actual_n >= required_actual
            and len(seen_days) >= max(1, required_days)
        ):
            return current[: index + 1]
    return None


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

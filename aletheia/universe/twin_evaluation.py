"""Read-only evaluation of prospective Universe Shadow policy windows.

The report compares allocation utility that can be reconstructed because every
candidate receives the same fixed-horizon observation.  It never writes a
Learning verdict and cannot authorize scorer, Paper, Risk, or Execution changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import math
from statistics import fmean
from typing import Any, Mapping

from aletheia.learning.outcomes import assess_candidate_outcome
from aletheia.storage.sqlite import get_db
from aletheia.universe.twin_experiment import (
    TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT,
    TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY,
    TWIN_EXPERIMENT_BEHAVIOR_VERSION,
    TWIN_EXPERIMENT_CONTRACT_VERSION,
    TWIN_EXPERIMENT_CONTROL_POLICY,
    TWIN_EXPERIMENT_COST_MODEL_VERSION,
    TWIN_EXPERIMENT_METRIC_VERSION,
    TWIN_EXPERIMENT_OUTCOME_CONTRACT,
    TWIN_EXPERIMENT_PATH_ID,
    TWIN_EXPERIMENT_PRIMARY_HORIZON,
    TWIN_EXPERIMENT_TREATMENT_POLICY,
    assign_twin_shadow_cluster,
    twin_behavior_contract_hash,
)
from aletheia.universe.twin_persistence import replay_twin_shadow_bundle


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _cvar(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    count = max(1, math.ceil(fraction * len(ordered)))
    return fmean(ordered[:count])


def _ess(weights: list[float]) -> float:
    denominator = sum(weight * weight for weight in weights)
    return (sum(weights) ** 2 / denominator) if denominator > 0 else 0.0


def _longest_consecutive_days(days: set[date]) -> int:
    longest = current = 0
    previous: date | None = None
    for day in sorted(days):
        current = current + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    return longest


def _rows_for_outcome_keys(keys: list[str], horizon: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    db = get_db()
    for start in range(0, len(keys), 400):
        chunk = keys[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = db.execute(
            "SELECT * FROM filter_candidate_outcomes "
            f"WHERE outcome_candidate_key IN ({placeholders}) AND horizon=?",
            (*chunk, horizon),
        ).fetchall()
        for row in rows:
            key = str(row["outcome_candidate_key"])
            if key in result:
                result[key] = {"_duplicate": True}
            else:
                result[key] = dict(row)
    return result


_IDENTITY_FIELDS = (
    "experiment_id",
    "behavior_contract_hash",
    "primary_horizon",
    "outcome_contract_version",
    "metric_version",
    "assignment_contract_version",
    "treatment_policy",
    "control_policy",
    "feature_source_fingerprint",
    "availability_profile_id",
)
_REQUIRED_IDENTITY_FIELDS = tuple(
    field for field in _IDENTITY_FIELDS if field != "availability_profile_id"
)

_RECORD_BATCH_IDENTITY_FIELDS = (
    "experiment_id",
    "policy_epoch_id",
    "behavior_contract_hash",
    "availability_profile_id",
    "assignment_unit_key",
    "assignment_arm",
    "assignment_propensity",
    "treatment_coefficient",
    "primary_horizon",
    "outcome_contract_version",
    "metric_version",
)


@dataclass
class _RecordAudit:
    records_by_run: dict[str, list[dict[str, Any]]]
    outcome_keys: list[str]
    expected_candidate_n: int = 0
    missingness_candidate_n: int = 0
    concentrated_candidate_n: int = 0


def _identity(row: dict[str, Any]) -> tuple[object, ...]:
    return tuple(row.get(field) for field in _IDENTITY_FIELDS)


def _audit_records(
    policy_epoch_id: str,
    policy_hash: str,
    batches: list[dict[str, Any]],
    expected_identity: tuple[object, ...],
    invalid: set[str],
) -> _RecordAudit:
    db = get_db()
    audit = _RecordAudit({}, [])
    assignment_units: set[str] = set()
    for batch in batches:
        if _identity(batch) != expected_identity:
            invalid.add("mixed_experiment_identity")
        if batch.get("promotion_eligible"):
            invalid.add("batch_promotion_eligible")
        arm = str(batch.get("assignment_arm") or "")
        propensity = _finite(batch.get("assignment_propensity"))
        coefficient = _finite(batch.get("treatment_coefficient"))
        if arm not in {"treatment", "control"}:
            invalid.add("invalid_assignment_arm")
        if propensity is None or not 0 < propensity <= 1:
            invalid.add("invalid_assignment_propensity")
        if coefficient != (1.0 if arm == "treatment" else 0.0):
            invalid.add("treatment_coefficient_mismatch")
        assignment_unit = str(batch.get("assignment_unit_key") or "")
        if assignment_unit != str(batch["scanner_run_id"]):
            invalid.add("assignment_unit_not_scanner_run")
        if assignment_unit in assignment_units:
            invalid.add("duplicate_assignment_unit")
        assignment_units.add(assignment_unit)
        expected_arm, expected_propensity = assign_twin_shadow_cluster(
            policy_hash, assignment_unit
        )
        if arm != expected_arm or propensity != expected_propensity:
            invalid.add("assignment_replay_mismatch")
        rows = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM universe_twin_shadow_records WHERE scanner_run_id=? "
                "ORDER BY candidate_index",
                (batch["scanner_run_id"],),
            ).fetchall()
        ]
        audit.records_by_run[str(batch["scanner_run_id"])] = rows
        try:
            if not replay_twin_shadow_bundle(batch, rows).matches:
                invalid.add("twin_replay_integrity_mismatch")
        except (KeyError, TypeError, ValueError):
            invalid.add("twin_replay_integrity_unavailable")
        audit.expected_candidate_n += int(batch.get("candidate_count") or 0)
        if len(rows) != int(batch.get("candidate_count") or 0):
            invalid.add("incomplete_cluster_records")
        if [row.get("candidate_index") for row in rows] != list(range(len(rows))):
            invalid.add("noncontiguous_candidate_index")
        for row in rows:
            for field in _RECORD_BATCH_IDENTITY_FIELDS:
                if row.get(field) != batch.get(field):
                    invalid.add(f"record_{field}_mismatch")
            if row.get("policy_epoch_id") != policy_epoch_id:
                invalid.add("record_policy_epoch_mismatch")
            if row.get("shadow_authority_applied"):
                invalid.add("shadow_authority_applied")
            if row.get("promotion_eligible"):
                invalid.add("record_promotion_eligible")
            if not row.get("shadow_valid"):
                invalid.add("shadow_policy_invalid")
            expected_selected = (
                row.get("shadow_pessimistic_selected")
                if arm == "treatment"
                else row.get("authority_selected")
            )
            if (
                row.get("assigned_policy_selected") is None
                or bool(row.get("assigned_policy_selected"))
                != bool(expected_selected)
            ):
                invalid.add("assigned_policy_selection_mismatch")
            if not row.get("outcome_candidate_key"):
                invalid.add("record_outcome_identity_missing")
            else:
                audit.outcome_keys.append(str(row["outcome_candidate_key"]))
            try:
                output = json.loads(str(row.get("compact_output_json") or "{}"))
            except (TypeError, ValueError):
                invalid.add("compact_output_invalid")
                continue
            shadow = output.get("shadow") if isinstance(output, dict) else None
            if isinstance(shadow, dict):
                audit.missingness_candidate_n += bool(
                    shadow.get("missingness_feature_ids")
                )
                audit.concentrated_candidate_n += "evidence_concentrated" in set(
                    shadow.get("flags") or []
                )
    return audit


@dataclass
class _ClusterAudit:
    complete_candidate_n: int = 0
    incomplete_cluster_n: int = 0
    treatment_cluster_n: int = 0
    control_cluster_n: int = 0
    turnover_changes: int = 0
    turnover_denominator: int = 0
    direct_effects: list[float] = field(default_factory=list)
    point_effects: list[float] = field(default_factory=list)
    treatment_weights: list[float] = field(default_factory=list)
    control_weights: list[float] = field(default_factory=list)
    treatment_returns: list[float] = field(default_factory=list)
    control_returns: list[float] = field(default_factory=list)
    treatment_mae: list[float] = field(default_factory=list)
    control_mae: list[float] = field(default_factory=list)
    treatment_costs: list[float] = field(default_factory=list)
    control_costs: list[float] = field(default_factory=list)
    eligible_days: set[date] = field(default_factory=set)
    cost_model_versions: set[str] = field(default_factory=set)


def _evaluate_clusters(
    batches: list[dict[str, Any]],
    records_by_run: dict[str, list[dict[str, Any]]],
    outcomes: dict[str, dict[str, Any]],
    invalid: set[str],
) -> _ClusterAudit:
    audit = _ClusterAudit()
    for batch in batches:
        rows = records_by_run[str(batch["scanner_run_id"])]
        nets: list[float] = []
        maes: list[float | None] = []
        costs: list[float] = []
        cluster_complete = True
        for row in rows:
            outcome = outcomes.get(str(row.get("outcome_candidate_key") or ""))
            if outcome is None or outcome.get("_duplicate"):
                cluster_complete = False
                continue
            assessment = assess_candidate_outcome(outcome)
            fee = _finite(outcome.get("estimated_fee_pct"))
            spread = _finite(outcome.get("estimated_spread_slippage_pct"))
            funding = _finite(outcome.get("estimated_funding_pct"))
            net = _finite(outcome.get("net_return_pct"))
            if (
                not assessment.eligible
                or net is None
                or fee is None
                or spread is None
                or funding is None
                or not outcome.get("cost_model_version")
            ):
                cluster_complete = False
                continue
            if outcome.get("cost_model_version") != TWIN_EXPERIMENT_COST_MODEL_VERSION:
                invalid.add("cost_model_version_mismatch")
            audit.cost_model_versions.add(str(outcome["cost_model_version"]))
            if outcome.get("entry_ts") != batch.get("captured_at"):
                invalid.add("outcome_entry_time_mismatch")
            settled_at = _parse_time(outcome.get("settled_at"))
            entry_at = _parse_time(outcome.get("entry_ts"))
            if settled_at is None or entry_at is None or settled_at < entry_at:
                invalid.add("retrospective_or_invalid_outcome_time")
            if bool(outcome.get("allocation_selected")) != bool(
                row.get("assigned_policy_selected")
            ):
                invalid.add("outcome_selection_identity_mismatch")
            nets.append(net)
            mae = _finite(outcome.get("mae_pct"))
            maes.append(abs(mae) if mae is not None else None)
            costs.append(fee + spread + funding)
        if not cluster_complete or len(nets) != len(rows):
            audit.incomplete_cluster_n += 1
            continue
        if not rows:
            invalid.add("empty_cluster")
            continue
        audit.complete_candidate_n += len(rows)
        step_selected = [bool(row.get("authority_selected")) for row in rows]
        treatment_selected = [
            bool(row.get("shadow_pessimistic_selected")) for row in rows
        ]
        point_selected = [bool(row.get("shadow_point_selected")) for row in rows]
        step_utility = fmean(
            net if selected else 0.0 for net, selected in zip(nets, step_selected)
        )
        treatment_utility = fmean(
            net if selected else 0.0
            for net, selected in zip(nets, treatment_selected)
        )
        point_utility = fmean(
            net if selected else 0.0 for net, selected in zip(nets, point_selected)
        )
        audit.direct_effects.append(treatment_utility - step_utility)
        audit.point_effects.append(point_utility - step_utility)
        arm = str(batch["assignment_arm"])
        propensity = float(batch["assignment_propensity"])
        selected_treatment = [
            (net, mae, cost)
            for net, mae, cost, selected in zip(
                nets, maes, costs, treatment_selected
            )
            if selected
        ]
        selected_control = [
            (net, mae, cost)
            for net, mae, cost, selected in zip(nets, maes, costs, step_selected)
            if selected
        ]
        audit.treatment_returns.extend(item[0] for item in selected_treatment)
        audit.control_returns.extend(item[0] for item in selected_control)
        audit.treatment_mae.extend(
            item[1] for item in selected_treatment if item[1] is not None
        )
        audit.control_mae.extend(
            item[1] for item in selected_control if item[1] is not None
        )
        audit.treatment_costs.extend(item[2] for item in selected_treatment)
        audit.control_costs.extend(item[2] for item in selected_control)
        audit.turnover_changes += sum(
            left != right for left, right in zip(step_selected, treatment_selected)
        )
        audit.turnover_denominator += len(rows)
        exposed = _parse_time(batch.get("captured_at"))
        if exposed is None:
            invalid.add("invalid_cluster_time")
        else:
            audit.eligible_days.add(exposed.date())
        if arm == "treatment":
            audit.treatment_cluster_n += 1
            audit.treatment_weights.append(1.0 / propensity)
        else:
            audit.control_cluster_n += 1
            audit.control_weights.append(1.0 / propensity)
    return audit


def _validate_epoch_contract(
    policy_epoch_id: str,
    epoch: Mapping[str, Any],
    epoch_values: object,
    first: Mapping[str, Any],
    invalid: set[str],
) -> None:
    """Validate the frozen v1 identity instead of accepting self-consistency."""

    if not isinstance(epoch_values, dict):
        invalid.add("policy_epoch_values_invalid")
        return
    expected_epoch_values = {
        "contract_version": TWIN_EXPERIMENT_CONTRACT_VERSION,
        "behavior_version": TWIN_EXPERIMENT_BEHAVIOR_VERSION,
        "authority": "shadow_only",
        "control_policy": TWIN_EXPERIMENT_CONTROL_POLICY,
        "treatment_policy": TWIN_EXPERIMENT_TREATMENT_POLICY,
        "primary_horizon": TWIN_EXPERIMENT_PRIMARY_HORIZON,
        "outcome_contract_version": TWIN_EXPERIMENT_OUTCOME_CONTRACT,
        "cost_model_version": TWIN_EXPERIMENT_COST_MODEL_VERSION,
        "metric_version": TWIN_EXPERIMENT_METRIC_VERSION,
        "promotion_eligible": False,
        "availability_profile_id": None,
    }
    for field, expected in expected_epoch_values.items():
        if epoch_values.get(field) != expected:
            invalid.add(f"{field}_contract_mismatch")
    declared_assignment = epoch_values.get("assignment")
    if not isinstance(declared_assignment, dict):
        invalid.add("assignment_contract_mismatch")
    else:
        if declared_assignment.get("contract_version") != (
            TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT
        ):
            invalid.add("assignment_contract_mismatch")
        if declared_assignment.get("unit") != "complete_scanner_run":
            invalid.add("assignment_unit_contract_mismatch")
        if _finite(declared_assignment.get("propensity")) != (
            TWIN_EXPERIMENT_ASSIGNMENT_PROPENSITY
        ):
            invalid.add("assignment_propensity_contract_mismatch")
    if str(epoch.get("epoch_kind") or "") != f"decision_policy:{TWIN_EXPERIMENT_PATH_ID}":
        invalid.add("policy_epoch_kind_mismatch")
    if first.get("experiment_id") != f"universe-shadow-{policy_epoch_id}":
        invalid.add("experiment_id_mismatch")
    expected_batch_values = {
        "assignment_contract_version": TWIN_EXPERIMENT_ASSIGNMENT_CONTRACT,
        "treatment_policy": TWIN_EXPERIMENT_TREATMENT_POLICY,
        "control_policy": TWIN_EXPERIMENT_CONTROL_POLICY,
        "primary_horizon": TWIN_EXPERIMENT_PRIMARY_HORIZON,
        "outcome_contract_version": TWIN_EXPERIMENT_OUTCOME_CONTRACT,
        "metric_version": TWIN_EXPERIMENT_METRIC_VERSION,
        "feature_source_fingerprint": epoch_values.get(
            "feature_source_fingerprint"
        ),
        "availability_profile_id": epoch_values.get("availability_profile_id"),
    }
    for field, expected in expected_batch_values.items():
        if first.get(field) != expected:
            invalid.add(f"{field}_mismatch")
    if first.get("behavior_contract_hash") != twin_behavior_contract_hash(
        str(epoch.get("effective_values_hash") or "")
    ):
        invalid.add("behavior_contract_hash_mismatch")


def evaluate_twin_shadow_epoch(policy_epoch_id: str) -> dict[str, Any]:
    """Return an honest diagnostic report for one exact prospective epoch."""

    db = get_db()
    epoch = db.execute(
        "SELECT * FROM learning_policy_epochs WHERE epoch_id=?",
        (policy_epoch_id,),
    ).fetchone()
    batches = [
        dict(row)
        for row in db.execute(
            "SELECT * FROM universe_twin_shadow_batches WHERE policy_epoch_id=? "
            "ORDER BY captured_at, scanner_run_id",
            (policy_epoch_id,),
        ).fetchall()
    ]
    base_report: dict[str, Any] = {
        "policy_epoch_id": policy_epoch_id,
        "authority": "shadow_only",
        "promotion_eligible": False,
        "experiment_id": None,
        "behavior_contract_hash": None,
        "primary_horizon": None,
        "assignment_propensity": None,
        "expected_cluster_n": len(batches),
        "complete_cluster_n": 0,
        "complete_candidate_n": 0,
        "expected_candidate_n": 0,
        "treatment_cluster_n": 0,
        "control_cluster_n": 0,
        "treatment_ess": 0.0,
        "control_ess": 0.0,
        "consecutive_eligible_utc_days": 0,
        "direct_replay_delta": None,
        "point_replay_delta": None,
        "propensity_aware_delta": None,
        "propensity_estimator_status": "not_independent_full_potential_replay",
        "estimator_sign_agreement": None,
        "harm_metrics": {},
        "diagnostics": {},
        "blocking_reasons": [],
        "invalid_reasons": [],
        "verdict": "insufficient_data",
    }
    if epoch is None:
        base_report["invalid_reasons"] = ["policy_epoch_not_available"]
        base_report["verdict"] = "invalid"
        return base_report
    if not batches:
        base_report["blocking_reasons"] = ["no_prospective_clusters"]
        return base_report

    try:
        epoch_values: object = json.loads(
            str(epoch["effective_values_json"] or "{}")
        )
    except (TypeError, ValueError):
        epoch_values = None

    first = batches[0]
    base_report.update(
        experiment_id=first.get("experiment_id"),
        behavior_contract_hash=first.get("behavior_contract_hash"),
        primary_horizon=first.get("primary_horizon"),
        assignment_propensity=first.get("assignment_propensity"),
    )
    expected_identity = _identity(first)
    invalid: set[str] = set()
    blockers: set[str] = {
        "anytime_valid_lower_bound_not_available",
        "feature_observation_lineage_not_available",
        "independent_dr_outcome_model_not_available",
        "noninferiority_margins_not_configured",
        "no_actual_authority_intervention",
    }
    if not first.get("availability_profile_id"):
        blockers.add("availability_profile_not_available")
    _validate_epoch_contract(
        policy_epoch_id,
        dict(epoch),
        epoch_values,
        first,
        invalid,
    )
    if any(not first.get(field) for field in _REQUIRED_IDENTITY_FIELDS):
        invalid.add("experiment_identity_incomplete")

    record_audit = _audit_records(
        policy_epoch_id,
        str(epoch["effective_values_hash"]),
        batches,
        expected_identity,
        invalid,
    )
    outcomes = _rows_for_outcome_keys(
        sorted(set(record_audit.outcome_keys)),
        str(first.get("primary_horizon") or ""),
    )
    cluster_audit = _evaluate_clusters(
        batches, record_audit.records_by_run, outcomes, invalid
    )
    direct = (
        fmean(cluster_audit.direct_effects)
        if cluster_audit.direct_effects
        else None
    )
    point_direct = (
        fmean(cluster_audit.point_effects) if cluster_audit.point_effects else None
    )
    # Full-candidate replay observes both policy utilities, so the residual in
    # an augmented propensity expression is identically zero. Keep the legacy
    # report field, but do not pretend it is independent corroboration.
    propensity_aware = direct
    treatment_ess = _ess(cluster_audit.treatment_weights)
    control_ess = _ess(cluster_audit.control_weights)
    consecutive_days = _longest_consecutive_days(cluster_audit.eligible_days)
    if cluster_audit.incomplete_cluster_n:
        blockers.add("fixed_horizon_outcomes_incomplete")
    if cluster_audit.treatment_cluster_n < 50 or cluster_audit.control_cluster_n < 50:
        blockers.add("minimum_cluster_units_not_met")
    if treatment_ess < 50 or control_ess < 50:
        blockers.add("propensity_ess_not_met")
    if cluster_audit.treatment_cluster_n == 0 or cluster_audit.control_cluster_n == 0:
        blockers.add("assignment_overlap_not_available")
    if consecutive_days < 7:
        blockers.add("consecutive_utc_dates_not_met")
    sign_agreement = True if direct is not None else None
    if len(cluster_audit.cost_model_versions) > 1:
        invalid.add("mixed_cost_model_versions")

    treatment_loss_rate = (
        sum(value < 0 for value in cluster_audit.treatment_returns)
        / len(cluster_audit.treatment_returns)
        if cluster_audit.treatment_returns
        else None
    )
    control_loss_rate = (
        sum(value < 0 for value in cluster_audit.control_returns)
        / len(cluster_audit.control_returns)
        if cluster_audit.control_returns
        else None
    )
    harm_metrics = {
        "treatment_loss_rate": treatment_loss_rate,
        "control_loss_rate": control_loss_rate,
        "loss_rate_delta": (
            treatment_loss_rate - control_loss_rate
            if treatment_loss_rate is not None and control_loss_rate is not None
            else None
        ),
        "treatment_mae_p95": _percentile(cluster_audit.treatment_mae, 0.95),
        "control_mae_p95": _percentile(cluster_audit.control_mae, 0.95),
        "treatment_net_cvar_5": _cvar(cluster_audit.treatment_returns, 0.05),
        "control_net_cvar_5": _cvar(cluster_audit.control_returns, 0.05),
        "treatment_mean_cost_pct": (
            fmean(cluster_audit.treatment_costs)
            if cluster_audit.treatment_costs
            else None
        ),
        "control_mean_cost_pct": (
            fmean(cluster_audit.control_costs)
            if cluster_audit.control_costs
            else None
        ),
        "selection_turnover_rate": (
            cluster_audit.turnover_changes / cluster_audit.turnover_denominator
            if cluster_audit.turnover_denominator
            else None
        ),
    }
    treatment_mae_p95 = harm_metrics["treatment_mae_p95"]
    control_mae_p95 = harm_metrics["control_mae_p95"]
    harm_metrics["mae_p95_worsening_pp"] = (
        treatment_mae_p95 - control_mae_p95
        if treatment_mae_p95 is not None and control_mae_p95 is not None
        else None
    )
    breach_reasons: list[str] = []
    if (
        harm_metrics["loss_rate_delta"] is not None
        and harm_metrics["loss_rate_delta"] > 0.15
    ):
        breach_reasons.append("loss_rate_increase_above_15pp")
    if (
        harm_metrics["mae_p95_worsening_pp"] is not None
        and harm_metrics["mae_p95_worsening_pp"] > 0.25
    ):
        breach_reasons.append("mae_p95_worsening_above_0_25pp")
    harm_metrics["outer_harm_breached"] = bool(breach_reasons)
    harm_metrics["outer_harm_breach_reasons"] = breach_reasons
    diagnostics = {
        "settled_candidate_coverage": (
            cluster_audit.complete_candidate_n / record_audit.expected_candidate_n
            if record_audit.expected_candidate_n
            else None
        ),
        "missingness_candidate_n": record_audit.missingness_candidate_n,
        "concentrated_candidate_n": record_audit.concentrated_candidate_n,
        "availability_profile_id": first.get("availability_profile_id"),
        "feature_observation_lineage": "not_available",
        "independent_dr_outcome_model": "not_available",
        "propensity_estimator": "not_independent_full_potential_replay",
        "anytime_valid_lower_bound": None,
        "noninferiority_margins": "not_configured",
    }
    sufficiently_powered = not any(
        reason
        in blockers
        for reason in {
            "fixed_horizon_outcomes_incomplete",
            "minimum_cluster_units_not_met",
            "propensity_ess_not_met",
            "assignment_overlap_not_available",
            "consecutive_utc_dates_not_met",
        }
    )
    if invalid:
        verdict = "invalid"
    elif not sufficiently_powered:
        verdict = "insufficient_data"
    elif harm_metrics["outer_harm_breached"]:
        verdict = "negative"
    else:
        verdict = "not_available"
    base_report.update(
        complete_cluster_n=len(cluster_audit.direct_effects),
        complete_candidate_n=cluster_audit.complete_candidate_n,
        expected_candidate_n=record_audit.expected_candidate_n,
        treatment_cluster_n=cluster_audit.treatment_cluster_n,
        control_cluster_n=cluster_audit.control_cluster_n,
        treatment_ess=treatment_ess,
        control_ess=control_ess,
        consecutive_eligible_utc_days=consecutive_days,
        direct_replay_delta=direct,
        point_replay_delta=point_direct,
        propensity_aware_delta=propensity_aware,
        estimator_sign_agreement=sign_agreement,
        harm_metrics=harm_metrics,
        diagnostics=diagnostics,
        blocking_reasons=sorted(blockers),
        invalid_reasons=sorted(invalid),
        verdict=verdict,
    )
    return base_report


__all__ = ["evaluate_twin_shadow_epoch"]

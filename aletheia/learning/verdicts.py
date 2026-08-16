"""Deterministic, measurement-only contribution verdict calculations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import combinations
import json
import math
import random
from statistics import fmean
from typing import Any

from aletheia.learning.cohorts import ContributionCohort, CohortSample
from aletheia.learning.registry import get_learning_path, metric_version_for_path


METRIC_VERSION = "universe_family_calibration_cluster_allocation_utility_v10"
RANDOMIZATION_INFERENCE_VERSION = "conditional-randomization-itt/v1"
RANDOMIZATION_SEED = 20260712
RANDOMIZATION_RESAMPLES = 2000
_MAX_EXACT_RANDOMIZATIONS = 20_000
_PRACTICAL_NEUTRAL_DELTA_PCT = 0.10
_HARM_MIN_SAMPLES_PER_ARM = 50
SHADOW_TRIAL_INTEGRITY_REASON = "trial_integrity_ready_no_actual_intervention"
SHADOW_TRIAL_INTEGRITY_CONTRACT = "ufc-shadow-trial-integrity/v1"
SHADOW_TRIAL_MIN_SAMPLES_PER_ARM = 10
SHADOW_TRIAL_MIN_MARKET_DAYS = 1


@dataclass(frozen=True)
class VerdictEvaluation:
    baseline: float | None
    observed: float | None
    delta: float | None
    confidence_low: float | None
    confidence_high: float | None
    harm_metrics: dict[str, Any]
    verdict: str
    reason: str
    unique_treatment_n: int
    unique_control_n: int


def evaluate_cohort(
    cohort: ContributionCohort,
    *,
    authority_alpha: float | None = None,
) -> VerdictEvaluation:
    """Classify one already-isolated cohort without changing any authority state."""
    assigned_treatment = cohort.treatment_samples
    raw_control = cohort.control_samples
    coverage, qualified_assigned_treatment, control = _market_day_coverage(
        cohort,
        assigned_treatment,
        raw_control,
    )
    treatment = qualified_assigned_treatment
    authority_applied_treatment = [
        sample for sample in qualified_assigned_treatment
        if sample.authority_applied
    ]
    actual_treatment = [
        sample for sample in qualified_assigned_treatment
        if sample.authority_applied and sample.behavior_changed
    ]
    coverage["qualified_authority_applied_treatment_n"] = len(
        authority_applied_treatment
    )
    coverage["qualified_actual_intervention_treatment_n"] = len(actual_treatment)
    treatment_values = [sample.net_return_pct for sample in treatment]
    control_values = [sample.net_return_pct for sample in control]
    observed = fmean(treatment_values) if treatment_values else None
    baseline = fmean(control_values) if control_values else None
    delta = observed - baseline if observed is not None and baseline is not None else None
    inference_alpha = 0.10 if authority_alpha is None else authority_alpha
    randomization = _conditional_randomization_inference(
        [*qualified_assigned_treatment, *control],
        alpha=inference_alpha,
    )
    # The legacy fields used an IID percentile bootstrap even though adjacent
    # Universe decisions share 1h/4h market paths.  That interval was
    # anti-conservative and is intentionally retired rather than relabelled.
    confidence_low = confidence_high = None
    # Preserve the randomized intent-to-treat estimand. Authority refusal can be
    # correlated with market or risk state, so selecting only applied rows would
    # turn the primary result into a biased post-assignment comparison.
    harm_metrics = _harm_metrics(cohort, treatment, control, delta)
    harm_metrics["inference_role"] = (
        "monitoring" if authority_alpha is None else "confirmatory"
    )
    harm_metrics["authority_alpha"] = authority_alpha
    harm_metrics["randomization_inference"] = randomization
    harm_metrics["harm_treatment_basis"] = "randomized_assignment_intent_to_treat"
    harm_metrics["market_day_coverage"] = coverage
    required = max(1, int(cohort.experiment.min_samples_per_arm))
    harm_metrics.update(_treatment_intervention_metrics(
        assigned_treatment=qualified_assigned_treatment,
        authority_applied_treatment=authority_applied_treatment,
        actual_treatment=actual_treatment,
        required=required,
        raw_assigned_treatment_n=len(assigned_treatment),
    ))

    def result(verdict: str, reason: str) -> VerdictEvaluation:
        return VerdictEvaluation(
            baseline,
            observed,
            delta,
            confidence_low,
            confidence_high,
            harm_metrics,
            verdict,
            reason,
            len(treatment),
            len(control),
        )

    if cohort.invalid_reasons:
        return result("invalid", _reason("invalid evidence", cohort.invalid_reasons))
    shadow_trial_required = min(SHADOW_TRIAL_MIN_SAMPLES_PER_ARM, required)
    shadow_trial_days = min(
        SHADOW_TRIAL_MIN_MARKET_DAYS,
        max(0, int(cohort.experiment.min_market_days)),
    )
    rejection_reasons = harm_metrics["treatment_authority_rejection_reasons"]
    full_measurement_ready = (
        len(assigned_treatment) >= required
        and len(raw_control) >= required
        and coverage["status"] in {"qualified", "not_required"}
        and len(qualified_assigned_treatment) >= required
        and len(control) >= required
    )
    shadow_trial_integrity_ready = (
        cohort.experiment.stage == "shadow"
        and not full_measurement_ready
        and not authority_applied_treatment
        and not actual_treatment
        and len(qualified_assigned_treatment) >= shadow_trial_required
        and len(control) >= shadow_trial_required
        and int(coverage["current_consecutive_market_days"]) >= shadow_trial_days
        and not bool(harm_metrics["breached"])
        and set(rejection_reasons) == {"experiment_stage_not_paper"}
    )
    harm_metrics["trial_integrity_readiness"] = {
        "contract_version": SHADOW_TRIAL_INTEGRITY_CONTRACT,
        "required_per_arm": shadow_trial_required,
        "required_market_days": shadow_trial_days,
        "qualified_treatment_n": len(qualified_assigned_treatment),
        "qualified_control_n": len(control),
        "current_consecutive_market_days": int(
            coverage["current_consecutive_market_days"]
        ),
        "status": (
            "not_applicable"
            if cohort.experiment.stage != "shadow"
            else "mature"
            if full_measurement_ready
            else "ready"
            if shadow_trial_integrity_ready
            else "collecting"
        ),
    }
    if shadow_trial_integrity_ready:
        # Shadow runs the static baseline in both arms, so waiting for the full
        # confirmatory window cannot estimate treatment contribution.  This
        # result says only that the prospective identity/outcome pipeline has
        # exercised both arms enough for an owner to start a bounded Paper-10
        # trial.  It is still insufficient_data and can never auto-promote.
        return result("insufficient_data", SHADOW_TRIAL_INTEGRITY_REASON)
    if len(assigned_treatment) < required or len(raw_control) < required:
        return result(
            "insufficient_data",
            "insufficient unique prospective treatment/control samples",
        )
    if coverage["status"] not in {"qualified", "not_required"}:
        return result("insufficient_data", "market_day_coverage_gap")
    if len(qualified_assigned_treatment) < required or len(control) < required:
        return result(
            "insufficient_data",
            "insufficient unique prospective treatment/control samples in continuous window",
        )
    assert delta is not None
    if harm_metrics["breached"]:
        return result("negative", _reason("harm gate breached", harm_metrics["breach_reasons"]))
    if not actual_treatment:
        return result("insufficient_data", "no_actual_intervention")
    if delta < 0:
        return result("negative", "treatment cost-aware mean trails control")
    if len(actual_treatment) < required:
        return result(
            "insufficient_data",
            "insufficient_actual_intervention_samples",
        )
    threshold = max(_PRACTICAL_NEUTRAL_DELTA_PCT, float(cohort.hypothesis.effect_threshold or 0.0))
    if (
        delta < threshold
        or randomization["p_value"] is None
        or float(randomization["p_value"]) > inference_alpha
    ):
        return result(
            "neutral",
            "effect is within the practical-neutral band or randomization inference is inconclusive",
        )
    return result(
        "positive",
        "cost-aware treatment mean exceeds control with deterministic confidence and no harm breach",
    )


def _treatment_intervention_metrics(
    *,
    assigned_treatment: list[CohortSample],
    authority_applied_treatment: list[CohortSample],
    actual_treatment: list[CohortSample],
    required: int,
    raw_assigned_treatment_n: int,
) -> dict[str, Any]:
    baseline = [
        sample for sample in assigned_treatment
        if not (sample.authority_applied and sample.behavior_changed)
    ]
    if not actual_treatment:
        intervention_reason = "no_actual_intervention"
    elif len(actual_treatment) < required:
        intervention_reason = "insufficient_actual_intervention_samples"
    else:
        intervention_reason = "sufficient_actual_intervention_samples"
    return {
        "raw_assigned_treatment_n": raw_assigned_treatment_n,
        "assigned_treatment_n": len(assigned_treatment),
        "authority_applied_treatment_n": len(authority_applied_treatment),
        "actual_intervention_treatment_n": len(actual_treatment),
        "baseline_treatment_n": len(baseline),
        "required_actual_intervention_n": required,
        "authority_application_rate": (
            len(authority_applied_treatment) / len(assigned_treatment)
            if assigned_treatment else None
        ),
        "actual_intervention_rate": (
            len(actual_treatment) / len(assigned_treatment)
            if assigned_treatment else None
        ),
        "actual_intervention_state": (
            "none"
            if not actual_treatment
            else "partial"
            if baseline
            else "complete"
        ),
        "actual_intervention_reason": intervention_reason,
        "treatment_authority_rejection_reasons": dict(sorted(Counter(
            sample.authority_reason or "authority_reason_unavailable"
            for sample in assigned_treatment
            if not sample.authority_applied
        ).items())),
        "treatment_non_intervention_reasons": dict(sorted(Counter(
            (
                f"authority:{sample.authority_reason or 'authority_reason_unavailable'}"
                if not sample.authority_applied
                else sample.behavior_change_reason
            )
            for sample in baseline
        ).items())),
    }


def _market_day_coverage(
    cohort: ContributionCohort,
    treatment: list[CohortSample],
    control: list[CohortSample],
) -> tuple[dict[str, Any], list[CohortSample], list[CohortSample]]:
    """Select the latest uninterrupted UTC exposure-day window.

    One qualified market day contains at least one complete eligible assignment
    unit from either randomized arm. Per-arm sufficiency remains the independent
    50-unit gate, so random daily allocation imbalance is visible but does not
    impersonate a runtime outage. A day with no eligible unit resets the current
    run; pre-gap samples do not contribute afterward. Planned outages have no
    implicit waiver because no durable waiver contract exists yet.
    """
    required_days = max(0, int(cohort.experiment.min_market_days))
    parsed_treatment = [(_parse_time(sample.exposed_at), sample) for sample in treatment]
    parsed_control = [(_parse_time(sample.exposed_at), sample) for sample in control]
    invalid_time_n = sum(
        parsed is None
        for parsed, _sample in (*parsed_treatment, *parsed_control)
    )
    treatment_days = {parsed.date() for parsed, _sample in parsed_treatment if parsed is not None}
    control_days = {parsed.date() for parsed, _sample in parsed_control if parsed is not None}
    observed_days = treatment_days | control_days
    common_days = sorted(treatment_days & control_days)
    runs = _consecutive_day_runs(sorted(observed_days))
    current_run = runs[-1] if runs else []
    current_days = set(current_run)
    selected_treatment = [
        sample
        for parsed, sample in parsed_treatment
        if parsed is not None and parsed.date() in current_days
    ]
    selected_control = [
        sample
        for parsed, sample in parsed_control
        if parsed is not None and parsed.date() in current_days
    ]

    if required_days == 0:
        status = "not_required"
        selected_treatment = treatment
        selected_control = control
    elif invalid_time_n:
        status = "invalid_time"
    elif len(current_run) >= required_days:
        status = "qualified"
    else:
        status = "gap"

    missing_limit = 31
    if observed_days:
        first_observed = min(observed_days)
        last_observed = max(observed_days)
        missing_treatment_n, missing_treatment = _missing_days(
            first_observed, last_observed, treatment_days, missing_limit
        )
        missing_control_n, missing_control = _missing_days(
            first_observed, last_observed, control_days, missing_limit
        )
        missing_market_n, missing_market = _missing_days(
            first_observed, last_observed, observed_days, missing_limit
        )
    else:
        missing_treatment_n = missing_control_n = missing_market_n = 0
        missing_treatment = missing_control = missing_market = []
    coverage = {
        "basis": "eligible_assignment_unit_exposure_utc_day_either_arm",
        "status": status,
        "required_consecutive_days": required_days,
        "max_allowed_missing_days": 0,
        "outage_waiver": "unsupported_fail_closed",
        "first_observed_day": min(observed_days).isoformat() if observed_days else None,
        "last_observed_day": max(observed_days).isoformat() if observed_days else None,
        "first_common_day": common_days[0].isoformat() if common_days else None,
        "last_common_day": common_days[-1].isoformat() if common_days else None,
        "current_window_start": current_run[0].isoformat() if current_run else None,
        "current_window_end": current_run[-1].isoformat() if current_run else None,
        "current_consecutive_market_days": len(current_run),
        "longest_consecutive_market_days": max((len(run) for run in runs), default=0),
        "observed_treatment_day_n": len(treatment_days),
        "observed_control_day_n": len(control_days),
        "qualified_treatment_n": len(selected_treatment),
        "qualified_control_n": len(selected_control),
        "invalid_exposure_time_n": invalid_time_n,
        "missing_treatment_day_n": missing_treatment_n,
        "missing_control_day_n": missing_control_n,
        "missing_market_day_n": missing_market_n,
        "missing_treatment_days": [day.isoformat() for day in missing_treatment],
        "missing_control_days": [day.isoformat() for day in missing_control],
        "missing_market_days": [day.isoformat() for day in missing_market],
        "missing_days_truncated": (
            missing_treatment_n > missing_limit
            or missing_control_n > missing_limit
            or missing_market_n > missing_limit
        ),
    }
    return coverage, selected_treatment, selected_control


def _consecutive_day_runs(days: list[date]) -> list[list[date]]:
    runs: list[list[date]] = []
    for day in days:
        if not runs or day != runs[-1][-1] + timedelta(days=1):
            runs.append([day])
        else:
            runs[-1].append(day)
    return runs


def _missing_days(
    first: date,
    last: date,
    present: set[date],
    limit: int,
) -> tuple[int, list[date]]:
    """Return an exact count plus a bounded prefix without spanning every date."""
    total = (last - first).days + 1
    missing_n = max(0, total - len(present))
    missing: list[date] = []
    cursor = first
    for day in sorted(present):
        while cursor < day and len(missing) < limit:
            missing.append(cursor)
            cursor += timedelta(days=1)
        if len(missing) >= limit:
            break
        cursor = day + timedelta(days=1)
    while cursor <= last and len(missing) < limit:
        missing.append(cursor)
        cursor += timedelta(days=1)
    return missing_n, missing


def _conditional_randomization_inference(
    samples: list[CohortSample],
    *,
    alpha: float,
) -> dict[str, Any]:
    """Test the randomized ITT null without assuming independent outcomes.

    Universe assigns complete scanner-run clusters prospectively from an opaque
    experiment salt.  Adjacent clusters can share most of a 1h/4h price path,
    so resampling their outcomes as IID observations is invalid.  Conditional
    randomization inference instead holds every observed cluster outcome fixed
    and reassigns the observed number of treatment labels.  Under the sharp
    no-effect null this remains valid when outcomes are serially dependent.
    """

    ordered = sorted(
        samples,
        key=lambda sample: (
            _parse_time(sample.exposed_at)
            or datetime.min.replace(tzinfo=timezone.utc),
            sample.assignment_unit_key,
        ),
    )
    values = [float(sample.net_return_pct) for sample in ordered]
    treatment_n = sum(sample.assignment == "treatment" for sample in ordered)
    total_n = len(values)
    control_n = total_n - treatment_n
    base = {
        "contract_version": RANDOMIZATION_INFERENCE_VERSION,
        "estimand": "randomized_assignment_intent_to_treat",
        "null": "sharp_no_assignment_effect",
        "alternative": "treatment_greater_than_control",
        "conditioning": "observed_treatment_count",
        "iid_outcome_assumption": False,
        "interference_assumption": (
            "assignment_unit_outcome_is_not_changed_by_other_assignments"
        ),
        "alpha": float(alpha),
        "seed": RANDOMIZATION_SEED,
        "treatment_n": treatment_n,
        "control_n": control_n,
        "total_n": total_n,
    }
    if not 0 < treatment_n < total_n:
        return {
            **base,
            "mode": "unavailable",
            "evaluated_assignments": 0,
            "p_value": None,
            "reason": "both_randomized_arms_required",
        }

    observed = fmean([
        sample.net_return_pct for sample in ordered
        if sample.assignment == "treatment"
    ]) - fmean([
        sample.net_return_pct for sample in ordered
        if sample.assignment == "control"
    ])
    indices = tuple(range(total_n))
    assignment_count = math.comb(total_n, treatment_n)
    total_sum = sum(values)
    tolerance = 1e-12

    def permuted_delta(treated_indices: tuple[int, ...] | list[int]) -> float:
        treated_sum = sum(values[index] for index in treated_indices)
        control_sum = total_sum - treated_sum
        return treated_sum / treatment_n - control_sum / control_n

    if assignment_count <= _MAX_EXACT_RANDOMIZATIONS:
        extreme_n = sum(
            permuted_delta(treated) >= observed - tolerance
            for treated in combinations(indices, treatment_n)
        )
        evaluated = assignment_count
        p_value = extreme_n / evaluated
        mode = "exact"
    else:
        rng = random.Random(RANDOMIZATION_SEED)
        extreme_n = 0
        for _ in range(RANDOMIZATION_RESAMPLES):
            treated = rng.sample(indices, treatment_n)
            extreme_n += int(permuted_delta(treated) >= observed - tolerance)
        evaluated = RANDOMIZATION_RESAMPLES
        # Phipson-Smyth correction: a sampled randomization p-value is never 0.
        p_value = (extreme_n + 1) / (evaluated + 1)
        mode = "monte_carlo"

    return {
        **base,
        "mode": mode,
        "evaluated_assignments": evaluated,
        "assignment_space_size": assignment_count,
        "observed_delta": observed,
        "p_value": p_value,
        "reason": "ok",
    }


def _harm_metrics(
    cohort: ContributionCohort,
    treatment: list[CohortSample],
    control: list[CohortSample],
    delta: float | None,
) -> dict[str, Any]:
    treatment_loss_rate = _loss_rate(treatment)
    control_loss_rate = _loss_rate(control)
    loss_rate_delta = (
        treatment_loss_rate - control_loss_rate
        if treatment_loss_rate is not None and control_loss_rate is not None else None
    )
    treatment_mae_p95 = _mae_p95(treatment)
    control_mae_p95 = _mae_p95(control)
    mae_p95_worsening = (
        treatment_mae_p95 - control_mae_p95
        if treatment_mae_p95 is not None and control_mae_p95 is not None else None
    )
    gates = _harm_gates(cohort)
    required_per_arm = max(
        _HARM_MIN_SAMPLES_PER_ARM,
        int(cohort.experiment.min_samples_per_arm),
    )
    harm_indeterminate_reason = None
    if len(treatment) < required_per_arm or len(control) < required_per_arm:
        harm_indeterminate_reason = "insufficient_samples"
    breached: list[str] = []
    if harm_indeterminate_reason is None:
        if loss_rate_delta is not None and loss_rate_delta > gates["max_loss_rate_increase"]:
            breached.append("loss_rate_increase")
        if mae_p95_worsening is None:
            harm_indeterminate_reason = "mae_unavailable"
        elif mae_p95_worsening > gates["max_mae_p95_worsening_pp"]:
            breached.append("mae_p95_worsening")
    outcome_sources = Counter(
        source
        for sample in cohort.samples
        for source in sample.member_outcome_sources
    )
    allocation_selection_sources = Counter(
        source
        for sample in cohort.samples
        for source in sample.member_allocation_selection_sources
    )
    allocation_total_member_n = sum(sample.member_count for sample in cohort.samples)
    allocation_selected_member_n = sum(
        sample.selected_member_count for sample in cohort.samples
    )
    treatment_member_n = sum(sample.member_count for sample in treatment)
    treatment_selected_member_n = sum(
        sample.selected_member_count for sample in treatment
    )
    control_member_n = sum(sample.member_count for sample in control)
    control_selected_member_n = sum(
        sample.selected_member_count for sample in control
    )
    try:
        metric_version = metric_version_for_path(cohort.hypothesis.path_id)
    except (AttributeError, KeyError):
        metric_version = METRIC_VERSION
    return {
        "metric_version": metric_version,
        "statistical_unit": get_learning_path(
            cohort.hypothesis.path_id
        ).statistical_unit,
        "stage": cohort.experiment.stage,
        "iid_bootstrap": "retired_overlapping_horizon_outcomes",
        "policy_epoch": cohort.key.policy_epoch,
        "horizon": cohort.key.horizon,
        "outcome_contract_version": cohort.key.outcome_contract_version,
        "behavior_contract_hash": cohort.key.behavior_contract_hash or None,
        "source_code_commits": sorted(cohort.source_code_commits),
        "source_code_commit_unknown_n": cohort.source_code_commit_unknown_n,
        "code_commit_provenance_complete": (
            cohort.source_code_commit_unknown_n == 0
        ),
        "raw_exposure_n": cohort.raw_exposure_n,
        "exposed_treatment_assignment_unit_n": len(
            cohort.exposed_treatment_assignment_units
        ),
        "exposed_authority_applied_treatment_unit_n": len(
            cohort.exposed_authority_applied_treatment_units
        ),
        "exposed_actual_intervention_treatment_unit_n": len(
            cohort.exposed_actual_intervention_treatment_units
        ),
        "exposed_control_assignment_unit_n": len(
            cohort.exposed_control_assignment_units
        ),
        "unique_assignment_unit_n": len(cohort.samples),
        "cluster_member_n": sum(sample.member_count for sample in cohort.samples),
        "duplicate_exposure_n": cohort.duplicate_exposure_n,
        "duplicate_outcome_n": cohort.duplicate_outcome_n,
        "mirrored_universe_row_n": cohort.mirrored_universe_row_n,
        "outcome_source_member_counts": dict(sorted(outcome_sources.items())),
        "allocation_selection_source_member_counts": dict(
            sorted(allocation_selection_sources.items())
        ),
        "evidence_conflict_fingerprints": sorted(
            cohort.evidence_conflict_fingerprints
        ),
        "evidence_conflicts": [
            cohort.evidence_conflicts[fingerprint]
            for fingerprint in sorted(cohort.evidence_conflicts)[:20]
        ],
        "evidence_conflict_total_n": len(cohort.evidence_conflicts),
        "evidence_conflict_provenance_truncated": len(cohort.evidence_conflicts) > 20,
        "allocation_selected_member_n": allocation_selected_member_n,
        "allocation_total_member_n": allocation_total_member_n,
        "treatment_member_n": treatment_member_n,
        "treatment_selected_member_n": treatment_selected_member_n,
        "control_member_n": control_member_n,
        "control_selected_member_n": control_selected_member_n,
        "allocation_selection_rate": (
            allocation_selected_member_n / allocation_total_member_n
            if allocation_total_member_n else None
        ),
        "exclusion_reasons": dict(sorted(cohort.exclusion_reasons.items())),
        "invalid_reasons": dict(sorted(cohort.invalid_reasons.items())),
        "authority_applied_treatment_n": sum(
            sample.authority_applied for sample in treatment
        ),
        "treatment_loss_rate": treatment_loss_rate,
        "control_loss_rate": control_loss_rate,
        "loss_rate_delta": loss_rate_delta,
        "treatment_mae_p95_abs_pp": treatment_mae_p95,
        "control_mae_p95_abs_pp": control_mae_p95,
        "mae_p95_worsening_pp": mae_p95_worsening,
        "harm_gates": gates,
        "harm_required_per_arm": required_per_arm,
        "harm_state": (
            "indeterminate"
            if harm_indeterminate_reason is not None
            else ("breached" if breached else "clear")
        ),
        "harm_indeterminate_reason": harm_indeterminate_reason,
        "net_delta_pct": delta,
        "breached": harm_indeterminate_reason is None and bool(breached),
        "breach_reasons": breached,
    }


def _harm_gates(cohort: ContributionCohort) -> dict[str, float]:
    gates: dict[str, float] = {
        "max_loss_rate_increase": 0.15,
        "max_mae_p95_worsening_pp": 0.25,
    }
    # Hypothesis sets the baseline harm budget.
    try:
        hypothesis_gates = json.loads(cohort.hypothesis.harm_budget_json or "{}")
    except (TypeError, ValueError):
        hypothesis_gates = {}
    if isinstance(hypothesis_gates, dict):
        for key in tuple(gates):
            value = _finite_float(hypothesis_gates.get(key))
            if value is not None:
                gates[key] = min(gates[key], value)
    # Experiment-level overrides may only tighten (smaller = more restrictive).
    # A misconfigured experiment must not silently widen the hypothesis safety envelope.
    try:
        experiment_gates = json.loads(cohort.experiment.harm_gate_json or "{}")
    except (TypeError, ValueError):
        experiment_gates = {}
    if isinstance(experiment_gates, dict):
        for key in tuple(gates):
            value = _finite_float(experiment_gates.get(key))
            if value is not None:
                gates[key] = min(gates[key], value)
    return gates


def _loss_rate(samples: list[CohortSample]) -> float | None:
    if not samples:
        return None
    return sum(sample.net_return_pct < 0 for sample in samples) / len(samples)


def _mae_p95(samples: list[CohortSample]) -> float | None:
    values = [-sample.mae_pct for sample in samples if sample.mae_pct is not None]
    return _quantile(values, 0.95) if values else None


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("cannot calculate quantile of no values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def _parse_time(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Normalize offset to UTC so downstream comparisons, hashes and log
        # rendering are consistent with the cohorts._parse_time contract
        # (audit LEARNING_MODULE_SDD_VS_REALITY §5 GAP-03 tail).
        return dt.astimezone(timezone.utc)
    except (AttributeError, ValueError):
        return None


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _reason(prefix: str, reasons: Any) -> str:
    names = sorted(reasons if not isinstance(reasons, Counter) else reasons.keys())
    return f"{prefix}: {', '.join(names)}"

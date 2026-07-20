"""Deterministic, measurement-only contribution verdict calculations."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import random
from statistics import fmean
from typing import Any

from aletheia.learning.cohorts import ContributionCohort, CohortSample


METRIC_VERSION = "universe_family_calibration_cluster_allocation_utility_v2"
BOOTSTRAP_SEED = 20260712
BOOTSTRAP_RESAMPLES = 2000
_PRACTICAL_NEUTRAL_DELTA_PCT = 0.10


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


def evaluate_cohort(cohort: ContributionCohort) -> VerdictEvaluation:
    """Classify one already-isolated cohort without changing any authority state."""
    treatment = cohort.treatment_samples
    control = cohort.control_samples
    treatment_values = [sample.net_return_pct for sample in treatment]
    control_values = [sample.net_return_pct for sample in control]
    observed = fmean(treatment_values) if treatment_values else None
    baseline = fmean(control_values) if control_values else None
    delta = observed - baseline if observed is not None and baseline is not None else None
    confidence_low, confidence_high = _bootstrap_interval(treatment_values, control_values)
    harm_metrics = _harm_metrics(cohort, treatment, control, delta)

    if cohort.invalid_reasons:
        return VerdictEvaluation(
            baseline, observed, delta, confidence_low, confidence_high, harm_metrics,
            "invalid", _reason("invalid evidence", cohort.invalid_reasons),
        )
    if _insufficient(cohort, treatment, control):
        return VerdictEvaluation(
            baseline, observed, delta, confidence_low, confidence_high, harm_metrics,
            "insufficient_data", "insufficient unique prospective treatment/control samples or time span",
        )
    if not any(sample.authority_applied for sample in treatment):
        return VerdictEvaluation(
            baseline, observed, delta, confidence_low, confidence_high, harm_metrics,
            "insufficient_data", "no_actual_intervention",
        )
    assert delta is not None
    if harm_metrics["breached"]:
        return VerdictEvaluation(
            baseline, observed, delta, confidence_low, confidence_high, harm_metrics,
            "negative", _reason("harm gate breached", harm_metrics["breach_reasons"]),
        )
    if delta < 0:
        return VerdictEvaluation(
            baseline, observed, delta, confidence_low, confidence_high, harm_metrics,
            "negative", "treatment cost-aware mean trails control",
        )
    threshold = max(_PRACTICAL_NEUTRAL_DELTA_PCT, float(cohort.hypothesis.effect_threshold or 0.0))
    if delta < threshold or confidence_low is None or confidence_low <= 0:
        return VerdictEvaluation(
            baseline, observed, delta, confidence_low, confidence_high, harm_metrics,
            "neutral", "effect is within the practical-neutral band or confidence is inconclusive",
        )
    return VerdictEvaluation(
        baseline, observed, delta, confidence_low, confidence_high, harm_metrics,
        "positive", "cost-aware treatment mean exceeds control with deterministic confidence and no harm breach",
    )


def _insufficient(
    cohort: ContributionCohort,
    treatment: list[CohortSample],
    control: list[CohortSample],
) -> bool:
    required = max(1, int(cohort.experiment.min_samples_per_arm))
    if len(treatment) < required or len(control) < required:
        return True
    entries = [_parse_time(sample.entry_ts) for sample in cohort.samples]
    times = [entry for entry in entries if entry is not None]
    if len(times) != len(cohort.samples):
        return True
    span_days = (max(times) - min(times)).total_seconds() / 86_400 if times else 0.0
    return span_days < max(0, int(cohort.experiment.min_market_days))


def _bootstrap_interval(treatment: list[float], control: list[float]) -> tuple[float | None, float | None]:
    if not treatment or not control:
        return None, None
    rng = random.Random(BOOTSTRAP_SEED)
    deltas = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        treated = fmean(treatment[rng.randrange(len(treatment))] for _ in treatment)
        baseline = fmean(control[rng.randrange(len(control))] for _ in control)
        deltas.append(treated - baseline)
    deltas.sort()
    return _quantile(deltas, 0.10), _quantile(deltas, 0.90)


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
    breached: list[str] = []
    if loss_rate_delta is not None and loss_rate_delta > gates["max_loss_rate_increase"]:
        breached.append("loss_rate_increase")
    if mae_p95_worsening is not None and mae_p95_worsening > gates["max_mae_p95_worsening_pct"]:
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
    return {
        "metric_version": METRIC_VERSION,
        "stage": cohort.experiment.stage,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "policy_epoch": cohort.key.policy_epoch,
        "horizon": cohort.key.horizon,
        "outcome_contract_version": cohort.key.outcome_contract_version,
        "code_commit": cohort.key.code_commit or None,
        "raw_exposure_n": cohort.raw_exposure_n,
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
        "allocation_selected_member_n": allocation_selected_member_n,
        "allocation_total_member_n": allocation_total_member_n,
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
        "treatment_mae_p95_abs_pct": treatment_mae_p95,
        "control_mae_p95_abs_pct": control_mae_p95,
        "mae_p95_worsening_pct": mae_p95_worsening,
        "harm_gates": gates,
        "net_delta_pct": delta,
        "breached": bool(breached),
        "breach_reasons": breached,
    }


def _harm_gates(cohort: ContributionCohort) -> dict[str, float]:
    gates: dict[str, float] = {
        "max_loss_rate_increase": 1.0,
        "max_mae_p95_worsening_pct": 0.25,
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
                gates[key] = value
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

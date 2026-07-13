"""Persist and inspect read-only Learning contribution verdicts.

This module is deliberately not a scheduler job and does not call any Learning
authority or trading consumer.  Its sole mutation is an idempotent append to the
existing measurement ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from aletheia.learning.cohorts import ContributionCohort, build_experiment_cohorts
from aletheia.learning.ledger import get_record, insert_record
from aletheia.learning.models import LearningContributionVerdict
from aletheia.learning.verdicts import METRIC_VERSION, VerdictEvaluation, evaluate_cohort
from aletheia.storage.sqlite import get_db


def evaluate_experiment(experiment_id: str) -> list[LearningContributionVerdict]:
    """Evaluate and persist all supported isolated cohorts for one experiment.

    Equal source inputs yield the same verdict id and leave the existing row in
    place.  New settled inputs yield a new immutable measurement record.
    """
    verdicts: list[LearningContributionVerdict] = []
    for cohort in build_experiment_cohorts(experiment_id):
        evaluation = evaluate_cohort(cohort)
        verdict = _build_verdict(cohort, evaluation)
        insert_record("learning_contribution_verdicts", verdict, ignore_duplicates=True)
        stored = get_record("learning_contribution_verdicts", verdict.verdict_id)
        verdicts.append(LearningContributionVerdict(**(stored or verdict.model_dump())))
    return verdicts


def inspect_experiment(experiment_id: str) -> dict[str, Any]:
    """Read an experiment's current source cohorts and persisted verdicts only."""
    experiment = get_record("learning_experiments", experiment_id)
    cohorts = build_experiment_cohorts(experiment_id)
    rows = get_db().execute(
        "SELECT * FROM learning_contribution_verdicts WHERE experiment_id=? "
        "ORDER BY created_at, verdict_id",
        (experiment_id,),
    ).fetchall()
    return {
        "experiment": experiment,
        "cohorts": [cohort.inspection() for cohort in cohorts],
        "verdicts": [dict(row) for row in rows],
    }


def _build_verdict(
    cohort: ContributionCohort,
    evaluation: VerdictEvaluation,
) -> LearningContributionVerdict:
    metrics = dict(evaluation.harm_metrics)
    metrics["cohort_input_fingerprint"] = _cohort_fingerprint(cohort, evaluation)
    verdict_id = f"contribution-{metrics['cohort_input_fingerprint'][:24]}"
    window_start, window_end = _window(cohort)
    return LearningContributionVerdict(
        verdict_id=verdict_id,
        experiment_id=cohort.key.experiment_id,
        hypothesis_id=cohort.key.hypothesis_id,
        stage=cohort.experiment.stage,
        window_start=window_start,
        window_end=window_end,
        unique_treatment_n=len(cohort.treatment_samples),
        unique_control_n=len(cohort.control_samples),
        baseline=evaluation.baseline,
        observed=evaluation.observed,
        delta=evaluation.delta,
        confidence_low=evaluation.confidence_low,
        confidence_high=evaluation.confidence_high,
        harm_metrics_json=json.dumps(metrics, sort_keys=True, separators=(",", ":")),
        verdict=evaluation.verdict,
        reason=evaluation.reason,
        created_at=window_end,
        policy_epoch=cohort.key.policy_epoch,
        target_horizon=cohort.key.horizon,
        outcome_contract_version=cohort.key.outcome_contract_version,
        metric_version=METRIC_VERSION,
        code_commit=cohort.key.code_commit or None,
    )


def _cohort_fingerprint(cohort: ContributionCohort, evaluation: VerdictEvaluation) -> str:
    payload = {
        "key": {
            "experiment_id": cohort.key.experiment_id,
            "hypothesis_id": cohort.key.hypothesis_id,
            "policy_epoch": cohort.key.policy_epoch,
            "horizon": cohort.key.horizon,
            "outcome_contract_version": cohort.key.outcome_contract_version,
            "code_commit": cohort.key.code_commit,
        },
        "samples": [
            {
                "candidate_key": sample.candidate_key,
                "outcome_candidate_key": sample.outcome_candidate_key,
                "assignment": sample.assignment,
                "net_return_pct": sample.net_return_pct,
                "mae_pct": sample.mae_pct,
                "entry_ts": sample.entry_ts,
                "exposed_at": sample.exposed_at,
                "authority_applied": sample.authority_applied,
            }
            for sample in sorted(cohort.samples, key=lambda row: (row.assignment, row.outcome_candidate_key))
        ],
        "exclusions": dict(sorted(cohort.exclusion_reasons.items())),
        "invalid": dict(sorted(cohort.invalid_reasons.items())),
        "duplicate_exposure_n": cohort.duplicate_exposure_n,
        "duplicate_outcome_n": cohort.duplicate_outcome_n,
        "minimum_samples": cohort.experiment.min_samples_per_arm,
        "minimum_market_days": cohort.experiment.min_market_days,
        "effect_threshold": cohort.hypothesis.effect_threshold,
        "harm_metrics": evaluation.harm_metrics,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _window(cohort: ContributionCohort) -> tuple[str, str]:
    timestamps = [sample.entry_ts for sample in cohort.samples]
    timestamps.extend(sample.exposed_at for sample in cohort.samples)
    if not timestamps:
        # A fully excluded cohort must still be durable and deterministic.
        return "1970-01-01T00:00:00+00:00", "1970-01-01T00:00:00+00:00"
    parsed = []
    for value in timestamps:
        try:
            timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        parsed.append(timestamp.astimezone(timezone.utc))
    if not parsed:
        return "1970-01-01T00:00:00+00:00", "1970-01-01T00:00:00+00:00"
    return min(parsed).isoformat(), max(parsed).isoformat()

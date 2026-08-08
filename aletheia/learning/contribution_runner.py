"""Persist and inspect read-only Learning contribution verdicts.

This module does not call any Learning authority or trading consumer. Its sole
mutation is an idempotent append to the existing measurement ledger.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

from aletheia.learning.cohorts import ContributionCohort, build_experiment_cohorts
from aletheia.learning.ledger import get_record, insert_record
from aletheia.learning.models import LearningContributionVerdict
from aletheia.learning.fixed_look import (
    InferenceContractError,
    consume_fixed_look_verdict,
    cutoff_assignment_unit_keys,
    freeze_fixed_look_cutoff,
    get_fixed_look_decision,
)
from aletheia.learning.verdicts import METRIC_VERSION, VerdictEvaluation, evaluate_cohort
from aletheia.storage.sqlite import get_db


def run_verdict_cycle() -> dict:
    """Manual compatibility helper for evaluating all open experiments."""
    db = get_db()
    rows = db.execute(
        "SELECT experiment_id FROM learning_experiments WHERE ended_at IS NULL",
    ).fetchall()
    evaluated = 0
    verdicts_written = 0
    skipped = 0
    for row in rows:
        experiment_id = row["experiment_id"]
        try:
            cohorts = build_experiment_cohorts(experiment_id)
            if not any(
                len(c.treatment_samples) + len(c.control_samples) > 0
                for c in cohorts
            ):
                skipped += 1
                continue
            new_verdicts = evaluate_experiment(experiment_id)
            evaluated += 1
            verdicts_written += len(new_verdicts)
        except Exception as exc:
            logger.warning("run_verdict_cycle: experiment %s failed: %s", experiment_id, exc)
            skipped += 1
    return {"evaluated": evaluated, "verdicts_written": verdicts_written, "skipped": skipped}


def evaluate_experiment(experiment_id: str) -> list[LearningContributionVerdict]:
    """Evaluate and persist all supported isolated cohorts for one experiment.

    Equal source inputs yield the same verdict id and leave the existing row in
    place.  New settled inputs yield a new immutable measurement record.
    """
    verdicts: list[LearningContributionVerdict] = []
    # Retention: a cohort whose exposures are still gathering data would
    # otherwise mint a durable ``insufficient_data`` placeholder row every 6h
    # cycle, inflating ``learning_contribution_verdicts`` for no gain.  Skip
    # only those "still developing" cohorts — the next cycle will re-evaluate
    # once real samples (or terminal reasons) land.  Cohorts that already carry
    # terminal signal — samples, invalid reasons, or non-transient exclusion
    # reasons (e.g. ``dirty_or_missing_entry_time`` for pre-floor observations)
    # — must still produce their verdict so downstream integrity checks see it.
    _TRANSIENT_EXCLUSIONS = frozenset({"unsettled", "missing_exact_outcome"})
    for cohort in build_experiment_cohorts(experiment_id):
        if (
            not cohort.samples
            and not cohort.invalid_reasons
            and not any(
                reason not in _TRANSIENT_EXCLUSIONS
                for reason in cohort.exclusion_reasons
            )
        ):
            continue
        evaluation = evaluate_cohort(cohort)
        verdict = _build_verdict(cohort, evaluation)
        insert_record("learning_contribution_verdicts", verdict, ignore_duplicates=True)
        stored = get_record("learning_contribution_verdicts", verdict.verdict_id)
        verdicts.append(LearningContributionVerdict(**(stored or verdict.model_dump())))
        confirmatory = _maybe_consume_confirmatory_verdict(cohort)
        if confirmatory is not None:
            verdicts.append(confirmatory)
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
    *,
    inference_role: str = "monitoring",
    authority_eligible: bool = False,
    inference_contract_version: str | None = None,
    inference_plan_hash: str | None = None,
    inference_cutoff_fingerprint: str | None = None,
) -> LearningContributionVerdict:
    metrics = dict(evaluation.harm_metrics)
    metrics["inference_role"] = inference_role
    metrics["authority_eligible"] = authority_eligible
    metrics["inference_contract_version"] = inference_contract_version
    metrics["inference_plan_hash"] = inference_plan_hash
    metrics["inference_cutoff_fingerprint"] = inference_cutoff_fingerprint
    metrics["cohort_input_fingerprint"] = _cohort_fingerprint(cohort, evaluation)
    verdict_id = f"contribution-{metrics['cohort_input_fingerprint'][:24]}"
    window_start, window_end = _window(cohort, evaluation)
    return LearningContributionVerdict(
        verdict_id=verdict_id,
        experiment_id=cohort.key.experiment_id,
        hypothesis_id=cohort.key.hypothesis_id,
        stage=cohort.experiment.stage,
        window_start=window_start,
        window_end=window_end,
        unique_treatment_n=evaluation.unique_treatment_n,
        unique_control_n=evaluation.unique_control_n,
        baseline=evaluation.baseline,
        observed=evaluation.observed,
        delta=evaluation.delta,
        confidence_low=evaluation.confidence_low,
        confidence_high=evaluation.confidence_high,
        harm_metrics_json=json.dumps(metrics, sort_keys=True, separators=(",", ":")),
        verdict=evaluation.verdict,
        reason=evaluation.reason,
        created_at=datetime.now(timezone.utc).isoformat(),
        policy_epoch=cohort.key.policy_epoch,
        target_horizon=cohort.key.horizon,
        outcome_contract_version=cohort.key.outcome_contract_version,
        metric_version=METRIC_VERSION,
        code_commit=(
            next(iter(cohort.source_code_commits))
            if (
                len(cohort.source_code_commits) == 1
                and cohort.source_code_commit_unknown_n == 0
            )
            else None
        ),
        behavior_contract_hash=cohort.key.behavior_contract_hash or None,
        inference_contract_version=inference_contract_version,
        inference_role=inference_role,
        authority_eligible=authority_eligible,
        inference_plan_hash=inference_plan_hash,
        inference_cutoff_fingerprint=inference_cutoff_fingerprint,
    )


def _maybe_consume_confirmatory_verdict(
    cohort: ContributionCohort,
) -> LearningContributionVerdict | None:
    if not str(cohort.experiment.stage).startswith("paper_"):
        return None
    decision = get_fixed_look_decision(cohort.experiment.experiment_id)
    if decision is None or decision.status in {"consumed", "invalidated"}:
        return None
    try:
        decision = freeze_fixed_look_cutoff(cohort.experiment.experiment_id)
        selected_keys = cutoff_assignment_unit_keys(decision)
    except InferenceContractError as exc:
        logger.warning(
            "fixed-look inference unavailable for experiment %s: %s",
            cohort.experiment.experiment_id,
            exc,
        )
        return None
    if decision.status != "cutoff_frozen" or not selected_keys:
        return None
    confirmatory_cohorts = build_experiment_cohorts(
        cohort.experiment.experiment_id,
        assignment_unit_keys=frozenset(selected_keys),
    )
    selected = next(
        (candidate for candidate in confirmatory_cohorts if candidate.key == cohort.key),
        None,
    )
    if selected is None or selected.invalid_reasons:
        return None
    evaluation = evaluate_cohort(
        selected,
        authority_alpha=float(decision.authority_alpha),
    )
    if evaluation.verdict == "insufficient_data":
        return None
    verdict = _build_verdict(
        selected,
        evaluation,
        inference_role="confirmatory",
        authority_eligible=True,
        inference_contract_version=decision.inference_contract_version,
        inference_plan_hash=decision.plan_hash,
        inference_cutoff_fingerprint=decision.cutoff_fingerprint,
    )
    if not consume_fixed_look_verdict(verdict):
        return None
    stored = get_record("learning_contribution_verdicts", verdict.verdict_id)
    return LearningContributionVerdict.model_validate(stored or verdict.model_dump())


def _cohort_fingerprint(cohort: ContributionCohort, evaluation: VerdictEvaluation) -> str:
    payload = {
        "key": {
            "experiment_id": cohort.key.experiment_id,
            "hypothesis_id": cohort.key.hypothesis_id,
            "policy_epoch": cohort.key.policy_epoch,
            "horizon": cohort.key.horizon,
            "outcome_contract_version": cohort.key.outcome_contract_version,
            "behavior_contract_hash": cohort.key.behavior_contract_hash,
        },
        "samples": [
            {
                "assignment_unit_key": sample.assignment_unit_key,
                "candidate_key": sample.candidate_key,
                "outcome_candidate_key": sample.outcome_candidate_key,
                "outcome_source": sample.outcome_source,
                "member_outcome_candidate_keys": sample.member_outcome_candidate_keys,
                "member_outcome_sources": sample.member_outcome_sources,
                "member_count": sample.member_count,
                "selected_member_count": sample.selected_member_count,
                "allocation_selected": sample.allocation_selected,
                "allocation_selection_source": sample.allocation_selection_source,
                "member_allocation_selection_sources": (
                    sample.member_allocation_selection_sources
                ),
                "assignment": sample.assignment,
                "net_return_pct": sample.net_return_pct,
                "mae_pct": sample.mae_pct,
                "entry_ts": sample.entry_ts,
                "exposed_at": sample.exposed_at,
                "authority_applied": sample.authority_applied,
                "authority_reason": sample.authority_reason,
                "behavior_changed": sample.behavior_changed,
                "behavior_change_reason": sample.behavior_change_reason,
            }
            for sample in sorted(cohort.samples, key=lambda row: (row.assignment, row.outcome_candidate_key))
        ],
        "exclusions": dict(sorted(cohort.exclusion_reasons.items())),
        "invalid": dict(sorted(cohort.invalid_reasons.items())),
        "duplicate_exposure_n": cohort.duplicate_exposure_n,
        "duplicate_outcome_n": cohort.duplicate_outcome_n,
        "mirrored_universe_row_n": cohort.mirrored_universe_row_n,
        "evidence_conflict_fingerprints": sorted(
            cohort.evidence_conflict_fingerprints
        ),
        "minimum_samples": cohort.experiment.min_samples_per_arm,
        "minimum_market_days": cohort.experiment.min_market_days,
        "effect_threshold": cohort.hypothesis.effect_threshold,
        "harm_metrics": evaluation.harm_metrics,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _window(
    cohort: ContributionCohort,
    evaluation: VerdictEvaluation,
) -> tuple[str, str]:
    samples = cohort.samples
    coverage = evaluation.harm_metrics.get("market_day_coverage")
    if isinstance(coverage, dict) and coverage.get("status") != "not_required":
        start = coverage.get("current_window_start")
        end = coverage.get("current_window_end")
        try:
            start_day = datetime.fromisoformat(str(start)).date()
            end_day = datetime.fromisoformat(str(end)).date()
        except (TypeError, ValueError):
            samples = []
        else:
            samples = [
                sample
                for sample in cohort.samples
                if (
                    (exposed := _parse_window_time(sample.exposed_at)) is not None
                    and start_day <= exposed.date() <= end_day
                )
            ]
    timestamps = [sample.entry_ts for sample in samples]
    timestamps.extend(sample.exposed_at for sample in samples)
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


def _parse_window_time(value: str) -> datetime | None:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)

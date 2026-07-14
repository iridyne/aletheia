"""Build exact, prospective contribution cohorts for supported Learning paths.

The first runtime slice deliberately supports one path only.  It never searches
by symbol or time proximity: an exposure can contribute only through the exact
``outcome_candidate_key`` it recorded before the outcome entered its horizon.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from typing import Any

from aletheia.learning.ledger import get_record
from aletheia.learning.models import LearningExperiment, LearningHypothesis
from aletheia.learning.outcomes import assess_candidate_outcome
from aletheia.storage.sqlite import get_db


SUPPORTED_PATH_ID = "universe_family_calibration"
SUPPORTED_PATH_IDS = frozenset({"universe_family_calibration", "ranker_case_score"})


@dataclass(frozen=True, order=True)
class CohortKey:
    experiment_id: str
    hypothesis_id: str
    policy_epoch: str
    horizon: str
    outcome_contract_version: str
    code_commit: str


@dataclass(frozen=True)
class CohortSample:
    candidate_key: str
    outcome_candidate_key: str
    assignment: str
    net_return_pct: float
    mae_pct: float | None
    entry_ts: str
    exposed_at: str
    authority_applied: bool


@dataclass
class ContributionCohort:
    key: CohortKey
    experiment: LearningExperiment
    hypothesis: LearningHypothesis
    samples: list[CohortSample] = field(default_factory=list)
    raw_exposure_n: int = 0
    duplicate_exposure_n: int = 0
    duplicate_outcome_n: int = 0
    exclusion_reasons: Counter[str] = field(default_factory=Counter)
    invalid_reasons: Counter[str] = field(default_factory=Counter)

    @property
    def treatment_samples(self) -> list[CohortSample]:
        return [sample for sample in self.samples if sample.assignment == "treatment"]

    @property
    def control_samples(self) -> list[CohortSample]:
        return [sample for sample in self.samples if sample.assignment == "control"]

    def inspection(self) -> dict[str, Any]:
        return {
            "experiment_id": self.key.experiment_id,
            "hypothesis_id": self.key.hypothesis_id,
            "policy_epoch": self.key.policy_epoch,
            "horizon": self.key.horizon,
            "outcome_contract_version": self.key.outcome_contract_version,
            "code_commit": self.key.code_commit or None,
            "raw_exposure_n": self.raw_exposure_n,
            "unique_treatment_n": len(self.treatment_samples),
            "unique_control_n": len(self.control_samples),
            "duplicate_exposure_n": self.duplicate_exposure_n,
            "duplicate_outcome_n": self.duplicate_outcome_n,
            "authority_applied_treatment_n": sum(
                sample.authority_applied for sample in self.treatment_samples
            ),
            "exclusion_reasons": dict(sorted(self.exclusion_reasons.items())),
            "invalid_reasons": dict(sorted(self.invalid_reasons.items())),
            "outcome_candidate_keys": sorted(sample.outcome_candidate_key for sample in self.samples),
        }


def build_experiment_cohorts(experiment_id: str) -> list[ContributionCohort]:
    """Return validly scoped cohorts without modifying Learning or trading state."""
    experiment_row = get_record("learning_experiments", experiment_id)
    if experiment_row is None:
        return []
    experiment = LearningExperiment(**experiment_row)
    hypothesis_row = get_record("learning_hypotheses", experiment.hypothesis_id)
    if hypothesis_row is None:
        return []
    hypothesis = LearningHypothesis(**hypothesis_row)
    if hypothesis.path_id not in SUPPORTED_PATH_IDS:
        return []

    rows = [dict(row) for row in get_db().execute(
        "SELECT * FROM learning_exposures WHERE experiment_id=? AND hypothesis_id=? "
        "AND path_id=? ORDER BY datetime(exposed_at), exposure_id",
        (experiment.experiment_id, hypothesis.hypothesis_id, hypothesis.path_id),
    ).fetchall()]
    cohorts: dict[CohortKey, ContributionCohort] = {}
    candidates: dict[CohortKey, dict[str, dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    seen_duplicate_outcomes: set[tuple[CohortKey, str]] = set()

    for exposure in rows:
        key = CohortKey(
            experiment.experiment_id,
            hypothesis.hypothesis_id,
            str(exposure.get("concurrent_policy_epoch") or ""),
            str(hypothesis.target_horizon or ""),
            str(exposure.get("outcome_contract_version") or ""),
            str(exposure.get("code_commit") or ""),
        )
        cohort = cohorts.setdefault(key, ContributionCohort(key, experiment, hypothesis))
        cohort.raw_exposure_n += 1
        exposure_stage = exposure.get("experiment_stage")
        if exposure_stage is not None and str(exposure_stage) != experiment.stage:
            cohort.invalid_reasons["experiment_stage_mismatch"] += 1
            continue
        if not key.policy_epoch:
            cohort.invalid_reasons["missing_policy_epoch"] += 1
            continue
        if not key.horizon:
            cohort.invalid_reasons["missing_target_horizon"] += 1
            continue
        if not key.outcome_contract_version:
            cohort.invalid_reasons["missing_outcome_contract_version"] += 1
            continue
        assignment = str(exposure.get("assignment") or "").lower()
        if assignment not in {"treatment", "control"}:
            cohort.invalid_reasons["invalid_assignment"] += 1
            continue
        outcome_key = str(exposure.get("outcome_candidate_key") or "")
        if not outcome_key:
            cohort.invalid_reasons["missing_outcome_candidate_key"] += 1
            continue
        if str(exposure.get("outcome_source") or "") != "fixed_horizon_candidate":
            cohort.invalid_reasons["unsupported_outcome_source"] += 1
            continue
        exposed_at = _parse_time(exposure.get("exposed_at"))
        if exposed_at is None:
            cohort.invalid_reasons["missing_exposure_time"] += 1
            continue

        outcome, outcome_reason, duplicate_outcomes, wrong_horizon_n = _exact_outcome(
            outcome_key, key.horizon,
        )
        if wrong_horizon_n:
            cohort.exclusion_reasons["wrong_horizon"] += wrong_horizon_n
        if duplicate_outcomes and (key, outcome_key) not in seen_duplicate_outcomes:
            cohort.duplicate_outcome_n += duplicate_outcomes
            seen_duplicate_outcomes.add((key, outcome_key))
        if outcome_reason:
            if outcome_reason in {"unsettled", "missing_exact_outcome", "wrong_horizon"}:
                if outcome_reason != "wrong_horizon" or not wrong_horizon_n:
                    cohort.exclusion_reasons[outcome_reason] += 1
            else:
                cohort.invalid_reasons[outcome_reason] += 1
            continue
        assert outcome is not None
        assessment = assess_candidate_outcome(outcome)
        if not assessment.eligible:
            # Observations before the data-quality floor or with unsettled status are
            # ineligible but not corrupt — exclude them without invalidating the cohort.
            # Structural issues (missing direction, missing cost model, etc.) indicate
            # corrupt data and do invalidate.
            if assessment.reason in {
                "unsettled",
                "dirty_or_missing_entry_time",
                "dirty_or_missing_close_time",
            }:
                cohort.exclusion_reasons[assessment.reason] += 1
            else:
                cohort.invalid_reasons[assessment.reason] += 1
            continue
        incomplete_cost_reason = _incomplete_cost_reason(outcome)
        if incomplete_cost_reason:
            cohort.invalid_reasons[incomplete_cost_reason] += 1
            continue
        entry_at = _parse_time(outcome.get("entry_ts"))
        settled_at = _parse_time(outcome.get("settled_at"))
        if entry_at is None or settled_at is None:
            cohort.invalid_reasons["missing_outcome_time"] += 1
            continue
        if entry_at < exposed_at or settled_at < exposed_at:
            cohort.invalid_reasons["retrospective_or_contaminated_exposure"] += 1
            continue
        net = _finite_float(outcome.get("net_return_pct"))
        if net is None:
            cohort.invalid_reasons["net_economics_unavailable"] += 1
            continue
        candidate = str(exposure.get("candidate_key") or outcome_key)
        candidates[key][outcome_key][assignment].append((exposure, outcome))

    for key, outcome_groups in candidates.items():
        cohort = cohorts[key]
        for outcome_key, by_assignment in outcome_groups.items():
            if len(by_assignment) > 1:
                cohort.invalid_reasons["unidentifiable_assignment_overlap"] += 1
                continue
            assignment, rows_for_assignment = next(iter(by_assignment.items()))
            if len(rows_for_assignment) > 1:
                cohort.duplicate_exposure_n += len(rows_for_assignment) - 1
            exposure, outcome = rows_for_assignment[0]
            net = _finite_float(outcome.get("net_return_pct"))
            assert net is not None
            cohort.samples.append(CohortSample(
                candidate_key=str(exposure.get("candidate_key") or outcome_key),
                outcome_candidate_key=outcome_key,
                assignment=assignment,
                net_return_pct=net,
                mae_pct=_finite_float(outcome.get("mae_pct")),
                entry_ts=str(outcome["entry_ts"]),
                exposed_at=str(exposure["exposed_at"]),
                authority_applied=_manifest_authority_applied(exposure.get("manifest_json")),
            ))
    for cohort in cohorts.values():
        cohort.samples.sort(key=lambda sample: (sample.assignment, sample.outcome_candidate_key))
    return [cohorts[key] for key in sorted(cohorts)]


def _manifest_authority_applied(manifest_json: Any) -> bool:
    """Read only an explicit JSON boolean; missing/malformed manifests fail closed."""
    try:
        manifest = json.loads(manifest_json or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("authority_applied") is True


def _exact_outcome(
    outcome_candidate_key: str,
    horizon: str,
) -> tuple[dict[str, Any] | None, str | None, int, int]:
    """Resolve exactly one deduplicated universe outcome, never a symbol match."""
    outcomes = [dict(row) for row in get_db().execute(
        "SELECT * FROM universe_forward_outcomes WHERE outcome_candidate_key=? "
        "ORDER BY datetime(entry_ts), id",
        (outcome_candidate_key,),
    ).fetchall()]
    matches = [outcome for outcome in outcomes if str(outcome.get("horizon") or "") == horizon]
    wrong_horizon_n = len(outcomes) - len(matches)
    if not matches:
        return None, "wrong_horizon" if outcomes else "missing_exact_outcome", 0, wrong_horizon_n
    distinct = {_outcome_fingerprint(outcome) for outcome in matches}
    if len(distinct) > 1:
        return None, "ambiguous_duplicate_outcome", 0, wrong_horizon_n
    duplicate_outcomes = len(matches) - 1
    return matches[0], None, duplicate_outcomes, wrong_horizon_n


def _outcome_fingerprint(outcome: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(outcome.get(field) for field in (
        "outcome_candidate_key", "horizon", "status", "entry_ts", "settled_at",
        "net_return_pct", "estimated_fee_pct", "estimated_spread_slippage_pct",
        "estimated_funding_pct", "mae_pct", "cost_model_version", "market_type", "direction",
    ))


def _incomplete_cost_reason(outcome: dict[str, Any]) -> str | None:
    if not outcome.get("cost_model_version"):
        return "missing_cost_model_version"
    for field in ("estimated_fee_pct", "estimated_spread_slippage_pct"):
        if _finite_float(outcome.get(field)) is None:
            return "incomplete_cost_model"
    market_type = str(outcome.get("market_type") or "").lower()
    if market_type in {"perp", "swap", "usdt_futures", "futures"}:
        if _finite_float(outcome.get("estimated_funding_pct")) is None:
            return "incomplete_derivative_cost_model"
    return None


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None

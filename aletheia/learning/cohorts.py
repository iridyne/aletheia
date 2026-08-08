"""Build exact, prospective contribution cohorts for supported Learning paths.

Each path must provide an explicit outcome/statistical-unit adapter.  Cohorts
never search by symbol or time proximity: an exposure can contribute only
through the exact ``outcome_candidate_key`` recorded before its horizon.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
from statistics import fmean
from typing import Any

from aletheia.learning.ledger import get_record
from aletheia.learning.models import LearningExperiment, LearningHypothesis
from aletheia.learning.outcomes import assess_candidate_outcome
from aletheia.learning.registry import LEARNING_PATHS
from aletheia.storage.sqlite import get_db


SUPPORTED_PATH_ID = "universe_family_calibration"
# Consumer wiring is not causal measurement support. A path enters this set only
# after its exact outcome and statistical-unit contracts are executable AND have
# focused passing cohort tests that exercise the full round-trip.
#
# Universe full-arm support:
#   - outcome contract: universe_forward_outcomes (universe-keyed, {learning_key}:{horizon})
#   - allocation_selected: populated by universe/service._register_experiment_candidate_outcomes
#   - statistical unit: full-arm Universe allocation member
#
# The paths below are consumer_wired (the runtime reads their authority) but are
# NOT measurement_supported — the cohort builder's _load_exact_outcomes requires
# allocation_selected to be non-null, which their filter_candidate_outcomes rows
# cannot supply by contract. Including them would leave every such outcome
# permanently unmeasured ("missing_allocation_result", now a benign exclusion —
# see _IDENTITY_EXCLUSION_REASONS) rather than measured, so keep them out until a
# per-path cohort adapter exists. See issue #324 for the full audit.
#
# Paths blocked from measurement until per-path cohort adapters are implemented:
#   operator_learning_bundle:   needs decision-utility outcome after prompt treatment
#   observer_advisory_bundle:   needs path-specific statistical-unit/outcome contract
#   ranker_case_score:          needs path-specific statistical-unit/outcome contract
#   ranker_memo_score:          needs path-specific statistical-unit/outcome contract
#   ranker_factor_filter:       needs path-specific statistical-unit/outcome contract
SUPPORTED_PATH_IDS = frozenset(
    path.path_id for path in LEARNING_PATHS if path.measurement_supported
)

_ECONOMIC_FIELDS = (
    "horizon", "entry_ts", "venue", "symbol", "market_type", "direction",
    "entry_price_usd", "exit_price_usd", "gross_return_pct",
    "directional_return_pct", "net_return_pct", "estimated_fee_pct",
    "estimated_spread_slippage_pct", "estimated_funding_pct", "mfe_pct",
    "mae_pct", "cost_model_version",
)
_MAX_CONFLICT_ROWS = 20

# Outcome-resolution reasons that mean "the outcome is still developing" — the
# next cycle re-evaluates once real samples (or terminal signal) land.
_TRANSIENT_EXCLUSION_REASONS = frozenset({
    "unsettled",
    "missing_exact_outcome",
    "wrong_horizon",
})

# Identity/allocation-resolution failures that mean we cannot determine WHICH
# outcome (or WHETHER it was allocated) an exposure maps to — as opposed to a
# genuine economic value conflict between two real settled outcomes for one
# identity, which stays ``invalid`` and refreezes (spec LE-B28:
# ``conflicting_mirrored_outcome`` / ``ambiguous_duplicate_outcome``).
#
# These fail closed as benign EXCLUSIONS rather than corrupt INVALIDs:
#   - conflicting_allocation_result: filter ``allocation_selected`` disagrees
#     with an authoritative universe_forward_outcomes mirror. That flag is
#     unreliable here — it is largely a ``_backfill_fco_allocation_selected``
#     artifact (NULL→0 for rows whose True-write was blocked by an earlier
#     INSERT OR IGNORE). Per issue #324 we must NOT default the unknown
#     allocation to True (that would silently rewrite ITT economics), and per
#     issue #323 missing/ambiguous identity must fail closed — so we exclude the
#     one ambiguous sample instead of invalidating the whole cohort.
#   - missing_allocation_result: allocation is unknown/pending. Issue #324's
#     acceptance is explicit — "a pending outcome remains unsettled, not
#     invalid:missing_allocation_result".
#
# Crucial invariant this preserves: one ambiguous-identity sample must not
# invalidate and refreeze an entire cohort of otherwise-clean samples (the
# blast-radius that produced the mass ``invalid_measurement_refreeze`` cascade).
# The excluded sample never contributes to the measured means either way; only
# the cohort-level verdict changes from destructive ``invalid`` to honest
# ``insufficient_data`` (or a real verdict from the surviving clean samples).
# (issues #160, #323, #324)
_IDENTITY_EXCLUSION_REASONS = frozenset({
    "conflicting_allocation_result",
    "missing_allocation_result",
})


@dataclass(frozen=True, order=True)
class CohortKey:
    experiment_id: str
    hypothesis_id: str
    policy_epoch: str
    horizon: str
    outcome_contract_version: str
    behavior_contract_hash: str


@dataclass(frozen=True)
class CohortSample:
    assignment_unit_key: str
    candidate_key: str
    outcome_candidate_key: str
    outcome_source: str
    member_outcome_candidate_keys: tuple[str, ...]
    member_outcome_sources: tuple[str, ...]
    member_count: int
    selected_member_count: int
    allocation_selected: bool
    allocation_selection_source: str
    member_allocation_selection_sources: tuple[str, ...]
    assignment: str
    net_return_pct: float
    mae_pct: float | None
    entry_ts: str
    exposed_at: str
    authority_applied: bool
    authority_reason: str | None
    behavior_changed: bool
    behavior_change_reason: str


@dataclass
class ContributionCohort:
    key: CohortKey
    experiment: LearningExperiment
    hypothesis: LearningHypothesis
    samples: list[CohortSample] = field(default_factory=list)
    raw_exposure_n: int = 0
    duplicate_exposure_n: int = 0
    duplicate_outcome_n: int = 0
    mirrored_universe_row_n: int = 0
    evidence_conflict_fingerprints: set[str] = field(default_factory=set)
    evidence_conflicts: dict[str, dict[str, Any]] = field(default_factory=dict)
    exclusion_reasons: Counter[str] = field(default_factory=Counter)
    invalid_reasons: Counter[str] = field(default_factory=Counter)
    source_code_commits: set[str] = field(default_factory=set)
    source_code_commit_unknown_n: int = 0

    @property
    def treatment_samples(self) -> list[CohortSample]:
        return [sample for sample in self.samples if sample.assignment == "treatment"]

    @property
    def authority_applied_treatment_samples(self) -> list[CohortSample]:
        return [sample for sample in self.treatment_samples if sample.authority_applied]

    @property
    def actual_treatment_samples(self) -> list[CohortSample]:
        return [
            sample for sample in self.authority_applied_treatment_samples
            if sample.behavior_changed
        ]

    @property
    def baseline_treatment_samples(self) -> list[CohortSample]:
        return [
            sample for sample in self.treatment_samples
            if not (sample.authority_applied and sample.behavior_changed)
        ]

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
            "behavior_contract_hash": self.key.behavior_contract_hash or None,
            "source_code_commits": sorted(self.source_code_commits),
            "source_code_commit_unknown_n": self.source_code_commit_unknown_n,
            "code_commit_provenance_complete": (
                self.source_code_commit_unknown_n == 0
            ),
            "raw_exposure_n": self.raw_exposure_n,
            "unique_treatment_n": len(self.treatment_samples),
            "assigned_treatment_n": len(self.treatment_samples),
            "actual_intervention_treatment_n": len(self.actual_treatment_samples),
            "baseline_treatment_n": len(self.baseline_treatment_samples),
            "unique_control_n": len(self.control_samples),
            "duplicate_exposure_n": self.duplicate_exposure_n,
            "duplicate_outcome_n": self.duplicate_outcome_n,
            "mirrored_universe_row_n": self.mirrored_universe_row_n,
            "evidence_conflict_fingerprints": sorted(self.evidence_conflict_fingerprints),
            "evidence_conflicts": [
                self.evidence_conflicts[fingerprint]
                for fingerprint in sorted(self.evidence_conflicts)[:20]
            ],
            "evidence_conflict_total_n": len(self.evidence_conflicts),
            "evidence_conflict_provenance_truncated": len(self.evidence_conflicts) > 20,
            "authority_applied_treatment_n": len(
                self.authority_applied_treatment_samples
            ),
            "treatment_authority_rejection_reasons": dict(sorted(Counter(
                sample.authority_reason or "authority_reason_unavailable"
                for sample in self.treatment_samples
                if not sample.authority_applied
            ).items())),
            "treatment_non_intervention_reasons": dict(sorted(Counter(
                _non_intervention_reason(sample)
                for sample in self.baseline_treatment_samples
            ).items())),
            "exclusion_reasons": dict(sorted(self.exclusion_reasons.items())),
            "invalid_reasons": dict(sorted(self.invalid_reasons.items())),
            "outcome_candidate_keys": sorted({
                outcome_key
                for sample in self.samples
                for outcome_key in sample.member_outcome_candidate_keys
            }),
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
    epoch_ids = sorted({
        str(row.get("concurrent_policy_epoch") or "") for row in rows
        if row.get("concurrent_policy_epoch")
    })
    valid_policy_epochs: set[str] = set()
    if epoch_ids:
        placeholders = ", ".join("?" for _ in epoch_ids)
        valid_policy_epochs = {
            str(row["epoch_id"])
            for row in get_db().execute(
                "SELECT epoch_id FROM learning_policy_epochs "
                f"WHERE epoch_id IN ({placeholders}) AND epoch_kind=?",
                (*epoch_ids, f"decision_policy:{hypothesis.path_id}"),
            ).fetchall()
        }
    outcome_resolutions = _load_exact_outcomes(
        {str(row.get("outcome_candidate_key") or "") for row in rows},
        str(hypothesis.target_horizon or ""),
        path_id=hypothesis.path_id,
    )
    cohorts: dict[CohortKey, ContributionCohort] = {}
    candidates: dict[CohortKey, dict[str, dict[str, list[tuple[dict[str, Any], dict[str, Any]]]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    seen_duplicate_outcomes: set[tuple[CohortKey, str]] = set()
    seen_mirrored_outcomes: set[tuple[CohortKey, str]] = set()
    seen_wrong_horizon_outcomes: set[tuple[CohortKey, str]] = set()
    expected_unit_members: dict[CohortKey, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    unit_assignments: dict[CohortKey, dict[str, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )

    for exposure in rows:
        key = CohortKey(
            experiment.experiment_id,
            hypothesis.hypothesis_id,
            str(exposure.get("concurrent_policy_epoch") or ""),
            str(hypothesis.target_horizon or ""),
            str(exposure.get("outcome_contract_version") or ""),
            str(exposure.get("behavior_contract_hash") or ""),
        )
        cohort = cohorts.setdefault(key, ContributionCohort(key, experiment, hypothesis))
        cohort.raw_exposure_n += 1
        source_commit = str(exposure.get("code_commit") or "")
        if source_commit:
            cohort.source_code_commits.add(source_commit)
        else:
            cohort.source_code_commit_unknown_n += 1
        exposure_stage = exposure.get("experiment_stage")
        if exposure_stage is not None and str(exposure_stage) != experiment.stage:
            cohort.invalid_reasons["experiment_stage_mismatch"] += 1
            continue
        if not key.policy_epoch:
            cohort.invalid_reasons["missing_policy_epoch"] += 1
            continue
        if key.policy_epoch not in valid_policy_epochs:
            cohort.invalid_reasons["policy_epoch_path_mismatch"] += 1
            continue
        if not key.horizon:
            cohort.invalid_reasons["missing_target_horizon"] += 1
            continue
        if not key.outcome_contract_version:
            cohort.invalid_reasons["missing_outcome_contract_version"] += 1
            continue
        if not key.behavior_contract_hash:
            cohort.invalid_reasons["missing_behavior_contract_hash"] += 1
            continue
        assignment = str(exposure.get("assignment") or "").lower()
        if assignment not in {"treatment", "control"}:
            cohort.invalid_reasons["invalid_assignment"] += 1
            continue
        outcome_key = str(exposure.get("outcome_candidate_key") or "")
        if not outcome_key:
            # A keyless exposure carries no identity to measure. Fail closed as a
            # benign exclusion (issue #323): the write side must persist the exact
            # candidate key, and one legacy/edge keyless exposure must not
            # invalidate and refreeze an entire cohort of otherwise-clean samples.
            cohort.exclusion_reasons["missing_outcome_candidate_key"] += 1
            continue
        assignment_unit_key = str(exposure.get("assignment_unit_key") or "")
        if not assignment_unit_key:
            # The historical assignment cluster was never stored and cannot be
            # reconstructed honestly from timestamps or neighboring rows.
            cohort.exclusion_reasons["legacy_missing_assignment_unit"] += 1
            continue
        expected_unit_members[key][assignment_unit_key].add(outcome_key)
        unit_assignments[key][assignment_unit_key].add(assignment)
        if str(exposure.get("outcome_source") or "") != "fixed_horizon_candidate":
            cohort.invalid_reasons["unsupported_outcome_source"] += 1
            continue
        exposed_at = _parse_time(exposure.get("exposed_at"))
        if exposed_at is None:
            cohort.invalid_reasons["missing_exposure_time"] += 1
            continue

        (
            outcome,
            outcome_reason,
            duplicate_outcomes,
            wrong_horizon_n,
            evidence_fingerprint,
            evidence_conflict,
        ) = outcome_resolutions.get(
            outcome_key,
            (None, "missing_exact_outcome", 0, 0, None, None),
        )
        if evidence_fingerprint:
            cohort.evidence_conflict_fingerprints.add(evidence_fingerprint)
            if evidence_conflict is not None:
                cohort.evidence_conflicts[evidence_fingerprint] = evidence_conflict
        if wrong_horizon_n and (key, outcome_key) not in seen_wrong_horizon_outcomes:
            cohort.exclusion_reasons["wrong_horizon"] += wrong_horizon_n
            seen_wrong_horizon_outcomes.add((key, outcome_key))
        if duplicate_outcomes and (key, outcome_key) not in seen_duplicate_outcomes:
            cohort.duplicate_outcome_n += duplicate_outcomes
            seen_duplicate_outcomes.add((key, outcome_key))
        if outcome_reason:
            if outcome_reason in _TRANSIENT_EXCLUSION_REASONS:
                if outcome_reason != "wrong_horizon" or not wrong_horizon_n:
                    cohort.exclusion_reasons[outcome_reason] += 1
            elif outcome_reason in _IDENTITY_EXCLUSION_REASONS:
                # Missing/ambiguous allocation identity → fail closed as a benign
                # exclusion, not a cohort-invalidating corruption. (issues #323/#324)
                cohort.exclusion_reasons[outcome_reason] += 1
            else:
                cohort.invalid_reasons[outcome_reason] += 1
            continue
        assert outcome is not None
        mirrored_rows = int(outcome.get("_mirrored_universe_row_n") or 0)
        if mirrored_rows and (key, outcome_key) not in seen_mirrored_outcomes:
            cohort.mirrored_universe_row_n += mirrored_rows
            seen_mirrored_outcomes.add((key, outcome_key))
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
        if (
            bool(outcome.get("_allocation_selected"))
            and _finite_float(outcome.get("mae_pct")) is None
        ):
            cohort.invalid_reasons["selected_mae_unavailable"] += 1
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
                if len({_exposure_fingerprint(row[0]) for row in rows_for_assignment}) > 1:
                    cohort.invalid_reasons["conflicting_duplicate_exposure"] += 1
                    continue
            exposure, outcome = rows_for_assignment[0]
            net = _finite_float(outcome.get("net_return_pct"))
            assert net is not None
            authority_applied, authority_reason = _manifest_authority(
                exposure.get("manifest_json")
            )
            behavior_changed, behavior_change_reason = _exposure_behavior_change(
                exposure,
                assignment=assignment,
                authority_applied=authority_applied,
            )
            if behavior_changed is None or behavior_change_reason is None:
                cohort.invalid_reasons["invalid_behavior_change_provenance"] += 1
                continue
            cohort.samples.append(CohortSample(
                assignment_unit_key=str(exposure["assignment_unit_key"]),
                candidate_key=str(exposure.get("candidate_key") or outcome_key),
                outcome_candidate_key=outcome_key,
                outcome_source=str(outcome.get("_learning_outcome_source") or "unknown"),
                member_outcome_candidate_keys=(outcome_key,),
                member_outcome_sources=(
                    str(outcome.get("_learning_outcome_source") or "unknown"),
                ),
                member_count=1,
                selected_member_count=int(bool(outcome.get("_allocation_selected"))),
                allocation_selected=bool(outcome.get("_allocation_selected")),
                allocation_selection_source=str(
                    outcome.get("_allocation_selection_source") or "unknown"
                ),
                member_allocation_selection_sources=(
                    str(outcome.get("_allocation_selection_source") or "unknown"),
                ),
                assignment=assignment,
                net_return_pct=net,
                mae_pct=_finite_float(outcome.get("mae_pct")),
                entry_ts=str(outcome["entry_ts"]),
                exposed_at=str(exposure["exposed_at"]),
                authority_applied=authority_applied,
                authority_reason=authority_reason,
                behavior_changed=behavior_changed,
                behavior_change_reason=behavior_change_reason,
            ))
    for cohort in cohorts.values():
        cohort.samples = _aggregate_assignment_units(
            cohort,
            expected_unit_members[cohort.key],
            unit_assignments[cohort.key],
        )
        cohort.samples.sort(key=lambda sample: (sample.assignment, sample.assignment_unit_key))
    return [cohorts[key] for key in sorted(cohorts)]


def _manifest_authority(manifest_json: Any) -> tuple[bool, str | None]:
    """Read explicit authority provenance; missing/malformed values fail closed."""
    try:
        manifest = json.loads(manifest_json or "{}")
    except (TypeError, ValueError):
        return False, None
    if not isinstance(manifest, dict):
        return False, None
    reason = manifest.get("authority_reason")
    return (
        manifest.get("authority_applied") is True,
        reason.strip() if isinstance(reason, str) and reason.strip() else None,
    )


def _exposure_behavior_change(
    exposure: dict[str, Any],
    *,
    assignment: str,
    authority_applied: bool,
) -> tuple[bool | None, str | None]:
    """Derive effective numeric behavior from durable before/after provenance."""
    try:
        before = json.loads(exposure.get("before_json") or "{}")
        after = json.loads(exposure.get("after_json") or "{}")
        manifest = json.loads(exposure.get("manifest_json") or "{}")
    except (TypeError, ValueError):
        return None, None
    if not all(isinstance(value, dict) for value in (before, after, manifest)):
        return None, None
    before_value = _finite_float(before.get("value"))
    after_value = _finite_float(after.get("value"))
    if before_value is None or after_value is None:
        return None, None
    delta = after_value - before_value
    if not math.isfinite(delta):
        return None, None
    changed = delta != 0.0
    if authority_applied and assignment != "treatment":
        return None, None
    if changed and not (authority_applied and assignment == "treatment"):
        return None, None
    declared = manifest.get("behavior_changed")
    if declared is not None and (
        not isinstance(declared, bool) or declared is not changed
    ):
        return None, None
    expected_reason = (
        "effective_treatment_delta"
        if changed
        else "no_effective_treatment_delta"
        if authority_applied
        else "control_assignment"
        if assignment == "control"
        else "authority_not_applied"
    )
    declared_reason = manifest.get("behavior_change_reason")
    if declared_reason is not None and (
        not isinstance(declared_reason, str)
        or declared_reason.strip() != expected_reason
    ):
        return None, None
    return changed, expected_reason


def _non_intervention_reason(sample: CohortSample) -> str:
    if not sample.authority_applied:
        return f"authority:{sample.authority_reason or 'authority_reason_unavailable'}"
    return sample.behavior_change_reason or "behavior_change_reason_unavailable"


def _load_exact_outcomes(
    outcome_candidate_keys: set[str],
    horizon: str,
    *,
    path_id: str | None = None,
) -> dict[
    str,
    tuple[
        dict[str, Any] | None,
        str | None,
        int,
        int,
        str | None,
        dict[str, Any] | None,
    ],
]:
    """Resolve all requested outcomes with two bounded, exact-key query sets."""
    requested = {key for key in outcome_candidate_keys if key}
    filter_keys = {_filter_base_key(key, horizon) for key in requested}
    filter_rows = _rows_for_keys("filter_candidate_outcomes", filter_keys)
    universe_rows = _rows_for_keys("universe_forward_outcomes", requested)
    filters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    universe: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in filter_rows:
        filters[str(row.get("outcome_candidate_key") or "")].append(row)
    for row in universe_rows:
        universe[str(row.get("outcome_candidate_key") or "")].append(row)

    resolved = {}
    for outcome_key in requested:
        filter_all = filters.get(_filter_base_key(outcome_key, horizon), [])
        universe_all = universe.get(outcome_key, [])
        filter_matches = [row for row in filter_all if str(row.get("horizon") or "") == horizon]
        universe_matches = [row for row in universe_all if str(row.get("horizon") or "") == horizon]
        wrong_horizon_n = (
            (len(filter_all) if filter_all and not filter_matches else 0)
            + len(universe_all) - len(universe_matches)
        )

        if filter_matches:
            distinct = {_outcome_fingerprint(row) for row in filter_matches}
            if len(distinct) > 1:
                fingerprint = _conflict_fingerprint(
                    "ambiguous_duplicate_outcome", filter_matches
                )
                resolved[outcome_key] = (
                    None, "ambiguous_duplicate_outcome", 0, wrong_horizon_n,
                    fingerprint,
                    _conflict_provenance(
                        "ambiguous_duplicate_outcome",
                        outcome_key,
                        [("filter_candidate_outcomes", row) for row in filter_matches],
                    ),
                )
                continue
            canonical = dict(filter_matches[0])
            allocation_selected = canonical.get("allocation_selected")
            allocation_selection_source = "filter_candidate_outcomes"
            if allocation_selected is None and universe_matches:
                allocation_selected = True
                allocation_selection_source = "universe_forward_outcomes_mirror_inference"
            if allocation_selected is None:
                resolved[outcome_key] = (
                    None, "missing_allocation_result", 0, wrong_horizon_n, None, None,
                )
                continue
            settled_mirrors = [
                row for row in universe_matches
                if str(row.get("status") or "").lower() == "settled"
            ]
            if (
                str(canonical.get("status") or "").lower() == "settled"
                and any(
                    _economic_fingerprint(row) != _economic_fingerprint(canonical)
                    for row in settled_mirrors
                )
            ):
                conflict_rows = [
                    ("filter_candidate_outcomes", canonical),
                    *(
                        ("universe_forward_outcomes", row)
                        for row in settled_mirrors
                    ),
                ]
                fingerprint = _conflict_fingerprint(
                    "conflicting_mirrored_outcome", [row for _, row in conflict_rows]
                )
                resolved[outcome_key] = (
                    None, "conflicting_mirrored_outcome", 0, wrong_horizon_n,
                    fingerprint,
                    _conflict_provenance(
                        "conflicting_mirrored_outcome",
                        outcome_key,
                        conflict_rows,
                        canonical_index=0,
                    ),
                )
                continue
            if not bool(allocation_selected) and universe_matches:
                conflict_rows = [
                    ("filter_candidate_outcomes", canonical),
                    *(
                        ("universe_forward_outcomes", row)
                        for row in universe_matches
                    ),
                ]
                fingerprint = _conflict_fingerprint(
                    "conflicting_allocation_result", [row for _, row in conflict_rows]
                )
                resolved[outcome_key] = (
                    None, "conflicting_allocation_result", 0, wrong_horizon_n,
                    fingerprint,
                    _conflict_provenance(
                        "conflicting_allocation_result",
                        outcome_key,
                        conflict_rows,
                        canonical_index=0,
                    ),
                )
                continue
            resolved[outcome_key] = (
                {
                    **canonical,
                    "_learning_outcome_source": "filter_candidate_outcomes",
                    "_mirrored_universe_row_n": len(universe_matches),
                    "_allocation_selected": bool(allocation_selected),
                    "_allocation_selection_source": allocation_selection_source,
                },
                None,
                len(filter_matches) - 1,
                wrong_horizon_n,
                None,
                None,
            )
            continue

        if universe_matches:
            distinct = {_outcome_fingerprint(row) for row in universe_matches}
            if len(distinct) > 1:
                fingerprint = _conflict_fingerprint(
                    "ambiguous_duplicate_outcome", universe_matches
                )
                resolved[outcome_key] = (
                    None, "ambiguous_duplicate_outcome", 0, wrong_horizon_n,
                    fingerprint,
                    _conflict_provenance(
                        "ambiguous_duplicate_outcome",
                        outcome_key,
                        [("universe_forward_outcomes", row) for row in universe_matches],
                    ),
                )
                continue
            resolved[outcome_key] = (
                {
                    **universe_matches[0],
                    "_learning_outcome_source": "universe_forward_outcomes_legacy_fallback",
                    "_mirrored_universe_row_n": 0,
                    "_allocation_selected": True,
                    "_allocation_selection_source": "universe_forward_outcomes_legacy_fallback",
                },
                None,
                len(universe_matches) - 1,
                wrong_horizon_n,
                None,
                None,
            )
            continue

        all_rows = filter_all + universe_all
        resolved[outcome_key] = (
            None,
            "wrong_horizon" if all_rows else "missing_exact_outcome",
            0,
            wrong_horizon_n,
            None,
            None,
        )
    return resolved


def _rows_for_keys(table: str, keys: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    ordered = sorted(keys)
    for start in range(0, len(ordered), 400):
        chunk = ordered[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(dict(row) for row in get_db().execute(
            f"SELECT * FROM {table} WHERE outcome_candidate_key IN ({placeholders}) "
            "ORDER BY outcome_candidate_key, horizon, datetime(entry_ts), id",
            tuple(chunk),
        ).fetchall())
    return rows


def _filter_base_key(outcome_candidate_key: str, horizon: str) -> str:
    suffix = f":{horizon}"
    return outcome_candidate_key[:-len(suffix)] if outcome_candidate_key.endswith(suffix) else outcome_candidate_key


def _aggregate_assignment_units(
    cohort: ContributionCohort,
    expected_members: dict[str, set[str]],
    assignments: dict[str, set[str]],
) -> list[CohortSample]:
    members_by_unit: dict[str, list[CohortSample]] = defaultdict(list)
    for sample in cohort.samples:
        members_by_unit[sample.assignment_unit_key].append(sample)

    samples: list[CohortSample] = []
    for unit_key in sorted(expected_members):
        unit_assignments = assignments.get(unit_key, set())
        if len(unit_assignments) != 1:
            cohort.invalid_reasons["conflicting_assignment_unit"] += 1
            continue
        members = members_by_unit.get(unit_key, [])
        actual_members = {sample.outcome_candidate_key for sample in members}
        if actual_members != expected_members[unit_key]:
            cohort.exclusion_reasons["incomplete_assignment_unit"] += 1
            continue
        authority_values = {sample.authority_applied for sample in members}
        if len(authority_values) != 1:
            cohort.invalid_reasons["conflicting_authority_application"] += 1
            continue
        authority_reasons = {sample.authority_reason for sample in members}
        behavior_changed = any(sample.behavior_changed for sample in members)
        behavior_change_reasons = {sample.behavior_change_reason for sample in members}
        assignment = next(iter(unit_assignments))
        if any(sample.assignment != assignment for sample in members):
            cohort.invalid_reasons["conflicting_assignment_unit"] += 1
            continue
        # Guard the min() keys below from an unparseable timestamp silently
        # collapsing to datetime.min ("earliest") and biasing span reasoning.
        # Members already survived a _parse_time check in build_experiment_cohorts,
        # but stringified round-trips downstream can still drift — surface any
        # residual dirt explicitly instead of masking it (audit GAP-08 tail).
        if any(
            _parse_time(sample.entry_ts) is None or _parse_time(sample.exposed_at) is None
            for sample in members
        ):
            cohort.invalid_reasons["unparseable_assignment_unit_ts"] += 1
            continue
        selected_members = [sample for sample in members if sample.allocation_selected]
        member_keys = tuple(sorted(actual_members))
        member_sources = tuple(
            sample.outcome_source
            for sample in sorted(members, key=lambda item: item.outcome_candidate_key)
        )
        source_set = set(member_sources)
        selection_sources = tuple(
            sample.allocation_selection_source
            for sample in sorted(members, key=lambda item: item.outcome_candidate_key)
        )
        selection_source_set = set(selection_sources)
        samples.append(CohortSample(
            assignment_unit_key=unit_key,
            candidate_key=unit_key,
            outcome_candidate_key=(
                member_keys[0] if len(member_keys) == 1 else f"assignment-unit:{unit_key}"
            ),
            outcome_source=next(iter(source_set)) if len(source_set) == 1 else "mixed",
            member_outcome_candidate_keys=member_keys,
            member_outcome_sources=member_sources,
            member_count=len(member_keys),
            selected_member_count=len(selected_members),
            allocation_selected=bool(selected_members),
            allocation_selection_source=(
                next(iter(selection_source_set))
                if len(selection_source_set) == 1 else "mixed"
            ),
            member_allocation_selection_sources=selection_sources,
            assignment=assignment,
            net_return_pct=fmean(
                sample.net_return_pct if sample.allocation_selected else 0.0
                for sample in members
            ),
            mae_pct=min([
                0.0,
                *(sample.mae_pct for sample in selected_members if sample.mae_pct is not None),
            ]),
            entry_ts=min(
                members,
                key=lambda s: _parse_time(s.entry_ts) or datetime.min.replace(tzinfo=timezone.utc),
            ).entry_ts,
            exposed_at=min(
                members,
                key=lambda s: _parse_time(s.exposed_at) or datetime.min.replace(tzinfo=timezone.utc),
            ).exposed_at,
            authority_applied=next(iter(authority_values)),
            authority_reason=(
                next(iter(authority_reasons))
                if len(authority_reasons) == 1 else "mixed_authority_reasons"
            ),
            behavior_changed=behavior_changed,
            behavior_change_reason=(
                "effective_treatment_delta"
                if behavior_changed
                else next(iter(behavior_change_reasons))
                if len(behavior_change_reasons) == 1
                else "no_effective_treatment_delta"
            ),
        ))
    return samples


def _exposure_fingerprint(exposure: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(exposure.get(field) for field in (
        "experiment_id", "hypothesis_id", "assignment", "assignment_unit_key",
        "candidate_key", "outcome_candidate_key", "outcome_source",
        "outcome_contract_version", "experiment_stage", "concurrent_policy_epoch",
        "behavior_contract_hash", "before_json", "after_json", "manifest_json", "exposed_at",
    ))


def _exact_outcome(
    outcome_candidate_key: str,
    horizon: str,
) -> tuple[
    dict[str, Any] | None,
    str | None,
    int,
    int,
    str | None,
    dict[str, Any] | None,
]:
    """Compatibility wrapper around the batched exact resolver."""
    return _load_exact_outcomes({outcome_candidate_key}, horizon)[outcome_candidate_key]


def _outcome_fingerprint(outcome: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(outcome.get(field) for field in (
        "outcome_candidate_key", "horizon", "status", "entry_ts", "settled_at",
        "net_return_pct", "estimated_fee_pct", "estimated_spread_slippage_pct",
        "estimated_funding_pct", "mae_pct", "cost_model_version", "market_type", "direction",
        "allocation_selected",
    ))


def _economic_fingerprint(outcome: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(outcome.get(field) for field in _ECONOMIC_FIELDS)


def _conflict_fingerprint(reason: str, rows: list[dict[str, Any]]) -> str:
    payload = {
        "reason": reason,
        "rows": [dict(sorted(row.items())) for row in rows],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _conflict_provenance(
    reason: str,
    outcome_candidate_key: str,
    rows: list[tuple[str, dict[str, Any]]],
    *,
    canonical_index: int | None = None,
) -> dict[str, Any]:
    """Return bounded, secret-free row provenance and exact differing values."""
    total_row_n = len(rows)
    bounded_rows = rows[:_MAX_CONFLICT_ROWS]
    summaries = [_conflict_row(source, row) for source, row in bounded_rows]
    result: dict[str, Any] = {
        "reason": reason,
        "outcome_candidate_key": outcome_candidate_key,
        "row_count": total_row_n,
        "row_provenance_truncated": total_row_n > len(bounded_rows),
    }
    if canonical_index is None:
        result["rows"] = summaries
        result["differing_fields"] = {
            field: sorted(
                {row.get(field) for _, row in bounded_rows},
                key=lambda value: (type(value).__name__, str(value)),
            )
            for field in _ECONOMIC_FIELDS
            if len({row.get(field) for _, row in bounded_rows}) > 1
        }
        return result

    canonical_source, canonical = rows[canonical_index]
    mirrors = [
        row
        for index, row in enumerate(rows)
        if index != canonical_index
    ]
    canonical_economics = _economic_fingerprint(canonical)
    mirrors.sort(
        key=lambda sourced_row: (
            _economic_fingerprint(sourced_row[1]) == canonical_economics,
        )
    )
    mirrors = mirrors[:_MAX_CONFLICT_ROWS - 1]
    result["canonical"] = _conflict_row(canonical_source, canonical)
    result["mirrors"] = [_conflict_row(source, row) for source, row in mirrors]
    result["differing_fields"] = {
        field: {
            "canonical": canonical.get(field),
            "mirrors": [row.get(field) for _, row in mirrors],
        }
        for field in _ECONOMIC_FIELDS
        if any(row.get(field) != canonical.get(field) for _, row in mirrors)
    }
    return result


def _conflict_row(source: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "row_id": row.get("id"),
        "status": row.get("status"),
        "entry_ts": row.get("entry_ts"),
        "settled_at": row.get("settled_at"),
        "settlement_price_source": row.get("settlement_price_source"),
        "cost_model_version": row.get("cost_model_version"),
    }


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

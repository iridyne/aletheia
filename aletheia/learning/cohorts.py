"""Build exact, prospective contribution cohorts for supported Learning paths.

The first runtime slice deliberately supports one path only.  It never searches
by symbol or time proximity: an exposure can contribute only through the exact
``outcome_candidate_key`` it recorded before the outcome entered its horizon.
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
from aletheia.storage.sqlite import get_db


SUPPORTED_PATH_ID = "universe_family_calibration"
# Consumer wiring is not causal measurement support. A path enters this set only
# after its exact outcome and statistical-unit contracts are executable.
#
# Outcome contract families:
#   A — universe_forward_outcomes (universe-keyed, {learning_key}:{horizon}):
#       universe_family_calibration, observer_advisory_bundle, ranker_factor_filter
#   B — filter_candidate_outcomes + universe_forward_outcomes mirror (family-keyed,
#       resolved via _filter_base_key strip of :{horizon}):
#       operator_learning_bundle, ranker_case_score, ranker_memo_score
#
# observer_advisory_bundle: candidate_key = observer-run key; outcome_candidate_key
#   = u_member.learning_candidate_key:4h — resolves via universe_forward_outcomes.
# operator_learning_bundle: uses the fixed_horizon_candidate outcome contract
#   (opportunity:{id}:{horizon}) and the boolean-treatment exposure path (same as
#   universe_family_calibration), so the existing cohort builder handles it without
#   modification. Known measurement bias: serenity_opportunities appears in both
#   arms (GAP-02, Phase 4); this produces a conservative underestimate of the
#   bundle effect rather than a false positive.
# ranker_case_score/memo_score: outcome_candidate_key = {learning_candidate_key}:4h
#   (family-anchored, only truthy after opportunity metadata carries the key — see
#   market_observer._analyze_symbol) — resolves via filter_candidate_outcomes (with
#   allocation_selected populated by universe/service._register_experiment_candidate_outcomes)
#   or via universe_forward_outcomes fallback.
SUPPORTED_PATH_IDS = frozenset({
    SUPPORTED_PATH_ID,
    "operator_learning_bundle",
    "observer_advisory_bundle",
    "ranker_case_score",
    "ranker_memo_score",
    "ranker_factor_filter",
})


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
            "mirrored_universe_row_n": self.mirrored_universe_row_n,
            "evidence_conflict_fingerprints": sorted(self.evidence_conflict_fingerprints),
            "authority_applied_treatment_n": sum(
                sample.authority_applied for sample in self.treatment_samples
            ),
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
    outcome_resolutions = _load_exact_outcomes(
        {str(row.get("outcome_candidate_key") or "") for row in rows},
        str(hypothesis.target_horizon or ""),
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

        outcome, outcome_reason, duplicate_outcomes, wrong_horizon_n, evidence_fingerprint = outcome_resolutions.get(
            outcome_key,
            (None, "missing_exact_outcome", 0, 0, None),
        )
        if evidence_fingerprint:
            cohort.evidence_conflict_fingerprints.add(evidence_fingerprint)
        if wrong_horizon_n and (key, outcome_key) not in seen_wrong_horizon_outcomes:
            cohort.exclusion_reasons["wrong_horizon"] += wrong_horizon_n
            seen_wrong_horizon_outcomes.add((key, outcome_key))
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
                authority_applied=_manifest_authority_applied(exposure.get("manifest_json")),
            ))
    for cohort in cohorts.values():
        cohort.samples = _aggregate_assignment_units(
            cohort,
            expected_unit_members[cohort.key],
            unit_assignments[cohort.key],
        )
        cohort.samples.sort(key=lambda sample: (sample.assignment, sample.assignment_unit_key))
    return [cohorts[key] for key in sorted(cohorts)]


def _manifest_authority_applied(manifest_json: Any) -> bool:
    """Read only an explicit JSON boolean; missing/malformed manifests fail closed."""
    try:
        manifest = json.loads(manifest_json or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("authority_applied") is True


def _load_exact_outcomes(
    outcome_candidate_keys: set[str],
    horizon: str,
) -> dict[str, tuple[dict[str, Any] | None, str | None, int, int, str | None]]:
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
                resolved[outcome_key] = (
                    None, "ambiguous_duplicate_outcome", 0, wrong_horizon_n,
                    _conflict_fingerprint("ambiguous_duplicate_outcome", filter_matches),
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
                    None, "missing_allocation_result", 0, wrong_horizon_n, None,
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
                resolved[outcome_key] = (
                    None, "conflicting_mirrored_outcome", 0, wrong_horizon_n,
                    _conflict_fingerprint(
                        "conflicting_mirrored_outcome", [canonical, *settled_mirrors]
                    ),
                )
                continue
            if not bool(allocation_selected) and universe_matches:
                resolved[outcome_key] = (
                    None, "conflicting_allocation_result", 0, wrong_horizon_n,
                    _conflict_fingerprint(
                        "conflicting_allocation_result", [canonical, *universe_matches]
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
            )
            continue

        if universe_matches:
            distinct = {_outcome_fingerprint(row) for row in universe_matches}
            if len(distinct) > 1:
                resolved[outcome_key] = (
                    None, "ambiguous_duplicate_outcome", 0, wrong_horizon_n,
                    _conflict_fingerprint("ambiguous_duplicate_outcome", universe_matches),
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
            )
            continue

        all_rows = filter_all + universe_all
        resolved[outcome_key] = (
            None,
            "wrong_horizon" if all_rows else "missing_exact_outcome",
            0,
            wrong_horizon_n,
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
        assignment = next(iter(unit_assignments))
        if any(sample.assignment != assignment for sample in members):
            cohort.invalid_reasons["conflicting_assignment_unit"] += 1
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
        ))
    return samples


def _exposure_fingerprint(exposure: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(exposure.get(field) for field in (
        "experiment_id", "hypothesis_id", "assignment", "assignment_unit_key",
        "candidate_key", "outcome_candidate_key", "outcome_source",
        "outcome_contract_version", "experiment_stage", "concurrent_policy_epoch",
        "code_commit", "before_json", "after_json", "manifest_json", "exposed_at",
    ))


def _exact_outcome(
    outcome_candidate_key: str,
    horizon: str,
) -> tuple[dict[str, Any] | None, str | None, int, int, str | None]:
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
    return tuple(outcome.get(field) for field in (
        "horizon", "entry_ts", "venue", "symbol", "market_type", "direction",
        "entry_price_usd", "exit_price_usd", "gross_return_pct",
        "directional_return_pct", "net_return_pct", "estimated_fee_pct",
        "estimated_spread_slippage_pct", "estimated_funding_pct", "mfe_pct",
        "mae_pct", "settlement_price_source", "cost_model_version",
    ))


def _conflict_fingerprint(reason: str, rows: list[dict[str, Any]]) -> str:
    payload = {
        "reason": reason,
        "rows": [dict(sorted(row.items())) for row in rows],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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

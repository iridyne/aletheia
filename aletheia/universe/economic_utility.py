"""Transparent 4h cost-after Candidate utility on independent T0 clusters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
from numbers import Real
from statistics import mean, stdev
from typing import Literal, Mapping, Sequence

from aletheia.analytics.temporal import parse_utc_instant
from aletheia.universe.candidate_eligibility import canonical_fingerprint
from aletheia.universe.opportunity_evidence import OPPORTUNITY_EVIDENCE_VERSION
from aletheia.universe.outcome_economics import COST_MODEL_VERSION


ECONOMIC_UTILITY_VERSION = "universe-economic-utility/v2"
ECONOMIC_ESTIMATOR_VERSION = "candidate-economic-utility-estimator/v3"
CANDIDATE_SELECTION_OUTCOME_VERSION = "candidate-selection-utility-outcome/v1"
INDEPENDENT_UNIT_VERSION = "scanner-t0-cross-section-nonoverlap-4h/v1"
INDEPENDENT_UNIT_INTERVAL = timedelta(hours=4)
_SHRINKAGE_K = 50
_TAIL_HARM_FLOOR_PCT = -15.0


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _cost_assumptions(value: Mapping[str, object] | None) -> dict[str, float] | None:
    try:
        result = {
            key: float((value or {})[key]) for key in ("execution_fee_pct", "slippage_bps", "fallback_spread_bps")
        }
    except (KeyError, TypeError, ValueError):
        return None
    return result if all(math.isfinite(item) for item in result.values()) else None


def select_non_overlapping_t0_clusters(
    clusters: Sequence[Mapping[str, object]],
    *,
    t0_field: str,
) -> list[Mapping[str, object]]:
    """Select the deterministic earliest prefix of non-overlapping 4h exposures."""

    ordered = sorted(
        (
            (t0, str(cluster.get("scanner_run_id") or ""), cluster)
            for cluster in clusters
            if (t0 := parse_utc_instant(cluster.get(t0_field))) is not None
        ),
        key=lambda item: (item[0], item[1]),
    )
    selected: list[Mapping[str, object]] = []
    last_t0: datetime | None = None
    for t0, _run_id, cluster in ordered:
        if last_t0 is None or t0 >= last_t0 + INDEPENDENT_UNIT_INTERVAL:
            selected.append(cluster)
            last_t0 = t0
    return selected


def _independent_values(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[float], list[float], list[Mapping[str, object]]]:
    """Collapse same-T0 symbols, then greedily select non-overlapping 4h units."""

    grouped: dict[str, list[Mapping[str, object]]] = {}
    for row in rows:
        entry = parse_utc_instant(row.get("entry_ts"))
        if entry is None:
            continue
        grouped.setdefault(entry.astimezone(timezone.utc).isoformat(), []).append(row)
    clusters = select_non_overlapping_t0_clusters(
        [
            {
                "cluster_t0": cluster_t0,
                "scanner_run_id": min(str(row.get("scanner_run_id") or "") for row in values),
                "net_return_pct": mean(float(row["net_return_pct"]) for row in values),
                "mae_pct": (
                    max(abs(float(row["mae_pct"])) for row in values)
                    if all(_finite(row.get("mae_pct")) is not None for row in values)
                    else None
                ),
                "symbols": frozenset(
                    str(row["symbol"]) for row in values if row.get("symbol")
                ),
            }
            for cluster_t0, values in grouped.items()
        ],
        t0_field="cluster_t0",
    )
    net_values = [float(cluster["net_return_pct"]) for cluster in clusters]
    mae_values = [
        float(cluster["mae_pct"])
        for cluster in clusters
        if cluster["mae_pct"] is not None
    ]
    return net_values, mae_values, clusters


@dataclass(frozen=True)
class _CohortSummary:
    parent_values: tuple[float, ...]
    child_values: Mapping[int, tuple[float, ...]]
    child_mae_values: Mapping[int, tuple[float, ...]]
    child_raw_min: Mapping[int, float | None]
    dates: frozenset[object]
    symbols: frozenset[str]
    coverage: float
    minimum_edge: float | None
    source_outcome_fingerprint: str | None
    identity_invalid: bool


@dataclass(frozen=True)
class EconomicUtilityCohortIndex:
    """One frozen O(history) index reused by every Candidate lookup in a refresh."""

    source_cutoff_at: str
    cost_assumptions: Mapping[str, float] | None
    cohorts: Mapping[tuple[str, str, str, str], _CohortSummary]


def build_economic_utility_cohort_index(
    outcomes: Sequence[Mapping[str, object]],
    *,
    source_cutoff_at: str,
    cost_assumptions: Mapping[str, object] | None = None,
) -> EconomicUtilityCohortIndex:
    cutoff = parse_utc_instant(source_cutoff_at)
    if cutoff is None:
        raise ValueError("source_cutoff_at_invalid")
    expected_costs = _cost_assumptions(cost_assumptions)
    by_cohort: dict[tuple[str, str, str, str], dict[str, Mapping[str, object]]] = {}
    invalid: set[tuple[str, str, str, str]] = set()
    comparable = (
        "scanner_run_id",
        "status",
        "entry_ts",
        "settled_at",
        "symbol",
        "net_return_pct",
        "mae_pct",
        "cost_model_version",
        "min_tradable_edge_pct",
        "strength_quintile",
        "mechanism_behavior_version",
        "outcome_contract_version",
        "cost_assumptions",
        "estimated_fee_pct",
        "estimated_spread_slippage_pct",
        "estimated_funding_pct",
    )
    for row in outcomes:
        entry = parse_utc_instant(row.get("entry_ts"))
        settled = parse_utc_instant(row.get("settled_at"))
        if (
            row.get("horizon") != "4h"
            or row.get("mechanism_behavior_version") != OPPORTUNITY_EVIDENCE_VERSION
            or row.get("outcome_contract_version") != CANDIDATE_SELECTION_OUTCOME_VERSION
            or row.get("cost_assumptions") != expected_costs
            or entry is None
            or entry > cutoff
            or (row.get("status") == "settled" and (settled is None or settled > cutoff))
        ):
            continue
        key = (
            str(row.get("mechanism_id") or ""),
            str(row.get("direction") or ""),
            str(row.get("venue") or ""),
            str(row.get("market_type") or "spot"),
        )
        identity = str(row.get("outcome_candidate_key") or "")
        if not identity:
            invalid.add(key)
            continue
        previous = by_cohort.setdefault(key, {}).get(identity)
        if previous is not None and any(previous.get(field) != row.get(field) for field in comparable):
            invalid.add(key)
            by_cohort[key][identity] = min(
                (previous, row),
                key=lambda item: canonical_fingerprint({
                    field: item.get(field) for field in comparable
                }),
            )
            continue
        by_cohort[key].setdefault(identity, row)

    summaries: dict[tuple[str, str, str, str], _CohortSummary] = {}
    for key, identified in by_cohort.items():
        parent_rows = list(identified.values())
        complete = [
            row
            for row in parent_rows
            if row.get("status") == "settled"
            and _finite(row.get("net_return_pct")) is not None
            and row.get("cost_model_version") == COST_MODEL_VERSION
            and row.get("scanner_run_id")
        ]
        parent_values, _, parent_clusters = _independent_values(complete)
        child_values: dict[int, tuple[float, ...]] = {}
        child_mae: dict[int, tuple[float, ...]] = {}
        child_raw_min: dict[int, float | None] = {}
        for quintile in range(1, 6):
            rows = [row for row in complete if row.get("strength_quintile") == quintile]
            values, maes, _ = _independent_values(rows)
            if any(_finite(row.get("mae_pct")) is None for row in rows):
                maes = []
            child_values[quintile] = tuple(values)
            child_mae[quintile] = tuple(maes)
            child_raw_min[quintile] = min(
                (float(row["net_return_pct"]) for row in rows),
                default=None,
            )
        edges = {_finite(row.get("min_tradable_edge_pct")) for row in complete}
        edges.discard(None)
        source_material = sorted(
            (
                {
                    "outcome_candidate_key": row.get("outcome_candidate_key"),
                    "scanner_run_id": row.get("scanner_run_id"),
                    "entry_ts": row.get("entry_ts"),
                    "symbol": row.get("symbol"),
                    "net_return_pct": row.get("net_return_pct"),
                    "mae_pct": row.get("mae_pct"),
                    "estimated_fee_pct": row.get("estimated_fee_pct"),
                    "estimated_spread_slippage_pct": row.get("estimated_spread_slippage_pct"),
                    "estimated_funding_pct": row.get("estimated_funding_pct"),
                    "min_tradable_edge_pct": row.get("min_tradable_edge_pct"),
                    "cost_model_version": row.get("cost_model_version"),
                    "mechanism_behavior_version": row.get("mechanism_behavior_version"),
                    "outcome_contract_version": row.get("outcome_contract_version"),
                    "cost_assumptions": row.get("cost_assumptions"),
                    "settled_at": row.get("settled_at"),
                }
                for row in complete
            ),
            key=lambda row: (str(row["outcome_candidate_key"]), str(row["scanner_run_id"])),
        )
        summaries[key] = _CohortSummary(
            tuple(parent_values),
            child_values,
            child_mae,
            child_raw_min,
            frozenset(
                entry.date()
                for cluster in parent_clusters
                if (entry := parse_utc_instant(cluster.get("cluster_t0"))) is not None
            ),
            frozenset(
                symbol
                for cluster in parent_clusters
                for symbol in cluster.get("symbols", ())
            ),
            len(complete) / len(parent_rows) if parent_rows else 0.0,
            next(iter(edges)) if len(edges) == 1 else None,
            canonical_fingerprint(source_material) if complete else None,
            key in invalid,
        )
    return EconomicUtilityCohortIndex(source_cutoff_at, expected_costs, summaries)


@dataclass(frozen=True)
class TailEvidenceV1:
    min_net_return_pct: float | None
    cvar_05_pct: float | None
    mae_p95_pct: float | None
    status: Literal["clear", "harm", "unavailable"]


@dataclass(frozen=True)
class EconomicUtilityEstimateV2:
    version: Literal["universe-economic-utility/v2"]
    candidate_key: str
    mechanism_id: str
    direction: Literal["long", "short"]
    venue: str
    market_type: str
    strength_percentile: float
    strength_quintile: int
    horizon: Literal["4h"]
    estimate_status: Literal["available", "diagnostic_parent_only", "unavailable"]
    expected_net_return_pct: float | None
    cluster_standard_error_pct: float | None
    uncertainty_penalty_pct: float | None
    economic_utility_pct: float | None
    min_tradable_edge_pct: float | None
    tail_evidence: TailEvidenceV1
    cohort_identity: str
    source_outcome_fingerprint: str | None
    estimator_fingerprint: str
    parent_independent_n: int = 0
    child_independent_n: int = 0
    outcome_coverage: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def estimate_economic_utility(
    *,
    candidate_key: str,
    mechanism_id: str,
    direction: Literal["long", "short"],
    venue: str,
    market_type: str,
    strength_percentile: float,
    outcomes: Sequence[Mapping[str, object]] = (),
    source_cutoff_at: str | None = None,
    cost_assumptions: Mapping[str, object] | None = None,
    cohort_index: EconomicUtilityCohortIndex | None = None,
) -> EconomicUtilityEstimateV2:
    if not math.isfinite(strength_percentile) or not 0 <= strength_percentile <= 1:
        raise ValueError("strength_percentile_out_of_range")
    quintile = min(5, int(strength_percentile * 5) + 1)
    if cohort_index is None:
        if source_cutoff_at is None:
            raise ValueError("source_cutoff_at_invalid")
        cohort_index = build_economic_utility_cohort_index(
            outcomes,
            source_cutoff_at=source_cutoff_at,
            cost_assumptions=cost_assumptions,
        )
    summary = cohort_index.cohorts.get((mechanism_id, direction, venue, market_type))
    parent_values = summary.parent_values if summary else ()
    child_values = summary.child_values.get(quintile, ()) if summary else ()
    child_mae = summary.child_mae_values.get(quintile, ()) if summary else ()
    coverage = summary.coverage if summary else 0.0
    parent_supported = bool(
        summary
        and len(parent_values) >= 50
        and len(summary.dates) >= 10
        and len(summary.symbols) >= 5
        and coverage >= 0.80
    )
    child_supported = len(child_values) >= 20
    status: Literal["available", "diagnostic_parent_only", "unavailable"] = (
        "available"
        if parent_supported and child_supported
        else "diagnostic_parent_only"
        if parent_supported
        else "unavailable"
    )
    if summary and summary.identity_invalid:
        status = "unavailable"
    if child_values and len(child_mae) == len(child_values):
        worst_count = max(1, math.ceil(len(child_values) * 0.05))
        raw_min = summary.child_raw_min.get(quintile) if summary else None
        tail = TailEvidenceV1(
            raw_min,
            mean(sorted(child_values)[:worst_count]),
            _percentile(child_mae, 0.95),
            "harm" if raw_min is not None and raw_min < _TAIL_HARM_FLOOR_PCT else "clear",
        )
    else:
        tail = TailEvidenceV1(None, None, None, "unavailable")
    if status == "available" and tail.status == "unavailable":
        status = "unavailable"
    expected = standard_error = utility = None
    if status == "available":
        expected = (len(child_values) * mean(child_values) + _SHRINKAGE_K * mean(parent_values)) / (
            len(child_values) + _SHRINKAGE_K
        )
        standard_error = stdev(child_values) / math.sqrt(len(child_values))
        utility = expected - standard_error if tail.status == "clear" else None
    minimum_edge = summary.minimum_edge if summary else None
    if status == "available" and minimum_edge is None:
        status, expected, standard_error, utility = "unavailable", None, None, None
    cohort = {
        "version": ECONOMIC_UTILITY_VERSION,
        "independent_unit": INDEPENDENT_UNIT_VERSION,
        "mechanism_id": mechanism_id,
        "direction": direction,
        "venue": venue,
        "market_type": market_type,
        "horizon": "4h",
        "strength_quintile": quintile,
        "cost_model_version": COST_MODEL_VERSION,
        "mechanism_behavior_version": OPPORTUNITY_EVIDENCE_VERSION,
        "outcome_contract_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
        "cost_assumptions": cohort_index.cost_assumptions,
    }
    estimator = {
        "version": ECONOMIC_ESTIMATOR_VERSION,
        "independent_unit": INDEPENDENT_UNIT_VERSION,
        "shrinkage_k": _SHRINKAGE_K,
        "uncertainty_multiplier": 1.0,
        "parent_min_clusters": 50,
        "child_min_clusters": 20,
        "minimum_dates": 10,
        "minimum_symbols": 5,
        "minimum_coverage": 0.80,
        "tail_harm_floor_pct": _TAIL_HARM_FLOOR_PCT,
    }
    return EconomicUtilityEstimateV2(
        ECONOMIC_UTILITY_VERSION,
        candidate_key,
        mechanism_id,
        direction,
        venue,
        market_type,
        strength_percentile,
        quintile,
        "4h",
        status,
        expected,
        standard_error,
        standard_error,
        utility,
        minimum_edge,
        tail,
        canonical_fingerprint(cohort),
        summary.source_outcome_fingerprint if summary else None,
        canonical_fingerprint(estimator),
        len(parent_values),
        len(child_values),
        coverage,
    )


__all__ = [
    "CANDIDATE_SELECTION_OUTCOME_VERSION",
    "ECONOMIC_ESTIMATOR_VERSION",
    "ECONOMIC_UTILITY_VERSION",
    "INDEPENDENT_UNIT_INTERVAL",
    "INDEPENDENT_UNIT_VERSION",
    "EconomicUtilityCohortIndex",
    "EconomicUtilityEstimateV2",
    "TailEvidenceV1",
    "build_economic_utility_cohort_index",
    "estimate_economic_utility",
    "select_non_overlapping_t0_clusters",
]

"""Transparent clustered 4h cost-after Candidate utility estimator."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from numbers import Real
from statistics import mean, stdev
from typing import Literal, Mapping, Sequence

from aletheia.analytics.temporal import parse_utc_instant
from aletheia.universe.candidate_eligibility import canonical_fingerprint
from aletheia.universe.opportunity_evidence import OPPORTUNITY_EVIDENCE_VERSION
from aletheia.universe.outcome_economics import COST_MODEL_VERSION


ECONOMIC_UTILITY_VERSION = "universe-economic-utility/v1"
ECONOMIC_ESTIMATOR_VERSION = "candidate-economic-utility-estimator/v1"
CANDIDATE_SELECTION_OUTCOME_VERSION = "candidate-selection-utility-outcome/v1"
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
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


@dataclass(frozen=True)
class TailEvidenceV1:
    min_net_return_pct: float | None
    cvar_05_pct: float | None
    mae_p95_pct: float | None
    status: Literal["clear", "harm", "unavailable"]


@dataclass(frozen=True)
class EconomicUtilityEstimateV1:
    version: Literal["universe-economic-utility/v1"]
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
    outcomes: Sequence[Mapping[str, object]],
    source_cutoff_at: str,
    cost_assumptions: Mapping[str, object] | None = None,
) -> EconomicUtilityEstimateV1:
    if not math.isfinite(strength_percentile) or not 0 <= strength_percentile <= 1:
        raise ValueError("strength_percentile_out_of_range")
    quintile = min(5, int(strength_percentile * 5) + 1)
    cutoff = parse_utc_instant(source_cutoff_at)
    if cutoff is None:
        raise ValueError("source_cutoff_at_invalid")
    try:
        expected_costs = {
            key: float((cost_assumptions or {})[key])
            for key in ("execution_fee_pct", "slippage_bps", "fallback_spread_bps")
        }
    except (KeyError, TypeError, ValueError):
        expected_costs = None
    if expected_costs is not None and any(not math.isfinite(value) for value in expected_costs.values()):
        expected_costs = None
    parent_by_identity: dict[str, Mapping[str, object]] = {}
    identity_invalid = False
    for row in outcomes:
        entry = parse_utc_instant(row.get("entry_ts"))
        settled = parse_utc_instant(row.get("settled_at"))
        if (
            row.get("mechanism_id") == mechanism_id
            and row.get("direction") == direction
            and str(row.get("venue") or "") == venue
            and str(row.get("market_type") or "spot") == market_type
            and row.get("horizon") == "4h"
            and row.get("mechanism_behavior_version") == OPPORTUNITY_EVIDENCE_VERSION
            and row.get("outcome_contract_version") == CANDIDATE_SELECTION_OUTCOME_VERSION
            and row.get("cost_assumptions") == expected_costs
            and entry is not None and entry <= cutoff
            and (
                row.get("status") != "settled"
                or (settled is not None and settled <= cutoff)
            )
        ):
            identity = str(row.get("outcome_candidate_key") or "")
            if not identity:
                identity_invalid = True
                continue
            previous = parent_by_identity.get(identity)
            comparable = (
                "scanner_run_id", "status", "entry_ts", "settled_at", "symbol",
                "net_return_pct", "mae_pct", "cost_model_version",
                "min_tradable_edge_pct", "strength_quintile",
                "mechanism_behavior_version", "outcome_contract_version",
                "cost_assumptions", "estimated_fee_pct",
                "estimated_spread_slippage_pct", "estimated_funding_pct",
            )
            if previous is not None and any(previous.get(key) != row.get(key) for key in comparable):
                identity_invalid = True
                continue
            parent_by_identity.setdefault(identity, row)
    parent_rows = list(parent_by_identity.values())
    complete = [
        row for row in parent_rows
        if row.get("status") == "settled"
        and _finite(row.get("net_return_pct")) is not None
        and row.get("cost_model_version") == COST_MODEL_VERSION
        and row.get("scanner_run_id")
    ]
    coverage = len(complete) / len(parent_rows) if parent_rows else 0.0

    def cluster_means(rows: Sequence[Mapping[str, object]]) -> list[float]:
        grouped: dict[str, list[float]] = {}
        seen: set[str] = set()
        for row in rows:
            observation = str(row.get("outcome_candidate_key") or "")
            if not observation or observation in seen:
                continue
            seen.add(observation)
            grouped.setdefault(str(row["scanner_run_id"]), []).append(float(row["net_return_pct"]))
        return [mean(values) for _, values in sorted(grouped.items())]

    parent_clusters = cluster_means(complete)
    child_rows = [row for row in complete if row.get("strength_quintile") == quintile]
    child_clusters = cluster_means(child_rows)
    dates = {
        parsed.date()
        for row in complete
        if (parsed := parse_utc_instant(row.get("entry_ts"))) is not None
    }
    symbols = {str(row.get("symbol") or "") for row in complete if row.get("symbol")}
    parent_supported = (
        len(parent_clusters) >= 50 and len(dates) >= 10 and len(symbols) >= 5 and coverage >= 0.80
    )
    child_supported = len(child_clusters) >= 20
    status: Literal["available", "diagnostic_parent_only", "unavailable"]
    status = "available" if parent_supported and child_supported else "diagnostic_parent_only" if parent_supported else "unavailable"
    if identity_invalid:
        status = "unavailable"
    net_values = [float(row["net_return_pct"]) for row in child_rows]
    mae_values = [abs(float(value)) for row in child_rows if (value := _finite(row.get("mae_pct"))) is not None]
    if net_values and len(mae_values) == len(child_rows):
        worst_count = max(1, math.ceil(len(net_values) * 0.05))
        minimum = min(net_values)
        tail = TailEvidenceV1(
            minimum,
            mean(sorted(net_values)[:worst_count]),
            _percentile(mae_values, 0.95),
            "harm" if minimum < _TAIL_HARM_FLOOR_PCT else "clear",
        )
    else:
        tail = TailEvidenceV1(None, None, None, "unavailable")
    if status == "available" and tail.status == "unavailable":
        status = "unavailable"
    expected = standard_error = utility = None
    if status == "available":
        parent_mean = mean(parent_clusters)
        child_mean = mean(child_clusters)
        expected = (len(child_clusters) * child_mean + _SHRINKAGE_K * parent_mean) / (
            len(child_clusters) + _SHRINKAGE_K
        )
        standard_error = stdev(child_clusters) / math.sqrt(len(child_clusters)) if len(child_clusters) > 1 else None
        if standard_error is None:
            status = "diagnostic_parent_only"
            expected = None
        else:
            utility = expected - standard_error
            if tail.status != "clear":
                utility = None
    edge_values = {_finite(row.get("min_tradable_edge_pct")) for row in complete}
    edge_values.discard(None)
    minimum_edge = next(iter(edge_values)) if len(edge_values) == 1 else None
    if status == "available" and minimum_edge is None:
        status, expected, standard_error, utility = "unavailable", None, None, None
    cohort = {
        "version": ECONOMIC_UTILITY_VERSION,
        "mechanism_id": mechanism_id,
        "direction": direction,
        "venue": venue,
        "market_type": market_type,
        "horizon": "4h",
        "strength_quintile": quintile,
        "cost_model_version": COST_MODEL_VERSION,
        "mechanism_behavior_version": OPPORTUNITY_EVIDENCE_VERSION,
        "outcome_contract_version": CANDIDATE_SELECTION_OUTCOME_VERSION,
        "cost_assumptions": expected_costs,
    }
    source_material = sorted([
        {
            "outcome_candidate_key": row.get("outcome_candidate_key"),
            "scanner_run_id": row.get("scanner_run_id"),
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
    ], key=lambda row: (str(row["outcome_candidate_key"]), str(row["scanner_run_id"])))
    estimator = {
        "version": ECONOMIC_ESTIMATOR_VERSION,
        "shrinkage_k": _SHRINKAGE_K,
        "uncertainty_multiplier": 1.0,
        "parent_min_clusters": 50,
        "child_min_clusters": 20,
        "minimum_dates": 10,
        "minimum_symbols": 5,
        "minimum_coverage": 0.80,
        "tail_harm_floor_pct": _TAIL_HARM_FLOOR_PCT,
    }
    return EconomicUtilityEstimateV1(
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
        canonical_fingerprint(source_material) if complete else None,
        canonical_fingerprint(estimator),
        len(parent_clusters),
        len(child_clusters),
        coverage,
    )


__all__ = [
    "CANDIDATE_SELECTION_OUTCOME_VERSION", "ECONOMIC_ESTIMATOR_VERSION",
    "ECONOMIC_UTILITY_VERSION", "EconomicUtilityEstimateV1",
    "TailEvidenceV1", "estimate_economic_utility",
]

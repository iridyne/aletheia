"""Derived diagnostics over frozen profit-loop facts; never rewrites facts."""

from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping

from ..storage.proposal_hypothesis import evaluate_hypothesis_prefix


DIAGNOSIS_CONTRACT_VERSION = "profit_loop_diagnosis/v1"


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _nested(root: Mapping[str, Any], *keys: str) -> Any:
    value: Any = root
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _ohlcv_rows(fact: Mapping[str, Any]) -> list[list[Any]]:
    rows = _nested(fact, "stages", "holding", "ohlcv", "rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, list) and len(row) >= 7]


def _path(fact: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _nested(fact, "stages", "holding", "trade_path")
    return value if isinstance(value, Mapping) else {}


def _hypothesis(fact: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = _nested(
        fact,
        "stages",
        "decision",
        "proposal",
        "attribution_snapshot",
        "position_hypothesis",
    )
    return value if isinstance(value, Mapping) else None


def _position(fact: Mapping[str, Any]) -> Mapping[str, Any]:
    value = _nested(fact, "stages", "exit", "position")
    return value if isinstance(value, Mapping) else {}


def _realized_volatility_and_slope(rows: list[list[Any]]) -> tuple[float | None, float | None]:
    closes = [_finite(row[4]) for row in rows]
    if len(closes) < 2 or any(value is None or value <= 0 for value in closes):
        return None, None
    values = [float(value) for value in closes if value is not None]
    returns = [math.log(values[index] / values[index - 1]) for index in range(1, len(values))]
    volatility = statistics.pstdev(returns) * math.sqrt(len(returns)) * 100.0
    mean_x = (len(values) - 1) / 2.0
    mean_y = statistics.fmean(values)
    denominator = sum((index - mean_x) ** 2 for index in range(len(values)))
    slope = (
        sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values)) / denominator / values[0] * 100.0
        if denominator and values[0]
        else 0.0
    )
    return volatility, slope


def _hypothesis_states(hypothesis: Mapping[str, Any] | None, rows: list[list[Any]]) -> list[str]:
    states: list[str] = []
    prefix: list[Mapping[str, Any]] = []
    for row in rows:
        prefix.append(
            {
                "ts": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            }
        )
        states.append(evaluate_hypothesis_prefix(hypothesis, completed_bars=prefix))
    return states or [evaluate_hypothesis_prefix(hypothesis, completed_bars=[])]


def position_metrics(fact: Mapping[str, Any]) -> dict[str, Any]:
    """Compute only preregistered minimal metrics from one immutable Position fact."""
    path = _path(fact)
    position = _position(fact)
    hypothesis = _hypothesis(fact)
    rows = _ohlcv_rows(fact)
    volatility, slope = _realized_volatility_and_slope(rows)
    mfe = _finite(path.get("mfe_pct"))
    if mfe is None:
        mfe = _finite(path.get("mfe_lower_bound_pct"))
    mae = _finite(path.get("mae_pct"))
    if mae is None:
        mae = _finite(path.get("mae_upper_bound_pct"))
    mfe = max(0.0, mfe) if mfe is not None else None
    initial_risk = _finite(_nested(hypothesis or {}, "initial_risk", "quote_amount"))
    entry = _finite(position.get("entry_price"))
    quantity = _finite(position.get("quantity"))
    notional = entry * quantity if entry is not None and quantity is not None else None
    mae_quote = abs(mae) / 100.0 * notional if mae is not None and notional is not None else None
    stop = _finite(_nested(hypothesis or {}, "initial_risk", "stop_price"))
    stop_distance_pct = (
        abs(entry - stop) / entry * 100.0 if entry is not None and stop is not None and entry > 0 else None
    )
    atr_pct = None
    for signal in (hypothesis or {}).get("supporting_signals", []):
        if isinstance(signal, Mapping) and signal.get("id") in {"atr", "atr_pct"}:
            atr_pct = _finite(signal.get("value"))
            break
    economics_raw = fact.get("economics")
    economics: Mapping[str, Any] = economics_raw if isinstance(economics_raw, Mapping) else {}
    gross = _finite(economics.get("gross_pnl"))
    net = _finite(economics.get("net_pnl"))
    cost = gross - net if gross is not None and net is not None else None
    realized_pct = _finite(path.get("realized_return_pct"))
    giveback = max(0.0, mfe - realized_pct) if mfe is not None and realized_pct is not None else None
    return {
        "directional_fixed_horizon_return_pct": None,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "mae_initial_r": (
            mae_quote / initial_risk
            if mae_quote is not None and initial_risk is not None and initial_risk > 0
            else None
        ),
        "atr_stop_ratio": (
            atr_pct / stop_distance_pct
            if atr_pct is not None and stop_distance_pct is not None and stop_distance_pct > 0
            else None
        ),
        "pre_trigger_mfe_lower_bound_pct": _finite(path.get("pre_stop_mfe_lower_bound_pct")),
        "peak_mfe_pct": mfe,
        "giveback_pct": giveback,
        "first_trigger_time_ms": path.get("first_stop_barrier_open_ms"),
        "realized_volatility_pct": volatility,
        "trend_slope_pct_per_bar": slope,
        "regime_transition": "not_evaluable",
        "gross_pnl": gross,
        "net_pnl": net,
        "entry_fee": _finite(economics.get("entry_fee")),
        "exit_fee": _finite(economics.get("exit_fee")),
        "funding_cost": _finite(economics.get("funding_cost")),
        "cost_initial_r": (
            cost / initial_risk if cost is not None and initial_risk is not None and initial_risk > 0 else None
        ),
        "hypothesis_prefix_states": _hypothesis_states(hypothesis, rows),
        "same_bar_ordering": (
            "ambiguous"
            if path.get("path_evidence_status") == "ambiguous"
            else "ordered"
            if path.get("first_stop_barrier_status") == "available"
            else "unavailable"
        ),
    }


def diagnose_position_fact(fact: Mapping[str, Any]) -> dict[str, Any]:
    metrics = position_metrics(fact)
    causal = _nested(fact, "causal_link", "status")
    economics_status = _nested(fact, "economics", "availability")
    path_status = _nested(fact, "stages", "holding", "ohlcv", "availability")
    exclusions = []
    if causal != "available":
        exclusions.append("causal_link_unavailable")
    if economics_status != "available":
        exclusions.append("economics_unavailable")
    if path_status != "available":
        exclusions.append("ordered_path_unavailable")
    gross = metrics["gross_pnl"]
    net = metrics["net_pnl"]
    cost_drag = gross is not None and net is not None and gross >= 0 > net
    invalidated = "invalidated" in metrics["hypothesis_prefix_states"]
    giveback_before_trigger = (
        metrics["pre_trigger_mfe_lower_bound_pct"] is not None
        and metrics["pre_trigger_mfe_lower_bound_pct"] > 0
        and metrics["same_bar_ordering"] == "ordered"
    )
    diagnoses = {
        "selection_error": {
            "status": "unavailable",
            "reason": "candidate_denominator_required",
        },
        "direction_hypothesis_failure": {
            "status": "directional" if invalidated else "insufficient_data",
            "reason": (
                "frozen_hypothesis_invalidated_in_prefix" if invalidated else "frozen_hypothesis_not_proven_failed"
            ),
        },
        "timing_error": {
            "status": "unavailable",
            "reason": "registered_delay_counterfactual_required",
        },
        "risk_expression_error": {
            "status": "unavailable",
            "reason": "registered_alternative_risk_expression_required",
        },
        "profit_management_error": {
            "status": "directional" if giveback_before_trigger else "insufficient_data",
            "reason": (
                "favorable_excursion_preceded_ordered_trigger"
                if giveback_before_trigger
                else "ordered_pre_trigger_excursion_not_proven"
            ),
        },
        "cost_drag": {
            "status": "measured" if cost_drag else "insufficient_data",
            "reason": "verified_costs_flip_gross_to_net" if cost_drag else "cost_flip_not_observed",
        },
        "decision_latency": {
            "status": "unavailable",
            "reason": "frozen_latency_fill_model_required",
        },
        "data_or_accounting_gap": {
            "status": "measured" if exclusions else "insufficient_data",
            "reason": exclusions or "required_evidence_available",
        },
    }
    identified = [
        key
        for key, value in diagnoses.items()
        if key != "data_or_accounting_gap" and value["status"] in {"measured", "directional"}
    ]
    primary = (
        "data_or_accounting_gap"
        if exclusions
        else identified[0]
        if len(identified) == 1
        else "multi_factor"
        if len(identified) > 1
        else "undetermined"
    )
    return {
        "contract_version": DIAGNOSIS_CONTRACT_VERSION,
        "fact_unit": "position_fact",
        "fact_id": fact.get("fact_id"),
        "status": "unavailable" if exclusions else "directional",
        "raw_count": 1,
        "eligible_count": 0 if exclusions else 1,
        "excluded_count": 1 if exclusions else 0,
        "unavailable_count": 1 if exclusions else 0,
        "metrics": metrics,
        "diagnoses": diagnoses,
        "primary_diagnosis": primary,
        "limitations": exclusions,
    }


def _group_returns(facts: Iterable[Mapping[str, Any]], predicate) -> list[float]:
    values = []
    for fact in facts:
        if not predicate(fact):
            continue
        outcome = fact.get("fixed_horizon_outcome")
        if not isinstance(outcome, Mapping) or outcome.get("status") != "settled":
            continue
        value = _finite(outcome.get("net_return_pct"))
        if value is not None:
            values.append(value)
    return values


def _candidate_kind(fact: Mapping[str, Any]) -> str:
    first = fact.get("first_exposure")
    return str(first.get("candidate_kind") or "") if isinstance(first, Mapping) else ""


def diagnose_candidate_report(report: Mapping[str, Any]) -> dict[str, Any]:
    facts = [fact for fact in report.get("facts", []) if isinstance(fact, Mapping)]
    comparisons = {
        "Selection": (
            _group_returns(
                facts,
                lambda fact: _candidate_kind(fact) == "opportunity" and fact.get("terminal_state") == "filtered",
            ),
            _group_returns(
                facts,
                lambda fact: _candidate_kind(fact) == "opportunity" and fact.get("terminal_state") != "filtered",
            ),
        ),
        "Decision": (
            _group_returns(
                facts,
                lambda fact: (
                    _candidate_kind(fact) == "pi_decision" and str(fact.get("terminal_state", "")).startswith("pi_")
                ),
            ),
            _group_returns(
                facts,
                lambda fact: (
                    _candidate_kind(fact) == "pi_decision"
                    and fact.get("terminal_state") in {"risk_reject", "approved_unfilled", "traded"}
                ),
            ),
        ),
        "Risk": (
            _group_returns(
                facts,
                lambda fact: _candidate_kind(fact) == "proposal" and fact.get("terminal_state") == "risk_reject",
            ),
            _group_returns(
                facts,
                lambda fact: (
                    _candidate_kind(fact) == "proposal"
                    and fact.get("terminal_state") in {"approved_unfilled", "traded"}
                ),
            ),
        ),
    }
    layers = {}
    for layer, (baseline, observed) in comparisons.items():
        baseline_mean = statistics.fmean(baseline) if baseline else None
        observed_mean = statistics.fmean(observed) if observed else None
        layers[layer] = {
            "status": "directional" if baseline and observed else "insufficient_data",
            "raw_count": len(baseline) + len(observed),
            "eligible_count": len(baseline) + len(observed),
            "excluded_count": max(0, len(facts) - len(baseline) - len(observed)),
            "baseline": baseline_mean,
            "observed": observed_mean,
            "delta_pct": (
                observed_mean - baseline_mean if baseline_mean is not None and observed_mean is not None else None
            ),
            "statistical_unit": "outcome_candidate_key",
            "economic_unit": "fixed_horizon_net_return_pct",
        }
    return {
        "contract_version": DIAGNOSIS_CONTRACT_VERSION,
        "fact_unit": "candidate_fact",
        "read_only": True,
        "behavior_changed": False,
        "as_of_at": report.get("as_of_at"),
        "fixed_horizon": report.get("fixed_horizon"),
        "raw_count": len(facts),
        "eligible_count": int(report.get("settled_outcome_count") or 0),
        "coverage": report.get("outcome_coverage"),
        "layers": layers,
    }

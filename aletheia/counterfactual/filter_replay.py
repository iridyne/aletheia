"""Bounded read-only replays for monotonic filter thresholds."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import random
from statistics import mean, median
from typing import Any, Iterable

from aletheia.analytics.filter_evidence import FIRST_EXPOSURE_CTE_SQL
from aletheia.storage.sqlite import get_db


_MAX_REPLAYS = 8
_MAX_OBSERVER_REPLAYS = 3
_MAX_ALTERNATIVES = 4
_MAX_CANDIDATES = 5000
_MIN_COVERAGE = 0.70
_SHRINKAGE_K = 20.0
_MIN_EDGE_DELTA_PCT = 0.05
_OBSERVER_RSI_GRID = (12.0, 10.0, 8.0, 6.0)
_OBSERVER_ATR_GRID = (0.8, 0.7, 0.6, 0.5)
_OBSERVER_MIN_SAMPLES = 200
_OBSERVER_MIN_COVERAGE = 0.80
_OBSERVER_MIN_EDGE_DELTA_PCT = 0.25
_OBSERVER_MAX_CONCENTRATION = 0.30
_OBSERVER_MAX_COMPANION_DEGRADATION_PCT = 0.25
_OBSERVER_BOOTSTRAP_ROUNDS = 500
_CONFIDENCE_LADDER = ("speculative", "medium", "high")


def build_family_ttl_replay(
    *,
    filter_version: str,
    stage: str,
    scanner_family: str,
    direction: str,
    market_type: str,
    horizon: str,
    current_ttl_sec: int,
    alternative_ttls: Iterable[int] | None = None,
    evaluated_since: str | None = None,
    evaluated_after: str | None = None,
    min_samples: int = 20,
    max_candidates: int = _MAX_CANDIDATES,
) -> dict[str, Any]:
    """Replay longer TTLs against fixed-version, first-exposure rejected rows.

    This is gate-only evidence. It estimates which already-observed candidates a
    longer TTL would have admitted; it does not replay downstream ranker, risk,
    execution, portfolio conflicts, or exchange availability.
    """
    version = str(filter_version or "").strip()
    if not version:
        raise ValueError("filter_version is required for a causal replay")
    family = str(scanner_family or "").strip()
    stage_s = str(stage or "").strip()
    side = str(direction or "").strip().lower()
    market = str(market_type or "").strip().lower()
    horizon_s = str(horizon or "").strip()
    if not all((stage_s, family, side, market, horizon_s)):
        raise ValueError("stage, scanner_family, direction, market_type and horizon are required")
    if evaluated_since and evaluated_after:
        raise ValueError("evaluated_since and evaluated_after are mutually exclusive")

    current_ttl = max(1, int(current_ttl_sec))
    candidate_cap = max(1, min(int(max_candidates), _MAX_CANDIDATES))
    sample_gate = max(1, int(min_samples))
    alternatives = _bounded_alternatives(current_ttl, alternative_ttls)
    passed_rows, passed_capped = _load_first_exposure_rows(
        filter_name="family_ttl",
        decision="passed",
        filter_version=version,
        stage=stage_s,
        scanner_family=family,
        direction=side,
        market_type=market,
        horizon=horizon_s,
        evaluated_since=evaluated_since,
        evaluated_after=evaluated_after,
        cap=candidate_cap,
    )
    rejected_rows, rejected_capped = _load_first_exposure_rows(
        filter_name="family_ttl",
        decision="rejected",
        filter_version=version,
        stage=stage_s,
        scanner_family=family,
        direction=side,
        market_type=market,
        horizon=horizon_s,
        evaluated_since=evaluated_since,
        evaluated_after=evaluated_after,
        cap=candidate_cap,
    )

    current_metrics = _outcome_metrics(passed_rows)
    prior_mean = current_metrics["raw_avg_net_pct"]
    if prior_mean is None:
        prior_mean = 0.0
    replayable: list[dict[str, Any]] = []
    invalid_inputs = 0
    for row in rejected_rows:
        required_ttl = _required_ttl(row, current_ttl)
        if required_ttl is None:
            invalid_inputs += 1
            continue
        replayable.append({**row, "required_ttl_sec": required_ttl})

    alternative_rows: list[dict[str, Any]] = []
    for ttl_sec in alternatives:
        admitted = [row for row in replayable if row["required_ttl_sec"] <= ttl_sec]
        metrics = _outcome_metrics(admitted)
        n = int(metrics["settled_n"])
        raw_mean = metrics["raw_avg_net_pct"]
        shrunk = (
            round((n * float(raw_mean) + _SHRINKAGE_K * prior_mean) / (n + _SHRINKAGE_K), 6)
            if n and raw_mean is not None
            else None
        )
        coverage = float(metrics["outcome_coverage"])
        readiness = "insufficient_data"
        if n >= sample_gate and coverage >= _MIN_COVERAGE and shrunk is not None:
            current_mean = float(current_metrics["raw_avg_net_pct"] or 0.0)
            readiness = (
                "ready_for_controlled_experiment"
                if (
                    shrunk >= _MIN_EDGE_DELTA_PCT
                    and shrunk - current_mean >= _MIN_EDGE_DELTA_PCT
                )
                else "no_support"
            )
        alternative_rows.append({
            "ttl_sec": ttl_sec,
            "newly_admitted_candidates": len(admitted),
            **metrics,
            "shrunk_avg_net_pct": shrunk,
            "readiness": readiness,
        })

    return {
        "read_only": True,
        "gate_only": True,
        "filter_name": "family_ttl",
        "filter_version": version,
        "stage": stage_s,
        "scanner_family": family,
        "direction": side,
        "market_type": market,
        "horizon": horizon_s,
        "evaluated_since": evaluated_since,
        "evaluated_after": evaluated_after,
        "assignment_policy": "first_exposure",
        "current_ttl_sec": current_ttl,
        "candidate_cap": candidate_cap,
        "candidate_cap_reached": passed_capped or rejected_capped,
        "min_samples": sample_gate,
        "current_pass": current_metrics,
        "rejected_candidates": len(rejected_rows),
        "invalid_replay_inputs": invalid_inputs,
        "alternatives": alternative_rows,
        "limitations": [
            "Gate-only replay; downstream ranker, risk, execution and portfolio conflicts are not replayed.",
            "Alternative cohorts are nested and observational, not randomized experiments.",
            "No threshold or configuration is mutated.",
        ],
    }


def build_ranker_confidence_replay(
    *,
    filter_version: str,
    stage: str,
    scanner_family: str,
    direction: str,
    market_type: str,
    horizon: str,
    evaluated_since: str | None = None,
    evaluated_after: str | None = None,
    min_samples: int = 20,
    max_candidates: int = _MAX_CANDIDATES,
) -> dict[str, Any]:
    """Replay lower confidence gates against fixed-version rejected candidates."""
    version = str(filter_version or "").strip()
    family = str(scanner_family or "").strip()
    stage_s = str(stage or "").strip()
    side = str(direction or "").strip().lower()
    market = str(market_type or "").strip().lower()
    horizon_s = str(horizon or "").strip()
    if not version:
        raise ValueError("filter_version is required for a causal replay")
    if not all((stage_s, family, side, market, horizon_s)):
        raise ValueError("stage, scanner_family, direction, market_type and horizon are required")
    if evaluated_since and evaluated_after:
        raise ValueError("evaluated_since and evaluated_after are mutually exclusive")

    candidate_cap = max(1, min(int(max_candidates), _MAX_CANDIDATES))
    sample_gate = max(1, int(min_samples))
    common = {
        "filter_name": "ranker_confidence_gate",
        "filter_version": version,
        "stage": stage_s,
        "scanner_family": family,
        "direction": side,
        "market_type": market,
        "horizon": horizon_s,
        "evaluated_since": evaluated_since,
        "evaluated_after": evaluated_after,
        "cap": candidate_cap,
    }
    passed_rows, passed_capped = _load_first_exposure_rows(
        decision="passed", **common
    )
    rejected_rows, rejected_capped = _load_first_exposure_rows(
        decision="rejected", **common
    )
    minimum_values = [
        _confidence_value(_json_dict(row.get("parameter_snapshot")).get("minimum"))
        for row in (*passed_rows, *rejected_rows)
    ]
    minimums = set(minimum_values)
    current_minimum = (
        next(iter(minimums))
        if minimum_values and None not in minimums and len(minimums) == 1
        else None
    )
    current_metrics = _outcome_metrics(passed_rows)
    prior_mean = float(current_metrics["raw_avg_net_pct"] or 0.0)
    invalid_inputs = 0
    alternatives: list[dict[str, Any]] = []

    if current_minimum is not None:
        current_rank = _CONFIDENCE_LADDER.index(current_minimum)
        for alternative_rank in range(current_rank - 1, -1, -1):
            minimum = _CONFIDENCE_LADDER[alternative_rank]
            admitted = []
            for row in rejected_rows:
                confidence = _confidence_value(
                    _json_dict(row.get("input_snapshot")).get("confidence")
                )
                if confidence is None:
                    invalid_inputs += 1
                    continue
                rank = _CONFIDENCE_LADDER.index(confidence)
                if alternative_rank <= rank < current_rank:
                    admitted.append(row)
            metrics = _outcome_metrics(admitted)
            shrunk = _shrunk_mean(metrics, prior_mean)
            delta = _metric_delta(shrunk, current_metrics["raw_avg_net_pct"])
            readiness = "insufficient_data"
            if (
                int(metrics["settled_n"]) >= sample_gate
                and float(metrics["outcome_coverage"]) >= _MIN_COVERAGE
                and shrunk is not None
                and delta is not None
            ):
                readiness = (
                    "ready_for_controlled_experiment"
                    if shrunk > 0 and delta >= _MIN_EDGE_DELTA_PCT
                    else "no_support"
                )
            alternatives.append({
                "minimum_confidence": minimum,
                "newly_admitted_candidates": len(admitted),
                **metrics,
                "shrunk_avg_net_pct": shrunk,
                "delta_vs_current_pass_pct": delta,
                "readiness": readiness,
            })

    return {
        "read_only": True,
        "gate_only": True,
        "filter_name": "ranker_confidence_gate",
        "filter_version": version,
        "stage": stage_s,
        "scanner_family": family,
        "direction": side,
        "market_type": market,
        "horizon": horizon_s,
        "evaluated_since": evaluated_since,
        "evaluated_after": evaluated_after,
        "assignment_policy": "first_exposure",
        "current_minimum_confidence": current_minimum,
        "identity_status": "exact" if current_minimum is not None else "invalid",
        "candidate_cap": candidate_cap,
        "candidate_cap_reached": passed_capped or rejected_capped,
        "min_samples": sample_gate,
        "current_pass": current_metrics,
        "rejected_candidates": len(rejected_rows),
        "invalid_replay_inputs": invalid_inputs,
        "alternatives": alternatives,
        "limitations": [
            "Gate-only replay; downstream ranker, risk, execution and portfolio conflicts are not replayed.",
            "Historical alternatives are observational; readiness authorizes only a prospective controlled experiment.",
            "No confidence threshold or configuration is mutated.",
        ],
    }


def build_observer_pre_filter_replay(
    *,
    filter_version: str,
    scanner_family: str,
    direction: str,
    market_type: str,
    evaluated_since: str | None = None,
    min_samples: int = _OBSERVER_MIN_SAMPLES,
    max_candidates: int = _MAX_CANDIDATES,
) -> dict[str, Any]:
    """Replay the precommitted RSI/ATR admission grid without changing behavior."""
    version = str(filter_version or "").strip()
    family = str(scanner_family or "").strip()
    side = str(direction or "").strip().lower()
    market = str(market_type or "").strip().lower()
    if not version:
        raise ValueError("filter_version is required for a causal replay")
    if not all((family, side, market)):
        raise ValueError("scanner_family, direction and market_type are required")

    candidate_cap = max(1, min(int(max_candidates), _MAX_CANDIDATES))
    sample_gate = max(_OBSERVER_MIN_SAMPLES, int(min_samples))
    rows_by_horizon: dict[str, dict[str, list[dict[str, Any]]]] = {}
    capped = False
    for horizon in ("1h", "4h", "24h"):
        rows_by_horizon[horizon] = {}
        for decision in ("passed", "rejected"):
            rows, hit_cap = _load_observer_first_exposure_rows(
                decision=decision,
                filter_version=version,
                scanner_family=family,
                direction=side,
                market_type=market,
                horizon=horizon,
                evaluated_since=evaluated_since,
                cap=candidate_cap,
            )
            rows_by_horizon[horizon][decision] = rows
            capped = capped or hit_cap

    alternatives: list[dict[str, Any]] = []
    for rsi_threshold in _OBSERVER_RSI_GRID:
        for atr_threshold in _OBSERVER_ATR_GRID:
            if (
                rsi_threshold == _OBSERVER_RSI_GRID[0]
                and atr_threshold == _OBSERVER_ATR_GRID[0]
            ):
                continue
            horizon_metrics: dict[str, dict[str, Any]] = {}
            for horizon, decisions in rows_by_horizon.items():
                admitted = [
                    row for row in decisions["rejected"]
                    if _observer_would_pass(row, rsi_threshold, atr_threshold)
                ]
                control = decisions["passed"]
                treatment = [*control, *admitted]
                control_metrics = _outcome_metrics(control)
                admitted_metrics = _outcome_metrics(admitted)
                treatment_metrics = _outcome_metrics(treatment)
                prior = float(control_metrics["raw_avg_net_pct"] or 0.0)
                admitted_metrics["shrunk_avg_net_pct"] = _shrunk_mean(
                    admitted_metrics, prior
                )
                treatment_metrics["shrunk_avg_net_pct"] = _shrunk_mean(
                    treatment_metrics, prior
                )
                treatment_metrics["delta_vs_control_pct"] = _metric_delta(
                    treatment_metrics["shrunk_avg_net_pct"],
                    control_metrics["raw_avg_net_pct"],
                )
                horizon_metrics[horizon] = {
                    "control": control_metrics,
                    "newly_admitted": admitted_metrics,
                    "treatment_union": treatment_metrics,
                }

            primary = horizon_metrics["24h"]
            newly_admitted = [
                row for row in rows_by_horizon["24h"]["rejected"]
                if _observer_would_pass(row, rsi_threshold, atr_threshold)
            ]
            treatment_rows = [
                *rows_by_horizon["24h"]["passed"],
                *newly_admitted,
            ]
            bootstrap_lcb = _bootstrap_delta_lcb(
                rows_by_horizon["24h"]["passed"],
                treatment_rows,
                seed_material=(
                    f"{version}|{family}|{side}|{market}|"
                    f"{rsi_threshold}|{atr_threshold}"
                ),
            )
            concentration = _improvement_concentration(
                newly_admitted,
                control_mean=float(
                    primary["control"]["raw_avg_net_pct"] or 0.0
                ),
            )
            run_ids = {
                str(row["run_id"])
                for row in rows_by_horizon["24h"]["rejected"]
                if row.get("run_id")
            }
            extra_calls_per_run = (
                len(newly_admitted) / len(run_ids) if run_ids else None
            )
            companion_ok = all(
                (
                    horizon_metrics[h]["treatment_union"]["delta_vs_control_pct"]
                    is not None
                    and horizon_metrics[h]["treatment_union"]["delta_vs_control_pct"]
                    >= -_OBSERVER_MAX_COMPANION_DEGRADATION_PCT
                )
                for h in ("1h", "4h")
            )
            admitted_metrics = primary["newly_admitted"]
            union_metrics = primary["treatment_union"]
            readiness = "insufficient_data"
            if (
                int(admitted_metrics["settled_n"]) >= sample_gate
                and float(admitted_metrics["outcome_coverage"])
                >= _OBSERVER_MIN_COVERAGE
            ):
                readiness = (
                    "ready_for_prospective_shadow"
                    if (
                        union_metrics["delta_vs_control_pct"] is not None
                        and union_metrics["delta_vs_control_pct"]
                        >= _OBSERVER_MIN_EDGE_DELTA_PCT
                        and bootstrap_lcb is not None
                        and bootstrap_lcb > 0.0
                        and concentration["max_symbol_share"]
                        <= _OBSERVER_MAX_CONCENTRATION
                        and concentration["max_utc_day_share"]
                        <= _OBSERVER_MAX_CONCENTRATION
                        and companion_ok
                    )
                    else "no_support"
                )
            alternatives.append({
                "rsi_deviation": rsi_threshold,
                "atr_pct": atr_threshold,
                "newly_admitted_candidates": len(newly_admitted),
                "extra_model_calls_per_observer_run": (
                    round(extra_calls_per_run, 4)
                    if extra_calls_per_run is not None else None
                ),
                "bootstrap_delta_lcb_pct": bootstrap_lcb,
                "improvement_concentration": concentration,
                "companion_horizons_ok": companion_ok,
                "horizon_metrics": horizon_metrics,
                "readiness": readiness,
            })

    return {
        "read_only": True,
        "gate_only": True,
        "filter_name": "observer_pre_filter",
        "filter_version": version,
        "stage": "observer_pre_filter",
        "scanner_family": family,
        "direction": side,
        "market_type": market,
        "horizon": "24h",
        "evaluated_since": evaluated_since,
        "assignment_policy": "first_exposure",
        "control_parameter": {
            "rsi_deviation": _OBSERVER_RSI_GRID[0],
            "atr_pct": _OBSERVER_ATR_GRID[0],
        },
        "candidate_cap": candidate_cap,
        "candidate_cap_reached": capped,
        "min_samples": sample_gate,
        "min_coverage": _OBSERVER_MIN_COVERAGE,
        "min_edge_delta_pct": _OBSERVER_MIN_EDGE_DELTA_PCT,
        "max_concentration": _OBSERVER_MAX_CONCENTRATION,
        "alternatives": alternatives,
        "limitations": [
            "Gate-only replay; no model, ranker, risk, execution or portfolio behavior is replayed.",
            "The 4x4 threshold grid was precommitted before inspecting replay winners.",
            "Historical alternatives are observational; readiness authorizes only a prospective shadow.",
            "No threshold, configuration, Opportunity or experiment state is mutated.",
        ],
    }


def build_bounded_filter_replays(
    recommendations: Iterable[Any],
    *,
    evaluated_since: str | None = None,
    min_samples: int = 20,
    max_replays: int = _MAX_REPLAYS,
) -> list[dict[str, Any]]:
    """Build supported replays only for observational overrestriction suspects."""
    reports: list[dict[str, Any]] = []
    replay_cap = max(0, min(int(max_replays), _MAX_REPLAYS))
    if replay_cap == 0:
        return reports
    rows = [_as_mapping(recommendation) for recommendation in recommendations]
    # The first mature non-TTL signal must not be starved merely because registry
    # order produced many TTL cells before it. Keep the ordering deterministic
    # and prioritize exact 24h Observer suspects, then stronger error signals.
    rows.sort(key=lambda row: (
        0 if (
            row.get("filter_name") == "observer_pre_filter"
            and row.get("horizon") == "24h"
        ) else 1 if row.get("filter_name") == "ranker_confidence_gate" else 2,
        -abs(float(row.get("error_signal") or 0.0)),
        str(row.get("filter_name") or ""),
        str(row.get("scanner_family") or ""),
    ))
    observer_replay_count = 0
    for row in rows:
        if row.get("verdict") != "overrestrictive_suspect":
            continue
        filter_name = row.get("filter_name")
        if filter_name == "observer_pre_filter":
            if (
                row.get("horizon") != "24h"
                or not row.get("filter_version")
                or observer_replay_count >= _MAX_OBSERVER_REPLAYS
            ):
                continue
            reports.append(build_observer_pre_filter_replay(
                filter_version=str(row["filter_version"]),
                scanner_family=str(row.get("scanner_family") or "unknown"),
                direction=str(row.get("direction") or ""),
                market_type=str(row.get("market_type") or ""),
                evaluated_since=evaluated_since,
                min_samples=max(_OBSERVER_MIN_SAMPLES, int(min_samples)),
            ))
            observer_replay_count += 1
            if len(reports) >= replay_cap:
                break
            continue
        if filter_name == "ranker_confidence_gate":
            if not row.get("filter_version"):
                continue
            reports.append(build_ranker_confidence_replay(
                filter_version=str(row["filter_version"]),
                stage=str(row.get("stage") or ""),
                scanner_family=str(row.get("scanner_family") or "unknown"),
                direction=str(row.get("direction") or ""),
                market_type=str(row.get("market_type") or ""),
                horizon=str(row.get("horizon") or ""),
                evaluated_since=evaluated_since,
                min_samples=min_samples,
            ))
            if len(reports) >= replay_cap:
                break
            continue
        if filter_name != "family_ttl":
            continue
        current_ttl = _ttl_from_snapshot(row.get("current_value"))
        if current_ttl is None or not row.get("filter_version"):
            continue
        reports.append(build_family_ttl_replay(
            filter_version=str(row["filter_version"]),
            stage=str(row.get("stage") or ""),
            scanner_family=str(row.get("scanner_family") or "unknown"),
            direction=str(row.get("direction") or ""),
            market_type=str(row.get("market_type") or ""),
            horizon=str(row.get("horizon") or ""),
            current_ttl_sec=current_ttl,
            evaluated_since=evaluated_since,
            min_samples=min_samples,
        ))
        if len(reports) >= replay_cap:
            break
    return reports


def _load_observer_first_exposure_rows(
    *,
    decision: str,
    filter_version: str,
    scanner_family: str,
    direction: str,
    market_type: str,
    horizon: str,
    evaluated_since: str | None,
    cap: int,
) -> tuple[list[dict[str, Any]], bool]:
    time_clause = (
        " AND datetime(fd.first_evaluated_at) >= datetime(?)"
        if evaluated_since else ""
    )
    params: list[Any] = [
        horizon, decision, filter_version, scanner_family, direction, market_type,
    ]
    if evaluated_since:
        params.append(evaluated_since)
    params.append(cap + 1)
    rows = get_db().execute(
        FIRST_EXPOSURE_CTE_SQL
        + "SELECT fd.outcome_candidate_key, fd.run_id, fd.symbol, "
        "fd.input_snapshot, fd.parameter_snapshot, fd.first_evaluated_at, "
        "fo.status, fo.net_return_pct "
        "FROM ranked_filter_decisions fd "
        "LEFT JOIN filter_candidate_outcomes fo "
        " ON fo.outcome_candidate_key=fd.outcome_candidate_key AND fo.horizon=? "
        "WHERE fd.exposure_rank=1 "
        " AND fd.filter_name='observer_pre_filter' "
        " AND fd.stage='observer_pre_filter' "
        " AND fd.decision=? AND fd.filter_version=? "
        " AND COALESCE(fd.scanner_family,'unknown')=? "
        " AND fd.direction=? AND fd.market_type=? "
        f"{time_clause} "
        "ORDER BY datetime(fd.first_evaluated_at), fd.outcome_candidate_key LIMIT ?",
        tuple(params),
    ).fetchall()
    capped = len(rows) > cap
    return [dict(row) for row in rows[:cap]], capped


def _load_first_exposure_rows(
    *,
    filter_name: str,
    decision: str,
    filter_version: str,
    stage: str,
    scanner_family: str,
    direction: str,
    market_type: str,
    horizon: str,
    evaluated_since: str | None,
    evaluated_after: str | None,
    cap: int,
) -> tuple[list[dict[str, Any]], bool]:
    if evaluated_after:
        time_clause = " AND julianday(fd.first_evaluated_at) > julianday(?)"
    elif evaluated_since:
        time_clause = " AND datetime(fd.first_evaluated_at) >= datetime(?)"
    else:
        time_clause = ""
    params: list[Any] = [
        horizon,
        filter_name,
        decision,
        filter_version,
        stage,
        scanner_family,
        direction,
        market_type,
    ]
    if evaluated_after or evaluated_since:
        params.append(evaluated_after or evaluated_since)
    params.append(cap + 1)
    rows = get_db().execute(
        FIRST_EXPOSURE_CTE_SQL +
        "SELECT fd.outcome_candidate_key, fd.input_snapshot, fd.parameter_snapshot, "
        "fd.first_evaluated_at, fo.status, fo.net_return_pct "
        "FROM ranked_filter_decisions fd "
        "LEFT JOIN filter_candidate_outcomes fo "
        " ON fo.outcome_candidate_key=fd.outcome_candidate_key AND fo.horizon=? "
        "WHERE fd.exposure_rank=1 AND fd.filter_name=? "
        " AND fd.decision=? AND fd.filter_version=? AND fd.stage=? "
        " AND COALESCE(fd.scanner_family,'unknown')=? "
        " AND fd.direction=? AND fd.market_type=? "
        f"{time_clause} "
        "ORDER BY datetime(fd.first_evaluated_at), fd.outcome_candidate_key LIMIT ?",
        tuple(params),
    ).fetchall()
    capped = len(rows) > cap
    return [dict(row) for row in rows[:cap]], capped


def _observer_would_pass(
    row: dict[str, Any], rsi_threshold: float, atr_threshold: float
) -> bool:
    inputs = _json_dict(row.get("input_snapshot"))
    try:
        rsi_deviation = abs(float(inputs["rsi"]) - 50.0)
    except (KeyError, TypeError, ValueError):
        rsi_deviation = None
    try:
        atr_pct = float(inputs["atr_pct"])
    except (KeyError, TypeError, ValueError):
        atr_pct = None
    return bool(
        (rsi_deviation is not None and rsi_deviation > rsi_threshold)
        or (atr_pct is not None and atr_pct > atr_threshold)
    )


def _shrunk_mean(metrics: dict[str, Any], prior: float) -> float | None:
    n = int(metrics["settled_n"])
    raw = metrics["raw_avg_net_pct"]
    if not n or raw is None:
        return None
    return round(
        (n * float(raw) + _SHRINKAGE_K * prior) / (n + _SHRINKAGE_K),
        6,
    )


def _metric_delta(value: Any, baseline: Any) -> float | None:
    if value is None or baseline is None:
        return None
    return round(float(value) - float(baseline), 6)


def _settled_values(rows: list[dict[str, Any]]) -> list[float]:
    return [
        float(row["net_return_pct"])
        for row in rows
        if row.get("status") == "settled"
        and row.get("net_return_pct") is not None
    ]


def _bootstrap_delta_lcb(
    control_rows: list[dict[str, Any]],
    treatment_rows: list[dict[str, Any]],
    *,
    seed_material: str,
) -> float | None:
    control = _settled_values(control_rows)
    treatment = _settled_values(treatment_rows)
    if len(control) < 2 or len(treatment) < 2:
        return None
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    deltas: list[float] = []
    for _ in range(_OBSERVER_BOOTSTRAP_ROUNDS):
        control_mean = mean(rng.choice(control) for _ in control)
        treatment_mean = mean(rng.choice(treatment) for _ in treatment)
        deltas.append(treatment_mean - control_mean)
    deltas.sort()
    index = max(0, int(len(deltas) * 0.05) - 1)
    return round(deltas[index], 6)


def _improvement_concentration(
    rows: list[dict[str, Any]], *, control_mean: float
) -> dict[str, float]:
    symbol: dict[str, float] = {}
    utc_day: dict[str, float] = {}
    for row in rows:
        if row.get("status") != "settled" or row.get("net_return_pct") is None:
            continue
        improvement = max(0.0, float(row["net_return_pct"]) - control_mean)
        if improvement <= 0:
            continue
        symbol_key = str(row.get("symbol") or "unknown")
        day_key = str(row.get("first_evaluated_at") or "")[:10] or "unknown"
        symbol[symbol_key] = symbol.get(symbol_key, 0.0) + improvement
        utc_day[day_key] = utc_day.get(day_key, 0.0) + improvement
    total = sum(symbol.values())
    if total <= 0:
        return {"max_symbol_share": 0.0, "max_utc_day_share": 0.0}
    return {
        "max_symbol_share": round(max(symbol.values(), default=0.0) / total, 4),
        "max_utc_day_share": round(max(utc_day.values(), default=0.0) / total, 4),
    }


def _bounded_alternatives(
    current_ttl: int, values: Iterable[int] | None
) -> list[int]:
    source = values or (
        round(current_ttl * 1.5), current_ttl * 2, current_ttl * 4, current_ttl * 8,
    )
    sanitized: set[int] = set()
    for value in source:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > current_ttl:
            sanitized.add(max(1, min(parsed, 86_400)))
    out = sorted(sanitized)
    return out[:_MAX_ALTERNATIVES]


def _required_ttl(row: dict[str, Any], fallback_ttl: int) -> int | None:
    inputs = _json_dict(row.get("input_snapshot"))
    params = _json_dict(row.get("parameter_snapshot"))
    now = _parse_time(inputs.get("now"))
    expires = _parse_time(inputs.get("expires_at"))
    if now is None or expires is None:
        return None
    try:
        row_ttl = max(1, int(params.get("ttl_sec") or fallback_ttl))
    except (TypeError, ValueError):
        return None
    lateness = max(0, round((now - expires).total_seconds()))
    return row_ttl + lateness


def _outcome_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [
        float(row["net_return_pct"])
        for row in rows
        if row.get("status") == "settled" and row.get("net_return_pct") is not None
    ]
    return {
        "candidate_count": len(rows),
        "settled_n": len(settled),
        "outcome_coverage": round(len(settled) / len(rows), 4) if rows else 0.0,
        "raw_avg_net_pct": round(mean(settled), 6) if settled else None,
        "median_net_pct": round(median(settled), 6) if settled else None,
    }


def _ttl_from_snapshot(value: Any) -> int | None:
    raw = _json_dict(value)
    try:
        ttl = int(raw.get("ttl_sec"))
        return ttl if ttl > 0 else None
    except (TypeError, ValueError):
        return None


def _confidence_value(value: Any) -> str | None:
    confidence = str(value or "").strip().lower()
    return confidence if confidence in _CONFIDENCE_LADDER else None


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    fields = getattr(value, "__dataclass_fields__", None)
    if fields:
        return {name: getattr(value, name) for name in fields}
    return {}

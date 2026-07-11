"""Bounded read-only replays for monotonic filter thresholds."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from statistics import mean, median
from typing import Any, Iterable

from aletheia.analytics.filter_evidence import FIRST_EXPOSURE_CTE_SQL
from aletheia.storage.sqlite import get_db


_MAX_REPLAYS = 8
_MAX_ALTERNATIVES = 4
_MAX_CANDIDATES = 5000
_MIN_COVERAGE = 0.70
_SHRINKAGE_K = 20.0
_MIN_EDGE_DELTA_PCT = 0.05


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
    for recommendation in recommendations:
        row = _as_mapping(recommendation)
        if row.get("verdict") != "overrestrictive_suspect":
            continue
        if row.get("filter_name") != "family_ttl":
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


def _load_first_exposure_rows(
    *,
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
        horizon, decision, filter_version, stage, scanner_family, direction, market_type,
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
        "WHERE fd.exposure_rank=1 AND fd.filter_name='family_ttl' "
        " AND fd.decision=? AND fd.filter_version=? AND fd.stage=? "
        " AND COALESCE(fd.scanner_family,'unknown')=? "
        " AND fd.direction=? AND fd.market_type=? "
        f"{time_clause} "
        "ORDER BY datetime(fd.first_evaluated_at), fd.outcome_candidate_key LIMIT ?",
        tuple(params),
    ).fetchall()
    capped = len(rows) > cap
    return [dict(row) for row in rows[:cap]], capped


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

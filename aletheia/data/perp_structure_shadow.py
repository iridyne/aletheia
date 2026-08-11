"""Forward settlement for observation-only Position X aggregate snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from statistics import mean, median

from aletheia.storage import repositories as repo


async def settle_due_perp_structure_outcomes(limit: int = 100) -> int:
    """Settle due structure rows on their exact venue/instrument OHLCV path."""
    from aletheia.universe.outcome_recorder import (
        _batch_fetch_market_observations,
        is_deferred,
    )

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    rows = repo.list_due_perp_structure_shadow_outcomes(now_iso, limit=limit)
    if not rows:
        return 0
    observations = await _batch_fetch_market_observations(rows, now=now)
    settled = 0
    for row in rows:
        observation = observations.get(row["id"])
        if is_deferred(observation):
            # The venue was never reached, so this row was never measured. It stays
            # due and is asked about again next cycle; failing it here would write
            # off a measurable observation permanently.
            continue
        exit_price = observation.get("exit_price") if observation else None
        entry_price = float(row["entry_price_usd"])
        if exit_price is None or exit_price <= 0 or entry_price <= 0:
            repo.fail_perp_structure_shadow_outcome(row["id"], now_iso)
            continue
        ratio = float(exit_price) / entry_price
        if ratio > 10.0 or ratio < 0.1:
            # A real verdict on a real observation: record what was seen and
            # rejected, so this row is not mistaken for one that was never
            # measured (see repo.fail_perp_structure_shadow_outcome).
            repo.fail_perp_structure_shadow_outcome(
                row["id"],
                now_iso,
                exit_price=float(exit_price),
                settlement_price_source=observation.get("price_source"),
            )
            continue
        raw_return = (float(exit_price) - entry_price) / entry_price * 100.0
        directional_return = raw_return if row["direction"] == "long" else -raw_return
        repo.settle_perp_structure_shadow_outcome(
            row["id"],
            exit_price=float(exit_price),
            directional_return_pct=directional_return,
            settled_at=now_iso,
            price_source=observation.get("price_source"),
            settlement_lag_sec=observation.get("settlement_lag_sec"),
        )
        settled += 1
    return settled


def shadow_window_status(*, now: datetime | None = None) -> dict:
    """Report whether a non-influential 14-day cohort is mature enough to review."""
    from aletheia.storage.sqlite import get_db

    current = now or datetime.now(timezone.utc)
    row = get_db().execute(
        "SELECT MIN(entry_ts) AS started_at, COUNT(*) AS total, "
        "SUM(CASE WHEN status='settled' THEN 1 ELSE 0 END) AS settled "
        "FROM perp_structure_shadow_outcomes"
    ).fetchone()
    started_at = row["started_at"] if row else None
    age_days = 0.0
    if started_at:
        parsed = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (current - parsed).total_seconds() / 86400.0)
    return {
        "started_at": started_at,
        "age_days": round(age_days, 2),
        "minimum_days": 14,
        "total": int(row["total"] or 0) if row else 0,
        "settled": int(row["settled"] or 0) if row else 0,
        "mature_for_review": bool(started_at and age_days >= 14),
        "decision_influence": "disabled",
    }


def evaluate_position_x_shadow(
    *,
    policy_epoch_id: str | None = None,
    horizons: tuple[str, ...] = ("1h", "4h", "24h"),
    minimum_samples: int = 30,
    minimum_days: int = 14,
    minimum_settlement_coverage: float = 0.80,
    max_ingestion_lag_seconds: int = 300,
    now: datetime | None = None,
) -> dict:
    """Compare Position X baselines without granting decision authority."""
    from aletheia.storage.sqlite import get_db

    if minimum_samples < 1 or minimum_days < 0 or max_ingestion_lag_seconds < 0:
        raise ValueError("sample, day, and ingestion-lag gates must be non-negative")
    if not 0 < minimum_settlement_coverage <= 1:
        raise ValueError("minimum_settlement_coverage must be between 0 and 1")
    if not horizons:
        raise ValueError("horizons must be non-empty")

    where = "WHERE o.policy_epoch_id = ?" if policy_epoch_id else ""
    params = (policy_epoch_id,) if policy_epoch_id else ()
    rows = [dict(row) for row in get_db().execute(
        f"""
        SELECT o.*, s.as_of_time AS snapshot_as_of_time,
               s.observed_at AS snapshot_observed_at,
               s.source_hash AS snapshot_source_hash,
               s.composite_z, s.dimension_scores_json, s.risk_context_json
        FROM perp_structure_shadow_outcomes o
        JOIN perp_structure_snapshots s ON s.id = o.snapshot_id
        {where}
        ORDER BY o.entry_ts, o.symbol, o.horizon
        """,
        params,
    ).fetchall()]
    epoch_ids = sorted({str(row["policy_epoch_id"]) for row in rows})
    if policy_epoch_id is None and len(epoch_ids) > 1:
        return {
            "status": "invalid_cohort",
            "reason": "policy_epoch_id is required when multiple epochs exist",
            "policy_epoch_ids": epoch_ids,
            "decision_influence": "disabled",
        }
    resolved_epoch_id = policy_epoch_id or (epoch_ids[0] if epoch_ids else None)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    results: dict[str, dict] = {}
    all_ready = True
    for horizon in horizons:
        cohort = [row for row in rows if row["horizon"] == horizon]
        eligible: list[tuple[dict, float, dict[str, float | None]]] = []
        exclusions: dict[str, int] = {}
        for row in cohort:
            if row["status"] != "settled":
                continue
            reason = _prospective_exclusion(row, max_ingestion_lag_seconds)
            if reason:
                exclusions[reason] = exclusions.get(reason, 0) + 1
                continue
            raw_return = (float(row["exit_price_usd"]) / float(row["entry_price_usd"]) - 1.0) * 100.0
            eligible.append((row, raw_return, _position_x_baseline_scores(row)))

        started_at = min((row["entry_ts"] for row, _, _ in eligible), default=None)
        age_days = 0.0
        if started_at:
            age_days = max(0.0, (current - _parse_time(started_at)).total_seconds() / 86400.0)
        coverage = len(eligible) / len(cohort) if cohort else 0.0
        contracts = {_feature_contract_identity(row) for row, _, _ in eligible}
        strata: dict[str, int] = {}
        batch_ids: set[str] = set()
        for row, _, _ in eligible:
            risk = _json_object(row.get("risk_context_json"))
            stratum = str(risk.get("availability_stratum") or "unknown")
            strata[stratum] = strata.get(stratum, 0) + 1
            if risk.get("batch_id"):
                batch_ids.add(str(risk["batch_id"]))
        contract_consistent = len(contracts) == 1 and None not in contracts
        provenance_complete = bool(batch_ids) and all(row.get("source_hash") for row, _, _ in eligible)
        ready = (
            len(eligible) >= minimum_samples
            and age_days >= minimum_days
            and coverage >= minimum_settlement_coverage
            and contract_consistent
            and provenance_complete
        )
        all_ready = all_ready and ready
        baseline_names = ("weighted_prior", "equal_weight", "cgo", "vap", "skw", "lfa", "wp")
        baseline_results = {
            name: _evaluate_baseline(eligible, name) for name in baseline_names
        }
        baseline_results["no_structure"] = {
            "samples": len(eligible),
            "score_coverage": 1.0 if eligible else 0.0,
            "mean_directional_return_pct": 0.0 if eligible else None,
            "median_directional_return_pct": 0.0 if eligible else None,
            "hit_rate": None,
            "spearman_ic": None,
        }
        results[horizon] = {
            "status": "ready_for_research_review" if ready else "insufficient_data",
            "total": len(cohort),
            "settled": sum(row["status"] == "settled" for row in cohort),
            "eligible": len(eligible),
            "settlement_coverage": round(coverage, 4),
            "started_at": started_at,
            "age_days": round(age_days, 2),
            "exclusions": exclusions,
            "feature_contracts_consistent": contract_consistent,
            "feature_contract_count": len(contracts),
            "provenance_complete": provenance_complete,
            "availability_strata": strata,
            "batch_count": len(batch_ids),
            "baselines": baseline_results,
        }

    return {
        "status": "ready_for_research_review" if rows and all_ready else "insufficient_data",
        "policy_epoch_id": resolved_epoch_id,
        "gates": {
            "minimum_samples_per_horizon": minimum_samples,
            "minimum_days": minimum_days,
            "minimum_settlement_coverage": minimum_settlement_coverage,
            "max_ingestion_lag_seconds": max_ingestion_lag_seconds,
        },
        "horizons": results,
        "economics": {
            "gross_directional_status": (
                "available" if any(result["eligible"] for result in results.values()) else "unavailable"
            ),
            "cost_aware_status": "unavailable",
            "reason": "perp structure outcomes do not persist fee, spread, slippage, or funding costs",
        },
        "decision_influence": "disabled",
    }


def _prospective_exclusion(row: dict, max_ingestion_lag_seconds: int) -> str | None:
    try:
        as_of = _parse_time(row["snapshot_as_of_time"])
        observed = _parse_time(row["snapshot_observed_at"])
        entry = _parse_time(row["entry_ts"])
        due = _parse_time(row["due_at"])
        settled = _parse_time(row["settled_at"])
        entry_price = float(row["entry_price_usd"])
        exit_price = float(row["exit_price_usd"])
    except (TypeError, ValueError):
        return "invalid_fields"
    if not all(math.isfinite(value) and value > 0 for value in (entry_price, exit_price)):
        return "invalid_price"
    if abs((entry - as_of).total_seconds()) > 1:
        return "entry_snapshot_mismatch"
    if observed < as_of:
        return "observed_before_source"
    if (observed - as_of).total_seconds() > max_ingestion_lag_seconds:
        return "ingestion_lag_exceeded"
    if observed >= due:
        return "observed_after_due"
    if settled < due:
        return "settled_before_due"
    if not row.get("settlement_price_source"):
        return "missing_settlement_source"
    if not row.get("source_hash") or row.get("source_hash") != row.get("snapshot_source_hash"):
        return "source_identity_mismatch"
    return None


def _position_x_baseline_scores(row: dict) -> dict[str, float | None]:
    dimensions = _json_object(row.get("dimension_scores_json"))
    risk = _json_object(row.get("risk_context_json"))
    complete = risk.get("availability_stratum") == "cgo+vap+skw+lfa+wp"

    scores = {
        "weighted_prior": _finite_number(row.get("composite_z")) if complete else None,
        "equal_weight": _finite_number(risk.get("equal_weight_score")) if complete else None,
    }
    for name in ("cgo", "vap", "skw", "lfa", "wp"):
        value = dimensions.get(name) if isinstance(dimensions, dict) else None
        percentile = _finite_number(value.get("percentile")) if isinstance(value, dict) else None
        scores[name] = (percentile - 50.0) / 100.0 if percentile is not None else None
    return scores


def _evaluate_baseline(
    eligible: list[tuple[dict, float, dict[str, float | None]]],
    name: str,
) -> dict:
    scored = [(scores[name], raw_return) for _, raw_return, scores in eligible if scores[name] is not None]
    directional = [raw_return * (1.0 if score > 0 else -1.0 if score < 0 else 0.0) for score, raw_return in scored]
    return {
        "samples": len(scored),
        "score_coverage": round(len(scored) / len(eligible), 4) if eligible else 0.0,
        "mean_directional_return_pct": round(mean(directional), 6) if directional else None,
        "median_directional_return_pct": round(median(directional), 6) if directional else None,
        "hit_rate": round(sum(value > 0 for value in directional) / len(directional), 4) if directional else None,
        "spearman_ic": _spearman([score for score, _ in scored], [value for _, value in scored]),
    }


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3:
        return None
    left_ranks = _average_ranks(left)
    right_ranks = _average_ranks(right)
    left_mean = mean(left_ranks)
    right_mean = mean(right_ranks)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_ranks, right_ranks, strict=True))
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left_ranks)
        * sum((value - right_mean) ** 2 for value in right_ranks)
    )
    return round(numerator / denominator, 6) if denominator else None


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        rank = (start + end + 1) / 2.0
        for index, _ in ordered[start:end]:
            ranks[index] = rank
        start = end
    return ranks


def _parse_time(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _finite_number(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _json_object(value: object) -> dict:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _feature_contract_identity(row: dict) -> str | None:
    risk = _json_object(row.get("risk_context_json"))
    if risk.get("benchmark_version") != "position-x-p1-v1" or not isinstance(risk.get("parameters"), dict):
        return None
    return json.dumps(
        {
            "benchmark_version": risk.get("benchmark_version"),
            "parameters": risk.get("parameters"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )

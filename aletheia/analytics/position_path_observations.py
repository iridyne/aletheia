"""Read-only analysis of append-only Position path observations."""

from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from aletheia.storage import repositories as repo
from aletheia.portfolio.position_path_observer import (
    expected_position_path_bucket_bounds,
)
from aletheia.analytics.outcome_integrity import (
    assess_reportable_outcome,
    validated_funding_economics,
)


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _excursions(
    *, direction: str, entry_price: float, rows: list[dict[str, Any]]
) -> tuple[float, float]:
    highs = [float(row["bar_high"]) for row in rows]
    lows = [float(row["bar_low"]) for row in rows]
    if direction == "long":
        mfe = (max(highs) - entry_price) / entry_price * 100.0
        mae = (entry_price - min(lows)) / entry_price * 100.0
    else:
        mfe = (entry_price - min(lows)) / entry_price * 100.0
        mae = (max(highs) - entry_price) / entry_price * 100.0
    return max(0.0, mfe), max(0.0, mae)

def _expected_bucket_bounds(position: dict[str, Any]) -> tuple[int | None, int | None]:
    if str(position.get("status") or "").lower() != "closed":
        if not position.get("closed_at"):
            return None, None
        position = {**position, "status": "closed"}
    return expected_position_path_bucket_bounds(position)


def _path_coverage(
    position: dict[str, Any], observations: list[dict[str, Any]]
) -> tuple[str, str, int, int, list[dict[str, Any]]]:
    first, last = _expected_bucket_bounds(position)
    if str(position.get("status") or "").lower() != "closed":
        return "unavailable", "position_not_closed", 0, 0, []
    if first is None or last is None:
        return "unavailable", "invalid_position_time_bounds", 0, 0, []
    if last < first:
        return (
            "unavailable",
            "holding_shorter_than_one_complete_5m_bucket",
            0,
            0,
            [],
        )
    expected = set(range(first, last + 1, 300_000))
    rows_by_bucket = {
        int(row["bucket_open_ms"]): row
        for row in observations
        if int(row["bucket_open_ms"]) in expected
    }
    available = {
        bucket
        for bucket, row in rows_by_bucket.items()
        if row.get("evidence_status") == "available"
    }
    unavailable = {
        bucket
        for bucket, row in rows_by_bucket.items()
        if row.get("evidence_status") != "available"
    }
    if unavailable:
        return (
            "partial_data",
            "unavailable_5m_buckets",
            len(expected),
            len(available),
            [],
        )
    if expected - available:
        return (
            "partial_data",
            "missing_5m_buckets",
            len(expected),
            len(available),
            [],
        )
    complete_rows = [rows_by_bucket[bucket] for bucket in sorted(expected)]
    return (
        "complete",
        "continuous_post_entry_5m_buckets",
        len(expected),
        len(complete_rows),
        complete_rows,
    )


def _signed_gross(
    *, direction: str, entry: float, exit_price: float, quantity: float
) -> float:
    if direction == "long":
        return (exit_price - entry) * quantity
    return (entry - exit_price) * quantity


def _stop_loss_decomposition(
    position: dict[str, Any],
    *,
    coverage_status: str,
    coverage_reason: str,
    expected_bucket_count: int,
    covered_bucket_count: int,
    mfe_pct: float | None,
    mae_pct: float | None,
    giveback_pct: float | None,
) -> dict[str, Any]:
    if str(position.get("close_reason") or "").lower() != "stop_loss":
        return {
            "status": "not_applicable",
            "root_cause": None,
            "reason": "close_reason_is_not_stop_loss",
        }

    entry = _finite(position.get("entry_price"))
    exit_price = _finite(
        position.get("current_price")
        if position.get("current_price") is not None
        else position.get("exit_price")
    )
    quantity = _finite(position.get("quantity"))
    realized = _finite(position.get("realized_pnl"))
    entry_fee = _finite(position.get("entry_fee"))
    exit_fee = _finite(position.get("exit_fee"))
    direction = str(position.get("direction") or "").lower()
    market_type = str(position.get("market_type") or "").lower()
    verified_funding, _funding_source = validated_funding_economics(
        market_type=market_type,
        execution_mode=position.get("execution_mode"),
        funding_cost=position.get("funding_cost"),
        funding_source=position.get("funding_source"),
    )
    price_verified = (
        bool(position.get("entry_fill_verified"))
        and bool(position.get("exit_fill_verified"))
        and str(position.get("entry_price_source") or "")
        in {"broker_fill", "local_fill"}
        and str(position.get("exit_price_source") or "")
        in {"broker_fill", "local_fill"}
    )
    cost_complete = (
        entry_fee is not None
        and entry_fee >= 0
        and exit_fee is not None
        and exit_fee >= 0
        and verified_funding is not None
    )
    economics_complete = (
        entry is not None
        and entry > 0
        and exit_price is not None
        and exit_price > 0
        and quantity is not None
        and quantity > 0
        and realized is not None
        and direction in {"long", "short"}
    )
    gross = (
        _signed_gross(
            direction=direction,
            entry=entry,
            exit_price=exit_price,
            quantity=quantity,
        )
        if economics_complete
        else None
    )
    total_cost = (
        entry_fee + exit_fee + verified_funding
        if cost_complete
        and entry_fee is not None
        and exit_fee is not None
        and verified_funding is not None
        else None
    )
    accounting_delta = (
        realized - (gross - total_cost)
        if gross is not None and total_cost is not None and realized is not None
        else None
    )
    accounting_tolerance = (
        max(1e-8, abs(gross - total_cost) * 1e-8, entry * quantity * 1e-10)
        if gross is not None
        and total_cost is not None
        and entry is not None
        and quantity is not None
        else None
    )
    common = {
        "gross_pnl": round(gross, 8) if gross is not None else None,
        "realized_net_pnl": realized,
        "verified_total_cost": round(total_cost, 8) if total_cost is not None else None,
        "accounting_delta": (
            round(accounting_delta, 8) if accounting_delta is not None else None
        ),
        "cost_evidence_status": "complete" if cost_complete else "unavailable",
        "price_evidence_status": "verified" if price_verified else "unavailable",
        "accounting_evidence_status": (
            "complete" if price_verified and cost_complete else "unavailable"
        ),
        "mfe_pct": round(mfe_pct, 4) if mfe_pct is not None else None,
        "mae_pct": round(mae_pct, 4) if mae_pct is not None else None,
        "giveback_to_exit_pct": (
            round(giveback_pct, 4) if giveback_pct is not None else None
        ),
        "path_coverage_status": coverage_status,
        "path_coverage_reason": coverage_reason,
        "expected_bucket_count": expected_bucket_count,
        "covered_bucket_count": covered_bucket_count,
        "missing_bucket_count": max(0, expected_bucket_count - covered_bucket_count),
    }
    if realized is not None and realized >= 0:
        return {
            "status": "not_applicable",
            "root_cause": "not_a_loss",
            "reason": "stop_loss_close_did_not_realize_a_net_loss",
            **common,
        }
    if coverage_status != "complete":
        return {
            "status": (
                "partial_data" if coverage_status == "partial_data" else "unavailable"
            ),
            "root_cause": "unknown",
            "reason": coverage_reason,
            **common,
        }
    if (
        price_verified
        and cost_complete
        and accounting_delta is not None
        and accounting_tolerance is not None
        and abs(accounting_delta) > accounting_tolerance
    ):
        return {
            "status": "available",
            "root_cause": "accounting_mismatch",
            "reason": "realized_pnl_differs_from_verified_economics",
            **common,
        }
    if gross is not None and gross > 0 and realized is not None and realized < 0:
        if not price_verified or not cost_complete:
            return {
                "status": "unavailable",
                "root_cause": "unknown",
                "reason": "cost_flip_requires_verified_close_and_cost_evidence",
                **common,
            }
        return {
            "status": "available",
            "root_cause": "cost_flip",
            "reason": "gross_profit_became_net_loss_after_verified_costs",
            **common,
        }
    if not economics_complete:
        return {
            "status": "unavailable",
            "root_cause": "unknown",
            "reason": "incomplete_close_economics",
            **common,
        }
    if not price_verified or not cost_complete:
        return {
            "status": "unavailable",
            "root_cause": "unknown",
            "reason": "path_classification_requires_verified_close_economics",
            **common,
        }
    if mfe_pct is None or mae_pct is None:
        return {
            "status": "unavailable",
            "root_cause": "unknown",
            "reason": "complete_path_excursions_unavailable",
            **common,
        }
    if mfe_pct > 0:
        return {
            "status": "available",
            "root_cause": "profit_giveback",
            "reason": "observed_favorable_excursion_ended_in_stop_loss",
            **common,
        }
    return {
        "status": "available",
        "root_cause": "never_favorable_after_entry",
        "reason": "bad_entry_candidate_no_observed_favorable_excursion",
        **common,
    }


def build_position_path_report(
    position_id: str,
    *,
    limit: int = 10_000,
    as_of: datetime | str | None = None,
) -> dict[str, Any]:
    """Summarize one Position without inventing unavailable path facts."""

    position = repo.get_position(position_id)
    if position is None:
        return {
            "position_id": position_id,
            "as_of_at": as_of.isoformat() if isinstance(as_of, datetime) else as_of,
            "status": "unavailable",
            "reason": "position_not_found",
            "observations": [],
        }
    observations = repo.list_position_path_observations_unified(
        position_id,
        limit=limit,
        as_of=as_of,
    )
    available = [
        row for row in observations if row.get("evidence_status") == "available"
    ]
    unavailable_reasons = Counter(
        str(row.get("evidence_reason") or "unknown")
        for row in observations
        if row.get("evidence_status") != "available"
    )
    (
        coverage_status,
        coverage_reason,
        expected_bucket_count,
        covered_bucket_count,
        complete_rows,
    ) = _path_coverage(position, observations)

    entry = _finite(position.get("entry_price"))
    exit_price = _finite(
        position.get("current_price")
        if position.get("current_price") is not None
        else position.get("exit_price")
    )
    direction = str(position.get("direction") or "").lower()
    mfe_pct = mae_pct = giveback_to_last_pct = giveback_to_exit_pct = None
    if (
        coverage_status == "complete"
        and complete_rows
        and entry is not None
        and entry > 0
        and direction in {"long", "short"}
    ):
        mfe_pct, mae_pct = _excursions(
            direction=direction, entry_price=entry, rows=complete_rows
        )
        last_close = float(complete_rows[-1]["bar_close"])
        last_return_pct = (
            (last_close - entry) / entry * 100.0
            if direction == "long"
            else (entry - last_close) / entry * 100.0
        )
        giveback_to_last_pct = max(0.0, mfe_pct - last_return_pct)
        if exit_price is not None and exit_price > 0:
            exit_return_pct = (
                (exit_price - entry) / entry * 100.0
                if direction == "long"
                else (entry - exit_price) / entry * 100.0
            )
            giveback_to_exit_pct = max(0.0, mfe_pct - exit_return_pct)

    decomposition = _stop_loss_decomposition(
        position,
        coverage_status=coverage_status,
        coverage_reason=coverage_reason,
        expected_bucket_count=expected_bucket_count,
        covered_bucket_count=covered_bucket_count,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        giveback_pct=giveback_to_exit_pct,
    )
    plan_versions = sorted(
        {
            version
            for version in (int(row.get("exit_plan_version") or 0) for row in observations)
            if version > 0
        }
    )
    return {
        "position_id": position_id,
        "as_of_at": as_of.isoformat() if isinstance(as_of, datetime) else as_of,
        "position_status": position.get("status"),
        "symbol": position.get("symbol"),
        "market_type": position.get("market_type"),
        "direction": position.get("direction"),
        "opened_at": position.get("opened_at"),
        "closed_at": position.get("closed_at"),
        "close_reason": position.get("close_reason"),
        "realized_pnl": position.get("realized_pnl"),
        "observation_count": len(observations),
        "available_count": len(available),
        "unavailable_count": len(observations) - len(available),
        "unavailable_reasons": dict(sorted(unavailable_reasons.items())),
        "first_bucket_open_ms": (
            observations[0]["bucket_open_ms"] if observations else None
        ),
        "last_bucket_open_ms": (
            observations[-1]["bucket_open_ms"] if observations else None
        ),
        "path_evidence_status": coverage_status,
        "path_evidence_reason": coverage_reason,
        "expected_bucket_count": expected_bucket_count,
        "covered_bucket_count": covered_bucket_count,
        "missing_bucket_count": max(0, expected_bucket_count - covered_bucket_count),
        "mfe_observed_pct": round(mfe_pct, 4) if mfe_pct is not None else None,
        "mae_observed_pct": round(mae_pct, 4) if mae_pct is not None else None,
        "max_giveback_to_last_observation_pct": (
            round(giveback_to_last_pct, 4)
            if giveback_to_last_pct is not None
            else None
        ),
        "giveback_to_exit_pct": (
            round(giveback_to_exit_pct, 4)
            if giveback_to_exit_pct is not None
            else None
        ),
        "stop_loss_decomposition": decomposition,
        "exit_plan_versions_observed": plan_versions,
        "observations": observations,
    }


def _recent_stop_loss_positions(*, cutoff: str, limit: int) -> list[dict[str, Any]]:
    rows = repo.get_db().execute(
        "SELECT * FROM positions "
        "WHERE status='closed' "
        "AND LOWER(COALESCE(close_reason, ''))='stop_loss' "
        "AND closed_at IS NOT NULL AND datetime(closed_at) >= datetime(?) "
        "ORDER BY datetime(closed_at) DESC, rowid DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def build_stop_loss_path_audit(
    *, hours: int = 168, limit: int = 200
) -> dict[str, Any]:
    """Aggregate mutually exclusive stop-loss attribution from persisted facts."""

    hours_i = max(1, int(hours or 168))
    limit_i = max(1, min(int(limit or 200), 10_000))
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(hours=hours_i)
    positions = _recent_stop_loss_positions(
        cutoff=cutoff.isoformat(), limit=limit_i
    )
    root_cause_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    coverage_counts: Counter[str] = Counter()
    exclusion_reason_counts: Counter[str] = Counter()
    classified_count = 0
    unknown_count = 0
    not_a_loss_count = 0
    unverified_net_pnl_count = 0
    excluded_outcome_count = 0
    samples: list[dict[str, Any]] = []

    for position in positions:
        assessment = assess_reportable_outcome(position)
        if not assessment.eligible:
            excluded_outcome_count += 1
            exclusion_reason_counts[assessment.reason] += 1
            samples.append(
                {
                    "position_id": position["id"],
                    "symbol": position.get("symbol"),
                    "closed_at": position.get("closed_at"),
                    "realized_net_pnl": _finite(position.get("realized_pnl")),
                    "included_in_loss_denominator": False,
                    "reportable_outcome": False,
                    "outcome_exclusion_reason": assessment.reason,
                }
            )
            continue
        report = build_position_path_report(str(position["id"]))
        decomposition = report.get("stop_loss_decomposition") or {}
        realized = _finite(position.get("realized_pnl"))
        is_loss = realized is not None and realized < 0
        if realized is None:
            unverified_net_pnl_count += 1
        elif not is_loss:
            not_a_loss_count += 1
        else:
            root_cause = str(decomposition.get("root_cause") or "unknown")
            reason = str(decomposition.get("reason") or "unknown")
            root_cause_counts[root_cause] += 1
            reason_counts[reason] += 1
            coverage_counts[
                str(decomposition.get("path_coverage_status") or "unavailable")
            ] += 1
            if root_cause == "unknown":
                unknown_count += 1
            else:
                classified_count += 1
        samples.append(
            {
                "position_id": position["id"],
                "symbol": position.get("symbol"),
                "closed_at": position.get("closed_at"),
                "realized_net_pnl": realized,
                "included_in_loss_denominator": is_loss,
                "reportable_outcome": True,
                "outcome_exclusion_reason": None,
                **decomposition,
            }
        )

    loss_sample_count = classified_count + unknown_count
    return {
        "generated_at": generated_at.isoformat(),
        "read_only": True,
        "behavior_changed": False,
        "status": (
            "insufficient_data"
            if not positions
            else "partial_data"
            if unknown_count or unverified_net_pnl_count or excluded_outcome_count
            else "available"
        ),
        "window_hours": hours_i,
        "cutoff": cutoff.isoformat(),
        "limit": limit_i,
        "stop_loss_position_count": len(positions),
        "eligible_outcome_count": len(positions) - excluded_outcome_count,
        "excluded_outcome_count": excluded_outcome_count,
        "outcome_exclusion_reasons": dict(sorted(exclusion_reason_counts.items())),
        "loss_sample_count": loss_sample_count,
        "classified_count": classified_count,
        "unknown_count": unknown_count,
        "not_a_loss_count": not_a_loss_count,
        "unverified_net_pnl_count": unverified_net_pnl_count,
        "classified_rate": (
            round(classified_count / loss_sample_count, 4)
            if loss_sample_count
            else None
        ),
        "root_cause_counts": dict(sorted(root_cause_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
        "path_coverage_counts": dict(sorted(coverage_counts.items())),
        "positions": samples,
    }

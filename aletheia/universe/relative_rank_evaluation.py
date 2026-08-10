"""Read-only cluster report for the relative-rank Shadow challenger."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import fmean
import json
from typing import Any

from aletheia.storage.sqlite import get_db


def _longest_days(days: set[date]) -> int:
    longest = current = 0
    previous = None
    for item in sorted(days):
        current = current + 1 if previous and item == previous + timedelta(days=1) else 1
        longest = max(longest, current)
        previous = item
    return longest


def _outcomes(keys: set[str], horizon: str) -> dict[str, dict[str, Any]]:
    if not keys:
        return {}
    db = get_db()
    rows: dict[str, dict[str, Any]] = {}
    ordered = sorted(keys)
    for start in range(0, len(ordered), 500):
        chunk = ordered[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in db.execute(
            f"SELECT * FROM filter_candidate_outcomes WHERE horizon=? AND outcome_candidate_key IN ({placeholders})",
            [horizon, *chunk],
        ).fetchall():
            rows[str(row["outcome_candidate_key"])] = dict(row)
    return rows


def evaluate_relative_rank_shadow_epoch(policy_epoch_id: str) -> dict[str, Any]:
    """Evaluate complete scanner runs; candidates inside a run are not IID."""

    batches = [
        dict(row)
        for row in get_db()
        .execute(
            "SELECT * FROM universe_relative_rank_shadow_batches "
            "WHERE policy_epoch_id=? ORDER BY captured_at, scanner_run_id",
            (policy_epoch_id,),
        )
        .fetchall()
    ]
    report: dict[str, Any] = {
        "policy_epoch_id": policy_epoch_id,
        "authority": "shadow_only",
        "promotion_eligible": False,
        "expected_cluster_n": len(batches),
        "complete_cluster_n": 0,
        "treatment_cluster_n": 0,
        "control_cluster_n": 0,
        "treatment_cluster_utility_mean": None,
        "control_cluster_utility_mean": None,
        "observed_cluster_utility_delta": None,
        "consecutive_eligible_utc_days": 0,
        "blocking_reasons": [],
        "invalid_reasons": [],
        "verdict": "insufficient_data",
    }
    if not batches:
        report["blocking_reasons"] = ["no_prospective_clusters"]
        return report

    first = batches[0]
    identity = tuple(
        first.get(field)
        for field in (
            "experiment_id",
            "behavior_contract_hash",
            "primary_horizon",
            "metric_version",
            "feature_source_fingerprint",
        )
    )
    invalid: set[str] = set()
    parsed: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    keys: set[str] = set()
    for batch in batches:
        if (
            tuple(
                batch.get(field)
                for field in (
                    "experiment_id",
                    "behavior_contract_hash",
                    "primary_horizon",
                    "metric_version",
                    "feature_source_fingerprint",
                )
            )
            != identity
        ):
            invalid.add("mixed_experiment_identity")
        try:
            records = json.loads(str(batch.get("records_json") or "[]"))
        except (TypeError, ValueError):
            invalid.add("records_json_invalid")
            continue
        if not isinstance(records, list) or len(records) != int(batch["candidate_count"]):
            invalid.add("candidate_count_mismatch")
            continue
        parsed.append((batch, records))
        keys.update(
            str(record["outcome_candidate_key"])
            for record in records
            if record.get("assigned_policy_selected") and record.get("outcome_candidate_key")
        )
    outcomes = _outcomes(keys, str(first.get("primary_horizon") or ""))
    utilities = {"treatment": [], "control": []}
    eligible_days: set[date] = set()
    incomplete = unavailable = 0
    for batch, records in parsed:
        if not batch.get("available"):
            unavailable += 1
            continue
        selected = [record for record in records if record.get("assigned_policy_selected")]
        selected_rows = [outcomes.get(str(record.get("outcome_candidate_key"))) for record in selected]
        if any(
            row is None or row.get("status") != "settled" or row.get("net_return_pct") is None for row in selected_rows
        ):
            incomplete += 1
            continue
        arm = str(batch.get("assignment_arm"))
        if arm not in utilities:
            invalid.add("assignment_arm_invalid")
            continue
        denominator = int(batch.get("eligible_count") or 0)
        if denominator <= 0:
            invalid.add("eligible_denominator_invalid")
            continue
        utilities[arm].append(sum(float(row["net_return_pct"]) for row in selected_rows) / denominator)
        try:
            eligible_days.add(date.fromisoformat(str(batch["captured_at"])[:10]))
        except ValueError:
            invalid.add("captured_at_invalid")

    treatment = utilities["treatment"]
    control = utilities["control"]
    treatment_mean = fmean(treatment) if treatment else None
    control_mean = fmean(control) if control else None
    blockers = {"no_actual_authority_intervention", "promotion_not_authorized"}
    if incomplete:
        blockers.add("fixed_horizon_outcomes_incomplete")
    if unavailable:
        blockers.add("minimum_candidate_count_not_met")
    if len(treatment) < 50 or len(control) < 50:
        blockers.add("minimum_cluster_units_not_met")
    consecutive_days = _longest_days(eligible_days)
    if consecutive_days < 7:
        blockers.add("consecutive_utc_dates_not_met")
    report.update(
        complete_cluster_n=len(treatment) + len(control),
        treatment_cluster_n=len(treatment),
        control_cluster_n=len(control),
        treatment_cluster_utility_mean=treatment_mean,
        control_cluster_utility_mean=control_mean,
        observed_cluster_utility_delta=(
            treatment_mean - control_mean if treatment_mean is not None and control_mean is not None else None
        ),
        consecutive_eligible_utc_days=consecutive_days,
        blocking_reasons=sorted(blockers),
        invalid_reasons=sorted(invalid),
        verdict="invalid"
        if invalid
        else (
            "insufficient_data"
            if "minimum_cluster_units_not_met" in blockers
            or "consecutive_utc_dates_not_met" in blockers
            or "fixed_horizon_outcomes_incomplete" in blockers
            else "not_available"
        ),
    )
    return report


__all__ = ["evaluate_relative_rank_shadow_epoch"]

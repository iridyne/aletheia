"""Read-only fixed-look qualification for Candidate Selection Utility V1."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import math
from statistics import mean, stdev
from typing import Any

from aletheia.learning.outcomes import assess_candidate_outcome
from aletheia.storage import repositories as repo
from aletheia.universe.candidate_selection_shadow import (
    CANDIDATE_SELECTION_RECORD_VERSION,
    candidate_selection_training_cutoff,
)


CANDIDATE_SELECTION_QUALIFICATION_VERSION = "candidate-selection-utility-qualification/v1"
_MIN_COMPLETE_CLUSTERS = 50
_MIN_CONSECUTIVE_DAYS = 7
_MIN_FULL_ARM_COVERAGE = 0.80
_LOSS_RATE_MARGIN = 0.05
_MAE_P95_MARGIN_PCT = 0.10
_CVAR_05_MARGIN_PCT = 0.10
_MEAN_COST_MARGIN_PCT = 0.05
_MAX_SYMBOL_SHARE = 0.30


def _parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _cvar(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    count = max(1, math.ceil(len(values) * fraction))
    return mean(sorted(values)[:count])


def _longest_days(days: set[date]) -> int:
    longest = current = 0
    previous = None
    for day in sorted(days):
        current = current + 1 if previous and day == previous + timedelta(days=1) else 1
        longest = max(longest, current)
        previous = day
    return longest


def _rows_for_keys(keys: set[str]) -> dict[str, dict[str, Any]]:
    result = {}
    ordered = sorted(keys)
    for offset in range(0, len(ordered), 500):
        chunk = ordered[offset:offset + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in repo.get_db().execute(
            f"SELECT * FROM filter_candidate_outcomes WHERE horizon='4h' AND outcome_candidate_key IN ({placeholders})",
            chunk,
        ).fetchall():
            result[str(row["outcome_candidate_key"])] = dict(row)
    return result


def _runtime_provenance_reason(row: dict[str, Any]) -> str | None:
    if row.get("status") != "settled":
        return None
    pairs = (
        ("producer_runtime_session_id", "producer_commit", "producer_behavior_fingerprint"),
        ("settlement_runtime_session_id", "settlement_producer_commit", "settlement_behavior_fingerprint"),
    )
    for session_field, commit_field, behavior_field in pairs:
        session_id = row.get(session_field)
        commit = row.get(commit_field)
        behavior = row.get(behavior_field)
        if not session_id or not commit or not behavior:
            return "runtime_provenance_unavailable"
        session = repo.get_runtime_session(str(session_id))
        if (
            session is None
            or session.get("boot_commit") != commit
            or session.get("behavior_fingerprint") != behavior
        ):
            return "runtime_provenance_unattested"
    if row.get("producer_commit") != row.get("settlement_producer_commit"):
        return "mixed_runtime_commit"
    if row.get("producer_behavior_fingerprint") != row.get("settlement_behavior_fingerprint"):
        return "mixed_runtime_behavior"
    return None


def evaluate_candidate_selection_policy(*, as_of_at: str | None = None) -> dict[str, Any]:
    """Evaluate the first outcome-independent fixed look; never admit authority."""

    now = as_of_at or datetime.now(timezone.utc).isoformat()
    latest = repo.get_db().execute(
        "SELECT policy_epoch_id FROM universe_twin_shadow_batches "
        "WHERE record_version=? AND policy_epoch_id IS NOT NULL "
        "ORDER BY captured_at DESC, scanner_run_id DESC LIMIT 1",
        (CANDIDATE_SELECTION_RECORD_VERSION,),
    ).fetchone()
    policy_epoch_id = str(latest["policy_epoch_id"]) if latest else None
    training_cutoff = (
        candidate_selection_training_cutoff(policy_epoch_id=policy_epoch_id)
        if policy_epoch_id else None
    )
    report: dict[str, Any] = {
        "version": CANDIDATE_SELECTION_QUALIFICATION_VERSION,
        "authority": "shadow_only",
        "promotion_eligible": False,
        "owner_admission_required": True,
        "training_cutoff": training_cutoff,
        "policy_epoch_id": policy_epoch_id,
        "selection_policy": None,
        "expected_cluster_n": 0,
        "complete_cluster_n": 0,
        "fixed_look_cluster_n": 0,
        "consecutive_eligible_utc_days": 0,
        "full_arm_outcome_coverage": 0.0,
        "target_cluster_utility_mean": None,
        "legacy_cluster_utility_mean": None,
        "paired_utility_delta": None,
        "paired_utility_lower_bound": None,
        "harm_metrics": {},
        "blocking_reasons": [],
        "invalid_reasons": [],
        "outcome_exclusion_counts": {},
        "verdict": "insufficient_data",
        "as_of_at": now,
    }
    if training_cutoff is None:
        report["blocking_reasons"] = ["estimator_training_support_not_met"]
        return report
    batches = [
        dict(row) for row in repo.get_db().execute(
            "SELECT * FROM universe_twin_shadow_batches WHERE record_version=? "
            "AND policy_epoch_id=? AND captured_at>=? ORDER BY captured_at, scanner_run_id",
            (CANDIDATE_SELECTION_RECORD_VERSION, policy_epoch_id, training_cutoff),
        ).fetchall()
    ]
    report["expected_cluster_n"] = len(batches)
    if not batches:
        report["blocking_reasons"] = ["no_post_freeze_clusters"]
        return report
    first = batches[0]
    identity_fields = (
        "policy_epoch_id", "behavior_contract_hash", "shadow_behavior_version",
        "primary_horizon", "outcome_contract_version", "metric_version",
    )
    invalid: set[str] = set()
    try:
        from aletheia.config.schema import UniverseConfig
        from aletheia.universe.candidate_selection_authority import (
            candidate_selection_policy_identity,
        )

        frozen_policy = json.loads(str(first["policy_json"]))
        report["selection_policy"] = frozen_policy.get("selection_policy")
        if report["selection_policy"] is None:
            frozen_config = UniverseConfig.model_validate(frozen_policy["universe"])
            report["selection_policy"] = candidate_selection_policy_identity(frozen_config)
    except (KeyError, TypeError, ValueError):
        invalid.add("selection_policy_identity_invalid")
    parsed = []
    all_keys: set[str] = set()
    for batch in batches:
        if tuple(batch.get(field) for field in identity_fields) != tuple(first.get(field) for field in identity_fields):
            invalid.add("mixed_policy_identity")
        rows = repo.list_universe_twin_shadow_records(str(batch["scanner_run_id"]))
        if len(rows) != int(batch.get("candidate_count") or 0):
            invalid.add("incomplete_cluster_records")
            continue
        records = []
        for row in rows:
            try:
                payload = json.loads(row["compact_output_json"])
                decision = payload["candidate_selection"]
                keys = payload["outcome_candidate_keys"]
                contract = payload["evidence_contract"]
            except (KeyError, TypeError, ValueError):
                invalid.add("candidate_selection_record_invalid")
                continue
            if contract.get("estimator_training_cutoff") != training_cutoff:
                invalid.add("estimator_training_cutoff_mismatch")
            if contract.get("behavior_contract_hash") != first.get("behavior_contract_hash"):
                invalid.add("behavior_contract_hash_mismatch")
            if not isinstance(keys, dict):
                invalid.add("outcome_key_map_invalid")
                continue
            all_keys.update(str(value) for value in keys.values())
            records.append((decision, keys, payload.get("target_selected") is True))
        parsed.append((batch, records))
    outcomes = _rows_for_keys(all_keys)
    observed_commits: set[str] = set()
    observed_behaviors: set[str] = set()
    clusters = []
    for batch, records in parsed:
        captured = _parse_time(batch.get("captured_at"))
        if captured is None:
            invalid.add("captured_at_invalid")
            continue
        target_values: list[float] = []
        legacy_values: list[float] = []
        target_mae: list[float] = []
        legacy_mae: list[float] = []
        target_costs: list[float] = []
        legacy_costs: list[float] = []
        target_symbols: list[str] = []
        legacy_symbols: list[str] = []
        cluster_complete = True
        cluster_expected_outcome_n = cluster_complete_outcome_n = 0
        cluster_exclusions: Counter[str] = Counter()
        try:
            capacity = int(json.loads(str(batch["policy_json"]))["universe"]["max_candidates"])
        except (KeyError, TypeError, ValueError):
            capacity = 0
        if capacity <= 0:
            invalid.add("candidate_capacity_invalid")
            continue
        for decision, keys, target_selected in records:
            for key in keys.values():
                cluster_expected_outcome_n += 1
                row = outcomes.get(str(key))
                provenance_reason = _runtime_provenance_reason(row) if row else None
                if provenance_reason:
                    invalid.add(provenance_reason)
                    cluster_exclusions[provenance_reason] += 1
                    cluster_complete = False
                    continue
                if row and row.get("status") == "settled":
                    observed_commits.add(str(row["producer_commit"]))
                    observed_behaviors.add(str(row["producer_behavior_fingerprint"]))
                assessment = assess_candidate_outcome(row, as_of_at=now) if row else None
                if assessment and assessment.eligible:
                    cluster_complete_outcome_n += 1
                else:
                    cluster_exclusions[assessment.reason if assessment else "missing_outcome"] += 1
            legacy = decision.get("legacy_control") or {}
            target_direction = decision.get("primary_direction")
            legacy_direction = legacy.get("primary_direction")
            selections = (
                (target_selected, target_direction, target_values, target_mae, target_costs, target_symbols),
                (legacy.get("authority_selected") is True, legacy_direction, legacy_values, legacy_mae, legacy_costs, legacy_symbols),
            )
            for selected, direction, values, maes, costs, symbols in selections:
                if not selected:
                    continue
                key = keys.get(direction) if direction in {"long", "short"} else None
                row = outcomes.get(str(key)) if key else None
                assessment = assess_candidate_outcome(row, as_of_at=now) if row else None
                if not assessment or not assessment.eligible or assessment.net_return_pct is None:
                    cluster_complete = False
                    continue
                mae = row.get("mae_pct")
                cost_fields = (
                    row.get("estimated_fee_pct"),
                    row.get("estimated_spread_slippage_pct"),
                    row.get("estimated_funding_pct"),
                )
                if (
                    not isinstance(mae, (int, float))
                    or not math.isfinite(float(mae))
                    or any(
                        not isinstance(value, (int, float)) or not math.isfinite(float(value))
                        for value in cost_fields
                    )
                ):
                    cluster_complete = False
                    continue
                values.append(float(assessment.net_return_pct))
                maes.append(abs(float(mae)))
                cost = sum(float(value) for value in cost_fields)
                costs.append(cost)
                symbols.append(str(row.get("symbol") or ""))
        clusters.append({
            "captured_at": captured,
            "complete": cluster_complete,
            "expected_outcome_n": cluster_expected_outcome_n,
            "complete_outcome_n": cluster_complete_outcome_n,
            "exclusion_counts": cluster_exclusions,
            "target": sum(target_values) / capacity,
            "legacy": sum(legacy_values) / capacity,
            "target_values": target_values,
            "legacy_values": legacy_values,
            "target_mae": target_mae,
            "legacy_mae": legacy_mae,
            "target_costs": target_costs,
            "legacy_costs": legacy_costs,
            "target_symbols": target_symbols,
            "legacy_symbols": legacy_symbols,
        })
    if len(observed_commits) != 1:
        invalid.add("mixed_runtime_commit")
    if len(observed_behaviors) != 1:
        invalid.add("mixed_runtime_behavior")
    report["runtime_provenance"] = {
        "producer_commit": next(iter(observed_commits)) if len(observed_commits) == 1 else None,
        "behavior_fingerprint": next(iter(observed_behaviors)) if len(observed_behaviors) == 1 else None,
    }
    if (report.get("selection_policy") or {}).get("runtime_identity") != report["runtime_provenance"]:
        invalid.add("runtime_policy_identity_mismatch")
    fixed_look = []
    fixed_look_population = []
    days: set[date] = set()
    for cluster in clusters:
        fixed_look_population.append(cluster)
        days.add(cluster["captured_at"].date())
        if cluster["complete"]:
            fixed_look.append(cluster)
        if (
            len(fixed_look_population) >= _MIN_COMPLETE_CLUSTERS
            and _longest_days(days) >= _MIN_CONSECUTIVE_DAYS
        ):
            break
    expected_outcome_n = sum(cluster["expected_outcome_n"] for cluster in fixed_look_population)
    complete_outcome_n = sum(cluster["complete_outcome_n"] for cluster in fixed_look_population)
    coverage = complete_outcome_n / expected_outcome_n if expected_outcome_n else 0.0
    exclusion_counts: Counter[str] = Counter()
    for cluster in fixed_look_population:
        exclusion_counts.update(cluster["exclusion_counts"])
    blockers = set()
    if coverage < _MIN_FULL_ARM_COVERAGE:
        blockers.add("full_arm_outcome_coverage_below_0_80")
    if len(fixed_look) < _MIN_COMPLETE_CLUSTERS:
        blockers.add("minimum_complete_clusters_not_met")
    consecutive_days = _longest_days(days)
    if consecutive_days < _MIN_CONSECUTIVE_DAYS:
        blockers.add("consecutive_utc_dates_not_met")
    deltas = [cluster["target"] - cluster["legacy"] for cluster in fixed_look]
    target_utility = [cluster["target"] for cluster in fixed_look]
    legacy_utility = [cluster["legacy"] for cluster in fixed_look]
    target_values = [value for cluster in fixed_look for value in cluster["target_values"]]
    legacy_values = [value for cluster in fixed_look for value in cluster["legacy_values"]]
    target_mae = [value for cluster in fixed_look for value in cluster["target_mae"]]
    legacy_mae = [value for cluster in fixed_look for value in cluster["legacy_mae"]]
    target_costs = [value for cluster in fixed_look for value in cluster["target_costs"]]
    legacy_costs = [value for cluster in fixed_look for value in cluster["legacy_costs"]]
    delta = mean(deltas) if deltas else None
    lower_bound = (
        delta - 1.645 * stdev(deltas) / math.sqrt(len(deltas))
        if delta is not None and len(deltas) > 1 else None
    )
    target_loss = sum(value < 0 for value in target_values) / len(target_values) if target_values else None
    legacy_loss = sum(value < 0 for value in legacy_values) / len(legacy_values) if legacy_values else None
    target_mae_p95 = _percentile(target_mae, 0.95)
    legacy_mae_p95 = _percentile(legacy_mae, 0.95)
    target_cvar = _cvar(target_values, 0.05)
    legacy_cvar = _cvar(legacy_values, 0.05)
    target_cost = mean(target_costs) if target_costs else None
    legacy_cost = mean(legacy_costs) if legacy_costs else None
    target_symbol_counts = Counter(symbol for cluster in fixed_look for symbol in cluster["target_symbols"])
    target_symbol_total = sum(target_symbol_counts.values())
    max_symbol_share = max(target_symbol_counts.values(), default=0) / target_symbol_total if target_symbol_total else None
    harm_reasons = []
    if target_loss is None or legacy_loss is None or target_loss - legacy_loss > _LOSS_RATE_MARGIN:
        harm_reasons.append("loss_rate_noninferiority_failed")
    if target_mae_p95 is None or legacy_mae_p95 is None or target_mae_p95 - legacy_mae_p95 > _MAE_P95_MARGIN_PCT:
        harm_reasons.append("mae_p95_noninferiority_failed")
    if target_cvar is None or legacy_cvar is None or target_cvar < legacy_cvar - _CVAR_05_MARGIN_PCT:
        harm_reasons.append("cvar_05_noninferiority_failed")
    if target_cost is None or legacy_cost is None or target_cost > legacy_cost + _MEAN_COST_MARGIN_PCT:
        harm_reasons.append("cost_noninferiority_failed")
    if max_symbol_share is None or max_symbol_share > _MAX_SYMBOL_SHARE:
        harm_reasons.append("symbol_concentration_failed")
    if any(value < -15.0 for value in target_values):
        harm_reasons.append("catastrophic_tail_floor_crossed")
    ready = not blockers and not invalid and lower_bound is not None
    if invalid:
        verdict = "invalid"
    elif not ready:
        verdict = "insufficient_data"
    elif harm_reasons or (delta is not None and delta < 0 and lower_bound < 0):
        verdict = "negative"
    elif lower_bound > 0 and mean(target_utility) > mean(legacy_utility):
        verdict = "positive"
    else:
        verdict = "neutral"
    report.update(
        complete_cluster_n=sum(cluster["complete"] for cluster in clusters),
        fixed_look_cluster_n=len(fixed_look),
        consecutive_eligible_utc_days=consecutive_days,
        full_arm_outcome_coverage=coverage,
        target_cluster_utility_mean=mean(target_utility) if target_utility else None,
        legacy_cluster_utility_mean=mean(legacy_utility) if legacy_utility else None,
        paired_utility_delta=delta,
        paired_utility_lower_bound=lower_bound,
        harm_metrics={
            "target_loss_rate": target_loss,
            "legacy_loss_rate": legacy_loss,
            "target_mae_p95_pct": target_mae_p95,
            "legacy_mae_p95_pct": legacy_mae_p95,
            "target_cvar_05_pct": target_cvar,
            "legacy_cvar_05_pct": legacy_cvar,
            "target_mean_cost_pct": target_cost,
            "legacy_mean_cost_pct": legacy_cost,
            "target_max_symbol_share": max_symbol_share,
            "noninferiority_reasons": sorted(harm_reasons),
        },
        blocking_reasons=sorted(blockers),
        invalid_reasons=sorted(invalid),
        outcome_exclusion_counts=dict(sorted(exclusion_counts.items())),
        verdict=verdict,
        qualification_eligible=verdict == "positive",
        promotion_eligible=verdict == "positive",
    )
    return report


__all__ = ["CANDIDATE_SELECTION_QUALIFICATION_VERSION", "evaluate_candidate_selection_policy"]

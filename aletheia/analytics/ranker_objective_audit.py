"""Read-only audit of persisted Ranker ordering against fixed-horizon outcomes.

The audit analyzes only durable shortlist evidence.  It never runs the Ranker,
loads policy configuration, settles outcomes, or mutates runtime state.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any

_HORIZONS = ("15m", "1h", "4h", "24h")
_ENTRY_ALIGNMENT_MAX_SEC = 60
_LEGACY_EPOCH = "legacy_or_unavailable"
_ADMISSION_REASONS = {
    "duplicate_symbol",
    "open_position_duplicate",
    "active_operator_carrier",
    "opposing_direction_queued",
    "signal_cooldown",
    "signal_queue_capacity",
    "signal_batch_limit",
}
_QUEUE_REASONS = {
    "opposing_direction_queued",
    "signal_cooldown",
    "signal_queue_capacity",
    "signal_batch_limit",
}
_DUPLICATE_REASONS = {
    "duplicate_symbol",
    "open_position_duplicate",
    "active_operator_carrier",
}


def build_ranker_objective_audit(*, hours: int = 24, limit: int = 5000) -> dict:
    """Build a bounded, JSON-serializable report from persisted SQLite facts."""
    import aletheia.storage.sqlite as sqlite_store

    lookback = max(1, int(hours or 24))
    bounded_limit = max(1, int(limit or 5000))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback)).isoformat()
    report = _base_report(lookback, cutoff, bounded_limit)
    try:
        with sqlite_store.readonly_db() as db, _query_only(db, report):
            _populate_report(db, report, cutoff, bounded_limit)
    except (sqlite3.OperationalError, FileNotFoundError) as exc:
        report["schema_errors"].append(str(exc))
    return report


@contextmanager
def _query_only(db, report: dict):
    """Enforce query-only temporarily without changing an injected connection."""
    previous: int | None = None
    try:
        row = db.execute("PRAGMA query_only").fetchone()
        previous = int(row[0]) if row is not None else None
        db.execute("PRAGMA query_only=ON")
        report["query_only_enforced"] = True
    except sqlite3.DatabaseError:
        report["query_only_enforced"] = False
    try:
        yield
    finally:
        if previous is not None:
            try:
                db.execute(f"PRAGMA query_only={previous}")
            except sqlite3.DatabaseError:
                pass


def _populate_report(db, report: dict, cutoff: str, bounded_limit: int) -> None:
    if not _table_exists(db, "shadow_trades"):
        report["exclusions"]["missing_shadow_trades_table"] = 1
        return
    rows, load_details = _load_observations(db, cutoff, bounded_limit)
    report["truncated"] = load_details["truncated"]
    report["persisted_membership_window_boundary_exclusions"] = load_details[
        "persisted_membership_window_boundary_exclusions"
    ]
    report["source_counts"]["persisted_shortlist_rows"] = len(rows)
    if not rows:
        return
    report["candidate_admission_summary"] = _candidate_admission_summary(db, rows)
    comparison_rows, required_exclusions = _eligible_batch_rows(
        rows,
        set(
            load_details["persisted_membership_window_boundary_exclusions"][
                "batch_ids"
            ]
        ),
    )
    report["batch_comparison_exclusions"] = required_exclusions
    report["source_counts"]["batch_comparison_rows"] = len(comparison_rows)
    report["outcome_linkage_exclusions"] = _attach_outcomes(db, comparison_rows)
    _attach_setup_and_late_entry(db, comparison_rows)
    _assign_buckets(comparison_rows)
    report["availability"] = _availability(rows)
    current = _summarize_variant("current_persisted_ranking", comparison_rows)
    current["available"] = True
    report["variants"] = {
        "current_persisted_ranking": current,
        "deduplicated_score_counterfactual": {
            "available": False,
            "reason": "durable score component facts sufficient to remove duplicated upstream contributions are unavailable",
            "sample_count": 0,
        },
    }
    report["populations"] = {
        "candidate_exposure_deduplicated": _summarize_variant(
            "candidate_exposure_deduplicated", _candidate_dedup(comparison_rows)
        ),
        "setup_exposure_deduplicated": _summarize_variant(
            "setup_exposure_deduplicated", _setup_dedup(comparison_rows)
        ),
    }
    report["strata"] = _strata(comparison_rows)
    report["persisted_window_dimensions_available"] = (
        not report["truncated"]
        and not required_exclusions["row_count"]
        and all(
            row[field] != "unavailable"
            for row in comparison_rows
            for field in (
                "family", "direction_value", "market", "duplicate_status",
                "queue_constraint", "late_entry_status",
            )
        )
    )
    report["exclusions"] = _exclusions(rows)
    report["exclusions"]["persisted_membership_cut_through_rows"] = load_details[
        "persisted_membership_window_boundary_exclusions"
    ]["excluded_loaded_row_count"]
    report["exclusions"]["invalid_batch_comparison_rows"] = required_exclusions[
        "row_count"
    ]
    report["status"] = (
        "persisted_window_data_available"
        if report["persisted_window_dimensions_available"]
        else "partial_data"
    )


def render_ranker_objective_audit(report: dict) -> str:
    """Render an explicitly observational, read-only audit summary."""
    window = report.get("window") or {}
    lines = [
        "Ranker Objective Audit — READ-ONLY",
        "Observational persisted-shortlist analysis; production ranking unchanged.",
        "",
        f"Window: {window.get('hours', 24)}h (cutoff {window.get('cutoff', 'n/a')})",
        f"Status: {report.get('status', 'insufficient_data')}",
        f"Intended batch completeness: {report.get('intended_batch_completeness')}",
        f"Writer atomicity: {str(bool(report.get('writer_atomicity'))).lower()}",
        "Within-batch metrics are descriptive over persisted members only; "
        "promotion/action authority: none.",
    ]
    if report.get("truncated"):
        lines.append(f"Evidence was truncated at limit={window.get('limit')}.")
    for error in report.get("schema_errors") or []:
        lines.append(f"Evidence unavailable: {error}")
    boundary = report.get("persisted_membership_window_boundary_exclusions") or {}
    if boundary.get("batch_count"):
        lines.append(
            "Persisted-membership window-boundary exclusions: "
            f"batch_count={boundary['batch_count']} "
            f"batch_ids={','.join(boundary.get('batch_ids') or [])} "
            f"loaded_rows={boundary.get('excluded_loaded_row_count', 0)}"
        )
    lines.append("")
    for name, variant in (report.get("variants") or {}).items():
        lines.append(f"{name}: sample={variant.get('sample_count', 0)} (headlines split by policy epoch)")
        for epoch, epoch_report in (variant.get("by_policy_epoch") or {}).items():
            lines.append(f"  epoch={epoch} sample={epoch_report.get('sample_count', 0)}")
            for batch, batch_report in (epoch_report.get("by_batch") or {}).items():
                lines.append(f"    batch={batch} sample={batch_report.get('sample_count', 0)}")
                for horizon in _HORIZONS:
                    item = (batch_report.get("horizons") or {}).get(horizon) or {}
                    lines.append(
                        f"      {horizon}: settled={item.get('settled_count', 0)} "
                        f"coverage={_fmt(item.get('coverage'))} mean_net={_fmt(item.get('mean_net_return_pct'))}"
                    )
    lines.append("")
    lines.append(
        "Candidate-level admission summary only; authoritative shadow-batch linkage unavailable."
    )
    admission = report.get("candidate_admission_summary") or {}
    for reason, count in sorted((admission.get("reason_counts") or {}).items()):
        lines.append(f"  {reason}: {count}")
    return "\n".join(lines)


def _base_report(hours: int, cutoff: str, limit: int) -> dict:
    return {
        "read_only": True,
        "behavior_changed": False,
        "scope": "persisted_shortlist_only",
        "interpretation": "observational",
        "intended_batch_completeness": "unavailable_on_current_schema",
        "writer_atomicity": False,
        "within_batch_metrics_scope": "descriptive_over_persisted_members_only",
        "actionable": False,
        "promotion_eligible": False,
        "status": "insufficient_data",
        "status_semantics": {
            "persisted_window_data_available": "all loaded persisted rows have every reported stratum dimension and no cutoff/limit boundary cuts through their persisted batch membership; this says nothing about intended shortlist membership",
            "partial_data": "persisted shortlist rows exist, but the window is truncated, a cutoff/limit boundary cuts through persisted membership, or at least one reported stratum dimension is unavailable",
            "insufficient_data": "no persisted shortlist rows were available for descriptive analysis",
        },
        "window": {"hours": hours, "cutoff": cutoff, "limit": limit},
        "source_counts": {"persisted_shortlist_rows": 0},
        "variants": {},
        "populations": {},
        "strata": [],
        "persisted_window_dimensions_available": False,
        "admission_effects": {},
        "ambiguous_admission_count": 0,
        "candidate_admission_summary": {
            "scope": "candidate_level",
            "authoritative_batch_linkage": False,
            "batch_linkage": "unavailable",
            "candidate_count": 0,
            "reason_counts": {},
        },
        "availability": {},
        "exclusions": {},
        "outcome_linkage_exclusions": {},
        "batch_comparison_exclusions": {"row_count": 0, "row_ids": [], "reason_counts": {}},
        "persisted_membership_window_boundary_exclusions": {
            "batch_count": 0,
            "batch_ids": [],
            "excluded_loaded_row_count": 0,
        },
        "truncated": False,
        "schema_errors": [],
        "query_only_enforced": False,
    }


def _load_observations(db, cutoff: str, limit: int) -> tuple[list[dict], dict]:
    cols = _columns(db, "shadow_trades")
    wanted = [
        "id", "batch_id", "opportunity_id", "venue", "symbol", "market_type",
        "direction", "scanner_family", "trade_score", "rank", "was_selected",
        "created_at", "resolved_at", "outcome_candidate_key", "policy_epoch_id",
        "cost_model_version", "entry_price",
    ]
    select = [name if name in cols else f"NULL AS {name}" for name in wanted]
    time_col = "created_at" if "created_at" in cols else None
    where = (
        f"WHERE {time_col} IS NULL OR datetime({time_col}) IS NULL "
        f"OR datetime({time_col}) >= datetime(?)"
        if time_col else ""
    )
    params: tuple[Any, ...] = (cutoff, limit + 1) if time_col else (limit + 1,)
    fetched = db.execute(
        f"SELECT {', '.join(select)} FROM shadow_trades {where} "
        "ORDER BY datetime(created_at) DESC, id ASC LIMIT ?",
        params,
    ).fetchall()
    truncated = len(fetched) > limit
    loaded = fetched[:limit]
    loaded_batch_counts = Counter(
        str(raw["batch_id"]) for raw in loaded if raw["batch_id"] is not None
    )
    boundary_cut_batch_ids: list[str] = []
    if loaded_batch_counts:
        batch_ids = sorted(loaded_batch_counts)
        placeholders = ",".join("?" for _ in batch_ids)
        # Compare loaded membership with all persisted membership for each batch.
        # The lookback cutoff and row limit are loading boundaries; neither may
        # leave a cut-through persisted-membership batch eligible.
        totals = db.execute(
            "SELECT batch_id,COUNT(*) AS row_count FROM shadow_trades "
            f"WHERE batch_id IN ({placeholders}) GROUP BY batch_id",
            tuple(batch_ids),
        ).fetchall()
        boundary_cut_batch_ids = sorted(
            str(item["batch_id"])
            for item in totals
            if int(item["row_count"]) > loaded_batch_counts[str(item["batch_id"])]
        )

    rows = []
    for raw in loaded:
        row = dict(raw)
        row["policy_epoch"] = str(row.get("policy_epoch_id") or _LEGACY_EPOCH)
        row["family"] = str(row.get("scanner_family") or "unavailable")
        row["direction_value"] = str(row.get("direction") or "unavailable").lower()
        row["market"] = _normalize_market(row.get("market_type")) if row.get("market_type") else "unavailable"
        row["canonical_symbol"] = str(row.get("symbol") or "unavailable").upper()
        row["outcomes"] = {}
        row["outcome_states"] = {horizon: "missing" for horizon in _HORIZONS}
        # Selection decisions have no authoritative shadow batch key.  Never
        # project their reasons onto shortlist rows.
        row["duplicate_status"] = "unavailable"
        row["queue_constraint"] = "unavailable"
        row["late_entry_status"] = "unavailable"
        rows.append(row)
    return rows, {
        "truncated": truncated,
        "persisted_membership_window_boundary_exclusions": {
            "batch_count": len(boundary_cut_batch_ids),
            "batch_ids": boundary_cut_batch_ids,
            "excluded_loaded_row_count": sum(
                str(row.get("batch_id")) in boundary_cut_batch_ids for row in rows
            ),
        },
    }


def _eligible_batch_rows(
    rows: list[dict], boundary_cut_batch_ids: set[str]
) -> tuple[list[dict], dict]:
    eligible: list[dict] = []
    reasons: Counter[str] = Counter()
    excluded_ids: list[str] = []
    for row in rows:
        row_reasons: set[str] = set()
        if row.get("id") is None or not str(row["id"]).strip():
            row_reasons.add("missing_row_id")
        if row.get("batch_id") is None or not str(row["batch_id"]).strip():
            row_reasons.add("missing_batch_id")
        elif str(row["batch_id"]) in boundary_cut_batch_ids:
            row_reasons.add("persisted_membership_cut_through")
        if row.get("policy_epoch_id") is None or not str(row["policy_epoch_id"]).strip():
            row_reasons.add("missing_policy_epoch_id")
        if row.get("created_at") is None or not str(row["created_at"]).strip():
            row_reasons.add("missing_required_time")
        elif _parse_time(row["created_at"]) is None:
            row_reasons.add("invalid_required_time")
        if row_reasons:
            reasons.update(row_reasons)
            excluded_ids.append(str(row.get("id") or "unavailable"))
        else:
            eligible.append(row)
    return eligible, {
        "row_count": len(excluded_ids),
        "row_ids": sorted(excluded_ids),
        "reason_counts": dict(sorted(reasons.items())),
    }


def _attach_outcomes(db, rows: list[dict]) -> dict[str, int]:
    required = {
        "outcome_candidate_key", "horizon", "status", "net_return_pct", "settled_at",
        "venue", "symbol", "market_type", "direction", "entry_ts", "cost_model_version",
        "entry_price_usd",
    }
    if not _table_exists(db, "filter_candidate_outcomes"):
        return {"missing_outcome_table": len(rows)} if rows else {}
    if not required <= _columns(db, "filter_candidate_outcomes"):
        return {"missing_outcome_contract_columns": len(rows)} if rows else {}
    keys = sorted(
        {str(row["outcome_candidate_key"]) for row in rows if row.get("outcome_candidate_key")}
    )
    if not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    outcomes = db.execute(
        "SELECT rowid,outcome_candidate_key,horizon,status,net_return_pct,settled_at,"
        "venue,symbol,market_type,direction,entry_ts,cost_model_version,entry_price_usd "
        f"FROM filter_candidate_outcomes WHERE outcome_candidate_key IN ({placeholders}) "
        "ORDER BY outcome_candidate_key,horizon,datetime(settled_at),rowid",
        tuple(keys),
    ).fetchall()
    grouped_outcomes: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for outcome in outcomes:
        horizon = str(outcome["horizon"])
        if horizon in _HORIZONS:
            grouped_outcomes[(str(outcome["outcome_candidate_key"]), horizon)].append(outcome)
    grouped_rows: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("outcome_candidate_key"):
            grouped_rows[str(row["outcome_candidate_key"])].append(row)

    exclusions: Counter[str] = Counter()
    for key, candidate_rows in grouped_rows.items():
        for horizon in _HORIZONS:
            facts = grouped_outcomes.get((key, horizon), [])
            if not facts:
                continue
            aligned: list[dict] = []
            for row in candidate_rows:
                reasons = {
                    reason
                    for fact in facts
                    if (reason := _outcome_linkage_exclusion(row, fact)) is not None
                }
                reason = sorted(reasons)[0] if reasons else None
                if reason:
                    row["outcome_states"][horizon] = reason
                    exclusions[reason] += 1
                else:
                    aligned.append(row)
            if not aligned:
                continue
            first = min(
                aligned,
                key=lambda row: (_parse_time(row["created_at"]), str(row["id"])),
            )
            for repeated in aligned:
                if repeated is not first:
                    repeated["outcome_states"][horizon] = "repeated_exposure"
                    exclusions["repeated_exposure"] += 1
            _apply_outcome_facts(first, horizon, facts)
    return dict(sorted(exclusions.items()))


def _outcome_linkage_exclusion(row: dict, outcome: Any) -> str | None:
    instrument = (
        row.get("venue"), row.get("symbol"), row.get("market_type"), row.get("direction"),
        outcome["venue"], outcome["symbol"], outcome["market_type"], outcome["direction"],
    )
    if any(value is None or not str(value).strip() for value in instrument):
        return "missing_instrument_identity"
    if (
        str(row["venue"]) != str(outcome["venue"])
        or str(row["symbol"]) != str(outcome["symbol"])
        or _normalize_market(row["market_type"]) != _normalize_market(outcome["market_type"])
        or str(row["direction"]).lower() != str(outcome["direction"]).lower()
    ):
        return "instrument_identity_mismatch"
    ranked_entry = _finite(row.get("entry_price"))
    outcome_entry = _finite(outcome["entry_price_usd"])
    if ranked_entry is None or outcome_entry is None:
        return "missing_entry_price_identity"
    if ranked_entry != outcome_entry:
        return "entry_price_mismatch"
    # Cost semantics belong to the canonical settled outcome. The established
    # shadow writer persists rank-time exposure before settlement economics exist,
    # so requiring its nullable projection would reject production-written rows.
    if (
        str(outcome["status"]).lower() == "settled"
        and (outcome["cost_model_version"] is None or not str(outcome["cost_model_version"]).strip())
    ):
        return "missing_cost_model_identity"
    ranked_at = _parse_time(row.get("created_at"))
    entry_at = _parse_time(outcome["entry_ts"])
    if ranked_at is None or entry_at is None:
        return "missing_entry_alignment_time"
    lag_sec = (ranked_at - entry_at).total_seconds()
    if lag_sec < 0 or lag_sec > _ENTRY_ALIGNMENT_MAX_SEC:
        return "entry_alignment_exceeded"
    return None


def _apply_outcome_facts(row: dict, horizon: str, facts: list[Any]) -> None:
    settled = [item for item in facts if str(item["status"]).lower() == "settled"]
    settled_values: set[float] = set()
    settled_has_null = False
    settled_has_malformed = False
    for item in settled:
        raw = item["net_return_pct"]
        if raw is None:
            settled_has_null = True
            continue
        value = _finite(raw)
        if value is None:
            settled_has_malformed = True
            continue
        settled_values.add(value)
    mixed_statuses = any(str(item["status"]).lower() != "settled" for item in facts)
    if (
        settled_has_malformed
        or len(settled_values) > 1
        or (settled_values and settled_has_null)
        or (settled and mixed_statuses)
    ):
        row["outcome_states"][horizon] = "conflict"
    elif len(settled_values) == 1 and len(settled) == len(facts):
        value = next(iter(settled_values))
        row["outcome_states"][horizon] = "settled"
        row["outcomes"][horizon] = value
    elif settled_has_null:
        row["outcome_states"][horizon] = "null"
    elif any(str(item["status"]).lower() in {"failed", "error"} for item in facts):
        row["outcome_states"][horizon] = "failed"
    elif any(str(item["status"]).lower() == "pending" for item in facts):
        row["outcome_states"][horizon] = "pending"
    else:
        row["outcome_states"][horizon] = "conflict"


def _candidate_admission_summary(db, rows: list[dict]) -> dict:
    summary = {
        "scope": "candidate_level",
        "authoritative_batch_linkage": False,
        "batch_linkage": "unavailable",
        "candidate_count": 0,
        "reason_counts": {reason: 0 for reason in sorted(_ADMISSION_REASONS)},
    }
    required = {
        "outcome_candidate_key", "stage", "reason_code", "evaluated_at", "sequence_no",
    }
    if not _table_exists(db, "filter_decisions") or not required <= _columns(db, "filter_decisions"):
        return summary
    keys = sorted(
        {str(row["outcome_candidate_key"]) for row in rows if row.get("outcome_candidate_key")}
    )
    if not keys:
        return summary
    decisions = db.execute(
        "SELECT rowid,outcome_candidate_key,reason_code,evaluated_at,sequence_no "
        "FROM filter_decisions WHERE stage='paper_trader_selection' "
        "AND outcome_candidate_key IN (" + ",".join("?" for _ in keys) + ") "
        "ORDER BY datetime(evaluated_at),sequence_no,rowid",
        tuple(keys),
    ).fetchall()
    first_by_candidate: dict[str, Any] = {}
    for decision in decisions:
        key = str(decision["outcome_candidate_key"] or "")
        if key and key not in first_by_candidate:
            first_by_candidate[key] = decision
    counts = Counter(
        str(decision["reason_code"])
        for decision in first_by_candidate.values()
        if str(decision["reason_code"]) in _ADMISSION_REASONS
    )
    summary["candidate_count"] = len(first_by_candidate)
    summary["reason_counts"] = {
        reason: counts.get(reason, 0) for reason in sorted(_ADMISSION_REASONS)
    }
    return summary


def _attach_setup_and_late_entry(db, rows: list[dict]) -> None:
    if not (_table_exists(db, "opportunities") and _table_exists(db, "universe_members")):
        return
    opp_cols = _columns(db, "opportunities")
    member_cols = _columns(db, "universe_members")
    if not {"id", "universe_member_id"} <= opp_cols or not {"id", "late_entry_penalty", "risk_flags"} <= member_cols:
        return
    ids = sorted({str(row["opportunity_id"]) for row in rows if row.get("opportunity_id")})
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    linked = db.execute(
        "SELECT o.id AS opportunity_id,u.late_entry_penalty,u.risk_flags "
        "FROM opportunities o LEFT JOIN universe_members u ON u.id=o.universe_member_id "
        f"WHERE o.id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    facts = {str(item["opportunity_id"]): dict(item) for item in linked}
    for row in rows:
        fact = facts.get(str(row.get("opportunity_id") or ""))
        if not fact or (fact.get("late_entry_penalty") is None and fact.get("risk_flags") is None):
            continue
        penalty = fact.get("late_entry_penalty")
        flags = _json_value(fact.get("risk_flags"), [])
        explicit = any("late_entry" in str(flag).lower() for flag in flags) if isinstance(flags, list) else False
        if explicit:
            row["late_entry_status"] = "explicit_risk_flag"
        elif penalty is not None and float(penalty) > 0:
            row["late_entry_status"] = "positive_penalty"
        elif penalty is not None:
            row["late_entry_status"] = "not_late"


def _assign_buckets(rows: list[dict]) -> None:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        groups[(str(row.get("batch_id") or "unavailable"), row["policy_epoch"])].append(row)
    for population in groups.values():
        scored = sorted(
            (row for row in population if row.get("trade_score") is not None),
            key=lambda row: (-float(row["trade_score"]), int(row["rank"] or 10**9), str(row["id"])),
        )
        quartile_size = max(1, (len(scored) + 3) // 4) if scored else 0
        for index, row in enumerate(scored):
            row["score_bucket"] = "top_quartile" if index < quartile_size else "remainder"
        for row in population:
            rank = row.get("rank")
            row.setdefault("score_bucket", "unavailable")
            row["rank_bucket"] = (
                "unavailable" if rank is None else "top_1" if int(rank) <= 1 else
                "top_3" if int(rank) <= 3 else "top_10" if int(rank) <= 10 else "remainder"
            )


def _candidate_dedup(rows: list[dict]) -> list[dict]:
    keyed: dict[tuple[str, str], list[dict]] = defaultdict(list)
    missing = []
    for row in rows:
        key = row.get("outcome_candidate_key")
        (keyed[(row["policy_epoch"], str(key))] if key else missing).append(row)
    chosen = [
        min(
            group,
            key=lambda row: (_parse_time(row.get("created_at")), str(row["id"])),
        )
        for group in keyed.values()
    ]
    return sorted(chosen + missing, key=lambda row: str(row["id"]))


def _setup_dedup(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        # Setup identity is meaningful only inside the persisted policy epoch and
        # shortlist exposure batch; never collapse a recurring setup across time.
        grouped[(row["policy_epoch"], str(row.get("batch_id") or row.get("id")), row["canonical_symbol"], row["direction_value"], row["market"], row["family"])].append(row)
    return sorted((_representative(group) for group in grouped.values()), key=lambda row: str(row["id"]))


def _representative(rows: list[dict]) -> dict:
    return min(
        rows,
        key=lambda row: (
            -(float(row["trade_score"]) if row.get("trade_score") is not None else float("-inf")),
            int(row["rank"]) if row.get("rank") is not None else 10**9,
            str(row.get("id") or ""),
            str(row.get("created_at") or ""),
        ),
    )


def _summarize_variant(name: str, rows: list[dict]) -> dict:
    epochs: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        epochs[row["policy_epoch"]].append(row)
    return {
        "label": name,
        "sample_count": len(rows),
        "settled_candidate_count": sum(1 for row in rows if row["outcomes"]),
        "headline_scope": "policy_epoch_only",
        "by_policy_epoch": {
            epoch: _summarize_epoch(group) for epoch, group in sorted(epochs.items())
        },
        "representative_ids": [str(row["id"]) for row in rows],
    }


def _summarize_epoch(rows: list[dict]) -> dict:
    batches: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        batches[str(row.get("batch_id") or "unavailable")].append(row)
    return {
        "sample_count": len(rows),
        "settled_candidate_count": sum(1 for row in rows if row["outcomes"]),
        "headline_metrics_suppressed": len(batches) > 1,
        "by_batch": {
            batch: _summarize_batch(group) for batch, group in sorted(batches.items())
        },
    }


def _summarize_batch(rows: list[dict]) -> dict:
    horizons = {horizon: _horizon_summary(rows, horizon) for horizon in _HORIZONS}
    return {
        "metric_scope": "descriptive_over_persisted_members_only",
        "sample_count": len(rows),
        "settled_candidate_count": sum(1 for row in rows if row["outcomes"]),
        "outcome_coverage": {horizon: horizons[horizon]["coverage"] for horizon in _HORIZONS},
        "outcome_states": {
            horizon: dict(sorted(Counter(row["outcome_states"][horizon] for row in rows).items()))
            for horizon in _HORIZONS
        },
        "horizons": horizons,
        "rank_buckets": _bucket_summaries(rows, "rank_bucket"),
        "score_buckets": _bucket_summaries(rows, "score_bucket"),
        "selection": _bucket_summaries(rows, "selection_status"),
    }


def _horizon_summary(rows: list[dict], horizon: str) -> dict:
    values = [row["outcomes"][horizon] for row in rows if horizon in row["outcomes"]]
    top = [row["outcomes"][horizon] for row in rows if horizon in row["outcomes"] and row["rank_bucket"] in {"top_1", "top_3"}]
    remainder = [row["outcomes"][horizon] for row in rows if horizon in row["outcomes"] and row["rank_bucket"] not in {"top_1", "top_3", "unavailable"}]
    return {
        "candidate_count": len(rows),
        "settled_count": len(values),
        "coverage": _round(len(values) / len(rows)) if rows else None,
        "mean_net_return_pct": _round(mean(values)) if values else None,
        "median_net_return_pct": _round(median(values)) if values else None,
        "top_k_vs_remainder_delta": _round(mean(top) - mean(remainder)) if top and remainder else None,
    }


def _bucket_summaries(rows: list[dict], field: str) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        value = ("selected" if row.get("was_selected") else "not_selected") if field == "selection_status" else str(row[field])
        grouped[value].append(row)
    return [
        {"bucket": bucket, "sample_count": len(group), "horizons": {h: _horizon_summary(group, h) for h in _HORIZONS}}
        for bucket, group in sorted(grouped.items())
    ]


def _strata(rows: list[dict]) -> list[dict]:
    fields = (
        ("policy_epoch", "policy_epoch"), ("scanner_family", "family"),
        ("direction", "direction_value"), ("market_type", "market"),
        ("duplicate_status", "duplicate_status"), ("queue_constraint", "queue_constraint"),
        ("late_entry_status", "late_entry_status"),
    )
    output = []
    combined: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    for row in rows:
        combined[(
            row["policy_epoch"], row["family"], row["direction_value"], row["market"],
            row["duplicate_status"], row["queue_constraint"], row["late_entry_status"],
        )].append(row)
    for values, group in sorted(combined.items()):
        for horizon in _HORIZONS:
            output.append({
                "dimension": "base",
                "policy_epoch": values[0],
                "scanner_family": values[1],
                "direction": values[2],
                "market_type": values[3],
                "duplicate_status": values[4],
                "queue_constraint": values[5],
                "late_entry_status": values[6],
                "horizon": horizon,
                **_horizon_summary(group, horizon),
            })
    for dimension, field in fields:
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            grouped[str(row[field])].append(row)
        for value, group in sorted(grouped.items()):
            for horizon in _HORIZONS:
                output.append({"dimension": dimension, "value": value, "horizon": horizon, **_horizon_summary(group, horizon)})
    output.sort(key=lambda item: (
        item["dimension"], str(item.get("value", "")), item["horizon"],
        str(item.get("policy_epoch", "")), str(item.get("scanner_family", "")),
    ))
    return output


def _availability(rows: list[dict]) -> dict:
    return {
        "trade_score": _available(rows, lambda row: row.get("trade_score") is not None),
        "outcome_candidate_key": _available(rows, lambda row: bool(row.get("outcome_candidate_key"))),
        "settled_net_outcome": _available(rows, lambda row: bool(row["outcomes"])),
        "late_entry": _available(rows, lambda row: row["late_entry_status"] != "unavailable"),
        "queue_constraint": _available(rows, lambda row: row["queue_constraint"] != "unavailable"),
    }


def _available(rows: list[dict], predicate) -> dict:
    count = sum(1 for row in rows if predicate(row))
    return {"available": count, "unavailable": len(rows) - count}


def _admission_effects(rows: list[dict]) -> dict:
    counts = Counter(row["admission_reason"] for row in rows if row["admission_reason"] in _ADMISSION_REASONS)
    return {reason: counts.get(reason, 0) for reason in sorted(_ADMISSION_REASONS)}


def _exclusions(rows: list[dict]) -> dict:
    return {
        "missing_trade_score": sum(row.get("trade_score") is None for row in rows),
        "missing_outcome_candidate_key": sum(not row.get("outcome_candidate_key") for row in rows),
        "without_settled_net_outcome": sum(not row["outcomes"] for row in rows),
        "late_entry_unavailable": sum(row["late_entry_status"] == "unavailable" for row in rows),
        "queue_evidence_unavailable": sum(row["queue_constraint"] == "unavailable" for row in rows),
    }


def _columns(db, table: str) -> set[str]:
    return {str(row["name"]) for row in db.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_exists(db, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _normalize_market(value: Any) -> str:
    return (
        "perp"
        if str(value).lower() in {"perp", "futures", "swap", "usdt_futures"}
        else "spot"
    )


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_value(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return default


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None) -> float | None:
    return round(float(value), 4) if value is not None else None


def _fmt(value: Any) -> str:
    return "unavailable" if value is None else str(value)

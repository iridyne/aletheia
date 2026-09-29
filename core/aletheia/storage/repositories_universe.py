"""Universe, candidate, filter and shadow persistence.

Shared repository helpers are imported inside functions to avoid the facade
re-export cycle and retain its transaction and connection owners.
"""

import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from aletheia.storage.models import (
    AuditEvent,
    UniverseAsset,
    UniverseTwinShadowBatch,
    UniverseTwinShadowEvidence,
)

__all__ = [
    "anchor_shadow_trade_exit_price_if_pending",
    "cancel_all_pending_forward_outcomes",
    "candidate_selection_cost_assumptions",
    "close_pending_filter_outcomes_for_inactive_benchmarks",
    "count_pending_filter_candidate_outcomes_missing_venue",
    "count_pending_filter_candidate_outcomes_unreachable_benchmark",
    "count_pending_forward_outcomes_missing_venue",
    "fail_filter_candidate_outcome",
    "fail_forward_outcome",
    "fail_perp_structure_shadow_outcome",
    "filter_outcome_unreachable_predicate",
    "filter_pressure_stats",
    "get_active_universe_members",
    "get_active_universe_policy",
    "get_active_universe_snapshot",
    "get_candidate_entry_lifecycle",
    "get_candidate_planning_for_candidate",
    "get_candidate_planning_generation",
    "get_decision_grade_shadow_outcome",
    "get_exit_breakeven_shadow_assignment",
    "get_latest_feature_snapshot",
    "get_universe_asset",
    "get_universe_discovery_benchmark",
    "get_universe_member",
    "get_universe_snapshot_created_at",
    "insert_filter_candidate_outcomes_batch",
    "insert_filter_decision",
    "insert_filter_decision_batch",
    "insert_universe_twin_shadow_bundle",
    "invalidate_universe_discovery_benchmark",
    "list_candidate_planning_cycles",
    "list_candidate_planning_refresh_assets",
    "list_due_perp_structure_shadow_outcomes",
    "list_entry_workbench_candidates",
    "list_exit_breakeven_shadow_report_rows",
    "list_forward_outcomes_by_member",
    "list_open_universe_twin_outcome_candidate_keys",
    "list_pending_filter_candidate_outcomes",
    "list_pending_filter_candidate_outcomes_for_keys",
    "list_pending_forward_outcomes",
    "list_pending_universe_discovery_outcomes",
    "list_shadow_trades",
    "list_universe_assets",
    "list_universe_discovery_batches",
    "list_universe_discovery_outcomes",
    "list_universe_feature_snapshots",
    "list_universe_members",
    "list_universe_snapshots",
    "list_universe_twin_shadow_records",
    "mark_outlier_outcomes_failed",
    "prune_perp_structure_shadow_outcomes",
    "prune_shadow_trades",
    "prune_universe_scanner_runs",
    "prune_universe_twin_shadow_evidence",
    "resolve_shadow_trade_if_pending",
    "settle_filter_candidate_outcome",
    "settle_forward_outcome",
    "settle_linked_shadow_outcomes",
    "settle_perp_structure_shadow_outcome",
    "sync_universe_assets",
    "upsert_universe_asset",
    "valid_keyed_shadow_projection_sql",
]


def get_exit_breakeven_shadow_assignment(position_id: str) -> dict | None:
    from aletheia.storage.repositories import get_db

    row = (
        get_db()
        .execute(
            "SELECT * FROM exit_breakeven_shadow_assignments WHERE position_id=?",
            (position_id,),
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def list_exit_breakeven_shadow_report_rows(
    *,
    closed_after: str | None = None,
) -> list[dict]:
    """Return every eligible closed assignment, including unsettled outcomes."""
    from aletheia.storage.repositories import get_db


    cutoff_clause = "AND julianday(position.closed_at) >= julianday(?) " if closed_after is not None else ""
    params = (closed_after,) if closed_after is not None else ()
    rows = (
        get_db()
        .execute(
            "SELECT assignment.position_id, outcome.status, outcome.treatment_changed, "
            "outcome.actual_closed_at, outcome.incremental_net_pnl, "
            "position.closed_at AS assigned_closed_at, position.close_reason "
            "FROM exit_breakeven_shadow_assignments AS assignment "
            "JOIN positions AS position ON position.id=assignment.position_id "
            "LEFT JOIN exit_breakeven_shadow_outcomes AS outcome "
            "ON outcome.position_id=assignment.position_id "
            "WHERE position.status='closed' AND position.outcome_eligible=1 "
            + cutoff_clause
            + "ORDER BY position.closed_at DESC",
            params,
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def _filter_outcome_loss_event(
    *,
    cutoff: str,
    retention_days: int,
    pending_rows: Sequence,
) -> AuditEvent:
    outcome_ids = [str(row[1]) for row in pending_rows]
    outcome_keys = [str(row[4]) for row in pending_rows]
    by_horizon: dict[str, int] = {}
    for row in pending_rows:
        horizon = str(row[2])
        by_horizon[horizon] = by_horizon.get(horizon, 0) + 1
    digest = hashlib.sha256(
        json.dumps(
            {"cutoff": cutoff, "outcome_ids": outcome_ids},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    dropped = len(pending_rows)
    return AuditEvent(
        id=f"filter-outcome-prune-{digest[:32]}",
        actor="system",
        source="prune_filter_candidate_outcomes",
        event_type="filter_outcome_evidence_dropped_unmeasured",
        entity_type="filter_candidate_outcomes",
        entity_id=f"{cutoff}:{digest[:16]}",
        after_state=json.dumps(
            {
                "batch_digest": digest,
                "dropped_unmeasured": dropped,
                "by_horizon": by_horizon,
                "cutoff": cutoff,
                "retention_days": retention_days,
                "outcome_ids": outcome_ids,
                "outcome_candidate_keys": outcome_keys,
            },
            sort_keys=True,
        ),
        message=(
            f"{dropped} filter-outcome rows were deleted at the {cutoff} "
            f"retention cutoff while still 'pending'. Their counterfactual "
            f"returns were never measured, so Learning contribution and filter "
            f"feedback over any window containing them are computed on an "
            f"incomplete population."
        ),
    )


def prune_shadow_trades(days: int = 90) -> int:
    from aletheia.storage.repositories import _delete_old_rows

    return _delete_old_rows("shadow_trades", days)


def _candidate_selection_retention_state() -> tuple[bool, str | None]:
    """Return whether policy state is readable and its configured target epoch."""

    try:
        from aletheia.config.loader import load_config

        universe = load_config(persist_migrations=False).universe
    except Exception:
        return False, None
    epoch = (
        str(universe.candidate_selection_policy_epoch)
        if universe.candidate_selection_policy == "candidate_selection_utility_v1"
        and universe.candidate_selection_policy_epoch
        else None
    )
    return True, epoch


def _protected_universe_outcome_ids(
    db,
    refs: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Resolve exact Lab claim refs to outcome/member/snapshot identities."""
    if not refs:
        return (), ()
    # SQLite's bound-variable limit is commonly 999. A claim set larger than
    # that is unusual; retaining this cleanup batch is safer than truncating
    # protection and deleting a claimed source underneath an active window.
    if len(refs) > 900:
        return ("__claim_set_too_large__",), ("__claim_set_too_large__",)
    placeholders = ",".join("?" for _ in refs)
    rows = db.execute(
        "SELECT id, member_id, snapshot_id, outcome_candidate_key FROM universe_forward_outcomes "
        f"WHERE id IN ({placeholders}) OR outcome_candidate_key IN ({placeholders})",
        (*refs, *refs),
    ).fetchall()
    matched = {
        str(value)
        for row in rows
        for value in (row[0], row[3])
        if value
    }
    if any(ref not in matched for ref in refs):
        return ("__claim_source_unavailable__",), ("__claim_source_unavailable__",)
    return (
        tuple(str(row[1]) for row in rows if row[1]),
        tuple(str(row[2]) for row in rows if row[2]),
    )


def upsert_universe_asset(asset: UniverseAsset) -> None:
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    db = get_db()
    was_in_tx = db.in_transaction
    row = db.execute(
        "SELECT id FROM universe_registry WHERE venue = ? AND symbol = ? AND market_type = ?",
        (asset.venue, asset.symbol, asset.market_type),
    ).fetchone()
    if row:
        execute_write(
            "UPDATE universe_registry SET status = ?, last_seen_at = ?, "
            "min_notional = ?, price_precision = ?, amount_precision = ?, metadata = ? "
            "WHERE id = ?",
            (
                asset.status,
                asset.last_seen_at,
                asset.min_notional,
                asset.price_precision,
                asset.amount_precision,
                asset.metadata,
                row["id"],
            ),
        )
    else:
        data = asset.model_dump()
        cols = ", ".join(data.keys())
        placeholders = ", ".join(["?"] * len(data))
        execute_write(f"INSERT INTO universe_registry ({cols}) VALUES ({placeholders})", list(data.values()))
    if not was_in_tx:
        db.commit()


def sync_universe_assets(
    assets: list[UniverseAsset],
    *,
    active_venue: str,
    stale_cutoff_iso: str,
) -> tuple[int, int, int]:
    """Bulk upsert one registry refresh and set-deactivate stale rows atomically."""
    from aletheia.storage.repositories import (
        execute_many_write,
        execute_write,
        get_db,
        transaction,
    )


    rows = [asset.model_dump() for asset in assets]
    columns = list(rows[0]) if rows else list(UniverseAsset.model_fields)
    placeholders = ", ".join(["?"] * len(columns))
    update_columns = [
        "status",
        "last_seen_at",
        "min_notional",
        "price_precision",
        "amount_precision",
        "is_blacklisted",
        "blacklist_reason",
        "metadata",
    ]
    update_sql = ", ".join(f"{column}=excluded.{column}" for column in update_columns)
    db = get_db()
    with transaction():
        if rows:
            execute_many_write(
                f"INSERT INTO universe_registry ({', '.join(columns)}) "
                f"VALUES ({placeholders}) "
                "ON CONFLICT(venue, symbol, market_type) DO UPDATE SET "
                f"{update_sql}",
                [[row[column] for column in columns] for row in rows],
            )
        other = execute_write(
            "UPDATE universe_registry SET status='inactive' WHERE venue != ? AND status='active'",
            (active_venue,),
        ).rowcount
        stale = execute_write(
            "UPDATE universe_registry SET status='inactive' WHERE venue = ? AND status='active' AND last_seen_at < ?",
            (active_venue, stale_cutoff_iso),
        ).rowcount
    return len(rows), other, stale


def list_universe_assets(
    venue: str | None = None,
    active_only: bool = True,
    limit: int | None = 500,
) -> list[dict]:
    from aletheia.storage.repositories import get_db

    db = get_db()
    sql = "SELECT * FROM universe_registry"
    params: list = []
    conditions = []
    if venue:
        conditions.append("venue = ?")
        params.append(venue)
    if active_only:
        conditions.append("status = 'active'")
        conditions.append("is_blacklisted = 0")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY last_seen_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [dict(r) for r in db.execute(sql, params).fetchall()]


def get_universe_asset(asset_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("universe_registry", asset_id)


def _universe_twin_shadow_capture_due_from_latest(
    captured_at: str,
    latest_captured_at: str | None,
    *,
    min_interval_sec: int,
) -> bool:
    if latest_captured_at is None:
        return True
    candidate = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    latest = datetime.fromisoformat(latest_captured_at.replace("Z", "+00:00"))
    if candidate.tzinfo is None:
        candidate = candidate.replace(tzinfo=timezone.utc)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    return candidate - latest >= timedelta(seconds=max(0, min_interval_sec))


def _validate_universe_twin_prospective_bundle(
    batch: UniverseTwinShadowBatch,
    evidence: Sequence[UniverseTwinShadowEvidence],
    outcome_rows: Sequence[Mapping],
) -> None:
    """Reject partial or internally inconsistent prospective identities."""
    from aletheia.storage.repositories import _CANDIDATE_SELECTION_RECORD_VERSIONS


    if batch.record_version in _CANDIDATE_SELECTION_RECORD_VERSIONS:
        authority_applied = bool(batch.promotion_eligible)
        if batch.record_version in {"candidate-selection-utility-record/v3", "candidate-selection-utility-record/v4"}:
            try:
                sampling = json.loads(batch.allocation_context_json)
            except (TypeError, ValueError):
                raise ValueError("candidate_selection_sampling_contract_invalid") from None
            if (
                sampling.get("evidence_sampling_contract")
                != "candidate-selection-evidence-cadence/v2"
                or sampling.get("minimum_interval_sec") != 4 * 60 * 60
            ):
                raise ValueError("candidate_selection_sampling_contract_invalid")
        if batch.record_version == "candidate-selection-utility-record/v4":
            authority_policy = sampling.get("authority_policy")
            if authority_policy not in {"candidate_selection_utility_v1", "legacy_total_score_v1"}:
                raise ValueError("candidate_selection_authority_contract_invalid")
            authority_applied = authority_policy == "candidate_selection_utility_v1"
            if batch.promotion_eligible:
                raise ValueError("candidate_selection_runtime_promotion_invalid")
        try:
            batch_blockers = json.loads(batch.blocking_reasons_json)
        except (TypeError, ValueError):
            raise ValueError("candidate_selection_blocking_reasons_invalid") from None
        if not isinstance(batch_blockers, list) or bool(batch_blockers) == authority_applied:
            raise ValueError("candidate_selection_authority_blocking_state_invalid")
        if any(item.shadow_authority_applied != authority_applied for item in evidence):
            raise ValueError("candidate_selection_authority_state_mixed")
        if any(item.promotion_eligible != batch.promotion_eligible for item in evidence):
            raise ValueError("candidate_selection_record_promotion_state_invalid")
        expected_keys: set[str] = set()
        expected_allocations: dict[str, bool] = {}
        for item in evidence:
            for field in (
                "experiment_id", "policy_epoch_id", "behavior_contract_hash",
                "primary_horizon", "outcome_contract_version", "metric_version",
            ):
                if getattr(item, field) != getattr(batch, field):
                    raise ValueError("candidate_selection_shadow_identity_mismatch")
            try:
                payload = json.loads(item.compact_output_json)
                keys = payload["outcome_candidate_keys"]
                contract = payload["evidence_contract"]
                target_selected = payload["target_selected"]
                primary_direction = payload["candidate_selection"]["primary_direction"]
            except (KeyError, TypeError, ValueError):
                raise ValueError("candidate_selection_shadow_contract_invalid") from None
            if not isinstance(target_selected, bool):
                raise ValueError("candidate_selection_target_selection_invalid")
            if item.shadow_pessimistic_selected is not target_selected:
                raise ValueError("candidate_selection_target_selection_mismatch")
            expected_assigned = target_selected if authority_applied else None
            if item.assigned_policy_selected is not expected_assigned:
                raise ValueError("candidate_selection_assigned_selection_mismatch")
            if contract.get("outcome_contract_version") != "candidate-selection-utility-outcome/v1":
                raise ValueError("candidate_selection_shadow_outcome_contract_invalid")
            if (
                batch.record_version in {"candidate-selection-utility-record/v3", "candidate-selection-utility-record/v4"}
                and contract.get("evidence_sampling_contract")
                != "candidate-selection-evidence-cadence/v2"
            ):
                raise ValueError("candidate_selection_sampling_contract_invalid")
            if contract.get("behavior_contract_hash") != batch.behavior_contract_hash:
                raise ValueError("candidate_selection_behavior_contract_mismatch")
            if contract.get("policy_epoch_id") != batch.policy_epoch_id:
                raise ValueError("candidate_selection_policy_epoch_mismatch")
            if contract.get("shadow_authority_applied") is not authority_applied:
                raise ValueError("candidate_selection_authority_contract_mismatch")
            if contract.get("promotion_eligible") is not batch.promotion_eligible:
                raise ValueError("candidate_selection_promotion_contract_mismatch")
            if not isinstance(keys, dict) or set(keys) - {"long", "short"}:
                raise ValueError("candidate_selection_shadow_outcome_keys_invalid")
            expected_keys.update(str(value) for value in keys.values())
            for direction, value in keys.items():
                expected_allocations[str(value)] = bool(
                    target_selected and direction == primary_direction
                )
        actual_keys = {str(row.get("outcome_candidate_key") or "") for row in outcome_rows}
        if actual_keys != expected_keys or "" in actual_keys:
            raise ValueError("candidate_selection_shadow_full_denominator_mismatch")
        if any(
            row.get("horizon") != "4h"
            or row.get("scanner_family") != "candidate_selection_utility"
            or row.get("entry_ts") != batch.captured_at
            for row in outcome_rows
        ):
            raise ValueError("candidate_selection_shadow_outcome_identity_invalid")
        if any(
            bool(row.get("allocation_selected"))
            != expected_allocations[str(row.get("outcome_candidate_key") or "")]
            for row in outcome_rows
        ):
            raise ValueError("candidate_selection_target_outcome_allocation_mismatch")
        return

    prospective_markers = (
        "experiment_id",
        "policy_epoch_id",
        "behavior_contract_hash",
        "assignment_unit_key",
        "assignment_contract_version",
        "assignment_arm",
        "assignment_propensity",
        "treatment_coefficient",
        "treatment_policy",
        "control_policy",
        "primary_horizon",
        "outcome_contract_version",
    )
    prospective = bool(outcome_rows) or any(getattr(batch, field) is not None for field in prospective_markers)
    if not prospective:
        return
    required_batch_fields = (
        "experiment_id",
        "policy_epoch_id",
        "behavior_contract_hash",
        "assignment_unit_key",
        "assignment_contract_version",
        "assignment_arm",
        "assignment_propensity",
        "treatment_coefficient",
        "treatment_policy",
        "control_policy",
        "primary_horizon",
        "outcome_contract_version",
        "metric_version",
    )
    if any(getattr(batch, field) is None for field in required_batch_fields):
        raise ValueError("universe_twin_shadow_experiment_identity_incomplete")
    if batch.assignment_unit_key != batch.scanner_run_id:
        raise ValueError("universe_twin_shadow_assignment_unit_mismatch")
    if batch.assignment_arm not in {"treatment", "control"}:
        raise ValueError("universe_twin_shadow_assignment_arm_invalid")
    if batch.assignment_propensity is None or not 0 < batch.assignment_propensity <= 1:
        raise ValueError("universe_twin_shadow_assignment_propensity_invalid")
    expected_coefficient = 1.0 if batch.assignment_arm == "treatment" else 0.0
    if batch.treatment_coefficient != expected_coefficient:
        raise ValueError("universe_twin_shadow_treatment_coefficient_mismatch")
    if batch.promotion_eligible:
        raise ValueError("universe_twin_shadow_promotion_not_allowed")
    if any(item.assigned_policy_selected is None for item in evidence):
        raise ValueError("universe_twin_shadow_assigned_selection_missing")
    selected_count = sum(bool(item.assigned_policy_selected) for item in evidence)
    if len(outcome_rows) != selected_count:
        raise ValueError("universe_twin_shadow_outcome_count_mismatch")

    record_identity_fields = (
        "experiment_id",
        "policy_epoch_id",
        "behavior_contract_hash",
        "availability_profile_id",
        "assignment_unit_key",
        "assignment_arm",
        "assignment_propensity",
        "treatment_coefficient",
        "primary_horizon",
        "outcome_contract_version",
        "metric_version",
    )
    for item in evidence:
        if any(getattr(item, field) != getattr(batch, field) for field in record_identity_fields):
            raise ValueError("universe_twin_shadow_record_experiment_identity_mismatch")
        if item.shadow_authority_applied:
            raise ValueError("universe_twin_shadow_authority_not_allowed")
        if item.promotion_eligible:
            raise ValueError("universe_twin_shadow_promotion_not_allowed")


def insert_universe_twin_shadow_bundle(
    batch: UniverseTwinShadowBatch,
    evidence: Sequence[UniverseTwinShadowEvidence],
    *,
    outcomes: Sequence | None = None,
    min_interval_sec: int = 55 * 60,
) -> int:
    """Atomically persist one immutable, cadence-bounded diagnostic batch.

    An identical retry is idempotent.  Reusing an identity with different
    canonical content is rejected so replay drift cannot be silently hidden.
    A second scanner run inside the minimum interval is skipped in the same
    writer transaction, preventing concurrent refreshes from bypassing cadence.
    """
    from aletheia.storage.repositories import (
        _CANDIDATE_SELECTION_RECORD_VERSIONS,
        _assert_twin_outcomes_match,
        _universe_twin_shadow_capture_due_from_latest,
        _validate_universe_twin_prospective_bundle,
        execute_many_write,
        execute_write,
        get_db,
        insert_filter_candidate_outcomes_batch,
        transaction,
    )


    batch_data = batch.model_dump()
    evidence_rows = [item.model_dump() for item in evidence]
    outcome_rows = [item.model_dump() for item in (outcomes or ())]
    if batch.candidate_count != len(evidence_rows):
        raise ValueError("universe_twin_shadow_candidate_count_mismatch")
    if any(item.scanner_run_id != batch.scanner_run_id for item in evidence):
        raise ValueError("universe_twin_shadow_scanner_run_mismatch")
    if sorted(item.candidate_index for item in evidence) != list(range(len(evidence))):
        raise ValueError("universe_twin_shadow_candidate_index_not_contiguous")
    if any(item.captured_at != batch.captured_at for item in evidence):
        raise ValueError("universe_twin_shadow_capture_time_mismatch")
    _validate_universe_twin_prospective_bundle(batch, evidence, outcome_rows)
    if (
        batch.experiment_id is not None
        and batch.record_version not in _CANDIDATE_SELECTION_RECORD_VERSIONS
    ):
        outcome_by_key = {str(row["outcome_candidate_key"]): row for row in outcome_rows}
        if len(outcome_by_key) != len(outcome_rows):
            raise ValueError("universe_twin_shadow_duplicate_outcome_identity")
        for item in evidence:
            if not item.assigned_policy_selected:
                if item.outcome_candidate_key:
                    raise ValueError("universe_twin_shadow_unselected_outcome_not_allowed")
                continue
            if not item.outcome_candidate_key:
                raise ValueError("universe_twin_shadow_record_outcome_missing")
            row = outcome_by_key.get(item.outcome_candidate_key)
            if row is None:
                raise ValueError("universe_twin_shadow_record_outcome_mismatch")
            if row.get("horizon") != item.primary_horizon:
                raise ValueError("universe_twin_shadow_outcome_horizon_mismatch")
            if row.get("entry_ts") != batch.captured_at:
                raise ValueError("universe_twin_shadow_outcome_entry_time_mismatch")
            selected = row.get("allocation_selected")
            if (
                selected is None
                or item.assigned_policy_selected is None
                or bool(selected) != bool(item.assigned_policy_selected)
            ):
                raise ValueError("universe_twin_shadow_outcome_selection_mismatch")
            try:
                contract = json.loads(item.compact_output_json)["evidence_contract"]
                assigned_direction = str(contract["assigned_policy_direction"])
                assigned_family = contract["assigned_policy_family"]
            except (KeyError, TypeError, ValueError):
                raise ValueError("universe_twin_shadow_assigned_direction_missing") from None
            if row.get("direction") != assigned_direction:
                raise ValueError("universe_twin_shadow_outcome_direction_mismatch")
            if row.get("scanner_family") != assigned_family:
                raise ValueError("universe_twin_shadow_outcome_family_mismatch")
    db = get_db()
    with transaction(immediate=True):
        stored_batch = db.execute(
            "SELECT batch_hash FROM universe_twin_shadow_batches WHERE scanner_run_id=?",
            (batch.scanner_run_id,),
        ).fetchone()
        if stored_batch is not None:
            if stored_batch["batch_hash"] != batch.batch_hash:
                raise ValueError("universe_twin_shadow_batch_identity_conflict")
            _assert_twin_outcomes_match(outcome_rows)
            return 0

        latest = db.execute(
            "SELECT captured_at FROM universe_twin_shadow_batches ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
        if not _universe_twin_shadow_capture_due_from_latest(
            batch.captured_at,
            str(latest["captured_at"]) if latest else None,
            min_interval_sec=min_interval_sec,
        ):
            return 0

        columns = list(batch_data)
        execute_write(
            f"INSERT INTO universe_twin_shadow_batches "
            f"({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [batch_data[column] for column in columns],
        )
        stored_batch = db.execute(
            "SELECT batch_hash FROM universe_twin_shadow_batches WHERE scanner_run_id=?",
            (batch.scanner_run_id,),
        ).fetchone()
        if stored_batch is None or stored_batch["batch_hash"] != batch.batch_hash:
            raise ValueError("universe_twin_shadow_batch_identity_conflict")

        inserted_evidence = 0
        if evidence_rows:
            record_columns = list(evidence_rows[0])
            before = db.total_changes
            execute_many_write(
                f"INSERT OR IGNORE INTO universe_twin_shadow_records "
                f"({', '.join(record_columns)}) VALUES "
                f"({', '.join('?' for _ in record_columns)})",
                [[row[column] for column in record_columns] for row in evidence_rows],
            )
            inserted_evidence = db.total_changes - before
            for item in evidence:
                stored = db.execute(
                    "SELECT record_hash FROM universe_twin_shadow_records WHERE id=?",
                    (item.id,),
                ).fetchone()
                if stored is None or stored["record_hash"] != item.record_hash:
                    raise ValueError("universe_twin_shadow_record_identity_conflict")
        if outcome_rows:
            _assert_twin_outcomes_match(outcome_rows, allow_missing=True)
            insert_filter_candidate_outcomes_batch(list(outcomes or ()))
            _assert_twin_outcomes_match(outcome_rows)
    return inserted_evidence


def _assert_twin_outcomes_match(
    expected_rows: Sequence[Mapping],
    *,
    allow_missing: bool = False,
) -> None:
    """Reject identity reuse while allowing legitimate later settlement fields."""
    from aletheia.storage.repositories import (
        _TWIN_OUTCOME_IMMUTABLE_FIELDS,
        get_db,
    )


    db = get_db()
    for expected in expected_rows:
        stored = db.execute(
            "SELECT * FROM filter_candidate_outcomes WHERE id=?",
            (expected["id"],),
        ).fetchone()
        if stored is None:
            if allow_missing:
                continue
            raise ValueError("universe_twin_shadow_outcome_identity_conflict")
        if any(stored[field] != expected.get(field) for field in _TWIN_OUTCOME_IMMUTABLE_FIELDS):
            raise ValueError("universe_twin_shadow_outcome_identity_conflict")


def list_universe_twin_shadow_records(scanner_run_id: str) -> list[dict]:
    from aletheia.storage.repositories import get_db

    rows = (
        get_db()
        .execute(
            "SELECT * FROM universe_twin_shadow_records WHERE scanner_run_id=? ORDER BY candidate_index",
            (scanner_run_id,),
        )
        .fetchall()
    )
    return [dict(row) for row in rows]


def get_active_universe_snapshot(market_type: str = "crypto") -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_snapshots WHERE status = 'active' AND source_kind='universe' AND market_type = ? ORDER BY created_at DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    return dict(row) if row else None


def get_universe_snapshot_created_at(snapshot_id: str) -> str | None:
    from aletheia.storage.repositories import get_db

    row = get_db().execute(
        "SELECT created_at FROM universe_snapshots WHERE id=?",
        (snapshot_id,),
    ).fetchone()
    return str(row["created_at"]) if row is not None else None


def _project_candidate_admission(row: dict) -> dict:
    """Expose only hash- and identity-validated admission evidence; never infer it."""
    from aletheia.universe.candidate_opportunity import CandidateOpportunityContext

    fields = ("admission_basis", "startup_reason", "pessimistic_strength")
    row.update(dict.fromkeys(fields))
    encoded = row.get("candidate_opportunity_context_json")
    if not encoded:
        return row
    try:
        context = CandidateOpportunityContext.model_validate_json(encoded)
    except ValueError:
        return row
    if (context.context_hash != row.get("candidate_opportunity_context_hash")
            or context.candidate_id != row.get("last_candidate_id", row.get("id"))
            or context.candidate_selection["policy"] != row.get("candidate_selection_policy")
            or context.candidate_selection.get("policy_epoch") != row.get("candidate_selection_policy_epoch")
            or context.opportunity["primary_mechanism"] != row.get("opportunity_mechanism")):
        return row
    row.update({field: context.opportunity.get(field) for field in fields})
    return row


def list_universe_members(snapshot_id: str, limit: int | None = 50) -> list[dict]:
    from aletheia.storage.repositories import (
        _project_candidate_admission,
        _select_all,
        get_db,
    )

    if limit is None:
        rows = (
            get_db()
            .execute(
                "SELECT * FROM universe_members WHERE snapshot_id = ? ORDER BY rank ASC",
                (snapshot_id,),
            )
            .fetchall()
        )
        return [_project_candidate_admission(dict(row)) for row in rows]
    return [_project_candidate_admission(row) for row in _select_all(
        "universe_members",
        where="snapshot_id = ?",
        params=(snapshot_id,),
        order="rank ASC",
        limit=limit,
    )]


def get_universe_member(member_id: str) -> dict | None:
    from aletheia.storage.repositories import (
        _project_candidate_admission,
        _select_one,
    )

    row = _select_one("universe_members", member_id)
    return _project_candidate_admission(row) if row is not None else None


def get_candidate_planning_generation(generation_id: str) -> dict | None:
    from aletheia.storage.repositories import _select_one

    return _select_one("candidate_planning_generations", generation_id)


def _effective_candidate_attention(row: dict | None, *, now: datetime | None = None) -> dict | None:
    """Project expired active attention without mutating its durable lifecycle."""

    if row is None or row.get("attention_status") != "active":
        return row
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    hard_expires_at = row.get("hard_expires_at")
    soft_expires_at = row.get("soft_expires_at")
    if hard_expires_at and str(hard_expires_at) <= timestamp:
        reason, terminal_at = "max_attention_age", hard_expires_at
    elif soft_expires_at and str(soft_expires_at) <= timestamp:
        reason, terminal_at = "ttl_expired", soft_expires_at
    else:
        return row
    projected = dict(row)
    projected.update(
        attention_status="terminal",
        attention_terminal_at=terminal_at,
        attention_terminal_reason=reason,
    )
    return projected


def get_candidate_planning_for_candidate(
    candidate_id: str,
    *,
    now: datetime | None = None,
) -> dict | None:
    from aletheia.storage.repositories import (
        _effective_candidate_attention,
        get_db,
    )

    row = (
        get_db()
        .execute(
            "SELECT * FROM candidate_planning_generations "
            "WHERE candidate_id=? "
            "ORDER BY is_current DESC, updated_at DESC, id DESC LIMIT 1",
            (candidate_id,),
        )
        .fetchone()
    )
    if row is None:
        row = (
            get_db()
            .execute(
                "SELECT * FROM candidate_planning_generations WHERE last_candidate_id=? "
                "ORDER BY is_current DESC, updated_at DESC, id DESC LIMIT 1",
                (candidate_id,),
            )
            .fetchone()
        )
    if row is None:
        row = get_db().execute(
            "SELECT pg.* FROM audit_events ae JOIN candidate_planning_generations pg "
            "ON pg.id=json_extract(CASE WHEN json_valid(ae.after_state) THEN ae.after_state ELSE '{}' END, '$.planning_generation_id') "
            "WHERE ae.event_type='candidate_planning_associated' AND ae.entity_type='candidate' "
            "AND ae.entity_id=? ORDER BY pg.is_current DESC,pg.cycle_number DESC,pg.id DESC LIMIT 1",
            (candidate_id,),
        ).fetchone()
    projected = _effective_candidate_attention(dict(row) if row else None, now=now)
    if projected is not None:
        for field in ("identity_version", "planner_contract_version", "activation_identity"):
            projected.pop(field, None)
    return projected


def list_candidate_planning_cycles(*, planning_key: str) -> list[dict]:
    from aletheia.storage.repositories import list_candidate_planning_cycles_batch

    return list_candidate_planning_cycles_batch(planning_keys=[planning_key])[planning_key]


def get_candidate_entry_lifecycle(candidate_id: str) -> dict:
    from aletheia.storage.repositories import (
        get_candidate,
        get_candidate_planning_for_candidate,
        list_candidate_planning_cycles,
    )

    from aletheia.trading.entry_lifecycle import project_candidate_entry_lifecycle

    candidate = get_candidate(candidate_id)
    if candidate is None:
        raise ValueError("candidate_not_found")
    generation = get_candidate_planning_for_candidate(candidate_id)
    cycles = (
        list_candidate_planning_cycles(planning_key=str(generation["planning_key"]))
        if generation else []
    )
    return project_candidate_entry_lifecycle(candidate, cycles)


def _candidate_attention_valid(row: dict, timestamp: str) -> bool:
    return (
        row.get("attention_status") == "active"
        and bool(row.get("soft_expires_at"))
        and bool(row.get("hard_expires_at"))
        and str(row["soft_expires_at"]) > timestamp
        and str(row["hard_expires_at"]) > timestamp
    )


def list_candidate_planning_refresh_assets(
    *, venue: str | None = None, now: datetime | None = None,
) -> list[dict]:
    """Return exact assets whose canonical active planning needs fresh facts."""
    from aletheia.storage.repositories import (
        _ACTIVE_ENTRY_PLAN_STATUSES,
        get_db,
    )

    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    plan_status_placeholders = ",".join("?" for _ in _ACTIVE_ENTRY_PLAN_STATUSES)
    rows = get_db().execute(
        "SELECT ur.* FROM candidate_planning_generations pg "
        "JOIN universe_members um ON um.id=pg.last_candidate_id "
        "JOIN universe_registry ur ON ur.id=um.asset_id "
        "WHERE pg.is_current=1 AND pg.attention_status='active' "
        "AND pg.soft_expires_at>? AND pg.hard_expires_at>? "
        "AND pg.status IN ('pending','planning','unavailable') "
        "AND pg.entry_plan_id IS NULL "
        + ("AND pg.venue=? " if venue is not None else "")
        +
        "AND ur.venue=pg.venue AND ur.symbol=pg.symbol AND ur.market_type=pg.market_type "
        "AND um.venue=pg.venue AND um.symbol=pg.symbol AND um.market_type=pg.market_type "
        "AND NOT EXISTS (SELECT 1 FROM entry_plans ep WHERE ep.planning_generation_id=pg.id "
        f"AND ep.status IN ({plan_status_placeholders})) "
        "ORDER BY COALESCE(pg.retry_not_before,pg.created_at),pg.created_at,pg.id",
        (timestamp, timestamp, *((venue,) if venue is not None else ()), *_ACTIVE_ENTRY_PLAN_STATUSES),
    ).fetchall()
    return list({row["id"]: dict(row) for row in rows}.values())


def list_entry_workbench_candidates(limit: int = 100) -> list[dict]:
    """Candidate attention plus exact active-Plan handoffs for Workbench filters."""
    from aletheia.storage.repositories import (
        _ACTIVE_ENTRY_PLAN_STATUSES,
        _project_candidate_admission,
        get_db,
        list_candidate_planning_cycles_batch,
    )


    timestamp = datetime.now(timezone.utc).isoformat()
    active_plan_states = _ACTIVE_ENTRY_PLAN_STATUSES
    placeholders = ",".join("?" for _ in active_plan_states)
    rows = [
        _project_candidate_admission(dict(row)) for row in get_db()
        .execute(
            "SELECT pg.*, um.rank, um.total_score, um.entry_price_usd, "
            "um.liquidity_score, um.spread_bps, um.inclusion_reason, um.risk_flags, "
            "um.execution_eligible, um.expires_at AS candidate_expires_at, "
            "um.candidate_selection_policy, um.candidate_selection_policy_epoch, "
            "um.candidate_selection_utility_pct, um.opportunity_mechanism, "
            "um.opportunity_mechanism_version, um.opportunity_source_component, "
            "um.opportunity_source_cutoff_at, um.opportunity_evidence_hash, "
            "um.candidate_opportunity_context_json, um.candidate_opportunity_context_hash, "
            "ep.id AS active_entry_plan_id, ep.status AS active_entry_plan_status "
            "FROM candidate_planning_generations pg "
            "LEFT JOIN universe_members um ON um.id=pg.last_candidate_id "
            "LEFT JOIN entry_plans ep ON ep.planning_generation_id=pg.id "
            f"AND ep.status IN ({placeholders}) "
            "WHERE pg.is_current=1 AND ((pg.attention_status='active' "
            "AND pg.soft_expires_at>? AND pg.hard_expires_at>?) OR ep.id IS NOT NULL) "
            "ORDER BY CASE WHEN pg.attention_status='active' THEN 0 ELSE 1 END, "
            "MIN(pg.soft_expires_at, pg.hard_expires_at), pg.id LIMIT ?",
            (*active_plan_states, timestamp, timestamp, max(1, min(int(limit), 200))),
        )
        .fetchall()
    ]
    from aletheia.trading.entry_lifecycle import project_candidate_entry_lifecycle

    cycles = list_candidate_planning_cycles_batch(planning_keys=[str(row["planning_key"]) for row in rows])
    projected = []
    for row in rows:
        projected.append({
            **row,
            **project_candidate_entry_lifecycle(
                row, cycles[str(row["planning_key"])]
            ),
        })
    return projected


def get_active_universe_members(limit: int | None = 20) -> list[dict]:
    """Return members from the active universe snapshot, optionally bounded."""
    from aletheia.storage.repositories import (
        get_active_universe_snapshot,
        list_universe_members,
    )

    snap = get_active_universe_snapshot()
    if not snap:
        return []
    return list_universe_members(snap["id"], limit=limit)


def get_latest_feature_snapshot(asset_id: str) -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_feature_snapshots WHERE asset_id = ? ORDER BY ts DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return dict(row) if row else None


def get_active_universe_policy(market_type: str = "crypto") -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    row = db.execute(
        "SELECT * FROM universe_policies WHERE active = 1 AND market_type = ? ORDER BY created_at DESC LIMIT 1",
        (market_type,),
    ).fetchone()
    return dict(row) if row else None


def list_universe_snapshots(market_type: str = "crypto", limit: int = 20) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "universe_snapshots",
        where="market_type = ? AND source_kind='universe'",
        params=(market_type,),
        order="created_at DESC",
        limit=limit,
    )


def _shadow_candidate_identity_sql(
    shadow_alias: str,
    outcome_alias: str,
) -> str:
    from aletheia.storage.repositories import SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC

    shadow = f"{shadow_alias}."
    outcome = f"{outcome_alias}."
    shadow_market = (
        f"CASE WHEN LOWER({shadow}market_type) IN "
        "('perp','futures','swap','usdt_futures') THEN 'perp' "
        f"ELSE LOWER({shadow}market_type) END"
    )
    outcome_market = (
        f"CASE WHEN LOWER({outcome}market_type) IN "
        "('perp','futures','swap','usdt_futures') THEN 'perp' "
        f"ELSE LOWER({outcome}market_type) END"
    )
    return (
        f"typeof({shadow}opportunity_id) = 'text' "
        f"AND {shadow}opportunity_id <> '' "
        f"AND {shadow}outcome_candidate_key = 'opportunity:' || {shadow}opportunity_id "
        f"AND typeof({shadow}venue) = 'text' AND {shadow}venue = {outcome}venue "
        f"AND typeof({shadow}symbol) = 'text' AND {shadow}symbol = {outcome}symbol "
        f"AND typeof({shadow}market_type) = 'text' "
        f"AND {shadow_market} = {outcome_market} "
        f"AND typeof({shadow}direction) = 'text' "
        f"AND LOWER({shadow}direction) = LOWER({outcome}direction) "
        f"AND typeof({shadow}entry_price) IN ('integer', 'real') "
        f"AND {shadow}entry_price = {outcome}entry_price_usd "
        f"AND julianday({shadow}created_at) >= julianday({outcome}entry_ts) "
        f"AND julianday({shadow}created_at) <= julianday({outcome}entry_ts, "
        f"'+{SHADOW_OUTCOME_ENTRY_ALIGNMENT_MAX_SEC} seconds')"
    )


def valid_keyed_shadow_projection_sql(
    shadow_alias: str = "shadow_trades",
    *,
    require_not_invalidated: bool = True,
) -> str:
    """Require one keyed shadow net to equal its canonical settled 24h outcome."""
    from aletheia.storage.repositories import (
        _shadow_candidate_identity_sql,
        complete_outcome_economics_sql,
    )

    shadow = f"{shadow_alias}." if shadow_alias else ""
    outcome = "canonical_outcome."
    numeric_pairs = (
        ("exit_price", "exit_price_usd"),
        ("pnl_pct", "directional_return_pct"),
        ("gross_return_pct", "gross_return_pct"),
        ("net_pnl_pct", "net_return_pct"),
        ("estimated_fee_pct", "estimated_fee_pct"),
        ("estimated_spread_slippage_pct", "estimated_spread_slippage_pct"),
        ("estimated_funding_pct", "estimated_funding_pct"),
        ("min_tradable_edge_pct", "min_tradable_edge_pct"),
        ("settlement_lag_sec", "settlement_lag_sec"),
    )
    equality = " ".join(
        f"AND typeof({shadow}{shadow_field}) IN ('integer', 'real') "
        f"AND {shadow}{shadow_field} = {outcome}{outcome_field}"
        for shadow_field, outcome_field in numeric_pairs
    )
    invalidation = f"{shadow}invalidated_at IS NULL AND " if require_not_invalidated else ""
    return invalidation + (
        "EXISTS (SELECT 1 FROM filter_candidate_outcomes AS canonical_outcome "
        f"WHERE {outcome}outcome_candidate_key = {shadow}outcome_candidate_key "
        f"AND {outcome}horizon = '24h' "
        f"AND {outcome}status = 'settled' "
        f"AND {complete_outcome_economics_sql('canonical_outcome')} "
        f"AND {_shadow_candidate_identity_sql(shadow_alias, 'canonical_outcome')} "
        f"AND typeof({shadow}resolved_at) = 'text' "
        f"AND {shadow}resolved_at = {outcome}settled_at "
        f"AND typeof({shadow}settlement_price_source) = 'text' "
        f"AND {shadow}settlement_price_source = {outcome}settlement_price_source "
        f"AND typeof({shadow}cost_model_version) = 'text' "
        f"AND {shadow}cost_model_version = {outcome}cost_model_version "
        f"{equality})"
    )


def get_decision_grade_shadow_outcome(
    shadow_id: str,
    horizon: str,
) -> dict | None:
    """Return exact canonical outcome evidence for one persisted shadow exposure."""
    from aletheia.storage.repositories import (
        _shadow_candidate_identity_sql,
        complete_outcome_economics_sql,
        get_db,
        valid_keyed_shadow_projection_sql,
    )

    normalized_horizon = str(horizon).strip().lower()
    authority_sql = (
        valid_keyed_shadow_projection_sql("shadow_trades")
        if normalized_horizon == "24h"
        else _shadow_candidate_identity_sql("shadow_trades", "canonical_outcome")
    )
    row = (
        get_db()
        .execute(
            "SELECT canonical_outcome.*, shadow_trades.id AS shadow_id, "
            "shadow_trades.batch_id AS shadow_batch_id, "
            "shadow_trades.opportunity_id AS shadow_opportunity_id, "
            "shadow_trades.was_selected AS shadow_was_selected, "
            "shadow_trades.created_at AS shadow_created_at, "
            "shadow_trades.rank AS shadow_rank "
            "FROM shadow_trades JOIN filter_candidate_outcomes AS canonical_outcome "
            "ON canonical_outcome.outcome_candidate_key=shadow_trades.outcome_candidate_key "
            "AND canonical_outcome.horizon=? "
            "WHERE shadow_trades.id=? AND canonical_outcome.status='settled' "
            f"AND {complete_outcome_economics_sql('canonical_outcome')} "
            f"AND {authority_sql} "
            "LIMIT 1",
            (normalized_horizon, shadow_id),
        )
        .fetchone()
    )
    return dict(row) if row else None


def _forward_outcome_insert_payload(outcome) -> dict:
    data = outcome.model_dump()
    if str(data.get("status") or "").lower() == "pending":
        venue = data.get("venue")
        if not isinstance(venue, str) or not venue.strip():
            raise ValueError("pending forward outcome requires a nonblank venue")
        data["venue"] = venue.strip()
    return data


def _filter_outcome_insert_payload(outcome) -> dict:
    from aletheia.storage.repositories import logger

    data = outcome.model_dump()
    if str(data.get("status") or "").lower() == "pending":
        venue = data.get("venue")
        if not isinstance(venue, str) or not venue.strip():
            raise ValueError("pending filter outcome requires a nonblank venue")
        data["venue"] = venue.strip()
    try:
        from aletheia.agent_runtime.runtime_session import current_provenance

        provenance = current_provenance()
    except Exception as exc:
        logger.warning("repositories: outcome insert provenance unavailable (%s)", type(exc).__name__)
        provenance = {}
    for field, source_field in (
        ("producer_runtime_session_id", "runtime_session_id"),
        ("producer_commit", "producer_commit"),
        ("producer_behavior_fingerprint", "behavior_fingerprint"),
    ):
        if data.get(field) is None:
            data[field] = provenance.get(source_field)
    return data


def list_pending_forward_outcomes(before_ts: str, limit: int = 500) -> list[dict]:
    from aletheia.storage.repositories import (
        SQLITE_PYTHON_STRIP_CHARS_SQL,
        _select_all,
    )

    return _select_all(
        "universe_forward_outcomes",
        where=(
            "status = 'pending' AND due_at <= ? "
            "AND typeof(venue) = 'text' "
            f"AND TRIM(venue, {SQLITE_PYTHON_STRIP_CHARS_SQL}) <> ''"
        ),
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def count_pending_forward_outcomes_missing_venue(before_ts: str) -> int:
    from aletheia.storage.repositories import (
        SQLITE_PYTHON_STRIP_CHARS_SQL,
        get_db,
    )

    db = get_db()
    row = db.execute(
        "SELECT COUNT(*) AS n FROM universe_forward_outcomes "
        "WHERE status = 'pending' AND due_at <= ? AND ("
        "typeof(venue) <> 'text' "
        f"OR TRIM(venue, {SQLITE_PYTHON_STRIP_CHARS_SQL}) = '')",
        (before_ts,),
    ).fetchone()
    return int(row["n"] or 0)


def settle_forward_outcome(
    outcome_id: str,
    exit_price: float,
    forward_return: float,
    settled_at: str,
    *,
    economics: dict | None = None,
    settlement_price_source: str | None = None,
    settlement_lag_sec: float | None = None,
) -> bool:
    from aletheia.storage.repositories import _settle_outcome_with_exact_venue

    updates = {
        "exit_price_usd": exit_price,
        "forward_return_pct": forward_return,
        "settled_at": settled_at,
        "status": "settled",
    }
    if economics:
        updates.update(economics)
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    if settlement_lag_sec is not None:
        updates["settlement_lag_sec"] = settlement_lag_sec
    return _settle_outcome_with_exact_venue(
        "universe_forward_outcomes",
        outcome_id,
        updates,
        kind="forward",
    )


def fail_forward_outcome(
    outcome_id: str,
    settled_at: str,
    *,
    exit_price: float | None = None,
    settlement_price_source: str | None = None,
) -> bool:
    """Record a terminal "not measurable" verdict for one forward row.

    Pass ``exit_price``/``settlement_price_source`` whenever the venue DID answer
    and the answer was rejected — a >10x deviation from entry, which is how
    cross-symbol contamination presents. Recording the rejected observation makes
    the row say what was seen, and leaves NULL meaning exactly one thing: no
    observation was ever taken.

    ``maintenance.settlement_requeue`` depends on that distinction to separate
    evidence destroyed by an unreachable venue from a genuine verdict, so never
    fail a row with a price in hand without passing it. Neither column is read by
    any consumer of a failed row: those all gate on ``net_return_pct IS NOT NULL``
    (outcome contract §8), which a failed row never has.
    """
    from aletheia.storage.repositories import (
        _update_pending,
        transaction,
    )

    updates: dict[str, object] = {
        "settled_at": settled_at,
        "status": "failed",
    }
    if exit_price is not None:
        updates["exit_price_usd"] = exit_price
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    with transaction(immediate=True):
        return _update_pending("universe_forward_outcomes", outcome_id, updates)


def cancel_all_pending_forward_outcomes() -> int:
    """Batch-cancel all pending forward outcomes (used on venue/strategy switch)."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    db = get_db()
    was_in_tx = db.in_transaction
    result = execute_write(
        "UPDATE universe_forward_outcomes SET status = 'cancelled' WHERE status = 'pending'",
    )
    if not was_in_tx:
        db.commit()
    return result.rowcount


def list_forward_outcomes_by_member(member_id: str) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "universe_forward_outcomes",
        where="member_id = ?",
        params=(member_id,),
        order="horizon ASC",
    )


def insert_filter_decision(decision) -> bool:
    """Insert one immutable filter fact; duplicate dedup keys are ignored."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    data = decision.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = execute_write(
        f"INSERT OR IGNORE INTO filter_decisions ({cols}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cur.rowcount)


def insert_filter_decision_batch(batch) -> bool:
    """Insert one immutable compressed filter batch."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    data = batch.model_dump()
    db = get_db()
    was_in_tx = db.in_transaction
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" for _ in data)
    cur = execute_write(
        f"INSERT OR IGNORE INTO filter_decision_batches ({cols}) VALUES ({placeholders})",
        list(data.values()),
    )
    if not was_in_tx:
        db.commit()
    return bool(cur.rowcount)


def insert_filter_candidate_outcomes_batch(
    outcomes: list, *, db: sqlite3.Connection | None = None,
) -> int:
    """Register fixed-horizon candidate outcomes without duplicating a candidate.

    Uses an upsert so that a later call carrying a definitive allocation_selected
    (True or False) can update a row that was first written with NULL by the filter
    evidence path. COALESCE(excluded.allocation_selected, existing) ensures an
    explicit True/False always wins, while a NULL incoming value never overwrites
    an already-resolved value.
    """
    from aletheia.storage.repositories import (
        _filter_outcome_insert_payload,
        execute_many_write,
        get_db,
        transaction,
    )

    if not outcomes:
        return 0
    rows = [_filter_outcome_insert_payload(outcome) for outcome in outcomes]
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    connection = db or get_db()
    statement = (
        f"INSERT INTO filter_candidate_outcomes ({', '.join(columns)}) VALUES ({placeholders})"
        " ON CONFLICT(outcome_candidate_key, horizon) DO UPDATE SET"
        " allocation_selected = COALESCE(excluded.allocation_selected,"
        "                                filter_candidate_outcomes.allocation_selected)"
    )
    values = [[row[column] for column in columns] for row in rows]
    before = connection.total_changes
    if db is not None:
        if not connection.in_transaction:
            raise RuntimeError("outcome_seed_connection_requires_transaction")
        connection.executemany(statement, values)
    else:
        with transaction():
            execute_many_write(statement, values)
    return connection.total_changes - before


def _benchmark_payload(value) -> dict:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"Unsupported benchmark payload type: {type(value)!r}")


def get_universe_discovery_benchmark(window_id: str | None = None) -> dict | None:
    from aletheia.storage.repositories import get_db

    db = get_db()
    if window_id:
        row = db.execute(
            "SELECT * FROM universe_discovery_benchmarks WHERE id=?", (window_id,)
        ).fetchone()
    else:
        row = db.execute(
            "SELECT * FROM universe_discovery_benchmarks "
            "WHERE status='active' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def invalidate_universe_discovery_benchmark(
    window_id: str, *, reason: str, at: str,
) -> bool:
    from aletheia.storage.repositories import (
        execute_write,
        transaction,
    )

    with transaction(immediate=True):
        cur = execute_write(
            "UPDATE universe_discovery_benchmarks SET status='invalidated', "
            "invalidated_at=?, invalidation_reason=? "
            "WHERE id=? AND status='active'",
            (at, reason, window_id),
        )
        return bool(cur.rowcount == 1)


def list_universe_discovery_batches(window_id: str) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "filter_decision_batches",
        where="benchmark_window_id=?",
        params=(window_id,),
        order="evaluated_at ASC, id ASC",
        limit=None,
    )


def list_universe_discovery_outcomes(window_id: str) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "filter_candidate_outcomes",
        where="benchmark_window_id=?",
        params=(window_id,),
        order="entry_ts ASC, id ASC",
        limit=None,
    )


def list_pending_universe_discovery_outcomes(before_ts: str, limit: int = 200) -> list[dict]:
    from aletheia.storage.repositories import (
        SQLITE_PYTHON_STRIP_CHARS_SQL,
        _lab_active_benchmark,
        _select_all,
        get_universe_discovery_benchmark,
    )

    if limit <= 0:
        return []
    try:
        active = _lab_active_benchmark() if _lab_active_benchmark is not None else None
    except Exception:
        active = None
    if active is None:
        active = get_universe_discovery_benchmark()
    if not active or str(active.get("status")) != "active":
        return []
    window_id = str(active.get("observation_id") or active.get("id"))
    return _select_all(
        "filter_candidate_outcomes",
        where=(
            "benchmark_window_id=? AND status='pending' AND due_at<=? "
            "AND typeof(venue)='text' AND TRIM(venue, "
            f"{SQLITE_PYTHON_STRIP_CHARS_SQL})<>''"
        ),
        params=(window_id, before_ts),
        order="due_at ASC, id ASC",
        limit=limit,
    )


def filter_outcome_unreachable_predicate() -> tuple[str, tuple]:
    """``(sql, params)`` for "this pending row can never be settled".

    Mirrors ``outcome_write_guard`` as a set question so a batch caller asks it
    once instead of once per row. When the Lab reader is unavailable the answer
    degrades to the registry-only form, which treats a window the registry has
    never seen as *reachable*: guessing the other way closes evidence a live Lab
    window still owns.
    """
    from aletheia.storage.repositories import (
        FILTER_OUTCOME_BENCHMARK_DEAD_SQL,
        _lab_live_window_ids,
    )

    live: frozenset[str] | None = None
    if _lab_live_window_ids is not None:
        try:
            live = frozenset(_lab_live_window_ids())
        except Exception:
            live = None
    if live is None:
        return FILTER_OUTCOME_BENCHMARK_DEAD_SQL, ()
    placeholders = ", ".join("?" for _ in live) or "''"
    return (
        f"(benchmark_window_id IS NOT NULL AND benchmark_window_id NOT IN ({placeholders}))",
        tuple(sorted(live)),
    )


def list_pending_filter_candidate_outcomes(
    before_ts: str,
    limit: int = 200,
) -> list[dict]:
    from aletheia.storage.repositories import (
        SQLITE_PYTHON_STRIP_CHARS_SQL,
        _select_all,
    )

    return _select_all(
        "filter_candidate_outcomes",
        where=(
            "status = 'pending' AND due_at <= ? "
            "AND (benchmark_window_id IS NULL OR EXISTS ("
            "SELECT 1 FROM universe_discovery_benchmarks b "
            "WHERE b.id=filter_candidate_outcomes.benchmark_window_id AND b.status='active')) "
            "AND typeof(venue) = 'text' "
            f"AND TRIM(venue, {SQLITE_PYTHON_STRIP_CHARS_SQL}) <> ''"
        ),
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def count_pending_filter_candidate_outcomes_missing_venue(before_ts: str) -> int:
    from aletheia.storage.repositories import (
        SQLITE_PYTHON_STRIP_CHARS_SQL,
        get_db,
    )

    row = (
        get_db()
        .execute(
            "SELECT COUNT(*) AS n FROM filter_candidate_outcomes "
            "WHERE status = 'pending' AND due_at <= ? "
            "AND (benchmark_window_id IS NULL OR EXISTS ("
            "SELECT 1 FROM universe_discovery_benchmarks b "
            "WHERE b.id=filter_candidate_outcomes.benchmark_window_id AND b.status='active')) AND ("
            "typeof(venue) <> 'text' "
            f"OR TRIM(venue, {SQLITE_PYTHON_STRIP_CHARS_SQL}) = '')",
            (before_ts,),
        )
        .fetchone()
    )
    return int(row["n"] or 0)


def count_pending_filter_candidate_outcomes_unreachable_benchmark(before_ts: str) -> int:
    """Due pending rows no settler can ever reach.

    Both the selector and the terminal-write guard refuse these rows, so they are
    not settler backlog: nothing will ever drain them. Counted separately so neither
    the settler log nor doctor reports them as a stalled settler.
    """
    from aletheia.storage.repositories import (
        filter_outcome_unreachable_predicate,
        get_db,
    )

    predicate, predicate_params = filter_outcome_unreachable_predicate()
    row = (
        get_db()
        .execute(
            "SELECT COUNT(*) AS n FROM filter_candidate_outcomes "
            "WHERE status = 'pending' AND due_at <= ? "
            f"AND {predicate}",
            (before_ts, *predicate_params),
        )
        .fetchone()
    )
    return int(row["n"] or 0)


def close_pending_filter_outcomes_for_inactive_benchmarks(*, at: str) -> int:
    """Give a terminal disposition to pending rows no settler can ever reach.

    A window the outcome write guard refuses can never settle its outcomes, so
    leaving those rows ``pending`` is a claim that they are backlog that drains on
    its own, and they never will — so they move to ``invalidated``.

    Not bounded by ``due_at``: a row whose window is already dead is unreachable
    whether or not its horizon has elapsed. Never writes an economics column and
    never deletes history — the row keeps its identity, prices, entry and due
    timestamps; only ``status`` changes. ``settled_at`` is deliberately left NULL:
    these rows were never settled, and stamping them as if they had been is the same
    class of lie this repair exists to remove.

    Idempotent: the predicate is ``status='pending'``, so a second pass matches
    nothing. ``at`` is accepted for call-site symmetry and audit context; the table
    has no invalidation-timestamp column to carry it.
    """
    from aletheia.storage.repositories import (
        execute_write,
        filter_outcome_unreachable_predicate,
        transaction,
    )

    del at
    predicate, predicate_params = filter_outcome_unreachable_predicate()
    with transaction(immediate=True, savepoint=True):
        cursor = execute_write(
            "UPDATE filter_candidate_outcomes SET status='invalidated' "
            "WHERE status='pending' "
            f"AND {predicate}",
            predicate_params,
        )
        return int(cursor.rowcount or 0)


def list_open_universe_twin_outcome_candidate_keys() -> set[str]:
    """Return exact horizon-qualified keys for unsettled Task-6 outcomes.

    Twin comparison outcomes are deliberately not Learning exposures, so the
    ordinary experiment join cannot discover them. A policy rotation must not
    strand the closing epoch's final 4h rows behind a historical canonical
    backlog, so pending outcome state—not open-epoch state—is authoritative.
    """
    from aletheia.storage.repositories import get_db


    rows = (
        get_db()
        .execute(
            "SELECT DISTINCT r.outcome_candidate_key, r.primary_horizon "
            "FROM universe_twin_shadow_records r "
            "JOIN universe_twin_shadow_batches b "
            "  ON b.scanner_run_id=r.scanner_run_id "
            "JOIN filter_candidate_outcomes o "
            "  ON o.outcome_candidate_key=r.outcome_candidate_key "
            " AND o.horizon=r.primary_horizon "
            "WHERE b.experiment_id IS NOT NULL "
            "AND b.policy_epoch_id IS NOT NULL "
            "AND o.status='pending' "
            "AND r.outcome_candidate_key IS NOT NULL "
            "AND r.primary_horizon IS NOT NULL "
            "UNION "
            "SELECT DISTINCT o.outcome_candidate_key, o.horizon AS primary_horizon "
            "FROM filter_candidate_outcomes o "
            "WHERE o.status='pending' "
            "AND o.scanner_family='candidate_selection_utility'"
        )
        .fetchall()
    )
    return {f"{row['outcome_candidate_key']}:{row['primary_horizon']}" for row in rows}


def candidate_selection_cost_assumptions(outcome_candidate_key: str) -> dict | None:
    """Return the immutable capture-time costs linked to one Candidate outcome."""
    from aletheia.storage.repositories import get_db


    row = get_db().execute(
        "SELECT json_extract(r.compact_output_json, '$.evidence_contract.cost_assumptions') "
        "FROM universe_twin_shadow_records r "
        "JOIN universe_twin_shadow_batches b ON b.scanner_run_id=r.scanner_run_id "
        "JOIN json_each(r.compact_output_json, '$.outcome_candidate_keys') j "
        "WHERE b.record_version IN ('candidate-selection-utility-record/v1', "
        "'candidate-selection-utility-record/v2', 'candidate-selection-utility-record/v3', "
        "'candidate-selection-utility-record/v4') "
        "AND j.value=? LIMIT 1",
        (outcome_candidate_key,),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        value = json.loads(str(row[0]))
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def list_pending_filter_candidate_outcomes_for_keys(
    before_ts: str,
    outcome_candidate_keys: set[str],
    limit: int = 200,
    *,
    deadline: float | None = None,
) -> tuple[list[dict], bool]:
    """Return (due canonical outcomes for exact Learning keys, truncated).

    Cost scales with ``limit``, NOT with the size of ``outcome_candidate_keys``.
    It used to be the other way around: the key set was chunked 400 at a time into
    ``outcome_candidate_key IN (...)`` queries, each of which fetched *all* its
    matches before a final sort truncated to ``limit``. With ~34k open-experiment
    keys and ~190k due rows that measured 33s — more than the caller's entire
    settlement budget — so the settlement loop never ran an iteration. It returned
    0 without raising and without logging, and canonical settlement stopped dead
    for hours while the forward mirror kept draining normally.

    Adding ``ORDER BY due_at LIMIT`` per chunk did NOT fix it (measured 36s): each
    chunk's matches are spread across the whole due range, so SQLite still had to
    walk almost all of it before the limit could stop it. The direction is
    therefore inverted — walk the cheap ``(status, due_at)`` index in due order
    and test key membership in Python. That reads only as far as it needs to
    collect ``limit`` rows, and yields the most-overdue priority rows first
    instead of an arbitrary chunk's worth.

    ``deadline`` is a ``time.monotonic()`` value. ``truncated`` is True when the
    walk stopped on the deadline or the scan cap, so the caller can say so instead
    of presenting a partial priority set as the complete one.
    """
    from aletheia.storage.repositories import (
        SQLITE_PYTHON_STRIP_CHARS_SQL,
        _PRIORITY_SCAN_BATCH_ROWS,
        _PRIORITY_SCAN_CAP_ROWS,
        filter_outcome_unreachable_predicate,
        get_db,
    )

    if not outcome_candidate_keys or limit <= 0:
        return [], False

    wanted: set[tuple[str, str]] = set()
    for key in outcome_candidate_keys:
        base, separator, horizon = str(key).rpartition(":")
        if separator and horizon in {"15m", "1h", "4h", "24h"}:
            wanted.add((base, horizon))
    if not wanted:
        return [], False

    db = get_db()
    matched_ids: list[str] = []
    scanned = 0
    truncated = False
    unreachable_sql, unreachable_params = filter_outcome_unreachable_predicate()
    # Keyset pagination over (due_at, id) so each batch is an ordered index range
    # read rather than a growing OFFSET scan. Only the four columns needed to test
    # membership are read here; full rows are fetched for matches alone.
    last_due = ""
    last_id = ""
    while len(matched_ids) < limit:
        if deadline is not None and time.monotonic() >= deadline:
            truncated = True
            break
        if scanned >= _PRIORITY_SCAN_CAP_ROWS:
            truncated = True
            break
        rows = db.execute(
            "SELECT id, outcome_candidate_key, horizon, due_at "
            "FROM filter_candidate_outcomes "
            "WHERE status='pending' AND due_at <= ? "
            f"AND NOT {unreachable_sql} "
            "AND typeof(venue) = 'text' "
            f"AND TRIM(venue, {SQLITE_PYTHON_STRIP_CHARS_SQL}) <> '' "
            "AND (due_at > ? OR (due_at = ? AND id > ?)) "
            "ORDER BY due_at ASC, id ASC LIMIT ?",
            (before_ts, *unreachable_params, last_due, last_due, last_id, _PRIORITY_SCAN_BATCH_ROWS),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            if (str(row["outcome_candidate_key"]), str(row["horizon"])) in wanted:
                matched_ids.append(str(row["id"]))
                if len(matched_ids) >= limit:
                    break
        scanned += len(rows)
        last_due = str(rows[-1]["due_at"])
        last_id = str(rows[-1]["id"])
        if len(rows) < _PRIORITY_SCAN_BATCH_ROWS:
            break  # reached the end of the due range

    if not matched_ids:
        return [], truncated

    by_id: dict[str, dict] = {}
    for offset in range(0, len(matched_ids), 400):
        chunk = matched_ids[offset : offset + 400]
        placeholders = ", ".join("?" for _ in chunk)
        for row in db.execute(
            f"SELECT * FROM filter_candidate_outcomes WHERE id IN ({placeholders})",
            tuple(chunk),
        ).fetchall():
            by_id[str(row["id"])] = dict(row)

    # Preserve the due_at order the scan established.
    return [by_id[i] for i in matched_ids if i in by_id], truncated


def settle_filter_candidate_outcome(
    outcome_id: str,
    *,
    exit_price: float,
    settled_at: str,
    economics: dict,
    settlement_price_source: str | None,
    settlement_lag_sec: float | None,
) -> bool:
    from aletheia.storage.repositories import _settle_outcome_with_exact_venue

    updates = {
        "exit_price_usd": exit_price,
        "settled_at": settled_at,
        "status": "settled",
        **economics,
    }
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    if settlement_lag_sec is not None:
        updates["settlement_lag_sec"] = settlement_lag_sec
    return _settle_outcome_with_exact_venue(
        "filter_candidate_outcomes",
        outcome_id,
        updates,
        kind="filter",
    )


def fail_filter_candidate_outcome(
    outcome_id: str,
    settled_at: str,
    *,
    exit_price: float | None = None,
    settlement_price_source: str | None = None,
    settlement_lag_sec: float | None = None,
) -> bool:
    """Record a terminal "not measurable" verdict for one canonical row.

    See :func:`fail_forward_outcome` for why a rejected observation is recorded
    rather than dropped: NULL on both columns is the marker that no observation
    was ever taken, which is what ``maintenance.settlement_requeue`` reclaims.
    """
    from aletheia.storage.repositories import _settle_outcome_with_exact_venue

    updates: dict[str, object] = {
        "settled_at": settled_at,
        "status": "failed",
    }
    if exit_price is not None:
        updates["exit_price_usd"] = exit_price
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    if settlement_lag_sec is not None:
        updates["settlement_lag_sec"] = settlement_lag_sec
    return _settle_outcome_with_exact_venue(
        "filter_candidate_outcomes",
        outcome_id,
        updates,
        kind="filter",
    )


def settle_linked_shadow_outcomes(
    outcome_candidate_key: str,
) -> int:
    """Project the unique decision-grade canonical 24h row onto linked shadows."""
    from aletheia.storage.repositories import (
        _shadow_candidate_identity_sql,
        complete_outcome_economics_sql,
        execute_write,
        get_db,
    )

    db = get_db()
    was_in_tx = db.in_transaction
    identity_sql = _shadow_candidate_identity_sql("shadow_trades", "canonical_outcome")
    canonical_where = (
        "canonical_outcome.outcome_candidate_key="
        "shadow_trades.outcome_candidate_key "
        "AND canonical_outcome.horizon='24h' "
        "AND canonical_outcome.status='settled' "
        "AND typeof(canonical_outcome.settled_at)='text' "
        "AND typeof(canonical_outcome.gross_return_pct) IN ('integer','real') "
        "AND ABS(canonical_outcome.gross_return_pct) < 1e999 "
        f"AND {complete_outcome_economics_sql('canonical_outcome')} "
        f"AND {identity_sql}"
    )
    cur = execute_write(
        "UPDATE shadow_trades SET (exit_price,pnl_pct,gross_return_pct,net_pnl_pct,"
        "estimated_fee_pct,estimated_spread_slippage_pct,estimated_funding_pct,"
        "min_tradable_edge_pct,min_cost_covering_edge_pct,settlement_price_source,settlement_lag_sec,"
        "cost_model_version,resolved_at) = (SELECT exit_price_usd,"
        "directional_return_pct,gross_return_pct,net_return_pct,estimated_fee_pct,"
        "estimated_spread_slippage_pct,estimated_funding_pct,min_tradable_edge_pct,"
        "min_cost_covering_edge_pct,"
        "settlement_price_source,settlement_lag_sec,cost_model_version,settled_at "
        "FROM filter_candidate_outcomes AS canonical_outcome "
        f"WHERE {canonical_where}) "
        "WHERE outcome_candidate_key=? AND resolved_at IS NULL "
        "AND EXISTS (SELECT 1 FROM filter_candidate_outcomes AS canonical_outcome "
        f"WHERE {canonical_where})",
        (outcome_candidate_key,),
    )
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def mark_outlier_outcomes_failed(max_abs_return_pct: float = 1_000_000) -> int:
    """Mark settled rows with physically impossible returns as failed.

    Catches cross-symbol price contamination (e.g. BTC's price applied to PEPE
    when ccxt returns wrong data without raising). Idempotent — safe to call on
    every refresh; after the first pass it finds 0 rows and returns immediately.
    """
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    db = get_db()
    result = execute_write(
        "UPDATE universe_forward_outcomes "
        "SET status = 'failed' "
        "WHERE status = 'settled' "
        "AND (ABS(forward_return_pct) > ? OR ABS(net_return_pct) > ?)",
        (max_abs_return_pct, max_abs_return_pct),
    )
    return result.rowcount


def filter_pressure_stats(
    *,
    cost_model_version: str,
    cutoff: str | None = None,
    settled_through: str | None = None,
) -> list[dict]:
    """Aggregate cost-aware returns for filter-rejected candidates where scanner_family is known.

    These rows come from risk/PI filter stages that run after scanner assignment, so
    scanner_family is populated. Returns per-family miss rates: how often do this
    family's rejected candidates produce positive net returns?
    """
    from aletheia.storage.repositories import (
        _COMPLETE_OUTCOME_ECONOMICS_SQL,
        _OUTCOME_MARKET_SQL,
        get_db,
    )

    from aletheia.analytics.learning_window import clean_window_cutoff

    db = get_db()
    cutoff = cutoff or clean_window_cutoff("1970-01-01T00:00:00+00:00")
    settled_clause = "AND settled_at <= ? " if settled_through is not None else ""
    rows = db.execute(
        "SELECT scanner_family, direction, "
        f"{_OUTCOME_MARKET_SQL} AS market_type, horizon, "
        "cost_model_version, "
        "COUNT(*) AS n, "
        "AVG(net_return_pct) AS avg_net, "
        "MIN(net_return_pct) AS min_net, "
        "MAX(net_return_pct) AS max_net, "
        "AVG(CASE WHEN net_return_pct > min_cost_covering_edge_pct "
        "  THEN 1.0 ELSE 0.0 END) AS miss_rate "
        "FROM filter_candidate_outcomes "
        "WHERE status = 'settled' "
        "AND cost_model_version = ? "
        "AND scanner_family IS NOT NULL "
        f"AND {_COMPLETE_OUTCOME_ECONOMICS_SQL} "
        "AND direction IN ('long', 'short') "
        "AND entry_ts >= ? "
        f"{settled_clause}"
        f"GROUP BY scanner_family, direction, {_OUTCOME_MARKET_SQL}, horizon, "
        "cost_model_version "
        "ORDER BY scanner_family, direction, market_type, horizon",
        (
            (cost_model_version, cutoff, settled_through)
            if settled_through is not None else (cost_model_version, cutoff)
        ),
    ).fetchall()
    return [dict(r) for r in rows]


def list_due_perp_structure_shadow_outcomes(
    before_ts: str,
    limit: int = 100,
) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    return _select_all(
        "perp_structure_shadow_outcomes",
        where="status = 'pending' AND due_at <= ?",
        params=(before_ts,),
        order="due_at ASC",
        limit=limit,
    )


def settle_perp_structure_shadow_outcome(
    outcome_id: str,
    *,
    exit_price: float,
    directional_return_pct: float,
    settled_at: str,
    price_source: str | None,
    settlement_lag_sec: float | None,
) -> bool:
    from aletheia.storage.repositories import (
        _update_pending,
        transaction,
    )

    with transaction(immediate=True):
        return _update_pending(
            "perp_structure_shadow_outcomes",
            outcome_id,
            {
                "exit_price_usd": exit_price,
                "directional_return_pct": directional_return_pct,
                "settled_at": settled_at,
                "settlement_price_source": price_source,
                "settlement_lag_sec": settlement_lag_sec,
                "status": "settled",
            },
        )


def fail_perp_structure_shadow_outcome(
    outcome_id: str,
    settled_at: str,
    *,
    exit_price: float | None = None,
    settlement_price_source: str | None = None,
) -> bool:
    """Record a terminal "not measurable" verdict for one Position X shadow row.

    See :func:`fail_forward_outcome` for why a rejected observation is recorded
    rather than dropped.
    """
    from aletheia.storage.repositories import (
        _update_pending,
        transaction,
    )

    updates: dict[str, object] = {
        "settled_at": settled_at,
        "status": "failed",
    }
    if exit_price is not None:
        updates["exit_price_usd"] = exit_price
    if settlement_price_source is not None:
        updates["settlement_price_source"] = settlement_price_source
    with transaction(immediate=True):
        return _update_pending("perp_structure_shadow_outcomes", outcome_id, updates)


def prune_perp_structure_shadow_outcomes(days: int = 30) -> int:
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write("DELETE FROM perp_structure_shadow_outcomes WHERE entry_ts < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_universe_scanner_runs(days: int = 30) -> int:
    """Prune universe_scanner_runs older than ``days`` (by started_at)."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    was_in_tx = db.in_transaction
    cur = execute_write("DELETE FROM universe_scanner_runs WHERE started_at < ?", (cutoff,))
    if not was_in_tx:
        db.commit()
    return cur.rowcount or 0


def prune_universe_twin_shadow_evidence(
    days: int = 30,
) -> int:
    """Prune sampled diagnostic evidence after its fixed retention window.

    Records are deleted before their logical parent batch. Canonical
    fixed-horizon outcomes follow their own retention, and Twin writes no
    Learning exposure or verdict, so open experiments do not extend this
    evidence lifetime.
    """
    from aletheia.storage.repositories import (
        _candidate_selection_retention_state,
        execute_write,
        get_db,
        transaction,
    )


    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    db = get_db()
    candidate_policy_available, configured_candidate_epoch = (
        _candidate_selection_retention_state()
    )
    with transaction(immediate=True):
        deleted = 0
        if candidate_policy_available:
            run_rows = db.execute(
                "WITH admissions(policy_epoch_id, admitted_at) AS ("
                "SELECT COALESCE(NULLIF(entity_id, ''), CASE WHEN "
                "json_valid(after_state) THEN json_extract(after_state, "
                "'$.candidate_selection_policy_epoch') END), MIN(timestamp) FROM audit_events "
                "WHERE event_type='candidate_selection_authority_admitted' GROUP BY 1), "
                "protected_epochs(policy_epoch_id) AS ("
                "SELECT policy_epoch_id FROM universe_twin_shadow_batches "
                "WHERE record_version IN ('candidate-selection-utility-record/v1', "
                "'candidate-selection-utility-record/v2', 'candidate-selection-utility-record/v3', "
                "'candidate-selection-utility-record/v4') "
                "AND promotion_eligible=1 AND policy_epoch_id IS NOT NULL "
                "UNION SELECT ? WHERE ? IS NOT NULL "
                "UNION SELECT policy_epoch_id FROM admissions), "
                "training(cutoff) AS (SELECT MAX(json_extract(policy_json, "
                "'$.estimator_training_cutoff')) FROM universe_twin_shadow_batches "
                "WHERE policy_epoch_id IN protected_epochs) "
                "SELECT b.scanner_run_id FROM universe_twin_shadow_batches b CROSS JOIN training "
                "LEFT JOIN admissions a ON a.policy_epoch_id=b.policy_epoch_id "
                "WHERE b.captured_at < ? AND ("
                "b.record_version NOT IN ('candidate-selection-utility-record/v1', "
                "'candidate-selection-utility-record/v2', 'candidate-selection-utility-record/v3', "
                "'candidate-selection-utility-record/v4') OR "
                "((training.cutoff IS NULL OR b.captured_at > training.cutoff) AND ("
                "COALESCE(b.policy_epoch_id, '') NOT IN protected_epochs OR "
                "(a.admitted_at IS NOT NULL AND b.captured_at > a.admitted_at))))",
                (configured_candidate_epoch, configured_candidate_epoch, cutoff),
            ).fetchall()
        else:
            run_rows = db.execute(
                "SELECT scanner_run_id FROM universe_twin_shadow_batches "
                "WHERE captured_at < ? "
                "AND record_version NOT IN ('candidate-selection-utility-record/v1', "
                "'candidate-selection-utility-record/v2', 'candidate-selection-utility-record/v3', "
                "'candidate-selection-utility-record/v4')",
                (cutoff,),
            ).fetchall()
        run_ids = [str(row[0]) for row in run_rows]
        if run_ids:
            placeholders = ", ".join("?" for _ in run_ids)
            deleted += (
                execute_write(
                    f"DELETE FROM universe_twin_shadow_records WHERE scanner_run_id IN ({placeholders})",
                    run_ids,
                ).rowcount
                or 0
            )
            deleted += (
                execute_write(
                    f"DELETE FROM universe_twin_shadow_batches WHERE scanner_run_id IN ({placeholders})",
                    run_ids,
                ).rowcount
                or 0
            )
        deleted += (
            execute_write(
                "DELETE FROM universe_relative_rank_shadow_batches WHERE captured_at < ?",
                (cutoff,),
            ).rowcount
            or 0
        )
    return deleted


def anchor_shadow_trade_exit_price_if_pending(
    shadow_id: str,
    *,
    exit_price: float,
) -> bool:
    """Anchor one legacy unkeyed close-event price without resolving the row."""
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    db = get_db()
    was_in_tx = db.in_transaction
    cursor = execute_write(
        "UPDATE shadow_trades SET exit_price = ? WHERE id = ? "
        "AND outcome_candidate_key IS NULL "
        "AND resolved_at IS NULL AND exit_price IS NULL",
        (exit_price, shadow_id),
    )
    if not was_in_tx:
        db.commit()
    return cursor.rowcount == 1


def resolve_shadow_trade_if_pending(
    shadow_id: str,
    *,
    exit_price: float,
    pnl_pct: float,
    resolved_at: str,
    economics: dict | None = None,
    settlement_price_source: str | None = None,
    settlement_lag_sec: float | None = None,
) -> bool:
    """Resolve one legacy unkeyed shadow once, leaving keyed rows untouched.

    When ``economics`` is provided (dict returned by
    ``aletheia.universe.outcome_economics.estimate_outcome_economics``), the
    cost-model fields are populated in the same UPDATE. Rows resolved without
    ``economics`` remain compatible with the historic minimal-writer contract:
    only ``exit_price``, ``pnl_pct``, ``resolved_at`` change.
    """
    from aletheia.storage.repositories import (
        execute_write,
        get_db,
    )

    db = get_db()
    was_in_tx = db.in_transaction
    if not economics:
        cursor = execute_write(
            "UPDATE shadow_trades SET exit_price = ?, pnl_pct = ?, resolved_at = ? "
            "WHERE id = ? AND outcome_candidate_key IS NULL "
            "AND resolved_at IS NULL",
            (exit_price, pnl_pct, resolved_at, shadow_id),
        )
    else:
        cursor = execute_write(
            "UPDATE shadow_trades SET "
            " exit_price = ?, pnl_pct = ?, "
            " gross_return_pct = ?, net_pnl_pct = ?, "
            " estimated_fee_pct = ?, estimated_spread_slippage_pct = ?, "
            " estimated_funding_pct = ?, min_tradable_edge_pct = ?, "
            " min_cost_covering_edge_pct = ?, "
            " settlement_price_source = ?, settlement_lag_sec = ?, "
            " cost_model_version = ?, resolved_at = ? "
            "WHERE id = ? AND outcome_candidate_key IS NULL "
            "AND resolved_at IS NULL",
            (
                exit_price,
                pnl_pct,
                economics.get("gross_return_pct"),
                economics.get("net_return_pct"),
                economics.get("estimated_fee_pct"),
                economics.get("estimated_spread_slippage_pct"),
                economics.get("estimated_funding_pct"),
                economics.get("min_tradable_edge_pct"),
                economics.get("min_cost_covering_edge_pct"),
                settlement_price_source,
                settlement_lag_sec,
                economics.get("cost_model_version"),
                resolved_at,
                shadow_id,
            ),
        )
    if not was_in_tx:
        db.commit()
    return cursor.rowcount == 1


def list_shadow_trades(
    batch_id: str | None = None,
    scanner_family: str | None = None,
    opportunity_id: str | None = None,
    was_selected: bool | None = None,
    resolved: bool | None = None,
    limit: int = 200,
) -> list[dict]:
    from aletheia.storage.repositories import _select_all

    conditions: list[str] = ["invalidated_at IS NULL"]
    params: list = []
    if batch_id is not None:
        conditions.append("batch_id = ?")
        params.append(batch_id)
    if scanner_family is not None:
        conditions.append("scanner_family = ?")
        params.append(scanner_family)
    if opportunity_id is not None:
        conditions.append("opportunity_id = ?")
        params.append(opportunity_id)
    if was_selected is not None:
        conditions.append("was_selected = ?")
        params.append(int(was_selected))
    if resolved is True:
        conditions.append("resolved_at IS NOT NULL")
    elif resolved is False:
        conditions.append("resolved_at IS NULL")
    where = " AND ".join(conditions) if conditions else None
    return _select_all("shadow_trades", where=where, params=tuple(params), limit=limit)


def list_universe_feature_snapshots(limit: int = 50) -> list[dict]:
    """Return recent feature snapshots with their registry instrument identity.

    ``universe_feature_snapshots`` stores only ``asset_id``; consumers that need
    a symbol or market must resolve it through ``universe_registry`` instead of
    treating an unjoined row as an incomplete feature vector.
    """
    from aletheia.storage.repositories import get_db

    db = get_db()
    rows = db.execute(
        "SELECT ufs.*, ur.symbol, ur.market_type, ur.venue "
        "FROM universe_feature_snapshots ufs "
        "JOIN universe_registry ur ON ur.id = ufs.asset_id "
        "ORDER BY datetime(ufs.ts) DESC, ufs.rowid DESC LIMIT ?",
        (max(0, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]

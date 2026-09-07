"""Deterministic, resumable import of the legacy Learning Experiment ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aletheia.observation_lab.db import lab_transaction, readonly_lab_db

LEGACY_TABLES = (
    "learning_hypotheses",
    "learning_hypothesis_nominations",
    "learning_experiments",
    "learning_exposures",
    "learning_contribution_verdicts",
    "learning_inference_decisions",
    "learning_lifecycle_events",
    "filter_shadow_experiments",
    "filter_shadow_evaluations",
    "strategy_experiments",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _legacy_source_cutoff(value: Any, *, source_kind: str) -> str:
    """Preserve an old evidence boundary without inventing a Main watermark."""
    return _json({
        "source_name": "main",
        "main_import_contiguous_sequence": None,
        "legacy_source_kind": source_kind,
        "legacy_source_cutoff": str(value or ""),
    })


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _rows(db: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not _table_exists(db, table):
        return []
    return [dict(row) for row in db.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()]


def _table_hash(rows: list[dict[str, Any]]) -> str:
    return _hash(rows)


def _source_commit() -> str | None:
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit

        return get_boot_commit()
    except Exception:
        return None


def _legacy_snapshot(db: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    return {table: _rows(db, table) for table in LEGACY_TABLES}


def _plan_from_snapshot(
    snapshot: dict[str, list[dict[str, Any]]], *, lab_path: Path | None = None,
) -> dict[str, Any]:
    experiments = snapshot["learning_experiments"]
    hypotheses = {str(row.get("hypothesis_id")): row for row in snapshot["learning_hypotheses"]}
    experiment_ids = {str(row.get("experiment_id")) for row in experiments}
    unprovable: list[dict[str, Any]] = []
    for row in experiments:
        experiment_id = str(row.get("experiment_id") or "")
        hypothesis_id = str(row.get("hypothesis_id") or "")
        if not experiment_id or not hypothesis_id or hypothesis_id not in hypotheses:
            unprovable.append({"table": "learning_experiments", "id": experiment_id or None, "reason": "hypothesis_identity_missing"})
    for row in snapshot["learning_exposures"]:
        experiment_id = str(row.get("experiment_id") or "")
        if experiment_id not in experiment_ids:
            unprovable.append({"table": "learning_exposures", "id": row.get("exposure_id"), "reason": "experiment_identity_missing"})
        for field in ("before_json", "after_json", "manifest_json"):
            value = row.get(field)
            if value:
                try:
                    json.loads(value)
                except (TypeError, ValueError):
                    unprovable.append({"table": "learning_exposures", "id": row.get("exposure_id"), "reason": f"{field}_invalid"})
    for row in snapshot["strategy_experiments"]:
        # The old strategy engine is retired; preserve its bytes as explicit,
        # non-authorizing historical evidence rather than reviving a runtime.
        unprovable.append({
            "table": "strategy_experiments",
            "id": row.get("id"),
            "reason": "retired_strategy_engine",
        })
    active = [
        {
            "experiment_id": row.get("experiment_id"),
            "stage": row.get("stage"),
            "status": row.get("status"),
            "started_at": row.get("started_at"),
        }
        for row in experiments
        if row.get("ended_at") is None and row.get("status") not in {"invalidated", "completed_no_support"}
    ]
    active.extend(
        {
        "experiment_id": str(row.get("id") or ""),
            "stage": row.get("stage") or "shadow",
            "status": row.get("status"),
            "started_at": row.get("started_at"),
        }
        for row in snapshot["filter_shadow_experiments"]
        if row.get("status") == "collecting"
    )
    fixed_look = [
        {
            "experiment_id": row.get("experiment_id"),
            "status": row.get("status"),
            "cutoff_fingerprint": row.get("cutoff_fingerprint"),
            "consumed_verdict_id": row.get("consumed_verdict_id"),
        }
        for row in snapshot["learning_inference_decisions"]
    ]
    return {
        "available": True,
        "source_commit": _source_commit(),
        "legacy_tables": {
            table: {"row_count": len(rows), "sha256": _table_hash(rows)}
            for table, rows in snapshot.items()
        },
        "active_legacy_experiments": active,
        "fixed_look_state": fixed_look,
        "unprovable_rows": unprovable,
        "estimated_lab_write_volume": {
            "windows": len(experiments) + len(snapshot["filter_shadow_experiments"]),
            "nominations": len(experiments) + len(snapshot["filter_shadow_experiments"])
            + len(snapshot["learning_hypothesis_nominations"])
            + len(snapshot["strategy_experiments"]),
            "contracts": len(experiments) + len(snapshot["filter_shadow_experiments"]),
            "exposures": len(snapshot["learning_exposures"]),
            "evaluations": len(snapshot["learning_contribution_verdicts"])
            + len(snapshot["filter_shadow_evaluations"]),
            "lifecycle_events": len(snapshot["learning_lifecycle_events"]),
        },
        "backup_checkpoint_readiness": {
            "lab_db_present": bool(lab_path and lab_path.is_file()),
            "reason": "ready" if lab_path and lab_path.is_file() else "lab_db_not_initialized",
        },
        "plan_fingerprint": _hash({table: _table_hash(rows) for table, rows in snapshot.items()}),
    }


def plan_legacy_learning_migration() -> dict[str, Any]:
    """Read the legacy ledger without creating or mutating Lab state."""
    from aletheia.storage.sqlite import readonly_db
    from aletheia.config.loader import get_data_dir

    with readonly_db(allow_missing=True) as db:
        if db is None:
            return {"available": False, "reason": "app_db_unavailable", "source_commit": _source_commit()}
        lab_path = get_data_dir() / "observation-lab" / "lab.db"
        return _plan_from_snapshot(
            _legacy_snapshot(db),
            lab_path=lab_path if lab_path.is_file() else None,
        )


def _insert_root(db: sqlite3.Connection, experiment: dict[str, Any], hypothesis: dict[str, Any] | None) -> tuple[str, int, bool]:
    experiment_id = str(experiment.get("experiment_id") or "")
    generation = max(1, int(experiment.get("stage_generation") or 1))
    prefix = str(experiment.get("_observation_prefix") or "legacy-learning")
    observation_id = f"{prefix}:{experiment_id}"
    stage = str(experiment.get("stage") or "legacy_unknown")
    raw_status = str(experiment.get("status") or "")
    terminal = experiment.get("ended_at") is not None or raw_status in {
        "invalidated",
        "completed_no_support",
        "refrozen",
        "retired",
        "invalid",
        "closed",
        "finalized",
        "aborted",
        "negative",
    }
    unverified_paper = stage.startswith("paper_") and not experiment.get("activation_verdict_id")
    status = (
        "invalidated"
        if unverified_paper or raw_status in {"invalidated", "invalid", "refrozen", "negative"}
        else "aborted"
        if raw_status == "aborted"
        else "finalized"
        if terminal
        else "active"
    )
    spec = {
        "legacy_import": "learning-ledger/v1",
        "legacy_experiment": experiment,
        "legacy_hypothesis": hypothesis,
        "authorizing": False,
        "unverified": unverified_paper,
    }
    spec_json = _json(spec)
    spec_hash = _hash(spec)
    started_at = str(experiment.get("started_at") or experiment.get("created_at") or _now())
    collect_until = str(experiment.get("ended_at") or started_at)
    if collect_until <= started_at:
        collect_until = "9999-12-31T00:00:00+00:00" if not terminal else started_at
    finalize_not_before = str(experiment.get("ended_at") or collect_until)
    db.execute(
        "INSERT OR IGNORE INTO observation_windows "
        "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, spec_json, spec_hash, "
        "producer_commit, started_at, collect_until, finalize_not_before, terminal_at, terminal_reason) "
        "VALUES (?, 'legacy_learning', 'legacy-import/v1', ?, ?, 'observe_only', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            observation_id, generation, f"legacy-learning:{experiment_id}", status, spec_json, spec_hash,
            _source_commit(), started_at, collect_until, finalize_not_before,
            experiment.get("ended_at") or (_now() if terminal else None),
            "legacy_unverified" if unverified_paper else "legacy_import",
        ),
    )
    row = db.execute(
        "SELECT spec_hash, status FROM observation_windows WHERE observation_id=? AND generation=?",
        (observation_id, generation),
    ).fetchone()
    if row is None or row[0] != spec_hash:
        raise sqlite3.IntegrityError("legacy_observation_identity_conflict")
    return observation_id, generation, bool(unverified_paper)


def _insert_contract(db: sqlite3.Connection, experiment: dict[str, Any], hypothesis: dict[str, Any] | None, observation_id: str, generation: int) -> None:
    experiment_id = str(experiment.get("experiment_id") or "")
    policy_hash = _hash({"experiment": experiment, "hypothesis": hypothesis})
    started_at = str(experiment.get("started_at") or experiment.get("created_at") or _now())
    identity = {
        "observation_id": observation_id,
        "generation": generation,
        "stage": str(experiment.get("stage") or "legacy_unknown"),
        "parent_generation": None,
        "assignment_policy": str(experiment.get("assignment_policy") or "prospective_first_exposure"),
        "assignment_namespace_key": experiment_id,
        "assignment_salt": str(experiment.get("assignment_salt") or "legacy-unavailable"),
        "assignment_unit_key_template": "{candidate_key}",
        "assignment_hash_version": "sha256-prefix16/0xffffffffffffffff",
        "allocation_pct": float(experiment.get("allocation_pct") or 0.0),
        "gates_json": str(experiment.get("promotion_gate_json") or "{}"),
        "harm_budget_json": str(experiment.get("harm_gate_json") or "{}"),
        "target_horizon": str((hypothesis or {}).get("target_horizon") or "legacy_unknown"),
        "outcome_contract_version": "legacy-import/v1",
        "metric_version": str(experiment.get("primary_metric") or "legacy_unknown"),
        "behavior_contract_hash": policy_hash,
        "behavior_policy_epoch_ref": None,
        "behavior_policy_epoch_hash": None,
        "owner_admission_ref": None,
        "started_at": started_at,
        "effective_until": experiment.get("ended_at"),
        "terminal_status": experiment.get("status") if experiment.get("ended_at") else None,
        "terminal_at": experiment.get("ended_at"),
    }
    contract_hash = _hash(identity)
    values = (*identity.values(), contract_hash, _now())
    columns = ", ".join((*identity, "contract_hash", "created_at"))
    placeholders = ", ".join("?" for _ in values)
    db.execute(
        f"INSERT OR IGNORE INTO observation_generation_contracts ({columns}) VALUES ({placeholders})",
        values,
    )
    existing = db.execute(
        "SELECT contract_hash FROM observation_generation_contracts WHERE observation_id=? AND generation=?",
        (observation_id, generation),
    ).fetchone()
    if existing is None or existing[0] != contract_hash:
        raise sqlite3.IntegrityError("legacy_contract_identity_conflict")


def _filter_shadow_experiment_record(row: dict[str, Any]) -> dict[str, Any]:
    """Project the old filter-shadow row into the generic Experiment shape."""
    return {
        "experiment_id": str(row.get("id") or ""),
        "hypothesis_id": None,
        "stage": row.get("stage") or "shadow",
        "assignment_salt": f"legacy-filter-shadow:{row.get('id')}",
        "allocation_pct": 0.5,
        "promotion_gate_json": _json({
            "min_samples": row.get("min_samples"),
            "min_coverage": row.get("min_coverage"),
            "min_edge_delta_pct": row.get("min_edge_delta_pct"),
        }),
        "harm_gate_json": "{}",
        "status": row.get("status") or "collecting",
        "started_at": row.get("started_at") or row.get("created_at"),
        "ended_at": row.get("completed_at"),
        "primary_metric": "legacy-filter-shadow",
        "_observation_prefix": "legacy-filter-shadow",
    }


def _decode_json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise ValueError("legacy_payload_must_be_object")
    return decoded


def _strict_bool(value: Any) -> bool | None:
    """Return an explicit legacy boolean without coercing unknown bytes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _insert_exposures(db: sqlite3.Connection, rows: list[dict[str, Any]], experiments: dict[str, dict[str, Any]], roots: dict[str, tuple[str, int, bool]]) -> tuple[int, int]:
    inserted = unprovable = 0
    for row in rows:
        experiment_id = str(row.get("experiment_id") or "")
        root = roots.get(experiment_id)
        if root is None:
            unprovable += 1
            continue
        observation_id, generation, root_unverified = root
        manifest: dict[str, Any] = {}
        manifest_valid = True
        try:
            before = _decode_json(row.get("before_json"))
            after = _decode_json(row.get("after_json"))
            manifest = _decode_json(row.get("manifest_json"))
        except (TypeError, ValueError, json.JSONDecodeError):
            before, after, manifest = {}, {}, {}
            manifest_valid = False
            root_unverified = True
        exposure_id = str(row.get("exposure_id") or "")
        source_receipt_id = (
            f"legacy:{exposure_id}"
            if exposure_id
            else f"legacy:row:{_hash(row)}"
        )
        # The Lab identity is namespaced by importer/source.  Keeping the raw
        # legacy id as the physical key can collide with a future Main receipt
        # (and with another imported ledger), while ``source_receipt_id`` keeps
        # the exact historical identity for replay/audit.
        target_exposure_id = (
            f"legacy:{exposure_id}"
            if exposure_id
            else hashlib.sha256(source_receipt_id.encode()).hexdigest()[:24]
        )
        baseline_hash = _hash(before)
        applied_hash = _hash(after)
        candidate = str(row.get("candidate_key") or f"legacy-exposure:{exposure_id}")
        assignment_key = str(row.get("assignment_unit_key") or candidate)
        assignment = str(row.get("assignment") or "unassigned")
        if assignment not in {"treatment", "control", "unassigned"}:
            assignment = "unassigned"

        # Preserve legacy authority/effect evidence when it is explicit.  A
        # malformed or ambiguous payload remains historical evidence only;
        # never infer a behavior-bearing exposure from a missing flag.
        explicit_applied = _strict_bool(manifest.get("authority_applied"))
        explicit_changed = _strict_bool(manifest.get("behavior_changed"))
        applied = int(explicit_applied is True and assignment == "treatment" and not root_unverified)
        behavior_changed = int(
            applied
            and (
                explicit_changed is True
                if explicit_changed is not None
                else after != before
            )
            and after != before
        )
        if not manifest_valid:
            applied = behavior_changed = 0

        active_manifest_snapshot = manifest.get("active_manifest_snapshot")
        if not isinstance(active_manifest_snapshot, list) or not all(
            isinstance(item, str) and item.strip() for item in active_manifest_snapshot
        ):
            active_manifest_snapshot = []
        else:
            active_manifest_snapshot = [str(item) for item in active_manifest_snapshot]
        manifest_set_fingerprint = manifest.get("manifest_set_fingerprint")
        if not isinstance(manifest_set_fingerprint, str) or not manifest_set_fingerprint.strip():
            manifest_set_fingerprint = _hash(active_manifest_snapshot) if active_manifest_snapshot else None
        revocation_snapshot_fingerprint = manifest.get("revocation_snapshot_fingerprint")
        if not isinstance(revocation_snapshot_fingerprint, str) or not revocation_snapshot_fingerprint.strip():
            revocation_snapshot_fingerprint = None
        manifest_id = manifest.get("manifest_id") or row.get("manifest_id")
        manifest_hash = manifest.get("manifest_hash") or row.get("manifest_hash")
        behavior_policy_epoch_ref = (
            manifest.get("behavior_policy_epoch_ref")
            or row.get("behavior_policy_epoch_ref")
            or row.get("concurrent_policy_epoch")
        )
        values = (
            target_exposure_id, source_receipt_id,
            None, observation_id, generation, manifest_id, manifest_hash, assignment_key, candidate,
            assignment, applied, behavior_changed, baseline_hash, applied_hash, _json(before), _json(after), row.get("outcome_candidate_key"),
            row.get("outcome_source"), "legacy_unverified" if root_unverified else "legacy_import",
            row.get("behavior_contract_hash") or manifest.get("behavior_contract_hash"), behavior_policy_epoch_ref,
            _json(active_manifest_snapshot), manifest_set_fingerprint, revocation_snapshot_fingerprint,
            "legacy", None, row.get("code_commit"), row.get("exposed_at") or _now(), _now(),
        )
        db.execute(
            "INSERT OR IGNORE INTO observation_exposures "
            "(exposure_id, source_receipt_id, receipt_sequence, observation_id, generation, manifest_id, manifest_hash, "
            "assignment_unit_key, candidate_key, assignment, applied, behavior_changed, baseline_payload_hash, applied_payload_hash, "
            "baseline_payload_json, applied_payload_json, outcome_candidate_key, outcome_source, resolution_reason, behavior_contract_hash, "
            "behavior_policy_epoch_ref, active_manifest_snapshot_json, manifest_set_fingerprint, revocation_snapshot_fingerprint, authority_source, "
            "runtime_session_id, producer_commit, exposed_at, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            values,
        )
        inserted += 1
    return inserted, unprovable


def apply_legacy_learning_migration(
    *,
    archive_root: str | None = None,
    retire_legacy_schema: bool = False,
    runtime_commit: str | None = None,
    rollback_backup_identity: str | None = None,
) -> dict[str, Any]:
    """Import one app snapshot into Lab; safe to rerun after interruption."""
    from aletheia.storage.sqlite import read_snapshot

    if retire_legacy_schema and not str(rollback_backup_identity or "").strip():
        raise ValueError("rollback_backup_identity_required")

    with read_snapshot() as app:
        snapshot = _legacy_snapshot(app)
    plan = _plan_from_snapshot(snapshot)
    if not plan["available"]:
        return plan
    # A cursor fingerprint is the identity of the legacy snapshot already
    # projected into Lab.  Do not overwrite it when the source changes: an
    # INSERT OR IGNORE retry would otherwise leave stale Lab rows while making
    # the new, unverified source look imported.
    with readonly_lab_db(allow_missing=True) as existing_lab:
        if existing_lab is not None:
            existing_cursor = existing_lab.execute(
                "SELECT source_checkpoint_fingerprint FROM observation_import_cursors "
                "WHERE source_name='legacy_learning'"
            ).fetchone()
            if (
                existing_cursor is not None
                and str(existing_cursor[0] or "")
                and str(existing_cursor[0]) != str(plan["plan_fingerprint"])
            ):
                raise RuntimeError("legacy_import_source_changed")
    experiments = {str(row.get("experiment_id")): row for row in snapshot["learning_experiments"]}
    hypotheses = {str(row.get("hypothesis_id")): row for row in snapshot["learning_hypotheses"]}
    roots: dict[str, tuple[str, int, bool]] = {}
    imported_windows = imported_contracts = 0
    imported_exposures = imported_evaluations = imported_lifecycle = 0
    imported_inference_plans = imported_nominations = 0
    unprovable = len(plan["unprovable_rows"])
    with lab_transaction(immediate=True) as lab:
        for experiment_id, experiment in experiments.items():
            root = _insert_root(lab, experiment, hypotheses.get(str(experiment.get("hypothesis_id"))))
            roots[experiment_id] = root
            imported_windows += 1
            source_nomination = f"legacy:experiment:{experiment_id}"
            hypothesis = hypotheses.get(str(experiment.get("hypothesis_id")))
            snapshot_json = _json({"experiment": experiment, "hypothesis": hypothesis})
            lab.execute(
                "INSERT OR IGNORE INTO observation_nominations "
                "(nomination_id, source_nomination_id, source_hash, observation_id, generation, disposition, snapshot_json, rejection_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (source_nomination, source_nomination, _hash(snapshot_json), root[0], root[1], "admitted", snapshot_json, "legacy_import", _now()),
            )
            _insert_contract(lab, experiment, hypothesis, root[0], root[1])
            imported_contracts += 1
        for shadow in snapshot["filter_shadow_experiments"]:
            legacy = _filter_shadow_experiment_record(shadow)
            experiment_id = str(legacy["experiment_id"])
            root = _insert_root(lab, legacy, None)
            roots[experiment_id] = root
            snapshot_json = _json({"filter_shadow_experiment": shadow, "authorizing": False})
            lab.execute(
                "INSERT OR IGNORE INTO observation_nominations "
                "(nomination_id, source_nomination_id, source_hash, observation_id, generation, disposition, snapshot_json, rejection_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"legacy:{shadow.get('id')}", f"legacy:filter-shadow:{shadow.get('id')}",
                    _hash(snapshot_json), root[0], root[1], "admitted", snapshot_json,
                    "legacy_import", shadow.get("created_at") or _now(),
                ),
            )
            _insert_contract(lab, legacy, None, root[0], root[1])
            imported_windows += 1
            imported_contracts += 1
        for old_nomination in snapshot["learning_hypothesis_nominations"]:
            source_id = str(old_nomination.get("nomination_id") or _hash(old_nomination))
            source_nomination_id = f"legacy:nomination:{source_id}"
            hypothesis_id = str(old_nomination.get("hypothesis_id") or "")
            matching = next(
                (root for exp_id, root in roots.items()
                 if str(experiments.get(exp_id, {}).get("hypothesis_id") or "") == hypothesis_id),
                (None, None, False),
            )
            envelope = old_nomination.get("envelope_json") or "{}"
            try:
                envelope_value = json.loads(envelope)
            except (TypeError, ValueError):
                envelope_value = {"legacy_unverified_envelope": str(envelope)}
            disposition = (
                "admitted" if str(old_nomination.get("publication_status") or "") in {"published", "admitted"}
                else "rejected" if str(old_nomination.get("publication_status") or "") in {"rejected", "invalidated"}
                else "pending"
            )
            lab.execute(
                "INSERT OR IGNORE INTO observation_nominations "
                "(nomination_id, source_nomination_id, source_hash, observation_id, generation, disposition, snapshot_json, rejection_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source_nomination_id, source_nomination_id,
                    _hash(old_nomination), matching[0], matching[1], disposition,
                    _json(envelope_value), "legacy_unverified" if matching[0] is None else None,
                    old_nomination.get("created_at") or _now(),
                ),
            )
            imported_nominations += 1
        for old_strategy in snapshot["strategy_experiments"]:
            source_id = str(old_strategy.get("id") or _hash(old_strategy))
            source_nomination_id = f"legacy:strategy:{source_id}"
            snapshot_json = _json({
                "strategy_experiment": old_strategy,
                "authorizing": False,
            })
            lab.execute(
                "INSERT OR IGNORE INTO observation_nominations "
                "(nomination_id, source_nomination_id, source_hash, observation_id, generation, disposition, snapshot_json, rejection_reason, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, 'rejected', ?, ?, ?)",
                (
                    source_nomination_id,
                    source_nomination_id,
                    _hash(snapshot_json),
                    snapshot_json,
                    "retired_strategy_engine",
                    old_strategy.get("created_at") or _now(),
                ),
            )
            imported_nominations += 1
        imported_exposures, exposure_gaps = _insert_exposures(
            lab, snapshot["learning_exposures"], experiments, roots,
        )
        unprovable += exposure_gaps
        for row in snapshot["learning_contribution_verdicts"]:
            root = roots.get(str(row.get("experiment_id")))
            if root is None:
                unprovable += 1
                continue
            result = dict(row)
            evaluation_id = f"legacy:{row.get('verdict_id') or _hash(row)}"
            source_cutoff = _legacy_source_cutoff(
                row.get("window_end") or row.get("created_at"),
                source_kind="learning_contribution_verdict",
            )
            lab.execute(
                "INSERT OR IGNORE INTO observation_evaluations "
                "(evaluation_id, observation_id, generation, evaluation_kind, source_cutoff, input_fingerprint, exposure_fingerprint, outcome_fingerprint, result_json, verdict, disposition, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (evaluation_id, root[0], root[1], "confirmatory" if row.get("inference_role") == "confirmatory" else "monitoring", source_cutoff, _hash(row), _hash({"experiment_id": row.get("experiment_id")}), _hash(row.get("harm_metrics_json") or "{}"), _json(result), str(row.get("verdict") or "legacy_unverified"), "legacy_unverified", row.get("created_at") or _now()),
            )
            lab.execute(
                "INSERT OR IGNORE INTO observation_results "
                "(result_id, observation_id, generation, spec_hash, evaluation_id, source_cutoff, verdict, "
                "disposition, result_json, result_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"legacy:result:{row.get('verdict_id') or _hash(row)}", root[0], root[1],
                    str(lab.execute(
                        "SELECT spec_hash FROM observation_windows WHERE observation_id=? AND generation=?",
                        (root[0], root[1]),
                    ).fetchone()[0]), evaluation_id,
                    source_cutoff,
                    str(row.get("verdict") or "legacy_unverified"), "legacy_unverified",
                    _json(result), _hash({"evaluation_id": evaluation_id, "result": result}),
                    row.get("created_at") or _now(),
                ),
            )
            imported_evaluations += 1
        verdict_ids = {str(row.get("verdict_id")) for row in snapshot["learning_contribution_verdicts"]}
        for row in snapshot["learning_inference_decisions"]:
            root = roots.get(str(row.get("experiment_id")))
            if root is None:
                unprovable += 1
                continue
            raw_plan = row.get("plan_json") or "{}"
            try:
                plan_value = json.loads(raw_plan)
                if not isinstance(plan_value, dict):
                    raise ValueError("legacy_plan_must_be_object")
            except (TypeError, ValueError, json.JSONDecodeError):
                plan_value = {"legacy_unverified_plan": str(raw_plan)}
            status = {
                "collecting": "collecting",
                "cutoff_frozen": "frozen",
                "consumed": "consumed",
                "invalidated": "invalidated",
            }.get(str(row.get("status") or ""), "invalidated")
            cutoff_json = row.get("cutoff_json")
            cutoff_fingerprint = row.get("cutoff_fingerprint")
            consumed_id = row.get("consumed_verdict_id")
            if status == "consumed" and str(consumed_id) not in verdict_ids:
                status, consumed_id = "invalidated", None
            plan_id = f"legacy:inference:{row.get('experiment_id')}"
            lab.execute(
                "INSERT OR IGNORE INTO observation_inference_plans "
                "(plan_id, observation_id, generation, inference_contract_version, plan_json, plan_hash, status, cutoff_json, cutoff_fingerprint, cutoff_frozen_at, consumed_evaluation_id, consumed_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan_id, root[0], root[1], row.get("inference_contract_version") or "legacy-import/v1",
                    _json(plan_value), str(row.get("plan_hash") or _hash(plan_value)), status,
                    cutoff_json, cutoff_fingerprint, row.get("cutoff_frozen_at"),
                    f"legacy:{consumed_id}" if consumed_id else None, row.get("consumed_at"),
                    row.get("created_at") or _now(), row.get("updated_at") or row.get("created_at") or _now(),
                ),
            )
            imported_inference_plans += 1
        for row in snapshot["filter_shadow_evaluations"]:
            root = roots.get(str(row.get("experiment_id") or ""))
            if root is None:
                unprovable += 1
                continue
            evaluation_id = f"legacy:filter-shadow-evaluation:{row.get('id') or _hash(row)}"
            source_cutoff = _legacy_source_cutoff(
                row.get("evidence_through_at") or row.get("created_at"),
                source_kind="filter_shadow_evaluation",
            )
            lab.execute(
                "INSERT OR IGNORE INTO observation_evaluations "
                "(evaluation_id, observation_id, generation, evaluation_kind, source_cutoff, input_fingerprint, exposure_fingerprint, outcome_fingerprint, result_json, verdict, disposition, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    evaluation_id, root[0], root[1], "monitoring",
                    source_cutoff,
                    _hash(row), _hash({"experiment_id": row.get("experiment_id")}),
                    _hash(row.get("note") or ""), _json(row), "legacy_unverified",
                    "legacy_unverified", row.get("created_at") or _now(),
                ),
            )
            lab.execute(
                "INSERT OR IGNORE INTO observation_results "
                "(result_id, observation_id, generation, spec_hash, evaluation_id, source_cutoff, verdict, "
                "disposition, result_json, result_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"legacy:filter-shadow-result:{row.get('id') or _hash(row)}", root[0], root[1],
                    str(lab.execute(
                        "SELECT spec_hash FROM observation_windows WHERE observation_id=? AND generation=?",
                        (root[0], root[1]),
                    ).fetchone()[0]), evaluation_id,
                    source_cutoff,
                    "legacy_unverified", "legacy_unverified", _json(row),
                    _hash({"evaluation_id": evaluation_id, "result": row}),
                    row.get("created_at") or _now(),
                ),
            )
            imported_evaluations += 1
        for row in snapshot["learning_lifecycle_events"]:
            root = roots.get(str(row.get("experiment_id")))
            if root is None:
                unprovable += 1
                continue
            event_id = f"legacy:{row.get('event_id') or _hash(row)}"
            lab.execute(
                "INSERT OR IGNORE INTO observation_lifecycle_events "
                "(event_id, dedup_key, observation_id, generation, from_status, to_status, action, reason, actor, source_receipt_id, audit_identity, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, event_id, root[0], root[1], row.get("from_stage"), row.get("to_stage") or "legacy_unverified", row.get("action") or "legacy_import", row.get("reason") or "legacy_import", "legacy_import", None, f"legacy-learning:{event_id}", row.get("created_at") or _now()),
            )
            imported_lifecycle += 1
        lab.execute(
            "INSERT INTO observation_import_cursors "
            "(source_name, contiguous_sequence, retry_sequences_json, source_checkpoint_json, source_checkpoint_fingerprint, updated_at) "
            "VALUES ('legacy_learning', 0, '[]', ?, ?, ?) ON CONFLICT(source_name) DO UPDATE SET source_checkpoint_json=excluded.source_checkpoint_json, source_checkpoint_fingerprint=excluded.source_checkpoint_fingerprint, updated_at=excluded.updated_at",
            (_json({"tables": plan["legacy_tables"], "source_commit": plan["source_commit"]}), plan["plan_fingerprint"], _now()),
        )
        archived = 0
        for observation_id, generation, _ in roots.values():
            row = lab.execute(
                "SELECT status FROM observation_windows WHERE observation_id=? AND generation=?",
                (observation_id, generation),
            ).fetchone()
            if row and row[0] in {"finalized", "invalidated", "aborted"}:
                # The archive writer is itself idempotent and verifies the bytes
                # before recording the immutable archive index.
                from aletheia.observation_lab.canonical import write_generation_archive

                write_generation_archive(observation_id, generation, archive_root=archive_root)
                archived += 1
    result = {
        "available": True,
        "plan_fingerprint": plan["plan_fingerprint"],
        "source_commit": plan["source_commit"],
        "imported": {
            "windows": imported_windows,
            "contracts": imported_contracts,
            "exposures": imported_exposures,
            "evaluations": imported_evaluations,
            "lifecycle_events": imported_lifecycle,
            "inference_plans": imported_inference_plans,
            "nominations": imported_nominations,
            "archived": archived,
        },
        "unprovable_rows": unprovable,
        "idempotent": True,
    }
    if retire_legacy_schema:
        from aletheia.observation_lab.canonical import (
            build_authority_cutover_proof,
            set_authority_source,
        )
        from aletheia.observation_lab.checkpoint import (
            compute_joint_checkpoint,
            save_joint_checkpoint,
        )
        from aletheia.storage.migrations import retire_legacy_experiment_tables

        # The explicit retirement flag is the cutover command: import first,
        # then record a joint checkpoint and the complete cutover proof before
        # selecting Lab authority, and finally remove legacy runtime tables.
        save_joint_checkpoint(compute_joint_checkpoint(notes="legacy_import_cutover"))
        proof = build_authority_cutover_proof(
            runtime_commit=runtime_commit,
            rollback_backup_identity=rollback_backup_identity,
        )
        set_authority_source(
            "lab",
            actor="migration",
            reason="legacy_import_and_schema_retirement",
            cutover_proof=proof,
        )
        result["legacy_schema_retirement"] = retire_legacy_experiment_tables()
    return result


__all__ = ["apply_legacy_learning_migration", "plan_legacy_learning_migration"]

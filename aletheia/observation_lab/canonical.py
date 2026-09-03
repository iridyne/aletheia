"""Canonical Observation ownership primitives.

This module is the narrow bridge between the Main fact plane and the Lab
Experiment plane.  It intentionally does not know about Risk, Orders, or
exchange credentials.  Main callers pass a production transaction connection
when they need the delivery receipt to commit atomically with their fact.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

from aletheia.observation_lab.db import (
    lab_transaction,
    readonly_lab_db,
)
from aletheia.observation_lab.manifest import (
    ExecutionManifest,
    ManifestResolution,
    load_active_manifests,
    resolve_treatment,
)
from aletheia.config.loader import get_data_dir

_log = logging.getLogger("aletheia.observation_lab.canonical")
_SUPPORTED_MANIFEST_VERSIONS = frozenset({"execution-manifest/v1", "execution-manifest/v2"})
_SAFETY_ACTIONS = frozenset({"withdraw", "refreeze", "invalidate", "revoke", "adopt"})
_OWNER_ACTIONS = _SAFETY_ACTIONS | frozenset({"admit", "close", "promote", "hold", "finalize"})
_OUTBOX_CAPACITY = 10_000

# A producer-only capture may lack a Lab Generation identity.  It remains
# durable transport/audit evidence, never an Exposure or an authority input.
# Treatment receipts and owner actions stay identity-strict in the importer.
_CAPTURE_RECEIPT_REASONS = frozenset({
    "adapter_unavailable", "authority_source_legacy", "authority_source_unavailable",
    "outbox_backpressure", "lab_unavailable", "lab_projection_unavailable",
    "observation_generation_not_active", "manifest_revoked", "adapter_version_incompatible",
    "adapter_eligibility_unavailable", "no_eligible_manifest", "manifest_conflict_no_admitted_match",
    "factorial_requires_versioned_adapter", "manifest_contract_unsupported", "baseline_manifest_mismatch",
    "main_deny_tombstone", "not_effective_yet", "effective_window_expired", "malformed_input",
    "malformed_effective_window", "assignment_namespace_unavailable", "assignment_hash_version_unsupported",
    "allocation_unavailable", "control_baseline_mismatch", "owner_admission_unavailable",
    "effect_mode_unsupported", "adapter_output_unavailable", "outcome_seed_unavailable",
    "joint_checkpoint_unavailable", "observe_only_generation",
})


def _capture_receipt_reason_allowed(reason: object) -> bool:
    """Recognize bounded resolver refusals that carry no Generation identity."""
    value = str(reason or "")
    return value in _CAPTURE_RECEIPT_REASONS or value.startswith("undeclared_conflict:")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _utc_text(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}_must_be_utc") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError(f"{field}_must_be_utc")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _text(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field}_required")
    return result


def _normalise_source_cutoff(
    value: object,
    *,
    connection: sqlite3.Connection | None = None,
) -> str:
    """Validate the immutable Main source cut used by one Evaluation.

    The Lab evaluator may only publish a structured cut.  The short
    ``main:sequence=N`` spelling remains a compatibility input, but is
    canonicalised before persistence.  When a Main import cursor is available
    in the same Lab transaction, a cut ahead of that cursor is impossible
    evidence and is rejected rather than silently treating moving outcomes as
    part of a fixed look.
    """
    text = _text(value, "source_cutoff")
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("source_cutoff_invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("source_cutoff_invalid")
    else:
        prefix = "main:sequence="
        if not text.startswith(prefix):
            raise ValueError("source_cutoff_invalid")
        raw_sequence = text[len(prefix):].strip()
        if not raw_sequence or not raw_sequence.isdigit():
            raise ValueError("source_cutoff_invalid")
        payload = {"main_import_contiguous_sequence": int(raw_sequence)}

    if "main_import_contiguous_sequence" not in payload:
        raise ValueError("source_cutoff_watermark_required")
    source_name = payload.get("source_name", "main")
    if source_name != "main":
        raise ValueError("source_cutoff_source_invalid")
    sequence = payload["main_import_contiguous_sequence"]
    if sequence is not None and (
        isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
    ):
        raise ValueError("source_cutoff_watermark_invalid")
    collect_until = payload.get("collect_until")
    if collect_until is not None:
        payload["collect_until"] = _utc_text(str(collect_until), "source_cutoff_collect_until")
    checkpoint_fingerprint = payload.get("source_checkpoint_fingerprint")
    if checkpoint_fingerprint is not None:
        checkpoint_fingerprint = _text(
            checkpoint_fingerprint, "source_checkpoint_fingerprint"
        )
        if len(checkpoint_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in checkpoint_fingerprint
        ):
            raise ValueError("source_cutoff_checkpoint_invalid")
        payload["source_checkpoint_fingerprint"] = checkpoint_fingerprint

    if connection is not None and sequence is not None:
        try:
            cursor = connection.execute(
                "SELECT contiguous_sequence, source_checkpoint_fingerprint "
                "FROM observation_import_cursors WHERE source_name='main'"
            ).fetchone()
        except sqlite3.OperationalError:
            cursor = None
        if cursor is not None and int(sequence) > int(cursor[0] or 0):
            raise ValueError("source_cutoff_ahead_of_import")
        # The cursor is monotonic.  A confirmatory evaluation may be retried
        # after later receipts advance it, so a historical checkpoint identity
        # must remain valid rather than being compared with the newest cursor.

    return _json(payload)


def _lab_write(db: sqlite3.Connection | None, writer):
    """Run one Lab mutation in the caller transaction when one exists."""
    if db is not None and db.in_transaction:
        return writer(db)
    with lab_transaction(immediate=True) as connection:
        return writer(connection)


def _require_generation_root(
    db: sqlite3.Connection,
    observation_id: str,
    generation: int,
) -> None:
    row = db.execute(
        "SELECT 1 FROM observation_windows WHERE observation_id=? AND generation=?",
        (observation_id, int(generation)),
    ).fetchone()
    if row is None:
        raise ValueError("observation_generation_root_unavailable")


def _resolve_managed_archive_path(stored_path: str) -> Path:
    """Resolve an archive path after a bundle restore without rewriting history."""
    path = Path(str(stored_path)).expanduser()
    if path.is_file():
        return path
    try:
        parts = path.parts
        marker = parts.index("observation-lab")
        relative = Path(*parts[marker:])
        if relative.parts[:2] != ("observation-lab", "archives"):
            return path
        managed_root = (get_data_dir() / "observation-lab" / "archives").resolve()
        candidate = (get_data_dir() / relative).resolve()
        candidate.relative_to(managed_root)
    except (ValueError, OSError):
        return path
    return candidate


def record_observation_nomination(
    *,
    source_nomination_id: str,
    source_hash: str,
    snapshot: Mapping[str, Any],
    disposition: str = "pending",
    observation_id: str | None = None,
    generation: int | None = None,
    rejection_reason: str | None = None,
    nomination_id: str | None = None,
    created_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Import one immutable Learning nomination snapshot into Lab."""
    source_nomination_id = _text(source_nomination_id, "source_nomination_id")
    source_hash = _text(source_hash, "source_hash")
    if disposition not in {"admitted", "rejected", "pending"}:
        raise ValueError("invalid_nomination_disposition")
    snapshot_json = _json(dict(snapshot))
    nomination_id = nomination_id or uuid.uuid4().hex
    created_at = _utc_text(created_at, "created_at") if created_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        existing = connection.execute(
            "SELECT * FROM observation_nominations WHERE source_nomination_id=?",
            (source_nomination_id,),
        ).fetchone()
        if existing is not None:
            if any(
                existing[name] != value
                for name, value in {
                    "source_hash": source_hash,
                    "snapshot_json": snapshot_json,
                    "disposition": disposition,
                    "observation_id": observation_id,
                    "generation": generation,
                    "rejection_reason": rejection_reason,
                }.items()
            ):
                raise sqlite3.IntegrityError("observation_nomination_identity_conflict")
            return str(existing["nomination_id"])
        connection.execute(
            "INSERT INTO observation_nominations "
            "(nomination_id, source_nomination_id, source_hash, observation_id, generation, "
            "disposition, snapshot_json, rejection_reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                nomination_id, source_nomination_id, source_hash, observation_id, generation,
                disposition, snapshot_json, rejection_reason, created_at,
            ),
        )
        return nomination_id

    return _lab_write(db, _write)


def record_generation_contract(
    *,
    observation_id: str,
    generation: int,
    stage: str,
    assignment_policy: str,
    assignment_namespace_key: str,
    assignment_salt: str,
    assignment_unit_key_template: str,
    allocation_pct: float,
    target_horizon: str,
    outcome_contract_version: str,
    metric_version: str,
    behavior_contract_hash: str,
    started_at: str,
    parent_generation: int | None = None,
    assignment_hash_version: str = "sha256-prefix16/2^64",
    gates: Mapping[str, Any] | None = None,
    harm_budget: Mapping[str, Any] | None = None,
    behavior_policy_epoch_ref: str | None = None,
    behavior_policy_epoch_hash: str | None = None,
    owner_admission_ref: str | None = None,
    effective_until: str | None = None,
    terminal_status: str | None = None,
    terminal_at: str | None = None,
    contract_hash: str | None = None,
    created_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Persist one immutable treatment/allocation contract for a Generation."""
    if int(generation) <= 0:
        raise ValueError("generation_must_be_positive")
    if not 0.0 <= float(allocation_pct) <= 1.0:
        raise ValueError("allocation_pct_out_of_range")
    if assignment_hash_version not in {
        "sha256-prefix16/0xffffffffffffffff", "sha256-prefix16/2^64",
    }:
        raise ValueError("unknown_assignment_hash_version")
    started_at = _utc_text(started_at, "started_at")
    effective_until = _utc_text(effective_until, "effective_until") if effective_until else None
    terminal_at = _utc_text(terminal_at, "terminal_at") if terminal_at else None
    if effective_until and effective_until <= started_at:
        raise ValueError("effective_until_must_be_later")
    if terminal_status is not None:
        terminal_status = _text(terminal_status, "terminal_status")
    identity = {
        "observation_id": observation_id,
        "generation": int(generation),
        "stage": _text(stage, "stage"),
        "parent_generation": parent_generation,
        "assignment_policy": _text(assignment_policy, "assignment_policy"),
        "assignment_namespace_key": _text(assignment_namespace_key, "assignment_namespace_key"),
        "assignment_salt": _text(assignment_salt, "assignment_salt"),
        "assignment_unit_key_template": _text(assignment_unit_key_template, "assignment_unit_key_template"),
        "assignment_hash_version": assignment_hash_version,
        "allocation_pct": float(allocation_pct),
        "gates_json": _json(gates or {}),
        "harm_budget_json": _json(harm_budget or {}),
        "target_horizon": _text(target_horizon, "target_horizon"),
        "outcome_contract_version": _text(outcome_contract_version, "outcome_contract_version"),
        "metric_version": _text(metric_version, "metric_version"),
        "behavior_contract_hash": _text(behavior_contract_hash, "behavior_contract_hash"),
        "behavior_policy_epoch_ref": behavior_policy_epoch_ref,
        "behavior_policy_epoch_hash": behavior_policy_epoch_hash,
        "owner_admission_ref": owner_admission_ref,
        "started_at": started_at,
        "effective_until": effective_until,
        "terminal_status": terminal_status,
        "terminal_at": terminal_at,
    }
    computed_hash = _hash(identity)
    if contract_hash is not None and contract_hash != computed_hash:
        raise ValueError("generation_contract_hash_mismatch")
    contract_hash = computed_hash
    created_at = _utc_text(created_at, "created_at") if created_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        _require_generation_root(connection, observation_id, int(generation))
        existing = connection.execute(
            "SELECT contract_hash FROM observation_generation_contracts "
            "WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchone()
        if existing is not None:
            if existing[0] != contract_hash:
                raise sqlite3.IntegrityError("observation_generation_contract_identity_conflict")
            return contract_hash
        columns = ", ".join((*identity.keys(), "contract_hash", "created_at"))
        placeholders = ", ".join("?" for _ in range(len(identity) + 2))
        connection.execute(
            f"INSERT INTO observation_generation_contracts ({columns}) VALUES ({placeholders})",
            (*identity.values(), contract_hash, created_at),
        )
        return contract_hash

    return _lab_write(db, _write)


def record_observation_evaluation(
    *,
    observation_id: str,
    generation: int,
    evaluation_kind: str,
    source_cutoff: str,
    input_fingerprint: str,
    exposure_fingerprint: str,
    outcome_fingerprint: str,
    result: Mapping[str, Any],
    verdict: str,
    disposition: str,
    evaluation_id: str | None = None,
    created_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Append an immutable evaluation with an explicit source cut."""
    if evaluation_kind not in {"monitoring", "confirmatory"}:
        raise ValueError("invalid_evaluation_kind")
    source_cutoff = _normalise_source_cutoff(source_cutoff)
    for value, field in (
        (source_cutoff, "source_cutoff"), (input_fingerprint, "input_fingerprint"),
        (exposure_fingerprint, "exposure_fingerprint"), (outcome_fingerprint, "outcome_fingerprint"),
        (verdict, "verdict"), (disposition, "disposition"),
    ):
        _text(value, field)
    result_json = _json(dict(result))
    evaluation_id = evaluation_id or uuid.uuid4().hex
    created_at = _utc_text(created_at, "created_at") if created_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        # Validate against the same Lab snapshot that stores the evaluation;
        # this prevents a caller from declaring a future Main watermark.
        _normalise_source_cutoff(source_cutoff, connection=connection)
        _require_generation_root(connection, observation_id, int(generation))
        existing = connection.execute(
            "SELECT * FROM observation_evaluations WHERE observation_id=? AND generation=? "
            "AND evaluation_kind=? AND input_fingerprint=?",
            (observation_id, int(generation), evaluation_kind, input_fingerprint),
        ).fetchone()
        if existing is not None:
            if any(existing[name] != value for name, value in {
                "source_cutoff": source_cutoff,
                "exposure_fingerprint": exposure_fingerprint,
                "outcome_fingerprint": outcome_fingerprint,
                "result_json": result_json,
                "verdict": verdict,
                "disposition": disposition,
            }.items()):
                raise sqlite3.IntegrityError("observation_evaluation_identity_conflict")
            return str(existing["evaluation_id"])
        connection.execute(
            "INSERT INTO observation_evaluations "
            "(evaluation_id, observation_id, generation, evaluation_kind, source_cutoff, input_fingerprint, "
            "exposure_fingerprint, outcome_fingerprint, result_json, verdict, disposition, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                evaluation_id, observation_id, int(generation), evaluation_kind, source_cutoff,
                input_fingerprint, exposure_fingerprint, outcome_fingerprint, result_json,
                verdict, disposition, created_at,
            ),
        )
        return evaluation_id

    return _lab_write(db, _write)


def record_observation_result(
    *,
    observation_id: str,
    generation: int,
    spec_hash: str,
    source_cutoff: str,
    verdict: str,
    disposition: str,
    result: Mapping[str, Any],
    evaluation_id: str | None = None,
    archive_id: str | None = None,
    result_id: str | None = None,
    created_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Publish one immutable Lab result identity for Learning consumption."""
    for value, field in (
        (observation_id, "observation_id"), (spec_hash, "spec_hash"),
        (source_cutoff, "source_cutoff"), (verdict, "verdict"),
        (disposition, "disposition"),
    ):
        _text(value, field)
    source_cutoff = _normalise_source_cutoff(source_cutoff)
    result_json = _json(dict(result))
    fingerprint = _hash({
        "observation_id": observation_id,
        "generation": int(generation),
        "spec_hash": spec_hash,
        "evaluation_id": evaluation_id,
        "source_cutoff": source_cutoff,
        "verdict": verdict,
        "disposition": disposition,
        "result_json": result_json,
        "archive_id": archive_id,
    })
    result_id = result_id or f"observation-result:{fingerprint}"
    created_at = _utc_text(created_at, "created_at") if created_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        _normalise_source_cutoff(source_cutoff, connection=connection)
        root = connection.execute(
            "SELECT spec_hash FROM observation_windows WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchone()
        if root is None:
            raise ValueError("observation_generation_root_unavailable")
        if str(root[0]) != spec_hash:
            raise ValueError("observation_result_spec_hash_mismatch")
        if evaluation_id is not None:
            evaluation = connection.execute(
                "SELECT observation_id, generation FROM observation_evaluations "
                "WHERE evaluation_id=?",
                (evaluation_id,),
            ).fetchone()
            if (
                evaluation is None
                or str(evaluation[0]) != observation_id
                or int(evaluation[1]) != int(generation)
            ):
                raise ValueError("observation_evaluation_unavailable")
        existing = connection.execute(
            "SELECT * FROM observation_results WHERE observation_id=? AND generation=? "
            "AND evaluation_id IS ?",
            (observation_id, int(generation), evaluation_id),
        ).fetchone()
        if existing is not None:
            if existing["result_fingerprint"] != fingerprint:
                raise sqlite3.IntegrityError("observation_result_identity_conflict")
            return str(existing["result_id"])
        connection.execute(
            "INSERT INTO observation_results "
            "(result_id, observation_id, generation, spec_hash, evaluation_id, source_cutoff, verdict, "
            "disposition, result_json, result_fingerprint, archive_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                result_id, observation_id, int(generation), spec_hash, evaluation_id,
                source_cutoff, verdict, disposition, result_json, fingerprint,
                archive_id, created_at,
            ),
        )
        return result_id

    return _lab_write(db, _write)


def latest_observation_result(
    observation_id: str,
    generation: int | None = None,
) -> dict[str, Any] | None:
    """Read an immutable result projection for Learning/review consumers."""
    from aletheia.observation_lab.db import readonly_lab_db

    try:
        with readonly_lab_db(allow_missing=True) as db:
            if db is None:
                return None
            if generation is None:
                row = db.execute(
                    "SELECT * FROM observation_results WHERE observation_id=? "
                    "ORDER BY generation DESC, created_at DESC LIMIT 1",
                    (observation_id,),
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM observation_results WHERE observation_id=? AND generation=? "
                    "ORDER BY created_at DESC LIMIT 1",
                    (observation_id, int(generation)),
                ).fetchone()
            return dict(row) if row else None
    except (FileNotFoundError, sqlite3.Error):
        return None


def register_observation_spec(
    spec: Mapping[str, Any],
    *,
    actor: str = "operator",
    source_nomination_id: str | None = None,
    source_hash: str | None = None,
    started_at: str | None = None,
    collect_until: str | None = None,
    finalize_not_before: str | None = None,
    producer_commit: str | None = None,
    return_created: bool = False,
) -> dict[str, Any]:
    """Register one observe-only Generation in Lab with one immutable snapshot.

    This is the small admission surface for CLI/research nominations.  It does
    not infer a treatment authority or touch Main experiment tables; Paper
    influence still requires an explicit owner action and Manifest.
    """
    value = dict(spec)
    actor = _text(actor, "actor")
    required = (
        "filter_name", "stage", "scanner_family", "direction",
        "market_type", "horizon", "control", "treatment",
    )
    missing = [key for key in required if not value.get(key)]
    if missing:
        raise ValueError(f"spec_is_missing:{','.join(missing)}")
    started = _utc_text(started_at, "started_at") if started_at else _now()
    collect_end = _utc_text(collect_until, "collect_until") if collect_until else "9999-12-31T00:00:00+00:00"
    finalize_at = _utc_text(finalize_not_before, "finalize_not_before") if finalize_not_before else collect_end
    if collect_end <= started or finalize_at < collect_end:
        raise ValueError("invalid_observation_interval")
    canonical_spec = {
        **value,
        "production_mutation_allowed": False,
        "authority_mode": "observe_only",
    }
    spec_json = _json(canonical_spec)
    spec_digest = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
    observation_id = str(value.get("observation_id") or f"filter:{spec_digest[:32]}")
    generation = int(value.get("generation") or 1)
    if generation <= 0:
        raise ValueError("generation_must_be_positive")
    namespace = str(value.get("assignment_namespace_key") or observation_id)
    assignment_salt = str(value.get("assignment_salt") or f"lab:{spec_digest}")
    allocation = float(value.get("allocation_pct") if value.get("allocation_pct") is not None else 0.5)
    behavior_hash = _hash({
        "filter_name": value["filter_name"],
        "control": value["control"],
        "treatment": value["treatment"],
    })
    default_outcome_contract = "filter_candidate_outcomes/v1"
    default_metric = "net_return_pct/v1"
    path_id = str(value.get("path_id") or "").strip()
    if path_id:
        try:
            from aletheia.learning.registry import (
                get_learning_path,
            )

            path = get_learning_path(path_id)
            default_outcome_contract = path.outcome_contract_version
            default_metric = path.metric_version
        except KeyError:
            pass
    contract_args = {
        "observation_id": observation_id,
        "generation": generation,
        "stage": str(value["stage"]),
        "assignment_policy": str(value.get("assignment_policy") or "prospective_first_exposure"),
        "assignment_namespace_key": namespace,
        "assignment_salt": assignment_salt,
        "assignment_unit_key_template": str(value.get("assignment_unit_key_template") or "{candidate_key}"),
        "allocation_pct": allocation,
        "target_horizon": str(value["horizon"]),
        "outcome_contract_version": str(value.get("outcome_contract_version") or default_outcome_contract),
        "metric_version": str(value.get("metric_version") or default_metric),
        "behavior_contract_hash": behavior_hash,
        "behavior_policy_epoch_ref": value.get("behavior_policy_epoch_ref"),
        "behavior_policy_epoch_hash": value.get("behavior_policy_epoch_hash"),
        "started_at": started,
        "effective_until": collect_end,
        "gates": value.get("gates") or {
            key: value[key]
            for key in ("min_samples_per_arm", "min_market_days", "effect_threshold")
            if key in value
        },
        "harm_budget": value.get("harm_budget") or value.get("harm_gate") or {},
        "owner_admission_ref": value.get("owner_admission_ref"),
    }
    source_nomination_id = source_nomination_id or f"cli:{observation_id}:{generation}"
    source_hash = source_hash or spec_digest

    with lab_transaction(immediate=True) as db:
        existing = db.execute(
            "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
            (observation_id, generation),
        ).fetchone()
        if existing is not None:
            if existing["spec_hash"] != spec_digest:
                raise ValueError("observation_generation_identity_conflict")
            result = dict(existing)
            if return_created:
                result["_created"] = False
            return result
        db.execute(
            "INSERT INTO observation_windows "
            "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
            "spec_json, spec_hash, producer_commit, started_at, collect_until, finalize_not_before) "
            "VALUES (?, 'filter_shadow', 'canonical-v1', ?, ?, 'observe_only', 'active', ?, ?, ?, ?, ?, ?)",
            (
                observation_id, generation, f"observation:{observation_id}", spec_json,
                spec_digest, producer_commit, started, collect_end, finalize_at,
            ),
        )
        record_observation_nomination(
            source_nomination_id=source_nomination_id,
            source_hash=source_hash,
            snapshot=canonical_spec,
            disposition="admitted",
            observation_id=observation_id,
            generation=generation,
            db=db,
        )
        record_generation_contract(db=db, **contract_args)
        _append_lifecycle_event(
            db,
            observation_id=observation_id,
            generation=generation,
            to_status="shadow",
            action="register",
            reason="observation_spec_registered",
            actor=actor,
            dedup_key=f"observation-register:{observation_id}:{generation}:{spec_digest}",
        )
        result = dict(db.execute(
            "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
            (observation_id, generation),
        ).fetchone())
        if return_created:
            result["_created"] = True
        return result


def register_inference_plan(
    *,
    observation_id: str,
    generation: int,
    inference_contract_version: str,
    plan: Mapping[str, Any],
    plan_id: str | None = None,
    status: str = "collecting",
    created_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Register one fixed-look plan; its identity cannot be rewritten."""
    if status not in {"collecting", "frozen", "consumed", "invalidated"}:
        raise ValueError("invalid_inference_plan_status")
    plan_json = _json(dict(plan))
    plan_hash = _hash({
        "observation_id": observation_id, "generation": int(generation),
        "inference_contract_version": inference_contract_version, "plan_json": plan_json,
    })
    plan_id = plan_id or uuid.uuid4().hex
    created_at = _utc_text(created_at, "created_at") if created_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        _require_generation_root(connection, observation_id, int(generation))
        existing = connection.execute(
            "SELECT * FROM observation_inference_plans WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchone()
        if existing is not None:
            if existing["plan_hash"] != plan_hash:
                raise sqlite3.IntegrityError("observation_inference_plan_identity_conflict")
            return str(existing["plan_id"])
        connection.execute(
            "INSERT INTO observation_inference_plans "
            "(plan_id, observation_id, generation, inference_contract_version, plan_json, plan_hash, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (plan_id, observation_id, int(generation), inference_contract_version, plan_json, plan_hash, status, created_at, created_at),
        )
        return plan_id

    return _lab_write(db, _write)


def freeze_inference_plan(
    plan_id: str,
    *,
    cutoff: Mapping[str, Any],
    cutoff_fingerprint: str | None = None,
    frozen_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Freeze a fixed-look cutoff exactly once."""
    cutoff_json = _json(dict(cutoff))
    expected = _hash(dict(cutoff))
    if cutoff_fingerprint is not None and cutoff_fingerprint != expected:
        raise ValueError("inference_cutoff_fingerprint_mismatch")
    cutoff_fingerprint = expected
    frozen_at = _utc_text(frozen_at, "frozen_at") if frozen_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT * FROM observation_inference_plans WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if row is None:
            raise ValueError("inference_plan_unavailable")
        if row["cutoff_fingerprint"] is not None:
            if row["cutoff_fingerprint"] != cutoff_fingerprint or row["cutoff_json"] != cutoff_json:
                raise sqlite3.IntegrityError("inference_cutoff_is_immutable")
            return str(row["plan_id"])
        if row["status"] not in {"collecting", "frozen"}:
            raise ValueError(f"inference_plan_{row['status']}")
        connection.execute(
            "UPDATE observation_inference_plans SET status='frozen', cutoff_json=?, cutoff_fingerprint=?, "
            "cutoff_frozen_at=?, updated_at=? WHERE plan_id=?",
            (cutoff_json, cutoff_fingerprint, frozen_at, _now(), plan_id),
        )
        return str(plan_id)

    return _lab_write(db, _write)


def consume_inference_plan(
    plan_id: str,
    *,
    evaluation_id: str,
    consumed_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Bind one frozen plan to one immutable Evaluation."""
    consumed_at = _utc_text(consumed_at, "consumed_at") if consumed_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT observation_id, generation, status, consumed_evaluation_id "
            "FROM observation_inference_plans WHERE plan_id=?",
            (plan_id,),
        ).fetchone()
        if row is None:
            raise ValueError("inference_plan_unavailable")
        evaluation = connection.execute(
            "SELECT observation_id, generation, evaluation_kind FROM observation_evaluations "
            "WHERE evaluation_id=?",
            (evaluation_id,),
        ).fetchone()
        if evaluation is None:
            raise ValueError("inference_evaluation_unavailable")
        if (
            str(evaluation[0]) != str(row[0])
            or int(evaluation[1]) != int(row[1])
        ):
            raise sqlite3.IntegrityError("inference_evaluation_identity_mismatch")
        if str(evaluation[2]) != "confirmatory":
            raise ValueError("inference_evaluation_not_confirmatory")
        if row["consumed_evaluation_id"] is not None:
            if row["consumed_evaluation_id"] != evaluation_id:
                raise sqlite3.IntegrityError("inference_plan_consumption_conflict")
            return plan_id
        if row["status"] != "frozen":
            raise ValueError("inference_plan_not_frozen")
        connection.execute(
            "UPDATE observation_inference_plans SET status='consumed', consumed_evaluation_id=?, "
            "consumed_at=?, updated_at=? WHERE plan_id=?",
            (evaluation_id, consumed_at, _now(), plan_id),
        )
        return plan_id

    return _lab_write(db, _write)


@runtime_checkable
class TreatmentAdapter(Protocol):
    """One exact production behavior adapter registered by identity."""

    adapter_id: str
    adapter_version: str
    behavior_dimension: str
    requires_outcome_seed: bool

    def assignment_unit_key(self, context: dict[str, Any]) -> str: ...

    def eligible(self, manifest: ExecutionManifest, context: dict[str, Any]) -> bool: ...

    def deterministic_baseline(self, context: dict[str, Any]) -> dict[str, Any]: ...

    def apply_payload(
        self,
        baseline: dict[str, Any],
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]: ...

    def build_outcome_seed(self, context: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class NumericTreatmentAdapter:
    """Built-in bounded numeric adapter for legacy Main call sites.

    The adapter carries no Experiment state. Lab supplies the immutable
    payload; Main only converts that payload back to the caller's numeric
    value.
    """

    adapter_id: str
    adapter_version: str = "v1"
    behavior_dimension: str = "numeric"
    requires_outcome_seed: bool = False

    def assignment_unit_key(self, context: dict[str, Any]) -> str:
        return str(context.get("assignment_key") or context.get("candidate_key") or "unassigned")

    def eligible(self, manifest: ExecutionManifest, context: dict[str, Any]) -> bool:
        population = manifest.eligible_population or {}
        expected_path = population.get("path_id")
        return expected_path is None or expected_path == context.get("path_id")

    def deterministic_baseline(self, context: dict[str, Any]) -> dict[str, Any]:
        return dict(context["baseline"])

    def apply_payload(
        self,
        baseline: dict[str, Any],
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        value = payload.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("numeric_observation_payload_unavailable")
        return {"value": float(value)}

    def build_outcome_seed(self, context: dict[str, Any]) -> Any:
        return context.get("outcome_seed")


_ADAPTERS: dict[tuple[str, str], TreatmentAdapter] = {}


def register_treatment_adapter(adapter: TreatmentAdapter, *, replace: bool = False) -> None:
    key = (_text(adapter.adapter_id, "adapter_id"), _text(adapter.adapter_version, "adapter_version"))
    if key in _ADAPTERS and not replace:
        raise ValueError(f"treatment_adapter_already_registered:{key[0]}:{key[1]}")
    _ADAPTERS[key] = adapter


def registered_treatment_adapters() -> tuple[tuple[str, str], ...]:
    return tuple(sorted(_ADAPTERS))


def _adapter(adapter_id: str, adapter_version: str | None) -> TreatmentAdapter | None:
    if adapter_version is not None:
        return _ADAPTERS.get((adapter_id, adapter_version))
    matches = [item for key, item in _ADAPTERS.items() if key[0] == adapter_id]
    return matches[0] if len(matches) == 1 else None


def _assignment_unit_key(
    adapter: TreatmentAdapter | None,
    context: Mapping[str, Any],
    candidate_key: str,
) -> str:
    if adapter is None:
        return candidate_key
    try:
        value = context.get("assignment_key") or adapter.assignment_unit_key(dict(context))
        return str(value).strip() or candidate_key
    except Exception:
        return candidate_key


@dataclass(frozen=True)
class AuthoritySource:
    source: str
    epoch: str
    actor: str
    reason: str
    updated_at: str


def get_authority_source(*, db: sqlite3.Connection | None = None) -> AuthoritySource:
    from aletheia.storage.sqlite import get_db

    row = (db or get_db()).execute(
        "SELECT authority_source, authority_epoch, actor, reason, updated_at "
        "FROM observation_authority_state WHERE state_key='experiment_authority'"
    ).fetchone()
    if row is None:
        raise RuntimeError("authority_state_unavailable")
    source = str(row[0])
    if source not in {"legacy", "lab"}:
        raise RuntimeError("invalid_experiment_authority_source")
    epoch = str(row[1] or "").strip()
    if not epoch:
        raise RuntimeError("invalid_experiment_authority_epoch")
    return AuthoritySource(source, epoch, str(row[2]), str(row[3]), str(row[4]))


_CUTOVER_PROOF_FIELDS = (
    "migration_plan_fingerprint",
    "retention_claim_fingerprint",
    "checkpoint_fingerprint",
    "runtime_commit",
    "rollback_backup_identity",
)
_CUTOVER_FINGERPRINT_FIELDS = frozenset({
    "migration_plan_fingerprint",
    "retention_claim_fingerprint",
    "checkpoint_fingerprint",
})


def _normalise_cutover_proof(proof: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(proof, Mapping):
        raise RuntimeError("authority_cutover_proof_required")
    result: dict[str, str] = {}
    for field in _CUTOVER_PROOF_FIELDS:
        value = _text(proof.get(field), field)
        if field in _CUTOVER_FINGERPRINT_FIELDS and (
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        ):
            raise RuntimeError(f"authority_cutover_{field}_invalid")
        result[field] = value
    return result


def _runtime_commit() -> str | None:
    try:
        from aletheia.agent_runtime.build_info import get_boot_commit
        from aletheia.update.git_ops import current_commit

        return get_boot_commit() or current_commit()
    except Exception:
        return None


def _retention_claim_fingerprint() -> str:
    """Hash exact Lab retention claims used by the cutover proof."""
    with readonly_lab_db(allow_missing=True) as lab:
        if lab is None:
            raise RuntimeError("authority_cutover_retention_unavailable")
        try:
            rows = lab.execute(
                "SELECT claim_id, observation_id, generation, source_kind, source_ref, "
                "source_hash, claim_domain, derived_from_fingerprint, status, created_at, "
                "released_at, release_proof_fingerprint, release_reason "
                "FROM observation_retention_claims ORDER BY claim_id"
            ).fetchall()
        except sqlite3.Error as exc:
            raise RuntimeError("authority_cutover_retention_unavailable") from exc
    return _hash([dict(row) for row in rows])


def build_authority_cutover_proof(
    *,
    runtime_commit: str | None = None,
    rollback_backup_identity: str | None = None,
) -> dict[str, str]:
    """Build the read-only identity bundle required for a Lab cutover."""
    from aletheia.observation_lab.checkpoint import (
        latest_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.observation_lab.migration import plan_legacy_learning_migration

    plan = plan_legacy_learning_migration()
    if not plan.get("available"):
        raise RuntimeError("authority_cutover_legacy_import_unavailable")
    # A fresh schema has no legacy evidence to prove.  Keep the operator
    # command usable for fresh installs; the durable authority switch still
    # validates Lab availability and records its own epoch.
    if not any(
        int(details.get("row_count") or 0)
        for details in plan.get("legacy_tables", {}).values()
        if isinstance(details, Mapping)
    ):
        return {}
    checkpoint = latest_joint_checkpoint()
    if checkpoint is None:
        raise RuntimeError("authority_cutover_checkpoint_unavailable")
    verdict = verify_joint_checkpoint()
    if not verdict.ok:
        raise RuntimeError("authority_cutover_checkpoint_unavailable")
    runtime = _text(runtime_commit or _runtime_commit(), "runtime_commit")
    backup = _text(rollback_backup_identity, "rollback_backup_identity")
    return {
        "migration_plan_fingerprint": _text(plan.get("plan_fingerprint"), "migration_plan_fingerprint"),
        "retention_claim_fingerprint": _retention_claim_fingerprint(),
        "checkpoint_fingerprint": _text(checkpoint.joint_fingerprint, "checkpoint_fingerprint"),
        "runtime_commit": runtime,
        "rollback_backup_identity": backup,
    }


def _record_authority_cutover_proof(
    connection: sqlite3.Connection,
    proof: Mapping[str, Any],
    *,
    actor: str,
    reason: str,
) -> str:
    values = _normalise_cutover_proof(proof)
    proof_id = f"observation-authority-proof:{uuid.uuid4().hex}"
    connection.execute(
        "INSERT OR IGNORE INTO observation_authority_cutover_proofs "
        "(proof_id, authority_source, migration_plan_fingerprint, retention_claim_fingerprint, "
        "checkpoint_fingerprint, runtime_commit, rollback_backup_identity, actor, reason, recorded_at) "
        "VALUES (?, 'lab', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            proof_id,
            values["migration_plan_fingerprint"],
            values["retention_claim_fingerprint"],
            values["checkpoint_fingerprint"],
            values["runtime_commit"],
            values["rollback_backup_identity"],
            _text(actor, "actor"),
            _text(reason, "reason"),
            _now(),
        ),
    )
    row = connection.execute(
        "SELECT proof_id FROM observation_authority_cutover_proofs WHERE authority_source='lab' "
        "AND migration_plan_fingerprint=? AND retention_claim_fingerprint=? "
        "AND checkpoint_fingerprint=? AND runtime_commit=? AND rollback_backup_identity=?",
        tuple(values[field] for field in _CUTOVER_PROOF_FIELDS),
    ).fetchone()
    if row is None:
        raise RuntimeError("authority_cutover_proof_record_unavailable")
    return str(row[0])


def record_authority_cutover_proof(
    proof: Mapping[str, Any],
    *,
    actor: str,
    reason: str,
    db: sqlite3.Connection | None = None,
) -> str:
    """Validate and persist one append-only Lab cutover proof."""

    def _write(connection: sqlite3.Connection) -> str:
        validated = _assert_lab_cutover_ready(connection, cutover_proof=proof)
        if validated is None:
            raise RuntimeError("authority_cutover_proof_not_required")
        return _record_authority_cutover_proof(
            connection, validated, actor=actor, reason=reason,
        )

    if db is not None:
        if db.in_transaction:
            return _write(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            result = _write(db)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
    from aletheia.storage.repositories import transaction
    from aletheia.storage.sqlite import get_db

    with transaction(immediate=True):
        return _write(db or get_db())


def _assert_lab_cutover_ready(
    connection: sqlite3.Connection,
    *,
    cutover_proof: Mapping[str, Any] | None = None,
) -> dict[str, str] | None:
    """Refuse Lab authority until the legacy evidence boundary is proven.

    An authority flip is a behavior boundary, not a configuration toggle.  A
    fresh database has no legacy evidence to migrate; an existing database must
    have an unchanged deterministic import checkpoint and no pending Main
    receipts that could be interpreted by two different authorities.
    """
    from aletheia.observation_lab.db import get_lab_db, readonly_lab_db
    from aletheia.storage.migrations import _LEGACY_EXPERIMENT_TABLES

    try:
        # Materialize/validate the canonical Lab schema before Main records the
        # new source.  This is safe for a fresh install and keeps a missing Lab
        # store from looking like a valid cutover.
        get_lab_db()
        pending = connection.execute(
            "SELECT COUNT(*) FROM observation_delivery_outbox "
            "WHERE acknowledged_at IS NULL"
        ).fetchone()[0]
    except (FileNotFoundError, OSError, sqlite3.Error) as exc:
        raise RuntimeError("authority_cutover_lab_unavailable") from exc
    if int(pending or 0):
        raise RuntimeError("authority_cutover_pending_main_receipts")

    # An active Lab generation may already have immutable exposures while its
    # canonical outcome is still in Main.  Every such outcome identity needs
    # an active exact claim before the source switch; otherwise ordinary Main
    # TTL could delete evidence underneath the new authority.
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                raise RuntimeError("authority_cutover_lab_unavailable")
            exposed_outcomes = {
                str(row[0])
                for row in lab.execute(
                    "SELECT DISTINCT e.outcome_candidate_key "
                    "FROM observation_exposures e "
                    "JOIN observation_windows w "
                    "ON w.observation_id=e.observation_id AND w.generation=e.generation "
                    "WHERE w.status='active' AND e.outcome_candidate_key IS NOT NULL"
                ).fetchall()
            }
            claimed_outcomes = {
                str(row[0])
                for row in lab.execute(
                    "SELECT DISTINCT source_ref FROM observation_retention_claims "
                    "WHERE status='active' AND source_kind='outcome_candidate_key'"
                ).fetchall()
            }
            # A claim row is only a protection proof when its source identity
            # can still be derived from the immutable Lab exposure/unit.  Do
            # this validation at the cutover boundary as well as on insert:
            # historical imports or direct DB edits must not turn a fabricated
            # active claim into retention authority.
            for claim in lab.execute(
                "SELECT observation_id, generation, source_kind, source_ref, source_hash "
                "FROM observation_retention_claims WHERE status='active' ORDER BY claim_id"
            ).fetchall():
                try:
                    _validate_retention_claim_source(
                        lab,
                        observation_id=str(claim[0]),
                        generation=int(claim[1]),
                        source_kind=str(claim[2]),
                        source_ref=str(claim[3]),
                        source_hash=str(claim[4]),
                    )
                except Exception as exc:
                    raise RuntimeError("authority_cutover_retention_claim_unproven") from exc
    except (FileNotFoundError, OSError, sqlite3.Error) as exc:
        raise RuntimeError("authority_cutover_retention_unavailable") from exc
    if exposed_outcomes - claimed_outcomes:
        raise RuntimeError("authority_cutover_retention_claim_missing")

    counts: dict[str, int] = {}
    for table in _LEGACY_EXPERIMENT_TABLES:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if exists is not None:
            counts[table] = int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0
            )
    if not any(counts.values()):
        return None

    # The import checkpoint is compared with a fresh read-only legacy snapshot;
    # a changed row after import must block cutover rather than silently
    # authorizing a stale Lab copy.
    from aletheia.observation_lab.migration import plan_legacy_learning_migration

    plan = plan_legacy_learning_migration()
    if not plan.get("available"):
        raise RuntimeError("authority_cutover_legacy_import_unavailable")
    if any(
        str(item.get("table")) != "strategy_experiments"
        for item in plan.get("unprovable_rows", ())
    ):
        raise RuntimeError("authority_cutover_unprovable_legacy_evidence")
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            cursor = None if lab is None else lab.execute(
                "SELECT source_checkpoint_fingerprint FROM observation_import_cursors "
                "WHERE source_name='legacy_learning'"
            ).fetchone()
    except (FileNotFoundError, OSError, sqlite3.Error) as exc:
        raise RuntimeError("authority_cutover_legacy_import_unavailable") from exc
    if cursor is None or str(cursor[0] or "") != str(plan.get("plan_fingerprint") or ""):
        raise RuntimeError("authority_cutover_legacy_import_checkpoint_missing")

    proof = _normalise_cutover_proof(cutover_proof)
    expected_plan = _text(plan.get("plan_fingerprint"), "migration_plan_fingerprint")
    if proof["migration_plan_fingerprint"] != expected_plan:
        raise RuntimeError("authority_cutover_migration_equivalence_mismatch")

    from aletheia.observation_lab.checkpoint import (
        latest_joint_checkpoint,
        verify_joint_checkpoint,
    )

    latest = latest_joint_checkpoint()
    try:
        verdict = verify_joint_checkpoint()
    except Exception as exc:
        raise RuntimeError("authority_cutover_checkpoint_unavailable") from exc
    if latest is None or not verdict.ok:
        raise RuntimeError("authority_cutover_checkpoint_unavailable")
    if proof["checkpoint_fingerprint"] != str(latest.joint_fingerprint):
        raise RuntimeError("authority_cutover_checkpoint_mismatch")
    if proof["retention_claim_fingerprint"] != _retention_claim_fingerprint():
        raise RuntimeError("authority_cutover_retention_mismatch")
    observed_runtime = _runtime_commit()
    if observed_runtime and proof["runtime_commit"] != observed_runtime:
        raise RuntimeError("authority_cutover_runtime_commit_mismatch")
    if plan.get("source_commit") and proof["runtime_commit"] != str(plan["source_commit"]):
        raise RuntimeError("authority_cutover_source_commit_mismatch")
    return proof


def _assert_legacy_rollback_ready(
    connection: sqlite3.Connection,
    *,
    rollback_proof: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Allow legacy authority only from an explicitly proven same cut.

    A Lab-to-legacy flip is not a recovery mechanism: it would re-enable the
    frozen Main ledger.  Keep the safe default (Lab baseline) unless the
    caller presents the exact predeclared cutover proof and the legacy rows
    still exist for an equivalent rollback target.
    """
    proof = _normalise_cutover_proof(rollback_proof)
    from aletheia.observation_lab.checkpoint import (
        latest_joint_checkpoint,
        verify_joint_checkpoint,
    )
    from aletheia.observation_lab.db import readonly_lab_db
    from aletheia.observation_lab.migration import plan_legacy_learning_migration
    from aletheia.storage.migrations import _LEGACY_EXPERIMENT_TABLES

    missing = [
        table for table in _LEGACY_EXPERIMENT_TABLES
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is None
    ]
    if missing:
        raise RuntimeError("authority_rollback_legacy_tables_unavailable")
    plan = plan_legacy_learning_migration()
    if not plan.get("available") or proof["migration_plan_fingerprint"] != str(
        plan.get("plan_fingerprint") or ""
    ):
        raise RuntimeError("authority_rollback_migration_equivalence_mismatch")
    if any(
        str(item.get("table")) != "strategy_experiments"
        for item in plan.get("unprovable_rows", ())
    ):
        raise RuntimeError("authority_rollback_unprovable_legacy_evidence")
    with readonly_lab_db(allow_missing=True) as lab:
        cursor = None if lab is None else lab.execute(
            "SELECT source_checkpoint_fingerprint FROM observation_import_cursors "
            "WHERE source_name='legacy_learning'"
        ).fetchone()
    if cursor is None or str(cursor[0] or "") != proof["migration_plan_fingerprint"]:
        raise RuntimeError("authority_rollback_import_checkpoint_mismatch")
    latest = latest_joint_checkpoint()
    try:
        verdict = verify_joint_checkpoint()
    except Exception as exc:
        raise RuntimeError("authority_rollback_checkpoint_unavailable") from exc
    if latest is None or not verdict.ok or proof["checkpoint_fingerprint"] != str(
        latest.joint_fingerprint
    ):
        raise RuntimeError("authority_rollback_checkpoint_mismatch")
    if proof["retention_claim_fingerprint"] != _retention_claim_fingerprint():
        raise RuntimeError("authority_rollback_retention_mismatch")
    observed_runtime = _runtime_commit()
    if observed_runtime and proof["runtime_commit"] != observed_runtime:
        raise RuntimeError("authority_rollback_runtime_commit_mismatch")
    if plan.get("source_commit") and proof["runtime_commit"] != str(plan["source_commit"]):
        raise RuntimeError("authority_rollback_source_commit_mismatch")
    recorded = connection.execute(
        "SELECT proof_id FROM observation_authority_cutover_proofs "
        "WHERE authority_source='lab' AND migration_plan_fingerprint=? "
        "AND retention_claim_fingerprint=? AND checkpoint_fingerprint=? "
        "AND runtime_commit=? AND rollback_backup_identity=? "
        "ORDER BY recorded_at DESC, proof_id DESC LIMIT 1",
        tuple(proof[field] for field in _CUTOVER_PROOF_FIELDS),
    ).fetchone()
    if recorded is None:
        raise RuntimeError("authority_rollback_proof_unrecorded")
    return {**proof, "proof_id": str(recorded[0])}


def set_authority_source(
    source: str,
    *,
    actor: str,
    reason: str,
    cutover_proof: Mapping[str, Any] | None = None,
    rollback_proof: Mapping[str, Any] | None = None,
    db: sqlite3.Connection | None = None,
) -> AuthoritySource:
    """Record one durable ``legacy``/``lab`` source; there is no dual mode."""
    if source not in {"legacy", "lab"}:
        raise ValueError("authority_source_must_be_legacy_or_lab")
    actor = _text(actor, "actor")
    reason = _text(reason, "reason")

    def _write(connection: sqlite3.Connection) -> AuthoritySource:
        current = get_authority_source(db=connection)
        if current.source == source:
            return current
        proof = None
        rollback = None
        if source == "lab":
            proof = _assert_lab_cutover_ready(connection, cutover_proof=cutover_proof)
        elif current.source == "lab":
            rollback = _assert_legacy_rollback_ready(
                connection, rollback_proof=rollback_proof,
            )
        epoch = f"{source}:{uuid.uuid4().hex}"
        proof_id = None
        if proof is not None:
            proof_id = _record_authority_cutover_proof(
                connection, proof, actor=actor, reason=reason,
            )
        updated = connection.execute(
            "UPDATE observation_authority_state SET authority_source=?, authority_epoch=?, "
            "actor=?, reason=?, updated_at=? WHERE state_key='experiment_authority'",
            (source, epoch, actor, reason, _now()),
        )
        if updated.rowcount != 1:
            raise RuntimeError("authority_state_unavailable")
        # Keep the cutover attributable in the structured Main audit ledger.
        # JSONL mirroring remains the existing scheduler-owned outbox path.
        connection.execute(
            "INSERT OR IGNORE INTO audit_events "
            "(id, timestamp, actor, source, event_type, entity_type, entity_id, after_state, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"observation-authority:{epoch}", _now(), actor,
                "observation_authority", "observation_authority_source_changed",
                "experiment_authority", "experiment_authority",
                _json({
                    "authority_source": source,
                    "authority_epoch": epoch,
                    "reason": reason,
                    "cutover_proof_id": proof_id,
                    "rollback_proof_id": rollback.get("proof_id") if rollback else None,
                }), reason,
            ),
        )
        return get_authority_source(db=connection)

    if db is not None:
        if db.in_transaction:
            return _write(db)
        db.execute("BEGIN IMMEDIATE")
        try:
            result = _write(db)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
    from aletheia.storage.repositories import transaction
    from aletheia.storage.sqlite import get_db

    with transaction(immediate=True):
        return _write(db or get_db())


@dataclass(frozen=True)
class ObservationTreatmentDecision:
    payload: dict[str, Any]
    applied: bool
    assignment: str
    behavior_changed: bool
    reason: str
    observation_id: str | None = None
    generation: int | None = None
    manifest_id: str | None = None
    manifest_hash: str | None = None
    active_manifest_snapshot: tuple[str, ...] = ()
    manifest_set_fingerprint: str | None = None
    revocation_snapshot_fingerprint: str | None = None
    authority_source: str | None = None
    authority_epoch: str | None = None
    outcome_source: str | None = None
    receipt_id: str | None = None


def enqueue_lab_audit(
    *,
    event_type: str,
    payload: Mapping[str, Any],
    dedup_key: str,
    observation_id: str | None = None,
    generation: int | None = None,
    audit_id: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Queue one Lab lifecycle/authority audit for hash-chain mirroring."""
    event_type = _text(event_type, "event_type")
    dedup_key = _text(dedup_key, "dedup_key")
    payload_json = _json(dict(payload))
    audit_id = audit_id or uuid.uuid4().hex
    now = _now()

    def _write(connection: sqlite3.Connection) -> str:
        existing = connection.execute(
            "SELECT audit_id, event_type, observation_id, generation, payload_json "
            "FROM lab_audit_outbox WHERE dedup_key=?",
            (dedup_key,),
        ).fetchone()
        if existing is not None:
            if any(existing[name] != value for name, value in {
                "event_type": event_type,
                "observation_id": observation_id,
                "generation": generation,
                "payload_json": payload_json,
            }.items()):
                raise sqlite3.IntegrityError("lab_audit_identity_conflict")
            return str(existing["audit_id"])
        connection.execute(
            "INSERT INTO lab_audit_outbox "
            "(audit_id, dedup_key, event_type, observation_id, generation, payload_json, status, attempts, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)",
            (audit_id, dedup_key, event_type, observation_id, generation, payload_json, now, now),
        )
        return audit_id

    return _lab_write(db, _write)


def _append_lifecycle_event(
    connection: sqlite3.Connection,
    *,
    observation_id: str,
    generation: int,
    to_status: str,
    action: str,
    reason: str,
    actor: str,
    dedup_key: str,
    from_status: str | None = None,
    source_receipt_id: str | None = None,
    audit_identity: str | None = None,
    created_at: str | None = None,
) -> str:
    root = connection.execute(
        "SELECT status FROM observation_windows WHERE observation_id=? AND generation=?",
        (observation_id, int(generation)),
    ).fetchone()
    if root is None:
        raise ValueError("observation_generation_root_unavailable")
    # A missing caller snapshot is filled from the current Lab root so the
    # lifecycle record remains attributable and retries compare the same fact.
    from_status = from_status if from_status is not None else str(root[0])
    event_id = uuid.uuid4().hex
    created_at = _utc_text(created_at, "created_at") if created_at else _now()
    audit_identity = audit_identity or f"observation-lifecycle:{dedup_key}"
    existing = connection.execute(
        "SELECT * FROM observation_lifecycle_events WHERE dedup_key=?", (dedup_key,)
    ).fetchone()
    if existing is not None:
        if any(existing[name] != value for name, value in {
            "observation_id": observation_id,
            "generation": int(generation),
            "from_status": from_status,
            "to_status": to_status,
            "action": action,
            "reason": reason,
            "actor": actor,
            "source_receipt_id": source_receipt_id,
            "audit_identity": audit_identity,
        }.items()):
            raise sqlite3.IntegrityError("observation_lifecycle_identity_conflict")
        return str(existing["event_id"])
    connection.execute(
        "INSERT INTO observation_lifecycle_events "
        "(event_id, dedup_key, observation_id, generation, from_status, to_status, action, reason, actor, "
        "source_receipt_id, audit_identity, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event_id, dedup_key, observation_id, int(generation), from_status, to_status,
            action, reason, actor, source_receipt_id, audit_identity, created_at,
        ),
    )
    terminal_status = {
        "finalized": "finalized",
        "invalidated": "invalidated",
        "aborted": "aborted",
        # The compact root schema keeps terminal storage finite; semantic
        # supersession remains explicit in the append-only lifecycle event.
        "superseded": "invalidated",
    }.get(to_status)
    if terminal_status is not None:
        connection.execute(
            "UPDATE observation_windows SET status=?, terminal_at=?, terminal_reason=? "
            "WHERE observation_id=? AND generation=? AND status='active'",
            (terminal_status, created_at, reason, observation_id, int(generation)),
        )
    enqueue_lab_audit(
        event_type=f"observation_{action}",
        payload={
            "event_id": event_id, "from_status": from_status,
            "to_status": to_status, "reason": reason,
            "actor": actor, "source_receipt_id": source_receipt_id,
        },
        dedup_key=audit_identity,
        observation_id=observation_id,
        generation=int(generation),
        db=connection,
    )
    return event_id


def append_lifecycle_event(
    *,
    observation_id: str,
    generation: int,
    to_status: str,
    action: str,
    reason: str,
    actor: str,
    dedup_key: str,
    from_status: str | None = None,
    source_receipt_id: str | None = None,
    audit_identity: str | None = None,
    created_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Append one idempotent lifecycle event and its audit-outbox row."""
    for value, field in (
        (observation_id, "observation_id"), (to_status, "to_status"),
        (action, "action"), (reason, "reason"), (actor, "actor"), (dedup_key, "dedup_key"),
    ):
        _text(value, field)
    return _lab_write(db, lambda connection: _append_lifecycle_event(
        connection,
        observation_id=observation_id,
        generation=int(generation),
        to_status=to_status,
        action=action,
        reason=reason,
        actor=actor,
        dedup_key=dedup_key,
        from_status=from_status,
        source_receipt_id=source_receipt_id,
        audit_identity=audit_identity,
        created_at=created_at,
    ))


def revoke_manifest(
    *,
    manifest_id: str,
    manifest_hash: str,
    observation_id: str,
    generation: int,
    reason: str,
    actor: str,
    revoked_at: str | None = None,
    source_action_id: str | None = None,
    lifecycle_dedup_key: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Atomically append revocation, lifecycle transition and audit intent."""
    manifest_id = _text(manifest_id, "manifest_id")
    manifest_hash = _text(manifest_hash, "manifest_hash")
    reason = _text(reason, "reason")
    actor = _text(actor, "actor")
    revoked_at = _utc_text(revoked_at, "revoked_at") if revoked_at else _now()
    lifecycle_dedup_key = lifecycle_dedup_key or f"manifest-revoke:{manifest_hash}"

    def _write(connection: sqlite3.Connection) -> str:
        manifest = connection.execute(
            "SELECT manifest_id, observation_id, generation, manifest_hash "
            "FROM execution_manifests WHERE manifest_id=?", (manifest_id,)
        ).fetchone()
        if manifest is None or any(
            manifest[name] != value for name, value in {
                "observation_id": observation_id,
                "generation": int(generation),
                "manifest_hash": manifest_hash,
            }.items()
        ):
            raise ValueError("manifest_identity_unavailable")
        event_id = _append_lifecycle_event(
            connection,
            observation_id=observation_id,
            generation=int(generation),
            to_status="invalidated",
            action="revoke",
            reason=reason,
            actor=actor,
            dedup_key=lifecycle_dedup_key,
            source_receipt_id=source_action_id,
        )
        existing = connection.execute(
            "SELECT revocation_id, reason FROM manifest_revocations "
            "WHERE manifest_id=? AND manifest_hash=?",
            (manifest_id, manifest_hash),
        ).fetchone()
        if existing is not None:
            if existing["reason"] != reason:
                raise sqlite3.IntegrityError("manifest_revocation_identity_conflict")
            return str(existing["revocation_id"])
        revocation_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO manifest_revocations "
            "(revocation_id, manifest_id, manifest_hash, observation_id, generation, revoked_at, reason, "
            "lifecycle_event_id, source_action_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                revocation_id, manifest_id, manifest_hash, observation_id, int(generation), revoked_at,
                reason, event_id, source_action_id, _now(),
            ),
        )
        return revocation_id

    return _lab_write(db, _write)


def apply_evaluation_safety_disposition(
    *,
    observation_id: str,
    generation: int,
    evaluation_id: str,
    verdict: str,
    reason: str,
    actor: str = "observation_evaluator",
    db: sqlite3.Connection | None = None,
) -> str | None:
    """Withdraw a generation after a persisted negative or invalid evaluation.

    The evaluator owns this Lab transaction; Main/Risk/Execution are not
    consulted.  A safety disposition is idempotent and atomically records the
    lifecycle event plus revocations for every Paper Manifest on the
    generation.  Insufficient/neutral/positive evidence remains non-terminal.
    """
    if verdict not in {"negative", "invalid"}:
        return None
    _text(observation_id, "observation_id")
    if int(generation) <= 0:
        raise ValueError("generation_must_be_positive")
    _text(evaluation_id, "evaluation_id")
    reason = _text(reason, "reason")[:400]
    actor = _text(actor, "actor")
    action = "auto_withdraw" if verdict == "negative" else "auto_invalidate"
    dedup_key = f"observation-safety:{evaluation_id}:{action}"
    now = _now()

    def _write(connection: sqlite3.Connection) -> str | None:
        evaluation = connection.execute(
            "SELECT observation_id, generation, verdict FROM observation_evaluations "
            "WHERE evaluation_id=?",
            (evaluation_id,),
        ).fetchone()
        if evaluation is None or any(
            evaluation[field] != expected
            for field, expected in {
                "observation_id": observation_id,
                "generation": int(generation),
                "verdict": verdict,
            }.items()
        ):
            raise ValueError("evaluation_safety_identity_unavailable")
        existing = connection.execute(
            "SELECT * FROM observation_lifecycle_events WHERE dedup_key=?",
            (dedup_key,),
        ).fetchone()
        if existing is not None:
            if any(existing[name] != value for name, value in {
                "observation_id": observation_id,
                "generation": int(generation),
                "from_status": "active",
                "to_status": "invalidated",
                "action": action,
                "reason": reason,
                "actor": actor,
                "source_receipt_id": evaluation_id,
                "audit_identity": f"observation-safety:{evaluation_id}",
            }.items()):
                raise sqlite3.IntegrityError("observation_safety_identity_conflict")
            return str(existing[0])
        root = connection.execute(
            "SELECT status FROM observation_windows WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchone()
        if root is None:
            raise ValueError("observation_generation_root_unavailable")
        if str(root[0]) != "active":
            # An owner action or restore may already have withdrawn this
            # generation. Do not manufacture a second terminal transition.
            return None
        event_id = _append_lifecycle_event(
            connection,
            observation_id=observation_id,
            generation=int(generation),
            from_status="active",
            to_status="invalidated",
            action=action,
            reason=reason,
            actor=actor,
            source_receipt_id=evaluation_id,
            dedup_key=dedup_key,
            audit_identity=f"observation-safety:{evaluation_id}",
            created_at=now,
        )
        manifests = connection.execute(
            "SELECT manifest_id, manifest_hash FROM execution_manifests "
            "WHERE observation_id=? AND generation=? AND effect_mode='paper_treatment'",
            (observation_id, int(generation)),
        ).fetchall()
        for manifest in manifests:
            connection.execute(
                "INSERT OR IGNORE INTO manifest_revocations "
                "(revocation_id, manifest_id, manifest_hash, observation_id, generation, "
                "revoked_at, reason, lifecycle_event_id, source_action_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    manifest[0],
                    manifest[1],
                    observation_id,
                    int(generation),
                    now,
                    reason,
                    event_id,
                    evaluation_id,
                    now,
                ),
            )
            enqueue_lab_audit(
                event_type="manifest_revoked",
                payload={
                    "manifest_id": manifest[0],
                    "manifest_hash": manifest[1],
                    "action": action,
                    "reason": reason,
                    "actor": actor,
                    "source_evaluation_id": evaluation_id,
                },
                dedup_key=f"observation-safety-manifest:{evaluation_id}:{manifest[1]}",
                observation_id=observation_id,
                generation=int(generation),
                db=connection,
            )
        return event_id

    return _lab_write(db, _write)


def publish_manifest_with_lifecycle(
    manifest: ExecutionManifest,
    *,
    actor: str,
    reason: str,
    inference_plan: Mapping[str, Any] | None = None,
    no_manifest_reason: str | None = None,
) -> dict[str, Any]:
    """Publish a Manifest and its owning lifecycle/audit state atomically."""
    from aletheia.observation_lab.manifest import publish_manifest

    actor = _text(actor, "actor")
    reason = _text(reason, "reason")
    if no_manifest_reason is not None:
        no_manifest_reason = _text(no_manifest_reason, "no_manifest_reason")
    with lab_transaction(immediate=True) as db:
        _require_generation_root(db, manifest.observation_id, int(manifest.generation))
        contract = db.execute(
            "SELECT * FROM observation_generation_contracts "
            "WHERE observation_id=? AND generation=?",
            (manifest.observation_id, int(manifest.generation)),
        ).fetchone()
        if contract is None:
            raise ValueError("generation_contract_unavailable")
        root = db.execute(
            "SELECT status, spec_hash, spec_json FROM observation_windows "
            "WHERE observation_id=? AND generation=?",
            (manifest.observation_id, int(manifest.generation)),
        ).fetchone()
        if root is None or str(root[0]) != "active":
            raise ValueError("observation_generation_not_active")
        if str(root[1]) != manifest.spec_hash:
            raise ValueError("manifest_spec_hash_mismatch")
        try:
            manifest_start = datetime.fromisoformat(
                str(manifest.effective_from).replace("Z", "+00:00")
            )
            manifest_end = (
                datetime.fromisoformat(str(manifest.effective_until).replace("Z", "+00:00"))
                if manifest.effective_until is not None else None
            )
            generation_start = datetime.fromisoformat(
                str(contract["started_at"]).replace("Z", "+00:00")
            )
            generation_end = (
                datetime.fromisoformat(str(contract["effective_until"]).replace("Z", "+00:00"))
                if contract["effective_until"] is not None else None
            )
            if any(
                value.tzinfo is None or value.utcoffset() != timedelta(0)
                for value in (manifest_start, generation_start, *(
                    [manifest_end] if manifest_end is not None else []
                ), *(
                    [generation_end] if generation_end is not None else []
                ))
            ):
                raise ValueError("manifest_generation_interval_must_be_utc")
            if manifest_start < generation_start:
                raise ValueError("manifest_effective_from_before_generation")
            if generation_end is not None and (
                manifest_end is None or manifest_end > generation_end
            ):
                raise ValueError("manifest_effective_until_after_generation")
        except (TypeError, ValueError) as exc:
            if str(exc).startswith("manifest_"):
                raise
            raise ValueError("manifest_generation_interval_invalid") from exc
        if str(contract["behavior_contract_hash"]) != manifest.behavior_contract_hash:
            raise ValueError("manifest_behavior_contract_mismatch")
        contract_fields = [
            ("assignment_policy", manifest.assignment_policy),
            ("assignment_salt", manifest.assignment_salt),
            ("assignment_unit_key_template", manifest.assignment_unit_key_template),
        ]
        if manifest.manifest_contract_version == "execution-manifest/v2":
            contract_fields.extend([
                ("assignment_namespace_key", manifest.assignment_namespace_key),
                ("assignment_hash_version", manifest.assignment_hash_version),
                ("behavior_policy_epoch_ref", manifest.behavior_policy_epoch_ref),
            ])
        for field, manifest_value in contract_fields:
            if contract[field] != manifest_value:
                raise ValueError(f"manifest_{field}_mismatch")
        if float(contract["allocation_pct"]) != float(manifest.allocation_pct):
            raise ValueError("manifest_allocation_mismatch")
        if manifest.effect_mode == "paper_treatment":
            if not manifest.owner_admission_ref:
                raise ValueError("paper_manifest_requires_owner_admission")
            if contract["owner_admission_ref"] != manifest.owner_admission_ref:
                raise ValueError("owner_admission_lineage_mismatch")
            if not str(contract["stage"] or "").startswith("paper_"):
                raise ValueError("paper_manifest_requires_paper_stage")
            spec = _safe_json_object(root["spec_json"])
            if (
                spec.get("production_mutation_allowed") is not True
                or spec.get("authorizing") is not True
            ):
                raise ValueError("observe_only_generation")
            if no_manifest_reason is None and _adapter(
                manifest.treatment_adapter_id, manifest.treatment_adapter_version,
            ) is None:
                raise ValueError("treatment_adapter_unavailable")
        elif inference_plan is not None:
            raise ValueError("observe_only_manifest_cannot_publish_inference_plan")
        if no_manifest_reason is not None:
            if inference_plan is not None:
                raise ValueError("no_manifest_reason_cannot_have_inference_plan")
            event_id = _append_lifecycle_event(
                db,
                observation_id=manifest.observation_id,
                generation=int(manifest.generation),
                to_status="active",
                action="manifest_not_published",
                reason=f"{reason}:{no_manifest_reason}",
                actor=actor,
                dedup_key=f"manifest-not-published:{manifest.manifest_hash}",
            )
            return {
                "published": False,
                "manifest_id": manifest.manifest_id,
                "manifest_hash": manifest.manifest_hash,
                "lifecycle_event_id": event_id,
                "inference_plan_id": None,
                "no_manifest_reason": no_manifest_reason,
            }
        plan_id = None
        if inference_plan is not None:
            plan_payload = dict(inference_plan.get("plan") or inference_plan)
            cutoff = inference_plan.get("cutoff")
            if (
                manifest.effect_mode == "paper_treatment"
                and cutoff is None
            ):
                raise ValueError("paper_manifest_requires_frozen_inference_plan")
            plan_id = register_inference_plan(
                observation_id=manifest.observation_id,
                generation=int(manifest.generation),
                inference_contract_version=str(inference_plan.get("inference_contract_version") or "v1"),
                plan=plan_payload,
                status=str(inference_plan.get("status") or "collecting"),
                db=db,
            )
            if cutoff is not None:
                freeze_inference_plan(plan_id, cutoff=dict(cutoff), db=db)
        elif manifest.effect_mode == "paper_treatment" and no_manifest_reason is None:
            raise ValueError("paper_manifest_requires_frozen_inference_plan")
        published = publish_manifest(manifest, db=db)
        to_status = "paper_active" if manifest.effect_mode == "paper_treatment" else "shadow"
        event_id = _append_lifecycle_event(
            db,
            observation_id=manifest.observation_id,
            generation=int(manifest.generation),
            to_status=to_status,
            action="manifest_publish",
            reason=reason,
            actor=actor,
            dedup_key=f"manifest-publish:{manifest.manifest_hash}",
        )
        return {
            "published": published,
            "manifest_id": manifest.manifest_id,
            "manifest_hash": manifest.manifest_hash,
            "lifecycle_event_id": event_id,
            "inference_plan_id": plan_id,
            "no_manifest_reason": no_manifest_reason,
        }


def _validate_retention_claim_source(
    connection: sqlite3.Connection,
    *,
    observation_id: str,
    generation: int,
    source_kind: str,
    source_ref: str,
    source_hash: str,
) -> None:
    """Require a retention claim to be derivable from immutable Lab evidence.

    Retention claims are a rebuildable index, not an authority source.  A
    caller may only claim a source identity already bound to this generation
    by an imported Exposure or frozen Unit.  In particular, the importer hash
    for an outcome candidate is recomputed from the receipt identity instead
    of trusting a caller-provided label.
    """
    if source_kind == "outcome_candidate_key":
        rows = connection.execute(
            "SELECT source_receipt_id, receipt_sequence, outcome_candidate_key "
            "FROM observation_exposures "
            "WHERE observation_id=? AND generation=? AND outcome_candidate_key=?",
            (observation_id, int(generation), source_ref),
        ).fetchall()
        if not rows:
            raise ValueError("retention_claim_source_unproven")
        for row in rows:
            expected = _hash({
                "receipt_id": row[0],
                "sequence": row[1],
                "outcome_candidate_key": row[2],
            })
            if expected == source_hash:
                return
        raise ValueError("retention_claim_source_hash_mismatch")

    if source_kind in {"receipt", "main_receipt"}:
        row = connection.execute(
            "SELECT source_receipt_id, receipt_sequence, outcome_candidate_key "
            "FROM observation_exposures "
            "WHERE observation_id=? AND generation=? AND source_receipt_id=?",
            (observation_id, int(generation), source_ref),
        ).fetchone()
        if row is None:
            raise ValueError("retention_claim_source_unproven")
        expected = _hash({
            "receipt_id": row[0],
            "sequence": row[1],
            "outcome_candidate_key": row[2],
        })
        if expected != source_hash:
            raise ValueError("retention_claim_source_hash_mismatch")
        return

    unit = connection.execute(
        "SELECT source_hash FROM observation_units "
        "WHERE observation_id=? AND generation=? AND source_kind=? AND source_ref=?",
        (observation_id, int(generation), source_kind, source_ref),
    ).fetchone()
    if unit is None:
        raise ValueError("retention_claim_source_unproven")
    if str(unit[0]) != source_hash:
        raise ValueError("retention_claim_source_hash_mismatch")


def upsert_retention_claim(
    *,
    observation_id: str,
    generation: int,
    source_kind: str,
    source_ref: str,
    source_hash: str,
    claim_domain: str,
    derived_from_fingerprint: str,
    claim_id: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Derive one exact active Main-source retention claim."""
    for value, field in (
        (source_kind, "source_kind"), (source_ref, "source_ref"),
        (source_hash, "source_hash"), (claim_domain, "claim_domain"),
        (derived_from_fingerprint, "derived_from_fingerprint"),
    ):
        _text(value, field)
    claim_id = claim_id or uuid.uuid4().hex
    now = _now()

    def _write(connection: sqlite3.Connection) -> str:
        _require_generation_root(connection, observation_id, int(generation))
        _validate_retention_claim_source(
            connection,
            observation_id=observation_id,
            generation=int(generation),
            source_kind=source_kind,
            source_ref=source_ref,
            source_hash=source_hash,
        )
        existing = connection.execute(
            "SELECT * FROM observation_retention_claims WHERE observation_id=? AND generation=? "
            "AND source_kind=? AND source_ref=?",
            (observation_id, int(generation), source_kind, source_ref),
        ).fetchone()
        if existing is not None:
            if any(existing[name] != value for name, value in {
                "source_hash": source_hash,
                "claim_domain": claim_domain,
                "derived_from_fingerprint": derived_from_fingerprint,
            }.items()):
                raise sqlite3.IntegrityError("retention_claim_identity_conflict")
            if existing["status"] == "released":
                raise ValueError("retention_claim_already_released")
            return str(existing["claim_id"])
        connection.execute(
            "INSERT INTO observation_retention_claims "
            "(claim_id, observation_id, generation, source_kind, source_ref, source_hash, claim_domain, "
            "derived_from_fingerprint, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)",
            (
                claim_id, observation_id, int(generation), source_kind, source_ref, source_hash,
                claim_domain, derived_from_fingerprint, now,
            ),
        )
        return claim_id

    return _lab_write(db, _write)


def release_retention_claim(
    claim_id: str,
    *,
    proof_fingerprint: str,
    reason: str,
    released_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Release a claim only with explicit settlement/import/archive proof."""
    proof_fingerprint = _text(proof_fingerprint, "proof_fingerprint")
    reason = _text(reason, "reason")
    released_at = _utc_text(released_at, "released_at") if released_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT status, release_proof_fingerprint, release_reason FROM observation_retention_claims "
            "WHERE claim_id=?", (claim_id,)
        ).fetchone()
        if row is None:
            raise ValueError("retention_claim_unavailable")
        if row["status"] == "released":
            if row["release_proof_fingerprint"] != proof_fingerprint or row["release_reason"] != reason:
                raise sqlite3.IntegrityError("retention_release_identity_conflict")
            return claim_id
        connection.execute(
            "UPDATE observation_retention_claims SET status='released', released_at=?, "
            "release_proof_fingerprint=?, release_reason=? WHERE claim_id=?",
            (released_at, proof_fingerprint, reason, claim_id),
        )
        return claim_id

    return _lab_write(db, _write)


def record_archive(
    *,
    observation_id: str,
    generation: int,
    terminal_status: str,
    root_identity: str,
    archive_schema: str,
    archive_path: str,
    archive_sha256: str,
    replay_fingerprint: str,
    terminal_at: str,
    archive_id: str | None = None,
    created_at: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Record a verified immutable archive root before any hot-row eviction."""
    archive_path = str(Path(archive_path).expanduser().resolve())
    path = Path(archive_path)
    if not path.is_file():
        raise ValueError("archive_file_unavailable")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != archive_sha256:
        raise ValueError("archive_sha256_mismatch")
    for value, field in (
        (terminal_status, "terminal_status"), (root_identity, "root_identity"),
        (archive_schema, "archive_schema"), (archive_sha256, "archive_sha256"),
        (replay_fingerprint, "replay_fingerprint"),
    ):
        _text(value, field)
    terminal_at = _utc_text(terminal_at, "terminal_at")
    archive_id = archive_id or uuid.uuid4().hex
    created_at = _utc_text(created_at, "created_at") if created_at else _now()

    def _write(connection: sqlite3.Connection) -> str:
        _require_generation_root(connection, observation_id, int(generation))

        def _mark_archive_identity() -> None:
            connection.execute(
                "UPDATE observation_windows SET artifact_path=?, artifact_sha256=? "
                "WHERE observation_id=? AND generation=?",
                (archive_path, archive_sha256, observation_id, int(generation)),
            )

        existing = connection.execute(
            "SELECT * FROM observation_archives WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchone()
        if existing is not None:
            if any(existing[name] != value for name, value in {
                "root_identity": root_identity,
                "archive_schema": archive_schema,
                "archive_path": archive_path,
                "archive_sha256": archive_sha256,
                "replay_fingerprint": replay_fingerprint,
                "terminal_status": terminal_status,
            }.items()):
                raise sqlite3.IntegrityError("observation_archive_identity_conflict")
            _mark_archive_identity()
            return str(existing["archive_id"])
        connection.execute(
            "INSERT INTO observation_archives "
            "(archive_id, observation_id, generation, terminal_status, root_identity, archive_schema, archive_path, "
            "archive_sha256, replay_fingerprint, terminal_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                archive_id, observation_id, int(generation), terminal_status, root_identity, archive_schema,
                archive_path, archive_sha256, replay_fingerprint, terminal_at, created_at,
            ),
        )
        _mark_archive_identity()
        return archive_id

    return _lab_write(db, _write)


def write_generation_archive(
    observation_id: str,
    generation: int,
    *,
    archive_root: str | None = None,
) -> dict[str, Any]:
    """Build and verify a deterministic archive for one terminal Generation."""
    import gzip
    import os
    from pathlib import Path
    from aletheia.observation_lab.db import lab_db_path

    from aletheia.observation_lab.db import get_lab_db

    db = get_lab_db()
    root = db.execute(
        "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
        (observation_id, int(generation)),
    ).fetchone()
    if root is None:
        raise ValueError("observation_generation_root_unavailable")
    if root["status"] == "active":
        raise ValueError("active_generation_cannot_be_archived")
    # ``record_archive`` writes these fields back to the root after the
    # archive bytes are built.  They are locator metadata, not evidence; keep
    # them out of the payload so a second archive pass has the same identity.
    root_payload = dict(root)
    root_payload["artifact_path"] = None
    root_payload["artifact_sha256"] = None
    tables = {
        "observation_window": root_payload,
        "generation_contract": db.execute(
            "SELECT * FROM observation_generation_contracts WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchone(),
        "units": db.execute(
            "SELECT * FROM observation_units WHERE observation_id=? AND generation=? ORDER BY unit_id",
            (observation_id, int(generation)),
        ).fetchall(),
        "exposures": db.execute(
            "SELECT * FROM observation_exposures WHERE observation_id=? AND generation=? ORDER BY exposure_id",
            (observation_id, int(generation)),
        ).fetchall(),
        "evaluations": db.execute(
            "SELECT * FROM observation_evaluations WHERE observation_id=? AND generation=? ORDER BY evaluation_id",
            (observation_id, int(generation)),
        ).fetchall(),
        "results": db.execute(
            "SELECT * FROM observation_results WHERE observation_id=? AND generation=? ORDER BY result_id",
            (observation_id, int(generation)),
        ).fetchall(),
        "manifests": db.execute(
            "SELECT * FROM execution_manifests WHERE observation_id=? AND generation=? ORDER BY manifest_id",
            (observation_id, int(generation)),
        ).fetchall(),
        "inference_plans": db.execute(
            "SELECT * FROM observation_inference_plans WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchall(),
        "lifecycle_events": db.execute(
            "SELECT * FROM observation_lifecycle_events WHERE observation_id=? AND generation=? ORDER BY event_id",
            (observation_id, int(generation)),
        ).fetchall(),
        "manifest_revocations": db.execute(
            "SELECT * FROM manifest_revocations WHERE observation_id=? AND generation=? ORDER BY revocation_id",
            (observation_id, int(generation)),
        ).fetchall(),
        "retention_claims": db.execute(
            "SELECT * FROM observation_retention_claims WHERE observation_id=? AND generation=? ORDER BY claim_id",
            (observation_id, int(generation)),
        ).fetchall(),
    }
    serial = {
        key: (
            None
            if value is None
            else dict(value)
            if isinstance(value, (sqlite3.Row, dict))
            else [dict(item) for item in value]
        )
        for key, value in tables.items()
    }
    row_counts = {
        key: (0 if value is None else len(value) if isinstance(value, list) else 1)
        for key, value in serial.items()
    }
    row_fingerprints = {
        key: _hash(value) for key, value in serial.items()
    }
    payload = {
        "archive_schema": "observation-generation-archive/v1",
        "observation_id": observation_id,
        "generation": int(generation),
        "terminal_status": root["status"],
        "spec_hash": root["spec_hash"],
        "row_counts": row_counts,
        "row_fingerprints": row_fingerprints,
        "rows": serial,
    }
    payload["replay_fingerprint"] = _hash(payload)
    data = gzip.compress(_json(payload).encode("utf-8"), mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    identity = _hash({
        "observation_id": observation_id, "generation": int(generation),
        "spec_hash": root["spec_hash"], "replay_fingerprint": payload["replay_fingerprint"],
    })
    base = Path(archive_root).expanduser().resolve() if archive_root else lab_db_path().parent / "archives"
    destination = base / observation_id.replace("/", "_") / f"generation-{int(generation)}.json.gz"
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    staged.write_bytes(data)
    if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
        staged.unlink(missing_ok=True)
        raise ValueError("archive_sha256_verification_failed")
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        staged.unlink(missing_ok=True)
        raise ValueError("archive_identity_conflict")
    if not destination.exists():
        os.replace(staged, destination)
    else:
        staged.unlink(missing_ok=True)
    archive_id = record_archive(
        observation_id=observation_id,
        generation=int(generation),
        terminal_status=str(root["status"]),
        root_identity=identity,
        archive_schema="observation-generation-archive/v1",
        archive_path=str(destination),
        archive_sha256=digest,
        replay_fingerprint=str(payload["replay_fingerprint"]),
        terminal_at=str(root["terminal_at"] or root["collect_until"]),
    )
    return {
        "archive_id": archive_id,
        "archive_path": str(destination),
        "archive_sha256": digest,
        "root_identity": identity,
        "replay_fingerprint": payload["replay_fingerprint"],
    }


def verify_generation_archive(archive_id: str) -> dict[str, Any]:
    """Verify archive bytes and the compact Lab root without mutating state."""
    import gzip

    # Verification is a read-only proof surface.  Do not materialize a fresh
    # Lab when the authority database is missing; that would turn unavailable
    # archive evidence into an empty, apparently valid store.
    with readonly_lab_db(allow_missing=True) as lab:
        if lab is None:
            return {"available": False, "reason": "lab_unavailable"}
        row = lab.execute(
            "SELECT * FROM observation_archives WHERE archive_id=?", (archive_id,)
        ).fetchone()
    if row is None:
        return {"available": False, "reason": "archive_unavailable"}
    row = dict(row)
    path = _resolve_managed_archive_path(str(row["archive_path"]))
    if not path.is_file():
        return {"available": False, "reason": "archive_file_unavailable"}
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != row["archive_sha256"]:
        return {"available": False, "reason": "archive_sha256_mismatch"}
    try:
        payload = json.loads(gzip.decompress(data))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"available": False, "reason": "archive_payload_invalid"}
    if not isinstance(payload, dict):
        return {"available": False, "reason": "archive_payload_invalid"}
    if any(
        payload.get(field) != expected
        for field, expected in (
            ("archive_schema", row["archive_schema"]),
            ("observation_id", row["observation_id"]),
            ("generation", int(row["generation"])),
            ("terminal_status", row["terminal_status"]),
        )
    ):
        return {"available": False, "reason": "archive_identity_mismatch"}
    replay = payload.get("replay_fingerprint")
    candidate = dict(payload)
    candidate.pop("replay_fingerprint", None)
    if replay != _hash(candidate) or replay != row["replay_fingerprint"]:
        return {"available": False, "reason": "archive_replay_fingerprint_mismatch"}
    rows = payload.get("rows")
    counts = payload.get("row_counts")
    fingerprints = payload.get("row_fingerprints")
    if not isinstance(rows, dict) or not isinstance(counts, dict) or not isinstance(fingerprints, dict):
        return {"available": False, "reason": "archive_row_proof_unavailable"}
    for key, value in rows.items():
        actual_count = 0 if value is None else len(value) if isinstance(value, list) else 1
        if counts.get(key) != actual_count or fingerprints.get(key) != _hash(value):
            return {"available": False, "reason": "archive_row_proof_mismatch"}
    root = rows.get("observation_window")
    if not isinstance(root, dict) or any(
        root.get(field) != expected
        for field, expected in (
            ("observation_id", row["observation_id"]),
            ("generation", int(row["generation"])),
            ("status", row["terminal_status"]),
            ("spec_hash", payload.get("spec_hash")),
        )
    ):
        return {"available": False, "reason": "archive_root_identity_mismatch"}
    expected_root_identity = _hash({
        "observation_id": row["observation_id"],
        "generation": int(row["generation"]),
        "spec_hash": payload.get("spec_hash"),
        "replay_fingerprint": replay,
    })
    if str(row["root_identity"]) != expected_root_identity:
        return {"available": False, "reason": "archive_root_identity_mismatch"}
    return {
        "available": True,
        "archive_id": archive_id,
        "observation_id": row["observation_id"],
        "generation": int(row["generation"]),
        "root_identity": row["root_identity"],
        "archive_sha256": digest,
        "replay_fingerprint": replay,
    }


def compact_finalized_generations(
    *, retention_days: int = 30, limit: int = 10, archive_root: str | None = None,
) -> dict[str, Any]:
    """Archive terminal generations, then evict only their hot evidence rows."""
    from aletheia.observation_lab.db import get_lab_db
    from aletheia.storage.sqlite import acquire_write_lock, get_db, release_write_lock

    cutoff = (datetime.now(timezone.utc).timestamp() - max(0, int(retention_days)) * 86400)
    db = get_lab_db()
    rows = db.execute(
        "SELECT observation_id, generation, terminal_at FROM observation_windows "
        "WHERE status IN ('finalized','invalidated','aborted') AND terminal_at IS NOT NULL "
        "ORDER BY terminal_at, observation_id, generation LIMIT ?",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    archived = evicted = 0
    skipped = 0
    for row in rows:
        try:
            terminal = datetime.fromisoformat(str(row[2]).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, OSError):
            skipped += 1
            continue
        if terminal > cutoff:
            continue
        observation_id = str(row[0])
        generation = int(row[1])
        archive = db.execute(
            "SELECT archive_id FROM observation_archives WHERE observation_id=? AND generation=?",
            (observation_id, generation),
        ).fetchone()
        try:
            if archive is None:
                archive_id = write_generation_archive(
                    observation_id, generation, archive_root=archive_root,
                )["archive_id"]
                archived += 1
            else:
                archive_id = str(archive[0])
            verified = verify_generation_archive(archive_id)
            if not verified.get("available"):
                skipped += 1
                continue
            # Joint Main/Lab work follows the backup order: Main writer permit
            # first, then the Lab writer permit.  This avoids a cross-store
            # lock inversion during restore/compaction.
            acquire_write_lock()
            try:
                main = get_db()
                with lab_transaction(immediate=True) as connection:
                    claims = connection.execute(
                    "SELECT * FROM observation_retention_claims "
                    "WHERE observation_id=? AND generation=? AND status='active' "
                    "ORDER BY claim_id",
                    (observation_id, generation),
                    ).fetchall()
                    unresolved_claim = False
                    for claim in claims:
                        table = {
                            "filter_candidate_outcomes": "filter_candidate_outcomes",
                            "universe_forward_outcomes": "universe_forward_outcomes",
                        }.get(str(claim["claim_domain"]))
                        source = None
                        if table is not None:
                            source = main.execute(
                                f"SELECT * FROM {table} WHERE "
                                "id=? OR outcome_candidate_key=? "
                                "ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END LIMIT 1",
                                (str(claim["source_ref"]), str(claim["source_ref"]), str(claim["source_ref"])),
                            ).fetchone()
                        if source is None or str(source["status"] or "") not in {
                            "settled", "failed", "cancelled",
                        }:
                            unresolved_claim = True
                            break
                        release_retention_claim(
                            str(claim["claim_id"]),
                            proof_fingerprint=_hash({
                                "archive": verified["replay_fingerprint"],
                                "claim": dict(claim),
                                "source": dict(source),
                            }),
                            reason="terminal_generation_archive_and_outcome_verified",
                            db=connection,
                        )
                    if unresolved_claim:
                        skipped += 1
                        continue
                    for table in (
                        "observation_exposures",
                        "observation_evaluations",
                        "observation_units",
                        "observation_inference_plans",
                    ):
                        result = connection.execute(
                            f"DELETE FROM {table} WHERE observation_id=? AND generation=?",
                            (observation_id, generation),
                        )
                        evicted += max(0, result.rowcount or 0)
            finally:
                release_write_lock()
        except (FileNotFoundError, OSError, sqlite3.Error, ValueError):
            _log.exception(
                "Observation generation compaction failed for %s:%s",
                observation_id, generation,
            )
            skipped += 1
    return {
        "available": True,
        "considered": len(rows),
        "archived": archived,
        "evicted_rows": evicted,
        "skipped": skipped,
        "retention_days": int(retention_days),
    }


def _decision(
    baseline: dict[str, Any],
    *,
    reason: str,
    assignment: str = "unassigned",
    applied: bool = False,
    behavior_changed: bool = False,
    manifest: ExecutionManifest | None = None,
    active_manifest_snapshot: tuple[str, ...] | list[str] | None = None,
    manifest_set_fingerprint: str | None = None,
    revocation_snapshot_fingerprint: str | None = None,
    authority_source: str | None = None,
    authority_epoch: str | None = None,
    outcome_source: str | None = None,
) -> ObservationTreatmentDecision:
    return ObservationTreatmentDecision(
        payload=dict(baseline),
        applied=applied,
        assignment=assignment,
        behavior_changed=behavior_changed,
        reason=reason,
        observation_id=manifest.observation_id if manifest else None,
        generation=manifest.generation if manifest else None,
        manifest_id=manifest.manifest_id if manifest else None,
        manifest_hash=manifest.manifest_hash if manifest else None,
        active_manifest_snapshot=tuple(active_manifest_snapshot or ()),
        manifest_set_fingerprint=manifest_set_fingerprint,
        revocation_snapshot_fingerprint=revocation_snapshot_fingerprint,
        authority_source=authority_source,
        authority_epoch=authority_epoch,
        outcome_source=outcome_source,
    )


def _deny_tombstone_exists(
    *,
    db: sqlite3.Connection,
    manifest: ExecutionManifest,
) -> bool:
    row = db.execute(
        "SELECT 1 FROM observation_deny_tombstones WHERE "
        "(manifest_hash=? OR (manifest_hash IS NULL AND observation_id=? AND generation=?)) "
        "LIMIT 1",
        (manifest.manifest_hash, manifest.observation_id, manifest.generation),
    ).fetchone()
    return row is not None


def resolve_observation_treatment(
    *,
    adapter_id: str,
    candidate_key: str,
    context: Mapping[str, Any],
    baseline: dict[str, Any] | None = None,
    adapter_version: str | None = None,
    now: str | None = None,
    db: sqlite3.Connection | None = None,
    receipt_id: str | None = None,
    outcome_candidate_key: str | None = None,
    outcome_seeds: list | None = None,
    consumer: str | None = None,
    consumer_stage: str | None = None,
    runtime_session_id: str | None = None,
    producer_commit: str | None = None,
    persist_receipt: bool = True,
) -> ObservationTreatmentDecision:
    """Resolve one unit using Lab authority, always retaining a Main receipt.

    A caller that already owns a production transaction passes ``db``.  The
    receipt then joins that transaction; the resolver never writes ``lab.db``.
    """
    context_dict = dict(context)
    from aletheia.storage.sqlite import get_db

    main_db = db or get_db()
    authority_unavailable = False
    try:
        authority = get_authority_source(db=main_db)
    except sqlite3.OperationalError as exc:
        # Pre-V2 databases have no cutover row and remain on legacy behavior;
        # any other storage error is an unavailable authority, not legacy truth.
        authority_unavailable = "no such table: observation_authority_state" not in str(exc)
        authority = AuthoritySource(
            "legacy", "legacy-v1", "system", "state_unavailable", ""
        )
    except (FileNotFoundError, sqlite3.Error, RuntimeError):
        # An unavailable or malformed cutover row cannot grant either authority.
        authority_unavailable = True
        authority = AuthoritySource(
            "legacy", "authority-unavailable", "system", "state_unavailable", ""
        )
    adapter = _adapter(adapter_id, adapter_version)
    assignment_unit_key = _assignment_unit_key(adapter, context_dict, candidate_key)
    if adapter is None:
        # The caller may still provide a deterministic baseline when an adapter
        # is unavailable; no inferred treatment is permitted.
        baseline_value = dict(baseline or context_dict.get("baseline") or {})
        result = _decision(baseline_value, reason="adapter_unavailable")
        return _attach_receipt(
            result,
            baseline=baseline_value,
            db=db,
            receipt_id=receipt_id,
            candidate_key=candidate_key,
            outcome_candidate_key=outcome_candidate_key,
            outcome_seeds=outcome_seeds,
            consumer=consumer,
            consumer_stage=consumer_stage,
            runtime_session_id=runtime_session_id,
            producer_commit=producer_commit,
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            authority_source=authority.source,
            authority_epoch=authority.epoch,
            assignment_unit_key=assignment_unit_key,
            persist_receipt=persist_receipt,
        )

    try:
        baseline_value = dict(baseline if baseline is not None else adapter.deterministic_baseline(context_dict))
        _json(baseline_value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("deterministic_baseline_must_be_finite_json") from exc

    if adapter.requires_outcome_seed and outcome_candidate_key and len(outcome_seeds or []) != 1:
        result = _decision(baseline_value, reason="outcome_seed_unavailable")
        return _attach_receipt(
            result,
            baseline=baseline_value,
            db=db,
            receipt_id=receipt_id,
            candidate_key=candidate_key,
            outcome_candidate_key=outcome_candidate_key,
            outcome_seeds=outcome_seeds,
            consumer=consumer,
            consumer_stage=consumer_stage,
            runtime_session_id=runtime_session_id,
            producer_commit=producer_commit,
            adapter_id=adapter_id,
            adapter_version=adapter.adapter_version,
            authority_source=authority.source,
            authority_epoch=authority.epoch,
            assignment_unit_key=assignment_unit_key,
            outcome_source="fixed_horizon_candidate",
            persist_receipt=persist_receipt,
        )

    if authority.source != "lab":
        result = _decision(
            baseline_value,
            reason=("authority_source_unavailable" if authority_unavailable else "authority_source_legacy"),
        )
        return _attach_receipt(
            result,
            baseline=baseline_value,
            db=db,
            receipt_id=receipt_id,
            candidate_key=candidate_key,
            outcome_candidate_key=outcome_candidate_key,
            outcome_seeds=outcome_seeds,
            consumer=consumer,
            consumer_stage=consumer_stage,
            runtime_session_id=runtime_session_id,
            producer_commit=producer_commit,
            adapter_id=adapter_id,
            adapter_version=adapter.adapter_version,
            authority_source=authority.source,
            authority_epoch=authority.epoch,
            assignment_unit_key=assignment_unit_key,
            persist_receipt=persist_receipt,
        )

    # A stored joint checkpoint is the cross-store cutover boundary.  Once one
    # exists, a mismatch means Main/Lab/Manifest identity is unproven; do not
    # let a Paper manifest continue changing behavior from that cut.
    try:
        from aletheia.observation_lab.checkpoint import verify_joint_checkpoint

        checkpoint_verdict = verify_joint_checkpoint(create_lab=False)
    except Exception as exc:
        _log.warning("Observation joint checkpoint unavailable: %s", exc)
        checkpoint_verdict = None
    if checkpoint_verdict is None or not checkpoint_verdict.ok:
        result = _decision(baseline_value, reason="joint_checkpoint_unavailable")
        return _attach_receipt(
            result,
            baseline=baseline_value,
            db=db,
            receipt_id=receipt_id,
            candidate_key=candidate_key,
            outcome_candidate_key=outcome_candidate_key,
            outcome_seeds=outcome_seeds,
            consumer=consumer,
            consumer_stage=consumer_stage,
            runtime_session_id=runtime_session_id,
            producer_commit=producer_commit,
            adapter_id=adapter_id,
            adapter_version=adapter.adapter_version,
            authority_source=authority.source,
            authority_epoch=authority.epoch,
            assignment_unit_key=assignment_unit_key,
            outcome_source=("fixed_horizon_candidate" if outcome_candidate_key else None),
            persist_receipt=persist_receipt,
        )

    try:
        pending = main_db.execute(
            "SELECT COUNT(*) FROM observation_delivery_outbox "
            "WHERE acknowledged_at IS NULL"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        # A rolling reader may see the pre-ack schema. Treat its full outbox
        # as pending rather than inventing a healthy capacity state.
        pending = main_db.execute(
            "SELECT COUNT(*) FROM observation_delivery_outbox"
        ).fetchone()[0]
    if int(pending or 0) >= _OUTBOX_CAPACITY:
        result = _decision(baseline_value, reason="outbox_backpressure")
        return _attach_receipt(
            result,
            baseline=baseline_value,
            db=db,
            receipt_id=receipt_id,
            candidate_key=candidate_key,
            outcome_candidate_key=outcome_candidate_key,
            outcome_seeds=outcome_seeds,
            consumer=consumer,
            consumer_stage=consumer_stage,
            runtime_session_id=runtime_session_id,
            producer_commit=producer_commit,
            adapter_id=adapter_id,
            adapter_version=adapter.adapter_version,
            authority_source=authority.source,
            authority_epoch=authority.epoch,
            assignment_unit_key=assignment_unit_key,
            persist_receipt=persist_receipt,
        )

    interference_key = context_dict.get("interference_key")
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                result = _decision(baseline_value, reason="lab_unavailable")
            else:
                manifests = load_active_manifests(
                    now=now, interference_key=interference_key, db=lab,
                )
                snapshot_hashes = tuple(m.manifest_hash for m in manifests)
                snapshot_fingerprint = _hash(list(snapshot_hashes))
                root_status: dict[str, str] = {}
                root_specs: dict[str, dict[str, Any]] = {}
                for manifest in manifests:
                    root = lab.execute(
                        "SELECT status, spec_json FROM observation_windows "
                        "WHERE observation_id=? AND generation=?",
                        (manifest.observation_id, manifest.generation),
                    ).fetchone()
                    # Pre-V2 manifests may not have a root row.  Keep those
                    # legacy Lab records readable; once a V2 root exists its
                    # terminal status is authoritative.
                    root_status[manifest.manifest_hash] = (
                        str(root["status"]) if root is not None else "active"
                    )
                    root_specs[manifest.manifest_hash] = (
                        _safe_json_object(root["spec_json"]) if root is not None else {}
                    )
                active_manifests = [
                    manifest for manifest in manifests
                    if root_status[manifest.manifest_hash] == "active"
                ]
                relevant_manifests = [
                    manifest for manifest in manifests
                    if manifest.treatment_adapter_id == adapter_id
                ]
                active_relevant_manifests = [
                    manifest for manifest in active_manifests
                    if manifest.treatment_adapter_id == adapter_id
                ]
                inactive_manifest = next(
                    (
                        manifest for manifest in relevant_manifests
                        if root_status[manifest.manifest_hash] != "active"
                    ),
                    None,
                )
                revocation_rows = lab.execute(
                    "SELECT manifest_hash, revoked_at, reason FROM manifest_revocations "
                    "WHERE manifest_hash IN ({}) ORDER BY manifest_hash, revoked_at".format(
                        ",".join("?" for _ in manifests) or "NULL"
                    ),
                    [m.manifest_hash for m in manifests],
                ).fetchall() if manifests else []
                revocation_fingerprint = _hash([tuple(row) for row in revocation_rows])
                try:
                    decision_now = datetime.fromisoformat(
                        str(now or _now()).replace("Z", "+00:00")
                    )
                    if decision_now.tzinfo is None or decision_now.utcoffset() is None:
                        raise ValueError("revocation_decision_time_must_be_utc")
                    decision_now = decision_now.astimezone(timezone.utc)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError("revocation_decision_time_unavailable") from exc
                revoked = set()
                for row in revocation_rows:
                    try:
                        revoked_at = datetime.fromisoformat(
                            str(row[1]).replace("Z", "+00:00")
                        )
                        if revoked_at.tzinfo is None or revoked_at.utcoffset() is None:
                            raise ValueError
                        if revoked_at.astimezone(timezone.utc) <= decision_now:
                            revoked.add(str(row[0]))
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ValueError("manifest_revocation_timestamp_unavailable") from exc
                active_unrevoked_manifests = [
                    manifest for manifest in active_manifests
                    if manifest.manifest_hash not in revoked
                ]
                active_relevant_unrevoked_manifests = [
                    manifest for manifest in active_relevant_manifests
                    if manifest.manifest_hash not in revoked
                ]
                revoked_manifest = next(
                    (
                        manifest for manifest in active_relevant_manifests
                        if manifest.manifest_hash in revoked
                    ),
                    None,
                )
                snapshot_args = {
                    "active_manifest_snapshot": snapshot_hashes,
                    "manifest_set_fingerprint": snapshot_fingerprint,
                    "revocation_snapshot_fingerprint": revocation_fingerprint,
                }
                if not active_relevant_unrevoked_manifests and inactive_manifest is not None:
                    result = _decision(
                        baseline_value,
                        reason="observation_generation_not_active",
                        manifest=inactive_manifest,
                        **snapshot_args,
                    )
                elif not active_relevant_unrevoked_manifests and revoked_manifest is not None:
                    result = _decision(
                        baseline_value,
                        reason="manifest_revoked",
                        manifest=revoked_manifest,
                        **snapshot_args,
                    )
                else:
                    incompatible = [
                        m for m in active_unrevoked_manifests
                        if m.treatment_adapter_id == adapter_id
                        and m.treatment_adapter_version != adapter.adapter_version
                    ]
                    if incompatible:
                        result = _decision(
                            baseline_value,
                            reason="adapter_version_incompatible",
                            manifest=incompatible[0],
                            **snapshot_args,
                        )
                    else:
                        eligible = []
                        for manifest in active_unrevoked_manifests:
                            if manifest.treatment_adapter_id != adapter_id:
                                continue
                            try:
                                if adapter.eligible(manifest, context_dict):
                                    eligible.append(manifest)
                            except Exception:
                                result = _decision(
                                    baseline_value,
                                    reason="adapter_eligibility_unavailable",
                                    manifest=manifest,
                                    **snapshot_args,
                                )
                                break
                        else:
                            if not eligible:
                                result = _decision(
                                    baseline_value,
                                    reason="no_eligible_manifest",
                                    **snapshot_args,
                                )
                            else:
                                from aletheia.observation_lab.conflict import resolve_manifest_conflict

                                conflict = resolve_manifest_conflict(active_unrevoked_manifests)
                                if conflict.outcome.startswith("undeclared_conflict"):
                                    result = _decision(
                                        baseline_value,
                                        reason=conflict.outcome,
                                        **snapshot_args,
                                    )
                                elif conflict.outcome == "factorial_ok":
                                    result = _decision(
                                        baseline_value,
                                        reason="factorial_requires_versioned_adapter",
                                        **snapshot_args,
                                    )
                                else:
                                    manifest = next(
                                        (item for item in eligible if item in conflict.admitted),
                                        None,
                                    )
                                    if manifest is None:
                                        result = _decision(
                                            baseline_value,
                                            reason="manifest_conflict_no_admitted_match",
                                            **snapshot_args,
                                        )
                                    elif manifest.manifest_contract_version not in _SUPPORTED_MANIFEST_VERSIONS:
                                        result = _decision(
                                            baseline_value,
                                            reason="manifest_contract_unsupported",
                                            manifest=manifest,
                                            **snapshot_args,
                                        )
                                    elif manifest.manifest_contract_version == "execution-manifest/v2" and (
                                        manifest.deterministic_baseline != baseline_value
                                    ):
                                        result = _decision(
                                            baseline_value,
                                            reason="baseline_manifest_mismatch",
                                            manifest=manifest,
                                            **snapshot_args,
                                        )
                                    elif manifest.effect_mode == "paper_treatment" and (
                                        root_specs[manifest.manifest_hash].get(
                                            "production_mutation_allowed"
                                        ) is not True
                                        or root_specs[manifest.manifest_hash].get(
                                            "authorizing"
                                        ) is not True
                                    ):
                                        result = _decision(
                                            baseline_value,
                                            reason="observe_only_generation",
                                            manifest=manifest,
                                            **snapshot_args,
                                        )
                                    elif _deny_tombstone_exists(db=main_db, manifest=manifest):
                                        result = _decision(
                                            baseline_value,
                                            reason="main_deny_tombstone",
                                            manifest=manifest,
                                            **snapshot_args,
                                        )
                                    else:
                                        assignment_key = assignment_unit_key
                                        resolved = resolve_treatment(
                                            manifest=manifest,
                                            candidate_key=candidate_key,
                                            assignment_key=assignment_key,
                                            now=now,
                                        )
                                        payload = dict(baseline_value)
                                        changed = False
                                        if resolved.applied:
                                            try:
                                                payload = dict(
                                                    adapter.apply_payload(
                                                        baseline_value,
                                                        resolved.payload,
                                                        context_dict,
                                                    )
                                                )
                                                _json(payload)
                                            except (TypeError, ValueError, OverflowError) as exc:
                                                _log.warning("treatment adapter output unavailable: %s", exc)
                                                payload = dict(baseline_value)
                                                resolved = ManifestResolution(
                                                    applied=False,
                                                    assignment=resolved.assignment,
                                                    manifest_id=resolved.manifest_id,
                                                    manifest_hash=resolved.manifest_hash,
                                                    payload=payload,
                                                    reason="adapter_output_unavailable",
                                                    behavior_changed=False,
                                                )
                                            changed = payload != baseline_value
                                        result = ObservationTreatmentDecision(
                                            payload=payload,
                                            applied=resolved.applied,
                                            assignment=resolved.assignment,
                                            behavior_changed=changed,
                                            reason=resolved.reason,
                                            observation_id=manifest.observation_id,
                                            generation=manifest.generation,
                                            manifest_id=manifest.manifest_id,
                                            manifest_hash=manifest.manifest_hash,
                                            active_manifest_snapshot=snapshot_hashes,
                                            manifest_set_fingerprint=snapshot_fingerprint,
                                            revocation_snapshot_fingerprint=revocation_fingerprint,
                                        )
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        _log.warning("Observation Lab treatment resolution unavailable: %s", exc)
        result = _decision(baseline_value, reason="lab_projection_unavailable")

    # Keep the resolver decision itself self-describing even when a fail-closed
    # branch did not need to carry a Manifest.
    if result.authority_source is None or result.authority_epoch is None:
        result = ObservationTreatmentDecision(
            **{
                **result.__dict__,
                "authority_source": authority.source,
                "authority_epoch": authority.epoch,
                "outcome_source": (
                    "fixed_horizon_candidate" if outcome_candidate_key else None
                ),
            }
        )

    return _attach_receipt(
        result,
        baseline=baseline_value,
        db=db,
        receipt_id=receipt_id,
        candidate_key=candidate_key,
        outcome_candidate_key=outcome_candidate_key,
        outcome_seeds=outcome_seeds,
        consumer=consumer,
        consumer_stage=consumer_stage,
        runtime_session_id=runtime_session_id,
        producer_commit=producer_commit,
        adapter_id=adapter_id,
        adapter_version=adapter.adapter_version,
        authority_source=authority.source,
        authority_epoch=authority.epoch,
        assignment_unit_key=assignment_unit_key,
        outcome_source=("fixed_horizon_candidate" if outcome_candidate_key else None),
        persist_receipt=persist_receipt,
    )


def _insert_receipt(connection: sqlite3.Connection, values: dict[str, Any]) -> str:
    existing = connection.execute(
        "SELECT * FROM observation_delivery_outbox "
        "WHERE receipt_id=?",
        (values["receipt_id"],),
    ).fetchone()
    if existing is not None:
        immutable_fields = (
            "receipt_kind", "observation_id", "generation", "manifest_id", "manifest_hash",
            "treatment_adapter_id", "treatment_adapter_version", "assignment_unit_key",
            "candidate_key", "consumer", "consumer_stage", "assignment", "applied",
            "behavior_changed", "baseline_payload_hash", "applied_payload_hash",
            "baseline_payload_json", "applied_payload_json", "outcome_candidate_key",
            "outcome_source", "resolution_reason", "behavior_contract_hash",
            "behavior_policy_epoch_ref", "active_manifest_snapshot_json",
            "manifest_set_fingerprint", "revocation_snapshot_fingerprint", "authority_source",
            "authority_epoch", "runtime_session_id", "producer_commit", "actor", "owner_action",
            "expected_manifest_hash", "expected_generation", "dedup_key",
        )
        if any(
            field in existing.keys() and existing[field] != values.get(field)
            for field in immutable_fields
        ):
            raise sqlite3.IntegrityError("observation_receipt_identity_conflict")
        return str(existing["receipt_id"])
    available_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(observation_delivery_outbox)").fetchall()
    }
    bound_values = {key: value for key, value in values.items() if key in available_columns}
    columns = ", ".join(bound_values)
    placeholders = ", ".join("?" for _ in bound_values)
    connection.execute(
        f"INSERT INTO observation_delivery_outbox ({columns}) VALUES ({placeholders})",
        tuple(bound_values.values()),
    )
    return str(values["receipt_id"])


def _outbox_capacity_available(
    connection: sqlite3.Connection, *, receipt_id: str | None = None,
) -> bool:
    """Return whether one new Lab-authority receipt fits the bounded outbox."""
    if receipt_id and connection.execute(
        "SELECT 1 FROM observation_delivery_outbox WHERE receipt_id=?",
        (receipt_id,),
    ).fetchone() is not None:
        return True
    try:
        pending = connection.execute(
            "SELECT COUNT(*) FROM observation_delivery_outbox "
            "WHERE acknowledged_at IS NULL"
        ).fetchone()[0]
    except sqlite3.OperationalError:
        # Rolling upgrades may not have the acknowledgement column yet; the
        # whole legacy outbox is conservatively treated as pending.
        pending = connection.execute(
            "SELECT COUNT(*) FROM observation_delivery_outbox"
        ).fetchone()[0]
    return int(pending or 0) < _OUTBOX_CAPACITY


def append_delivery_receipt(
    *,
    receipt_kind: str = "treatment",
    receipt_id: str | None = None,
    observation_id: str | None = None,
    generation: int | None = None,
    manifest_id: str | None = None,
    manifest_hash: str | None = None,
    treatment_adapter_id: str | None = None,
    treatment_adapter_version: str | None = None,
    assignment_unit_key: str | None = None,
    candidate_key: str | None = None,
    consumer: str | None = None,
    consumer_stage: str | None = None,
    assignment: str = "unassigned",
    applied: bool = False,
    behavior_changed: bool = False,
    baseline_payload: dict[str, Any] | None = None,
    applied_payload: dict[str, Any] | None = None,
    outcome_candidate_key: str | None = None,
    outcome_seeds: list | None = None,
    outcome_source: str | None = None,
    resolution_reason: str = "unavailable",
    behavior_contract_hash: str | None = None,
    behavior_policy_epoch_ref: str | None = None,
    active_manifest_snapshot: list[str] | None = None,
    manifest_set_fingerprint: str | None = None,
    revocation_snapshot_fingerprint: str | None = None,
    authority_source: str = "legacy",
    authority_epoch: str | None = None,
    runtime_session_id: str | None = None,
    producer_commit: str | None = None,
    actor: str | None = None,
    owner_action: str | None = None,
    expected_manifest_hash: str | None = None,
    expected_generation: int | None = None,
    dedup_key: str | None = None,
    db: sqlite3.Connection | None = None,
) -> str:
    """Append one Main-owned receipt, joining an existing transaction when given."""
    if receipt_kind not in {"treatment", "capture", "owner_action"}:
        raise ValueError("invalid_observation_receipt_kind")
    if assignment not in {"treatment", "control", "unassigned"}:
        raise ValueError("invalid_observation_receipt_assignment")
    if behavior_changed and (not applied or assignment != "treatment"):
        raise ValueError("behavior_change_requires_applied_treatment")
    if authority_source not in {"legacy", "lab"}:
        raise ValueError("invalid_observation_authority_source")
    baseline_payload = dict(baseline_payload or {})
    applied_payload = dict(applied_payload or baseline_payload)
    _json(baseline_payload)
    _json(applied_payload)
    values = {
        "receipt_id": receipt_id or uuid.uuid4().hex,
        "receipt_kind": receipt_kind,
        "observation_id": observation_id,
        "generation": generation,
        "manifest_id": manifest_id,
        "manifest_hash": manifest_hash,
        "treatment_adapter_id": treatment_adapter_id,
        "treatment_adapter_version": treatment_adapter_version,
        "assignment_unit_key": assignment_unit_key,
        "candidate_key": candidate_key,
        "consumer": consumer,
        "consumer_stage": consumer_stage,
        "assignment": assignment,
        "applied": int(bool(applied)),
        "behavior_changed": int(bool(behavior_changed)),
        "baseline_payload_hash": _hash(baseline_payload),
        "applied_payload_hash": _hash(applied_payload),
        "baseline_payload_json": _json(baseline_payload),
        "applied_payload_json": _json(applied_payload),
        "outcome_candidate_key": outcome_candidate_key,
        "outcome_source": outcome_source,
        "resolution_reason": _text(resolution_reason, "resolution_reason"),
        "behavior_contract_hash": behavior_contract_hash,
        "behavior_policy_epoch_ref": behavior_policy_epoch_ref,
        "active_manifest_snapshot_json": _json(active_manifest_snapshot or []),
        "manifest_set_fingerprint": manifest_set_fingerprint,
        "revocation_snapshot_fingerprint": revocation_snapshot_fingerprint,
        "authority_source": authority_source,
        "authority_epoch": authority_epoch,
        "runtime_session_id": runtime_session_id,
        "producer_commit": producer_commit,
        "actor": actor,
        "owner_action": owner_action,
        "expected_manifest_hash": expected_manifest_hash,
        "expected_generation": expected_generation,
        "dedup_key": dedup_key,
        "applied_at": _now(),
    }
    from aletheia.storage.repositories import insert_filter_candidate_outcomes_batch

    if db is not None and not db.in_transaction:
        raise RuntimeError("receipt_requires_main_transaction")
    if db is not None and db.in_transaction:
        if authority_source == "lab" and not _outbox_capacity_available(
            db, receipt_id=values["receipt_id"]
        ):
            raise RuntimeError("observation_outbox_capacity")
        insert_filter_candidate_outcomes_batch(outcome_seeds or [], db=db)
        return _insert_receipt(db, values)
    from aletheia.storage.repositories import transaction
    with transaction(immediate=True):
        from aletheia.storage.sqlite import get_db
        connection = db or get_db()
        if authority_source == "lab" and not _outbox_capacity_available(
            connection, receipt_id=values["receipt_id"]
        ):
            raise RuntimeError("observation_outbox_capacity")
        insert_filter_candidate_outcomes_batch(outcome_seeds or [])
        return _insert_receipt(connection, values)


def _attach_receipt(
    decision: ObservationTreatmentDecision,
    *,
    baseline: dict[str, Any],
    db: sqlite3.Connection | None,
    receipt_id: str | None,
    candidate_key: str,
    outcome_candidate_key: str | None,
    outcome_seeds: list | None,
    consumer: str | None,
    consumer_stage: str | None,
    runtime_session_id: str | None,
    producer_commit: str | None,
    adapter_id: str,
    adapter_version: str | None,
    authority_source: str = "legacy",
    authority_epoch: str | None = None,
    assignment_unit_key: str | None = None,
    outcome_source: str | None = None,
    persist_receipt: bool = True,
) -> ObservationTreatmentDecision:
    if not persist_receipt:
        return ObservationTreatmentDecision(**{
            **decision.__dict__,
            "receipt_id": receipt_id,
            "authority_source": authority_source,
            "authority_epoch": authority_epoch,
            "outcome_source": outcome_source,
        })
    if authority_source == "lab":
        # The bounded outbox is itself the backpressure boundary.  Once full,
        # retain the deterministic baseline decision but do not append another
        # fallback/capture row that would exceed the declared capacity.  An
        # existing receipt remains idempotently readable during retries.
        from aletheia.storage.sqlite import get_db

        receipt_db = db or get_db()
        if not _outbox_capacity_available(receipt_db, receipt_id=receipt_id):
            return ObservationTreatmentDecision(**{
                **decision.__dict__,
                "payload": dict(baseline),
                "applied": False,
                "assignment": "unassigned",
                "behavior_changed": False,
                "reason": "outbox_backpressure",
                "receipt_id": None,
                "authority_source": authority_source,
                "authority_epoch": authority_epoch,
                "outcome_source": outcome_source,
            })
    receipt = append_delivery_receipt(
        receipt_kind=("treatment" if decision.observation_id is not None else "capture"),
        receipt_id=receipt_id,
        observation_id=decision.observation_id,
        generation=decision.generation,
        manifest_id=decision.manifest_id,
        manifest_hash=decision.manifest_hash,
        treatment_adapter_id=adapter_id,
        treatment_adapter_version=adapter_version,
        assignment_unit_key=assignment_unit_key or candidate_key,
        candidate_key=candidate_key,
        consumer=consumer,
        consumer_stage=consumer_stage,
        assignment=decision.assignment,
        applied=decision.applied,
        behavior_changed=decision.behavior_changed,
        baseline_payload=baseline,
        applied_payload=decision.payload,
        outcome_candidate_key=outcome_candidate_key,
        outcome_seeds=outcome_seeds,
        outcome_source=outcome_source,
        resolution_reason=decision.reason,
        active_manifest_snapshot=list(decision.active_manifest_snapshot)
        or ([decision.manifest_hash] if decision.manifest_hash else []),
        manifest_set_fingerprint=decision.manifest_set_fingerprint,
        revocation_snapshot_fingerprint=decision.revocation_snapshot_fingerprint,
        authority_source=authority_source,
        authority_epoch=authority_epoch,
        runtime_session_id=runtime_session_id,
        producer_commit=producer_commit,
        db=db,
    )
    return ObservationTreatmentDecision(**{
        **decision.__dict__,
        "receipt_id": receipt,
        "authority_source": authority_source,
        "authority_epoch": authority_epoch,
        "outcome_source": outcome_source,
    })


def record_owner_action(
    *,
    action: str,
    actor: str,
    reason: str,
    observation_id: str | None = None,
    generation: int | None = None,
    manifest_id: str | None = None,
    manifest_hash: str | None = None,
    expected_manifest_hash: str | None = None,
    expected_generation: int | None = None,
    dedup_key: str | None = None,
) -> str:
    """Persist safety denial plus owner-action transport in one app transaction."""
    if action not in _OWNER_ACTIONS:
        raise ValueError("unknown_owner_action")
    actor = _text(actor, "actor")
    reason = _text(reason, "reason")
    dedup_key = _text(dedup_key, "dedup_key")
    if not observation_id or generation is None or int(generation) <= 0:
        raise ValueError("owner_action_identity_required")
    if expected_generation is None:
        expected_generation = int(generation)
    if int(expected_generation) != int(generation) or int(expected_generation) <= 0:
        raise ValueError("owner_action_generation_mismatch")
    from aletheia.storage.repositories import transaction
    with transaction(immediate=True):
        from aletheia.storage.sqlite import get_db
        db = get_db()
        if action in _SAFETY_ACTIONS:
            tombstone_id = uuid.uuid4().hex
            db.execute(
                "INSERT OR IGNORE INTO observation_deny_tombstones "
                "(tombstone_id, observation_id, generation, manifest_id, manifest_hash, reason, actor, source_action_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tombstone_id,
                    observation_id,
                    generation,
                    manifest_id,
                    manifest_hash or expected_manifest_hash,
                    reason,
                    actor,
                    dedup_key,
                    _now(),
                ),
            )
        authority = get_authority_source(db=db)
        return append_delivery_receipt(
            receipt_kind="owner_action",
            receipt_id=f"owner-action:{dedup_key}",
            observation_id=observation_id,
            generation=generation,
            manifest_id=manifest_id,
            manifest_hash=manifest_hash,
            assignment="unassigned",
            # Preserve the authenticated operator explanation across the
            # asynchronous Main -> Lab boundary.  The importer uses this
            # exact value for the lifecycle/audit reason.
            resolution_reason=reason,
            authority_source=authority.source,
            authority_epoch=authority.epoch,
            actor=actor,
            owner_action=action,
            expected_manifest_hash=expected_manifest_hash,
            expected_generation=expected_generation,
            dedup_key=dedup_key,
            db=db,
        )


_STAGE_SUCCESSORS = {
    "shadow": "paper_10",
    "paper_10": "paper_25",
    "paper_25": "paper_50",
    "paper_50": "paper_active",
}


def _apply_owner_action(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> tuple[str, int]:
    """Apply one imported owner intent and return its event/generation identity."""
    observation_id = str(row["observation_id"])
    generation = int(row["generation"])
    action = str(row["owner_action"] or "")
    reason = str(row["resolution_reason"] or "owner_action")
    actor = str(row["actor"] or "unknown")
    receipt_id = str(row["receipt_id"])
    existing_event = connection.execute(
        "SELECT event_id, generation FROM observation_lifecycle_events "
        "WHERE source_receipt_id=? ORDER BY generation DESC, created_at DESC, event_id DESC LIMIT 1",
        (receipt_id,),
    ).fetchone()
    if existing_event is not None:
        # The Lab transaction may be replayed after Main acknowledgement or a
        # cursor restore.  Return the already committed lifecycle identity
        # before inspecting the now-terminal predecessor again.
        return str(existing_event[0]), int(existing_event[1])
    root = connection.execute(
        "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
        (observation_id, generation),
    ).fetchone()
    if root is None:
        raise ValueError("observation_generation_root_unavailable")
    if str(root["status"]) != "active":
        raise ValueError(f"owner_action_generation_{root['status']}")
    if row["expected_manifest_hash"]:
        manifest = connection.execute(
            "SELECT manifest_hash FROM execution_manifests WHERE manifest_id=?",
            (row["manifest_id"],),
        ).fetchone()
        if manifest is None or str(manifest[0]) != str(row["expected_manifest_hash"]):
            raise ValueError("owner_action_stale_manifest")
    contract = connection.execute(
        "SELECT * FROM observation_generation_contracts WHERE observation_id=? AND generation=?",
        (observation_id, generation),
    ).fetchone()
    current_stage = str(contract["stage"] if contract else "")
    successor_stage = {
        "admit": "paper_10",
        "promote": _STAGE_SUCCESSORS.get(current_stage),
        "refreeze": current_stage,
    }.get(action)
    if action == "admit" and current_stage != "shadow":
        raise ValueError("owner_action_admit_requires_shadow")
    if action == "promote" and successor_stage is None:
        raise ValueError("owner_action_promote_stage_unavailable")
    if action in {"admit", "promote", "refreeze"}:
        if contract is None:
            raise ValueError("generation_contract_unavailable")
        next_generation = int(connection.execute(
            "SELECT COALESCE(MAX(generation), 0) + 1 FROM observation_windows WHERE observation_id=?",
            (observation_id,),
        ).fetchone()[0])
        now = _now()
        connection.execute(
            "UPDATE observation_windows SET status='invalidated', terminal_at=?, terminal_reason=? "
            "WHERE observation_id=? AND generation=? AND status='active'",
            (now, f"superseded_by_owner_action:{action}", observation_id, generation),
        )
        predecessor_event = _append_lifecycle_event(
            connection,
            observation_id=observation_id,
            generation=generation,
            from_status="active",
            to_status="superseded",
            action=action,
            reason=reason,
            actor=actor,
            source_receipt_id=receipt_id,
            dedup_key=f"owner-action-predecessor:{receipt_id}",
            audit_identity=f"observation-owner:{receipt_id}:predecessor",
        )
        spec = _safe_json_object(root["spec_json"])
        spec["stage"] = successor_stage
        paper_successor = str(successor_stage).startswith("paper_") and action in {"admit", "promote"}
        spec["authority_mode"] = "external_authority" if paper_successor else "observe_only"
        # Registration is always observe-only.  Only this authenticated owner
        # transition can opt a successor into a behavior-bearing generation;
        # the separate Manifest publication still has to prove the adapter,
        # frozen look, and owner lineage in the same Lab transaction.
        spec["production_mutation_allowed"] = paper_successor
        spec["authorizing"] = paper_successor
        spec_json = _json(spec)
        spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
        collect_until = str(root["collect_until"])
        if collect_until <= now:
            collect_until = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(timespec="microseconds")
        connection.execute(
            "INSERT INTO observation_windows "
            "(observation_id, kind, handler_version, generation, slot_key, authority_mode, status, "
            "spec_json, spec_hash, producer_commit, started_at, collect_until, finalize_not_before) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
            (
                observation_id, root["kind"], root["handler_version"], next_generation,
                root["slot_key"], spec["authority_mode"], spec_json, spec_hash,
                root["producer_commit"], now, collect_until, collect_until,
            ),
        )
        if contract is not None:
            gates = _safe_json_object(contract["gates_json"])
            harm_budget = _safe_json_object(contract["harm_budget_json"])
            record_generation_contract(
                observation_id=observation_id,
                generation=next_generation,
                stage=str(successor_stage),
                parent_generation=generation,
                assignment_policy=str(contract["assignment_policy"]),
                assignment_namespace_key=str(contract["assignment_namespace_key"]),
                assignment_salt=str(contract["assignment_salt"]),
                assignment_unit_key_template=str(contract["assignment_unit_key_template"]),
                assignment_hash_version=str(contract["assignment_hash_version"]),
                allocation_pct=(
                    {"paper_10": 0.10, "paper_25": 0.25, "paper_50": 0.50}.get(
                        str(successor_stage), float(contract["allocation_pct"])
                    )
                ),
                target_horizon=str(contract["target_horizon"]),
                outcome_contract_version=str(contract["outcome_contract_version"]),
                metric_version=str(contract["metric_version"]),
                behavior_contract_hash=str(contract["behavior_contract_hash"]),
                behavior_policy_epoch_ref=contract["behavior_policy_epoch_ref"],
                behavior_policy_epoch_hash=contract["behavior_policy_epoch_hash"],
                owner_admission_ref=(
                    f"owner-action:{receipt_id}"
                    if action in {"admit", "promote"}
                    else contract["owner_admission_ref"]
                ),
                started_at=now,
                effective_until=collect_until,
                gates=gates,
                harm_budget=harm_budget,
                db=connection,
            )
        if paper_successor:
            # Every treatment-bearing successor has a fresh Lab-owned fixed-
            # look plan before it can publish a Paper Manifest.  The owner
            # action does not invent a cutoff; publication must freeze the
            # exact cutoff atomically when the executable contract is issued.
            predecessor_plan = connection.execute(
                "SELECT inference_contract_version FROM observation_inference_plans "
                "WHERE observation_id=? AND generation=?",
                (observation_id, generation),
            ).fetchone()
            register_inference_plan(
                observation_id=observation_id,
                generation=next_generation,
                inference_contract_version=(
                    str(predecessor_plan[0]) if predecessor_plan else "observation-fixed-look/v1"
                ),
                plan={
                    "source": "owner_action",
                    "owner_action_receipt_id": receipt_id,
                    "parent_generation": generation,
                },
                db=connection,
            )
        successor_event = _append_lifecycle_event(
            connection,
            observation_id=observation_id,
            generation=next_generation,
            from_status="active",
            to_status=str(successor_stage),
            action=action,
            reason=reason,
            actor=actor,
            source_receipt_id=receipt_id,
            dedup_key=f"owner-action-successor:{receipt_id}",
            audit_identity=f"observation-owner:{receipt_id}:successor",
        )
        if paper_successor:
            # A Paper stage without a published Manifest is intentionally not
            # executable yet.  Record that explicit state in the same Lab
            # transaction so an operator/API cannot mistake admission for
            # treatment authority.
            _append_lifecycle_event(
                connection,
                observation_id=observation_id,
                generation=next_generation,
                to_status=str(successor_stage),
                action="manifest_not_published",
                reason="owner_action_pending_manifest",
                actor=actor,
                dedup_key=f"owner-action-manifest-pending:{receipt_id}",
                audit_identity=f"observation-owner:{receipt_id}:manifest-pending",
            )
        return successor_event, next_generation
    terminal_status = {
        "withdraw": "invalidated", "invalidate": "invalidated", "revoke": "invalidated",
        "close": "aborted", "finalize": "finalized",
    }.get(action, "active")
    if action == "adopt":
        if not str(current_stage).startswith("paper_"):
            raise ValueError("owner_action_adopt_requires_paper_stage")
        if not row["manifest_id"] or not row["manifest_hash"]:
            raise ValueError("owner_action_adoption_manifest_required")
        manifest = connection.execute(
            "SELECT observation_id, generation, manifest_hash, effect_mode "
            "FROM execution_manifests WHERE manifest_id=?",
            (row["manifest_id"],),
        ).fetchone()
        if manifest is None or any(
            manifest[field] != expected
            for field, expected in {
                "observation_id": observation_id,
                "generation": generation,
                "manifest_hash": row["manifest_hash"],
            }.items()
        ):
            raise ValueError("owner_action_adoption_manifest_unavailable")
        if str(manifest["effect_mode"]) != "paper_treatment":
            raise ValueError("owner_action_adoption_manifest_not_behavioral")
        # ``observation_windows`` deliberately keeps a compact invalidated
        # root rather than adding a second mutable status vocabulary.  The
        # lifecycle event below records the stronger semantic ``superseded``
        # disposition and preserves the adoption decision immutably.
        terminal_status = "invalidated"
    if action == "finalize":
        # A forced terminal action must not manufacture a finalized result.
        # If no immutable evaluation/result exists yet, preserve the evidence
        # as an aborted generation and leave finalization to the normal kernel
        # path (or an explicit result-bearing action).
        result_exists = connection.execute(
            "SELECT 1 FROM observation_results WHERE observation_id=? AND generation=? LIMIT 1",
            (observation_id, generation),
        ).fetchone()
        if result_exists is None:
            terminal_status = "aborted"
            reason = f"{reason}:finalize_without_result"
    event_id = _append_lifecycle_event(
        connection,
        observation_id=observation_id,
        generation=generation,
        to_status="superseded" if action == "adopt" else terminal_status,
        action=action,
        reason=reason,
        actor=actor,
        source_receipt_id=receipt_id,
        dedup_key=f"owner-action:{receipt_id}",
        audit_identity=f"observation-owner:{receipt_id}",
        from_status="active",
    )
    return event_id, generation


def _safe_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(decoded) if isinstance(decoded, dict) else {}


def _lab_receipt_application_update(
    lab: sqlite3.Connection,
    row: sqlite3.Row,
) -> tuple[str, str, str | None, int | None, str | None, str | None] | None:
    """Recover a committed Lab application so Main acknowledgement can retry.

    Main acknowledgement is intentionally a second transaction after the Lab
    commit.  If that transaction is interrupted, the cursor is already ahead;
    this lookup turns the durable Lab evidence back into the same mutable Main
    status update without importing the receipt twice.
    """
    receipt_id = str(row["receipt_id"])
    kind = str(row["receipt_kind"] or "")
    if kind in {"treatment", "capture"}:
        exposure = lab.execute(
            "SELECT observation_id, generation FROM observation_exposures "
            "WHERE source_receipt_id=?",
            (receipt_id,),
        ).fetchone()
        if exposure is not None:
            return (
                receipt_id, "applied", str(exposure[0]), int(exposure[1]), None, None,
            )
        # Identity-less capture receipts intentionally have no Exposure row;
        # their durable audit payload is still sufficient acknowledgement proof.
        audits = lab.execute(
            "SELECT event_type, payload_json FROM lab_audit_outbox "
            "WHERE event_type IN ('observation_receipt_unclassified', "
            "'observation_receipt_rejected_after_terminalization')",
        ).fetchall()
        for audit in audits:
            payload = _safe_json_object(audit[1])
            if str(payload.get("source_receipt_id") or "") != receipt_id:
                continue
            status = (
                "rejected"
                if audit[0] == "observation_receipt_rejected_after_terminalization"
                or payload.get("status") == "rejected"
                else "applied"
            )
            return (
                receipt_id, status, row["observation_id"], row["generation"], None,
                payload.get("reason"),
            )
        return None
    if kind == "owner_action":
        event = lab.execute(
            "SELECT event_id, observation_id, generation FROM observation_lifecycle_events "
            "WHERE source_receipt_id=? ORDER BY created_at DESC, event_id DESC LIMIT 1",
            (receipt_id,),
        ).fetchone()
        if event is not None:
            return (
                receipt_id, "applied", str(event[1]), int(event[2]), str(event[0]), None,
            )
        audits = lab.execute(
            "SELECT payload_json FROM lab_audit_outbox "
            "WHERE event_type='observation_owner_action_rejected'",
        ).fetchall()
        for audit in audits:
            payload = _safe_json_object(audit[0])
            if str(payload.get("source_receipt_id") or "") == receipt_id:
                return (
                    receipt_id, "conflicted" if "conflict" in str(payload.get("application_error") or "")
                    else "rejected", row["observation_id"], row["generation"], None,
                    str(payload.get("application_error") or "")[:400] or None,
                )
    return None


def import_delivery_receipts(*, limit: int = 100, source_name: str = "main") -> dict[str, Any]:
    """At-least-once import from Main outbox; cursor advances only per success."""
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import transaction

    if not (get_data_dir() / "observation-lab" / "lab.db").is_file():
        return {"available": False, "reason": "lab_unavailable", "processed": 0}
    try:
        main = get_db()
        with lab_transaction(immediate=True) as lab:
            cursor = lab.execute(
                "SELECT contiguous_sequence, retry_sequences_json, source_checkpoint_json "
                "FROM observation_import_cursors WHERE source_name=?",
                (source_name,),
            ).fetchone()
            watermark = int(cursor[0]) if cursor else 0
            retry: list[int] = json.loads(cursor[1]) if cursor else []
            try:
                previous_checkpoint = json.loads(cursor[2]) if cursor and cursor[2] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                previous_checkpoint = {}
            receipt_chain = (
                str(previous_checkpoint.get("receipt_chain_fingerprint"))
                if isinstance(previous_checkpoint, dict)
                and previous_checkpoint.get("receipt_chain_fingerprint")
                else None
            )
            rows = main.execute(
                "SELECT * FROM observation_delivery_outbox WHERE sequence>? ORDER BY sequence LIMIT ?",
                (watermark, max(1, min(int(limit), 1000))),
            ).fetchall()
            # A previous Lab commit may have advanced the cursor while Main's
            # acknowledgement transaction failed.  Rebuild those updates from
            # canonical Lab evidence before reading the next prefix.
            pending_ack_rows = main.execute(
                "SELECT * FROM observation_delivery_outbox "
                "WHERE acknowledged_at IS NULL AND sequence<=? ORDER BY sequence",
                (watermark,),
            ).fetchall()
            processed = 0
            failed: int | None = None
            failed_reason: str | None = None
            application_updates: list[tuple[str, str, str | None, int | None, str | None, str | None]] = []
            for pending_row in pending_ack_rows:
                recovered = _lab_receipt_application_update(lab, pending_row)
                if recovered is not None:
                    application_updates.append(recovered)
            for row in rows:
                try:
                    sequence = int(row["sequence"])
                    # SQLite sequence gaps are not evidence gaps: acknowledged
                    # receipts may have been pruned only after their Lab import,
                    # and a restored source can legitimately begin above the
                    # previous watermark. A present malformed row still stops
                    # the prefix before any later receipt is consumed.
                    kind = str(row["receipt_kind"] or "")
                    missing_identity = not row["observation_id"] or row["generation"] is None
                    if missing_identity and not (
                        kind == "capture"
                        and _capture_receipt_reason_allowed(row["resolution_reason"])
                    ):
                        raise ValueError("unclassified_receipt_identity")
                    if missing_identity:
                        # A blocked/read-only producer can be imported exactly
                        # once without manufacturing an Observation identity or
                        # statistical denominator.  The audit intent is durable
                        # Lab evidence and remains non-authorizing.
                        enqueue_lab_audit(
                            event_type="observation_receipt_unclassified",
                            payload={
                                "source_receipt_id": row["receipt_id"],
                                "sequence": sequence,
                                "receipt_kind": kind,
                                "resolution_reason": row["resolution_reason"],
                                "candidate_key": row["candidate_key"],
                                "consumer": row["consumer"],
                                "consumer_stage": row["consumer_stage"],
                                "receipt": {key: row[key] for key in row.keys()},
                            },
                            dedup_key=f"observation-receipt-capture:{row['receipt_id']}",
                            db=lab,
                        )
                        watermark = sequence
                        application_updates.append(
                            (str(row["receipt_id"]), "applied", None, None, None, None)
                        )
                        retry = [item for item in retry if item != watermark]
                        receipt_chain = _hash({
                            "prior": receipt_chain,
                            "sequence": sequence,
                            "receipt_id": str(row["receipt_id"]),
                        })
                        processed += 1
                        continue
                    if kind in {"treatment", "capture"}:
                        root = lab.execute(
                            "SELECT status FROM observation_windows "
                            "WHERE observation_id=? AND generation=?",
                            (row["observation_id"], row["generation"]),
                        ).fetchone()
                        if root is None:
                            # A present receipt whose generation was never
                            # registered is durable transport evidence, not a
                            # reason to invent an Exposure or block later
                            # sequence numbers forever.  Advance the cursor
                            # with an explicit non-authorizing rejection.
                            enqueue_lab_audit(
                                event_type="observation_receipt_unclassified",
                                payload={
                                    "source_receipt_id": row["receipt_id"],
                                    "observation_id": row["observation_id"],
                                    "generation": row["generation"],
                                    "reason": "observation_generation_root_unavailable",
                                    "status": "rejected",
                                    "receipt": {key: row[key] for key in row.keys()},
                                },
                                dedup_key=f"observation-receipt-root:{row['receipt_id']}",
                                observation_id=row["observation_id"],
                                generation=int(row["generation"]),
                                db=lab,
                            )
                            application_updates.append(
                                (
                                    str(row["receipt_id"]), "rejected",
                                    str(row["observation_id"]), int(row["generation"]),
                                    None, "observation_generation_root_unavailable",
                                )
                            )
                            watermark = sequence
                            retry = [item for item in retry if item != watermark]
                            receipt_chain = _hash({
                                "prior": receipt_chain,
                                "sequence": sequence,
                                "receipt_id": str(row["receipt_id"]),
                            })
                            processed += 1
                            continue
                        if str(root[0]) != "active":
                            # Do not append a late Exposure after a terminal
                            # result was fingerprinted.  Preserve the receipt
                            # as explicit non-authorizing audit evidence.
                            enqueue_lab_audit(
                                event_type="observation_receipt_rejected_after_terminalization",
                                payload={
                                    "source_receipt_id": row["receipt_id"],
                                    "observation_id": row["observation_id"],
                                    "generation": row["generation"],
                                    "terminal_status": str(root[0]),
                                    "reason": "receipt_after_generation_terminalization",
                                    "receipt": {key: row[key] for key in row.keys()},
                                },
                                dedup_key=f"observation-receipt-terminal:{row['receipt_id']}",
                                observation_id=row["observation_id"],
                                generation=int(row["generation"]),
                                db=lab,
                            )
                            application_updates.append(
                                (
                                    str(row["receipt_id"]), "rejected", str(row["observation_id"]),
                                    int(row["generation"]), None,
                                    "receipt_after_generation_terminalization",
                                )
                            )
                            watermark = sequence
                            retry = [item for item in retry if item != watermark]
                            receipt_chain = _hash({
                                "prior": receipt_chain,
                                "sequence": sequence,
                                "receipt_id": str(row["receipt_id"]),
                            })
                            processed += 1
                            continue
                        lab.execute(
                            "INSERT OR IGNORE INTO observation_exposures "
                            "(exposure_id, source_receipt_id, receipt_sequence, observation_id, generation, manifest_id, manifest_hash, "
                            "assignment_unit_key, candidate_key, assignment, applied, behavior_changed, baseline_payload_hash, applied_payload_hash, "
                            "baseline_payload_json, applied_payload_json, outcome_candidate_key, outcome_source, resolution_reason, behavior_contract_hash, "
                            "behavior_policy_epoch_ref, active_manifest_snapshot_json, manifest_set_fingerprint, revocation_snapshot_fingerprint, "
                            "authority_source, authority_epoch, runtime_session_id, producer_commit, exposed_at, imported_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                uuid.uuid4().hex,
                                row["receipt_id"], row["sequence"], row["observation_id"], row["generation"],
                                row["manifest_id"], row["manifest_hash"], row["assignment_unit_key"] or row["candidate_key"] or "unassigned",
                                row["candidate_key"] or "unassigned", row["assignment"], row["applied"], row["behavior_changed"],
                                row["baseline_payload_hash"], row["applied_payload_hash"], row["baseline_payload_json"], row["applied_payload_json"],
                                row["outcome_candidate_key"], row["outcome_source"], row["resolution_reason"], row["behavior_contract_hash"],
                                row["behavior_policy_epoch_ref"], row["active_manifest_snapshot_json"], row["manifest_set_fingerprint"],
                                row["revocation_snapshot_fingerprint"], row["authority_source"],
                                row["authority_epoch"] if "authority_epoch" in row.keys() else None,
                                row["runtime_session_id"], row["producer_commit"],
                                row["applied_at"], _now(),
                            ),
                        )
                        outcome_key = row["outcome_candidate_key"]
                        if outcome_key:
                            # An exposure's exact candidate identity is the
                            # smallest safe retention claim.  The claim is
                            # created in the same Lab transaction, so pruning
                            # cannot observe the exposure without its guard.
                            claim_domain = (
                                "universe_forward_outcomes"
                                if str(row["outcome_source"] or "")
                                in {"universe_forward_outcome", "forward_outcome"}
                                else "filter_candidate_outcomes"
                            )
                            try:
                                upsert_retention_claim(
                                    observation_id=str(row["observation_id"]),
                                    generation=int(row["generation"]),
                                    source_kind="outcome_candidate_key",
                                    source_ref=str(outcome_key),
                                    source_hash=_hash({
                                        "receipt_id": row["receipt_id"],
                                        "sequence": int(row["sequence"]),
                                        "outcome_candidate_key": str(outcome_key),
                                    }),
                                    claim_domain=claim_domain,
                                    derived_from_fingerprint=str(
                                        row["applied_payload_hash"] or row["baseline_payload_hash"]
                                    ),
                                    db=lab,
                                )
                            except ValueError as exc:
                                if str(exc) not in {
                                    "retention_claim_already_released",
                                    "observation_generation_root_unavailable",
                                }:
                                    raise
                        application_updates.append(
                            (
                                str(row["receipt_id"]), "applied", str(row["observation_id"]),
                                int(row["generation"]), None, None,
                            )
                        )
                    elif kind == "owner_action":
                        action = str(row["owner_action"] or "owner_action")
                        event_id, applied_generation = _apply_owner_action(lab, row)
                        # Admission/promotion/hold are lifecycle facts, not
                        # safety denials.  Terminal close/finalize still
                        # withdraw any attached behavior Manifest.
                        if (
                            action in (_SAFETY_ACTIONS | {"close", "finalize"})
                            and row["manifest_id"]
                            and row["manifest_hash"]
                        ):
                            lab.execute(
                                "INSERT OR IGNORE INTO manifest_revocations "
                                "(revocation_id, manifest_id, manifest_hash, observation_id, generation, revoked_at, reason, source_action_id, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (
                                    uuid.uuid4().hex, row["manifest_id"], row["manifest_hash"], row["observation_id"],
                                    row["generation"], row["applied_at"], row["resolution_reason"], row["receipt_id"], _now(),
                                ),
                            )
                            enqueue_lab_audit(
                                event_type="manifest_revoked",
                                payload={
                                    "manifest_id": row["manifest_id"],
                                    "manifest_hash": row["manifest_hash"],
                                    "action": action,
                                    "reason": row["resolution_reason"],
                                    "actor": row["actor"] or "unknown",
                                    "source_receipt_id": row["receipt_id"],
                                },
                                dedup_key=f"observation-owner-revoke:{row['receipt_id']}",
                                observation_id=row["observation_id"],
                                generation=row["generation"],
                                db=lab,
                            )
                        application_updates.append(
                            (
                                str(row["receipt_id"]), "applied", str(row["observation_id"]),
                                applied_generation, event_id, None,
                            )
                        )
                    watermark = sequence
                    receipt_chain = _hash({
                        "prior": receipt_chain,
                        "sequence": sequence,
                        "receipt_id": str(row["receipt_id"]),
                    })
                    retry = [item for item in retry if item != watermark]
                    processed += 1
                except (ValueError, sqlite3.IntegrityError) as exc:
                    # A malformed/stale owner intent is a terminal application
                    # decision, not a transient importer outage.  Preserve the
                    # receipt, advance the cursor, and surface the rejection.
                    if str(row["receipt_kind"] or "") != "owner_action":
                        failed = int(row["sequence"])
                        failed_reason = str(exc)[:400]
                        retry = sorted(set(retry + [failed]))
                        _log.warning("Lab receipt import failed at sequence %s: %s", failed, exc)
                        break
                    status = "conflicted" if any(
                        token in str(exc)
                        for token in ("mismatch", "stale", "conflict", "identity")
                    ) else "rejected"
                    enqueue_lab_audit(
                        event_type="observation_owner_action_rejected",
                        payload={
                            "source_receipt_id": row["receipt_id"],
                            "action": row["owner_action"],
                            "reason": row["resolution_reason"],
                            "application_error": str(exc)[:400],
                            "receipt": {key: row[key] for key in row.keys()},
                        },
                        dedup_key=f"observation-owner-rejected:{row['receipt_id']}",
                        observation_id=row["observation_id"],
                        generation=row["generation"],
                        db=lab,
                    )
                    sequence = int(row["sequence"])
                    watermark = sequence
                    receipt_chain = _hash({
                        "prior": receipt_chain,
                        "sequence": sequence,
                        "receipt_id": str(row["receipt_id"]),
                    })
                    application_updates.append(
                        (
                            str(row["receipt_id"]), status, str(row["observation_id"]),
                            int(row["generation"]), None, str(exc)[:400],
                        )
                    )
                    retry = [item for item in retry if item != watermark]
                    processed += 1
                except Exception as exc:  # retry the failed row before any later row
                    failed = int(row["sequence"])
                    failed_reason = str(exc)[:400]
                    retry = sorted(set(retry + [failed]))
                    _log.warning("Lab receipt import failed at sequence %s: %s", failed, exc)
                    break
            last_receipt_id = (
                str(rows[processed - 1]["receipt_id"])
                if processed and processed <= len(rows)
                else None
            )
            checkpoint = {
                "source_name": source_name,
                "contiguous_sequence": watermark,
                "last_receipt_id": last_receipt_id,
                "retry_sequences": retry,
                "receipt_chain_fingerprint": receipt_chain,
            }
            checkpoint_json = _json(checkpoint)
            checkpoint_fingerprint = _hash(checkpoint)
            lab.execute(
                "INSERT INTO observation_import_cursors (source_name, contiguous_sequence, retry_sequences_json, "
                "source_checkpoint_json, source_checkpoint_fingerprint, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(source_name) DO UPDATE SET contiguous_sequence=excluded.contiguous_sequence, "
                "retry_sequences_json=excluded.retry_sequences_json, source_checkpoint_json=excluded.source_checkpoint_json, "
                "source_checkpoint_fingerprint=excluded.source_checkpoint_fingerprint, updated_at=excluded.updated_at",
                (source_name, watermark, _json(retry), checkpoint_json, checkpoint_fingerprint, _now()),
            )
            lab.execute(
                "INSERT INTO observation_job_health (job_id, status, last_started_at, input_watermark, backlog_count, last_succeeded_at, "
                "last_failed_at, consecutive_failures, last_error, updated_at) "
                "VALUES ('delivery_importer', ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, "
                "last_started_at=excluded.last_started_at, input_watermark=excluded.input_watermark, backlog_count=excluded.backlog_count, "
                "last_succeeded_at=COALESCE(excluded.last_succeeded_at, observation_job_health.last_succeeded_at), "
                "last_failed_at=excluded.last_failed_at, consecutive_failures=excluded.consecutive_failures, "
                "last_error=excluded.last_error, updated_at=excluded.updated_at",
                (
                    "error" if failed is not None else "ok", _now(), watermark, len(rows) - processed,
                    _now() if failed is None and processed else None,
                    _now() if failed is not None else None,
                    1 if failed is not None else 0,
                    failed_reason,
                    _now(),
                ),
            )
            result = {
                "available": True,
                "processed": processed,
                "contiguous_sequence": watermark,
                "failed_sequence": failed,
                "failed_reason": failed_reason,
                "retry_sequences": retry,
                "backlog_count": max(0, len(rows) - processed),
            }
        # Acknowledgement is deliberately after the Lab commit. If this Main
        # update fails, the rows remain pending and the next pass retries the
        # acknowledgement without re-importing the canonical exposure.
        if application_updates:
            with transaction(immediate=True):
                main.executemany(
                    "UPDATE observation_delivery_outbox SET acknowledged_at=?, "
                    "lab_application_status=?, lab_applied_observation_id=?, lab_applied_generation=?, "
                    "lab_applied_event_id=?, lab_application_error=?, lab_applied_at=? "
                    "WHERE receipt_id=? AND acknowledged_at IS NULL",
                    [
                        (
                            _now(), status, observation_id, generation, event_id, error,
                            _now(), receipt_id,
                        )
                        for receipt_id, status, observation_id, generation, event_id, error
                        in application_updates
                    ],
                )
        return result
    except (FileNotFoundError, sqlite3.Error) as exc:
        return {"available": False, "reason": "outbox_unavailable", "error": type(exc).__name__, "processed": 0}


def prune_observation_delivery_outbox(
    *, keep_acknowledged: int = 1000, limit: int = 1000,
    safety_grace_seconds: int = 3600,
) -> dict[str, Any]:
    """Delete only old, acknowledged receipts beyond the bounded audit window."""
    from aletheia.storage.sqlite import get_db
    from aletheia.storage.repositories import transaction

    keep = max(0, int(keep_acknowledged))
    batch = max(1, min(int(limit), 10_000))
    try:
        db = get_db()
        rows = db.execute(
            "SELECT sequence, receipt_id, acknowledged_at FROM observation_delivery_outbox "
            "WHERE acknowledged_at IS NOT NULL ORDER BY sequence DESC",
        ).fetchall()
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=max(0, int(safety_grace_seconds))
        )
        sequences: list[int] = []
        for row in rows[keep:]:
            try:
                acknowledged_at = datetime.fromisoformat(
                    str(row[2]).replace("Z", "+00:00")
                )
                if acknowledged_at.tzinfo is None or acknowledged_at > cutoff:
                    continue
            except (TypeError, ValueError, OSError):
                # Malformed acknowledgement timestamps are not deletion proof.
                continue
            sequences.append(int(row[0]))
            if len(sequences) >= batch:
                break
        authority = get_authority_source(db=db)
        replay_proof = None
        if sequences and authority.source == "lab":
            # After cutover, acknowledgement alone is not a replay proof.  The
            # Lab cursor must advertise a verified source checkpoint covering
            # every row we are about to remove; an unavailable/malformed proof
            # leaves the Main transport intact.
            with readonly_lab_db(allow_missing=True) as lab:
                if lab is None:
                    return {
                        "available": False,
                        "reason": "replay_proof_unavailable",
                        "deleted": 0,
                        "authority_source": authority.source,
                    }
                cursor = lab.execute(
                    "SELECT contiguous_sequence, retry_sequences_json, source_checkpoint_json, "
                    "source_checkpoint_fingerprint FROM observation_import_cursors WHERE source_name='main'"
                ).fetchone()
                if cursor is None:
                    return {
                        "available": False,
                        "reason": "replay_proof_unavailable",
                        "deleted": 0,
                        "authority_source": authority.source,
                    }
                try:
                    checkpoint = json.loads(cursor[2]) if cursor[2] else None
                    retry = json.loads(cursor[1] or "[]")
                    valid_checkpoint = (
                        isinstance(checkpoint, dict)
                        and checkpoint.get("source_name") == "main"
                        and isinstance(retry, list)
                        and isinstance(cursor[3], str)
                        and _hash(checkpoint) == cursor[3]
                        and isinstance(checkpoint.get("receipt_chain_fingerprint"), str)
                        and len(checkpoint["receipt_chain_fingerprint"]) == 64
                        and int(checkpoint.get("contiguous_sequence", -1)) >= int(cursor[0])
                        and not any(int(item) in sequences for item in retry)
                        and all(int(sequence) <= int(checkpoint["contiguous_sequence"]) for sequence in sequences)
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    valid_checkpoint = False
                if not valid_checkpoint:
                    return {
                        "available": False,
                        "reason": "replay_proof_unavailable",
                        "deleted": 0,
                        "authority_source": authority.source,
                    }
                # Check the exact source identities in canonical Lab evidence,
                # including compact archives after hot-row eviction.  A cursor
                # hash by itself cannot prove that the rows being deleted were
                # actually imported.
                receipt_ids = {str(row[1]) for row in rows if int(row[0]) in sequences}
                covered_receipts = {
                    str(row[0])
                    for row in lab.execute(
                        "SELECT source_receipt_id FROM observation_exposures "
                        "WHERE source_receipt_id IS NOT NULL"
                    ).fetchall()
                }
                covered_receipts.update(
                    str(row[0])
                    for row in lab.execute(
                        "SELECT source_receipt_id FROM observation_lifecycle_events "
                        "WHERE source_receipt_id IS NOT NULL"
                    ).fetchall()
                )
                for audit in lab.execute(
                    "SELECT payload_json FROM lab_audit_outbox"
                ).fetchall():
                    try:
                        payload = json.loads(audit[0] or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if isinstance(payload, dict) and payload.get("source_receipt_id"):
                        covered_receipts.add(str(payload["source_receipt_id"]))
                import gzip

                for archive in lab.execute(
                    "SELECT archive_path, archive_sha256, replay_fingerprint "
                    "FROM observation_archives"
                ).fetchall():
                    try:
                        data = _resolve_managed_archive_path(str(archive[0])).read_bytes()
                        if hashlib.sha256(data).hexdigest() != str(archive[1]):
                            continue
                        payload = json.loads(gzip.decompress(data))
                        replay = payload.get("replay_fingerprint")
                        candidate = dict(payload)
                        candidate.pop("replay_fingerprint", None)
                        if replay != str(archive[2]) or replay != _hash(candidate):
                            continue
                        archive_rows = payload.get("rows") or {}
                        for key in ("exposures", "lifecycle_events"):
                            for item in archive_rows.get(key) or ():
                                if isinstance(item, dict) and item.get("source_receipt_id"):
                                    covered_receipts.add(str(item["source_receipt_id"]))
                    except (OSError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
                        continue
                if not receipt_ids.issubset(covered_receipts):
                    return {
                        "available": False,
                        "reason": "replay_proof_unavailable",
                        "deleted": 0,
                        "authority_source": authority.source,
                    }
                replay_proof = str(cursor[3])
        if not sequences:
            return {
                "available": True,
                "deleted": 0,
                "kept_acknowledged": keep,
                "safety_grace_seconds": max(0, int(safety_grace_seconds)),
                "authority_source": authority.source,
            }
        with transaction(immediate=True):
            db.executemany(
                "DELETE FROM observation_delivery_outbox WHERE sequence=? "
                "AND acknowledged_at IS NOT NULL",
                [(sequence,) for sequence in sequences],
            )
        return {
            "available": True,
            "deleted": len(sequences),
            "kept_acknowledged": keep,
            "safety_grace_seconds": max(0, int(safety_grace_seconds)),
            "authority_source": authority.source,
            "replay_proof_fingerprint": replay_proof,
        }
    except (FileNotFoundError, sqlite3.Error) as exc:
        return {
            "available": False,
            "reason": "outbox_unavailable",
            "error": type(exc).__name__,
            "deleted": 0,
        }


def mirror_lab_audit_outbox(*, limit: int = 100) -> dict[str, Any]:
    """Mirror durable Lab audit intents into Main's hash-chain audit log.

    The Lab row is acknowledged only after ``write_audits_deduplicated``
    reports that the stable audit identity is present.  A failed mirror stays
    retryable and never turns a missing audit record into a successful state.
    """
    from aletheia.files.jsonl import write_audits_deduplicated

    if not (get_data_dir() / "observation-lab" / "lab.db").is_file():
        return {"available": False, "reason": "lab_unavailable", "processed": 0}
    try:
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                return {"available": False, "reason": "lab_unavailable", "processed": 0}
            rows = lab.execute(
                "SELECT * FROM lab_audit_outbox WHERE status IN ('pending','failed') "
                "ORDER BY created_at, audit_id LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
            events: list[dict[str, Any]] = []
            invalid: dict[str, str] = {}
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                    if not isinstance(payload, dict):
                        raise ValueError("audit_payload_must_be_object")
                    event: dict[str, Any] = {
                        "id": row["audit_id"],
                        "timestamp": row["created_at"],
                        "actor": "observation_lab",
                        "source": "observation_lab",
                        "event_type": row["event_type"],
                        "entity_type": "observation_generation",
                        "entity_id": (
                            f"{row['observation_id']}:{row['generation']}"
                            if row["observation_id"] is not None
                            else row["audit_id"]
                        ),
                        "after": payload,
                    }
                    events.append(event)
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    invalid[str(row["audit_id"])] = str(exc)
        mirrored = write_audits_deduplicated(events)
        now = _now()
        with lab_transaction(immediate=True) as lab:
            for row in rows:
                audit_id = str(row["audit_id"])
                if audit_id in invalid:
                    lab.execute(
                        "UPDATE lab_audit_outbox SET status='failed', attempts=attempts+1, "
                        "last_error=?, updated_at=? WHERE audit_id=?",
                        (invalid[audit_id], now, audit_id),
                    )
                elif audit_id in mirrored:
                    lab.execute(
                        "UPDATE lab_audit_outbox SET status='mirrored', attempts=attempts+1, "
                        "last_error=NULL, mirrored_at=?, updated_at=? WHERE audit_id=?",
                        (now, now, audit_id),
                    )
                else:
                    lab.execute(
                        "UPDATE lab_audit_outbox SET status='failed', attempts=attempts+1, "
                        "last_error=?, updated_at=? WHERE audit_id=?",
                        ("audit_chain_write_unavailable", now, audit_id),
                    )
        return {
            "available": True,
            "processed": len(mirrored),
            "mirrored": sorted(mirrored),
            "failed": sorted(set(invalid) | ({str(row["audit_id"]) for row in rows} - mirrored - set(invalid))),
        }
    except (FileNotFoundError, OSError, sqlite3.Error) as exc:
        return {"available": False, "reason": "audit_mirror_unavailable", "error": type(exc).__name__, "processed": 0}


def observation_bridge_health() -> dict[str, Any]:
    """Operational facts for ``observe overview``; missing state stays unavailable."""
    from aletheia.storage.sqlite import get_db

    try:
        db = get_db()
        data_root = get_data_dir() / "observation-lab"
        lab_path = data_root / "lab.db"
        lab_wal_path = data_root / "lab.db-wal"
        lab_files = {
            "path": str(lab_path),
            "status": "present" if lab_path.is_file() else "missing",
            "bytes": lab_path.stat().st_size if lab_path.is_file() else None,
            "wal_bytes": lab_wal_path.stat().st_size if lab_wal_path.is_file() else None,
        }
        try:
            app_path = get_data_dir() / "app.db"
            app_files = {
                "path": str(app_path),
                "status": "present" if app_path.is_file() else "missing",
                "bytes": app_path.stat().st_size if app_path.is_file() else None,
                "wal_bytes": (
                    app_path.with_name(app_path.name + "-wal").stat().st_size
                    if app_path.with_name(app_path.name + "-wal").is_file()
                    else None
                ),
            }
        except OSError:
            app_files = {"path": str(get_data_dir() / "app.db"), "status": "unavailable"}
        try:
            pending = db.execute(
                "SELECT COUNT(*) FILTER (WHERE acknowledged_at IS NULL), "
                "MIN(applied_at) FILTER (WHERE acknowledged_at IS NULL), MAX(sequence) "
                "FROM observation_delivery_outbox"
            ).fetchone()
        except sqlite3.OperationalError:
            pending = db.execute(
                "SELECT COUNT(*), MIN(applied_at), MAX(sequence) FROM observation_delivery_outbox"
            ).fetchone()
        fallback_rows = db.execute(
            "SELECT resolution_reason, COUNT(*) AS count FROM observation_delivery_outbox "
            "WHERE applied=0 GROUP BY resolution_reason ORDER BY resolution_reason"
        ).fetchall()
        try:
            application_rows = db.execute(
                "SELECT COALESCE(lab_application_status, 'pending'), COUNT(*) FROM "
                "observation_delivery_outbox GROUP BY COALESCE(lab_application_status, 'pending')"
            ).fetchall()
            application_counts = {str(row[0]): int(row[1]) for row in application_rows}
        except sqlite3.OperationalError:
            application_counts = None
        authority = get_authority_source(db=db)
        with readonly_lab_db(allow_missing=True) as lab:
            if lab is None:
                return {
                    "available": False,
                    "reason": "lab_unavailable",
                    "authority_source": authority.source,
                    "outbox_pending_count": int(pending[0] or 0),
                    "outbox_oldest_at": pending[1],
                    "outbox_high_watermark": int(pending[2] or 0),
                    "fallback_counts": {str(row[0]): int(row[1]) for row in fallback_rows},
                    "outbox_application_counts": application_counts,
                    "active_retention_claim_count": None,
                    "active_retention_claims_by_domain": None,
                    "storage": {"app_db": app_files, "lab_db": lab_files},
                }
            cursor = lab.execute(
                "SELECT contiguous_sequence, retry_sequences_json, source_checkpoint_json "
                "FROM observation_import_cursors WHERE source_name='main'"
            ).fetchone()
            health = lab.execute(
                "SELECT status, input_watermark, backlog_count, last_started_at, last_succeeded_at, "
                "last_failed_at, consecutive_failures, last_error FROM observation_job_health "
                "WHERE job_id='delivery_importer'"
            ).fetchone()
            manifest_rows = lab.execute(
                "SELECT manifest_hash, manifest_contract_version, effect_mode, created_at, "
                "treatment_adapter_id, treatment_adapter_version, effective_from, "
                "effective_until, observation_windows.status "
                "FROM execution_manifests LEFT JOIN observation_windows "
                "ON observation_windows.observation_id=execution_manifests.observation_id "
                "AND observation_windows.generation=execution_manifests.generation "
                "ORDER BY execution_manifests.created_at DESC"
            ).fetchall()
            revocation_rows = lab.execute(
                "SELECT manifest_hash, revoked_at, reason FROM manifest_revocations "
                "ORDER BY manifest_hash, revoked_at"
            ).fetchall()
            snapshot_now = datetime.now(timezone.utc)
            active_rows = []
            for row in manifest_rows:
                start = datetime.fromisoformat(str(row[6]).replace("Z", "+00:00"))
                end = (
                    datetime.fromisoformat(str(row[7]).replace("Z", "+00:00"))
                    if row[7] is not None else None
                )
                if (
                    start.tzinfo is None
                    or start.utcoffset() != timedelta(0)
                    or (end is not None and (end.tzinfo is None or end.utcoffset() != timedelta(0)))
                    or (row[8] is not None and str(row[8]) != "active")
                ):
                    raise ValueError("manifest_interval_timezone_unavailable")
                if start <= snapshot_now and (end is None or end > snapshot_now):
                    active_rows.append(row)
            effective_revocations = []
            revoked_hashes = set()
            for row in revocation_rows:
                revoked_at = datetime.fromisoformat(str(row[1]).replace("Z", "+00:00"))
                if revoked_at.tzinfo is None or revoked_at.utcoffset() != timedelta(0):
                    raise ValueError("manifest_revocation_timezone_unavailable")
                if revoked_at <= snapshot_now:
                    effective_revocations.append(row)
                    revoked_hashes.add(str(row[0]))
            active_rows = [row for row in active_rows if str(row[0]) not in revoked_hashes]
            manifest_snapshot = _hash({
                "manifests": [tuple(row) for row in active_rows],
                "revocations": [tuple(row) for row in effective_revocations],
            })
            latest_manifest_at = active_rows[0][3] if active_rows else None
            claims = lab.execute(
                "SELECT COUNT(*) FROM observation_retention_claims WHERE status='active'"
            ).fetchone()[0]
            claim_rows = lab.execute(
                "SELECT claim_domain, COUNT(*) FROM observation_retention_claims "
                "WHERE status='active' GROUP BY claim_domain ORDER BY claim_domain"
            ).fetchall()
            claims_by_domain = {
                str(row[0]): int(row[1]) for row in claim_rows
            }
            archives = lab.execute("SELECT COUNT(*) FROM observation_archives").fetchone()[0]
            results = lab.execute("SELECT COUNT(*) FROM observation_results").fetchone()[0]
            imported_exposures = lab.execute(
                "SELECT COUNT(*) FROM observation_exposures"
            ).fetchone()[0]
            archive_backlog = lab.execute(
                "SELECT COUNT(*) FROM observation_windows WHERE status IN ('finalized','invalidated','aborted') "
                "AND NOT EXISTS (SELECT 1 FROM observation_archives a "
                "WHERE a.observation_id=observation_windows.observation_id "
                "AND a.generation=observation_windows.generation)"
            ).fetchone()[0]
            hot_rows = {
                table: int(lab.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
                for table in (
                    "observation_units", "observation_exposures",
                    "observation_evaluations", "observation_inference_plans",
                )
            }
            jobs = {
                str(row["job_id"]): dict(row)
                for row in lab.execute(
                    "SELECT job_id, status, input_watermark, backlog_count, last_started_at, "
                    "last_succeeded_at, last_failed_at, consecutive_failures, last_error "
                    "FROM observation_job_health ORDER BY job_id"
                ).fetchall()
            }
            try:
                latest_manifest_age = max(
                    0.0,
                    datetime.now(timezone.utc).timestamp()
                    - datetime.fromisoformat(str(latest_manifest_at).replace("Z", "+00:00")).timestamp(),
                ) if latest_manifest_at else None
            except (TypeError, ValueError, OSError):
                latest_manifest_age = None
            retry_sequences = json.loads(cursor[1]) if cursor else None
            proven_missing = None
            if cursor and cursor[2]:
                try:
                    checkpoint = json.loads(cursor[2])
                except (TypeError, ValueError, json.JSONDecodeError):
                    checkpoint = None
                candidate_missing = (
                    checkpoint.get("proven_missing_committed_receipts")
                    if isinstance(checkpoint, dict) else None
                )
                if isinstance(candidate_missing, list) and all(
                    isinstance(item, int) and item >= 0 for item in candidate_missing
                ):
                    proven_missing = candidate_missing
            return {
                "available": True,
                "authority_source": authority.source,
                "authority_epoch": authority.epoch,
                "storage": {"app_db": app_files, "lab_db": lab_files},
                "outbox_pending_count": int(pending[0] or 0),
                "outbox_oldest_at": pending[1],
                "outbox_high_watermark": int(pending[2] or 0),
                "fallback_counts": {str(row[0]): int(row[1]) for row in fallback_rows},
                "outbox_application_counts": application_counts,
                "lab_imported_watermark": int(cursor[0]) if cursor else None,
                "lab_import_retry_sequences": retry_sequences,
                "proven_missing_committed_receipts": proven_missing,
                "imported_exposure_count": int(imported_exposures or 0),
                "importer_health": dict(health) if health else None,
                "job_health": jobs,
                "manifest_snapshot_fingerprint": manifest_snapshot,
                "latest_manifest_at": latest_manifest_at,
                "latest_manifest_age_seconds": latest_manifest_age,
                "manifest_contracts": sorted({str(row[1]) for row in active_rows}),
                "manifest_effect_modes": sorted({str(row[2]) for row in active_rows}),
                "manifest_adapter_versions": sorted({
                    f"{row[4]}:{row[5]}" for row in active_rows
                }),
                "manifest_contract_compatibility": {
                    "supported_versions": sorted(_SUPPORTED_MANIFEST_VERSIONS),
                    "active_versions": sorted({str(row[1]) for row in active_rows}),
                    "unsupported_versions": sorted({
                        str(row[1]) for row in active_rows
                        if str(row[1]) not in _SUPPORTED_MANIFEST_VERSIONS
                    }),
                    "compatible": all(
                        str(row[1]) in _SUPPORTED_MANIFEST_VERSIONS
                        and _adapter(str(row[4]), str(row[5])) is not None
                        for row in active_rows
                    ),
                },
                "active_manifest_count": len(active_rows),
                "active_revocation_count": len(effective_revocations),
                "active_retention_claim_count": int(claims or 0),
                "active_retention_claims_by_domain": claims_by_domain,
                "archive_count": int(archives or 0),
                "result_count": int(results or 0),
                "archive_backlog_count": int(archive_backlog or 0),
                "hot_rows": hot_rows,
                "hot_capacity_budget": {
                    "retention_days": 30,
                    "max_generations_per_compaction": 10,
                    "max_rows": 100_000,
                },
                "hot_capacity_status": (
                    "over_budget"
                    if sum(hot_rows.values()) > 100_000
                    else "within_budget"
                ),
            }
    except (FileNotFoundError, sqlite3.Error, RuntimeError, TypeError, ValueError) as exc:
        return {"available": False, "reason": "bridge_health_unavailable", "error": type(exc).__name__}


__all__ = [
    "AuthoritySource",
    "NumericTreatmentAdapter",
    "ObservationTreatmentDecision",
    "TreatmentAdapter",
    "append_delivery_receipt",
    "apply_evaluation_safety_disposition",
    "append_lifecycle_event",
    "build_authority_cutover_proof",
    "compact_finalized_generations",
    "consume_inference_plan",
    "enqueue_lab_audit",
    "freeze_inference_plan",
    "get_authority_source",
    "import_delivery_receipts",
    "prune_observation_delivery_outbox",
    "mirror_lab_audit_outbox",
    "observation_bridge_health",
    "record_archive",
    "record_authority_cutover_proof",
    "record_generation_contract",
    "record_observation_evaluation",
    "record_observation_nomination",
    "record_observation_result",
    "register_observation_spec",
    "record_owner_action",
    "register_treatment_adapter",
    "register_inference_plan",
    "registered_treatment_adapters",
    "publish_manifest_with_lifecycle",
    "release_retention_claim",
    "revoke_manifest",
    "resolve_observation_treatment",
    "set_authority_source",
    "latest_observation_result",
    "upsert_retention_claim",
    "verify_generation_archive",
    "write_generation_archive",
]

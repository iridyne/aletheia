"""Canonical Observation Lab evidence and atomic lifecycle writes."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Mapping

from aletheia.storage.time_utils import utcnow_iso_us as _now
from extensions.observation_lab.canonical_adapters import (
    _adapter,
    _hash,
    _json,
    _safe_json_object,
    _text,
    _utc_text,
)
from extensions.observation_lab.db import lab_transaction
from extensions.observation_lab.manifest import ExecutionManifest

__all__ = [
    "_append_lifecycle_event",
    "_lab_write",
    "_normalise_source_cutoff",
    "_require_generation_root",
    "append_lifecycle_event",
    "apply_evaluation_safety_disposition",
    "consume_inference_plan",
    "enqueue_lab_audit",
    "freeze_inference_plan",
    "latest_observation_result",
    "publish_manifest_with_lifecycle",
    "record_generation_contract",
    "record_observation_evaluation",
    "record_observation_nomination",
    "record_observation_result",
    "register_inference_plan",
    "register_observation_spec",
    "revoke_manifest",
]

_log = logging.getLogger("aletheia.observation_lab.canonical")


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
    from extensions.observation_lab.db import readonly_lab_db

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
    from extensions.counterfactual.filter_experiments import _ADAPTER_CAUSAL_IDENTITY

    adapter_identity = _ADAPTER_CAUSAL_IDENTITY.get(str(value["filter_name"]))
    if adapter_identity is not None:
        value["adapter_identity_hash"] = _hash(adapter_identity)
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
            from extensions.learning.registry import (
                get_learning_path,
            )

            path = get_learning_path(path_id)
            default_outcome_contract = path.outcome_contract_version
            default_metric = path.metric_version
        except KeyError:
            _log.warning("observation_lab: learning-path lookup failed for path_id; falling back to default outcome contract and metric", exc_info=True)
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
    from extensions.observation_lab.manifest import publish_manifest

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

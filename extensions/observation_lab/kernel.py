"""Reusable prospective Observation window lifecycle (Issue #2614)."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from extensions.observation_lab.db import get_lab_db, lab_db_path, lab_transaction


@dataclass(frozen=True)
class ObservationProgress:
    summary: dict[str, Any]
    can_finalize: bool = False
    invalidation_reason: str | None = None
    units: tuple[dict[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ObservationHandler:
    kind: str
    version: str
    normalize_spec: Callable[[dict[str, Any]], dict[str, Any]]
    capture: Callable[[dict[str, Any], dict[str, Any]], ObservationProgress] | None
    advance: Callable[[dict[str, Any]], ObservationProgress]
    finalize: Callable[[dict[str, Any]], dict[str, Any]]
    retention_claims: Callable[[dict[str, Any]], tuple[dict[str, Any], ...]]


_HANDLERS: dict[tuple[str, str], ObservationHandler] = {}
_VALID_AUTHORITY = frozenset({"observe_only", "external_authority"})
_VALID_ADMISSION = frozenset({"admitted", "unavailable", "excluded"})

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _utc(value: str | datetime | None = None) -> datetime:
    if value is None:
        return _now()
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_utc_time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive_utc_time")
    return parsed.astimezone(timezone.utc)


def register_handler(handler: ObservationHandler, *, replace: bool = False) -> None:
    key = (handler.kind, handler.version)
    if key in _HANDLERS and not replace:
        raise ValueError(f"observation_handler_already_registered:{handler.kind}:{handler.version}")
    _HANDLERS[key] = handler


def registered_handlers() -> tuple[tuple[str, str], ...]:
    return tuple(sorted(_HANDLERS))


def _handler(kind: str, version: str) -> ObservationHandler | None:
    return _HANDLERS.get((kind, version))


def _row(
    observation_id: str,
    generation: int,
    *,
    db=None,
) -> dict[str, Any]:
    result = (db or get_lab_db()).execute(
        "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
        (observation_id, int(generation)),
    ).fetchone()
    if result is None:
        raise KeyError(f"{observation_id}:{generation}")
    return dict(result)


def active_window(
    *, slot_key: str | None = None,
    kind: str | None = None,
    db=None,
) -> dict[str, Any] | None:
    clauses = ["status='active'"]
    params: list[Any] = []
    if slot_key is not None:
        clauses.append("slot_key=?")
        params.append(slot_key)
    if kind is not None:
        clauses.append("kind=?")
        params.append(kind)
    result = (db or get_lab_db()).execute(
        f"SELECT * FROM observation_windows WHERE {' AND '.join(clauses)} "
        "ORDER BY started_at DESC, observation_id, generation DESC LIMIT 1",
        params,
    ).fetchone()
    return dict(result) if result else None


def start_window(
    *,
    observation_id: str,
    kind: str,
    handler_version: str,
    slot_key: str,
    authority_mode: str,
    spec: dict[str, Any],
    started_at: str | datetime,
    collect_until: str | datetime,
    finalize_not_before: str | datetime,
    producer_commit: str | None = None,
    new_generation: bool = False,
) -> dict[str, Any]:
    # producer_commit is provenance only; it is not part of spec_hash.
    handler = _handler(kind, handler_version)
    if handler is None:
        raise ValueError(f"handler_unavailable:{kind}:{handler_version}")
    if authority_mode not in _VALID_AUTHORITY:
        raise ValueError("invalid_authority_mode")
    started = _utc(started_at)
    collect_end = _utc(collect_until)
    finalize_at = _utc(finalize_not_before)
    if not started < collect_end or finalize_at < collect_end:
        raise ValueError("invalid_observation_interval")
    normalized = handler.normalize_spec(dict(spec))
    if not isinstance(normalized, dict):
        raise TypeError("normalized_observation_spec_must_be_dict")
    spec_json = _json(normalized)
    spec_hash = hashlib.sha256(spec_json.encode("utf-8")).hexdigest()
    now = _utc().isoformat()

    with lab_transaction(immediate=True) as db:
        active = db.execute(
            "SELECT * FROM observation_windows WHERE slot_key=? AND status='active'",
            (slot_key,),
        ).fetchone()
        if active is not None:
            current = dict(active)
            same = (
                current["kind"] == kind
                and current["handler_version"] == handler_version
                and current["authority_mode"] == authority_mode
                and current["spec_hash"] == spec_hash
            )
            if same and not new_generation:
                return current
            next_generation = int(
                db.execute(
                    "SELECT COALESCE(MAX(generation), 0) + 1 FROM observation_windows "
                    "WHERE slot_key=?",
                    (slot_key,),
                ).fetchone()[0]
            )
            db.execute(
                "UPDATE observation_windows SET status='invalidated', terminal_at=?, "
                "terminal_reason=? WHERE observation_id=? AND generation=? AND status='active'",
                (
                    now,
                    f"superseded_by_generation:{next_generation}",
                    current["observation_id"],
                    current["generation"],
                ),
            )
            from extensions.observation_lab.canonical import _append_lifecycle_event

            _append_lifecycle_event(
                db,
                observation_id=str(current["observation_id"]),
                generation=int(current["generation"]),
                to_status="invalidated",
                action="supersede",
                reason=f"superseded_by_generation:{next_generation}",
                actor="observation_kernel",
                dedup_key=(
                    f"observation-supersede:{current['observation_id']}:{current['generation']}:{next_generation}"
                ),
                from_status="active",
            )
        else:
            next_generation = int(
                db.execute(
                    "SELECT COALESCE(MAX(generation), 0) + 1 FROM observation_windows "
                    "WHERE slot_key=?",
                    (slot_key,),
                ).fetchone()[0]
            )
        db.execute(
            "INSERT INTO observation_windows (observation_id,kind,handler_version,generation,"
            "slot_key,authority_mode,status,spec_json,spec_hash,producer_commit,started_at,collect_until,"
            "finalize_not_before) VALUES (?,?,?,?,?,?,'active',?,?,?,?,?,?)",
            (
                observation_id,
                kind,
                handler_version,
                next_generation,
                slot_key,
                authority_mode,
                spec_json,
                spec_hash,
                producer_commit,
                started.isoformat(),
                collect_end.isoformat(),
                finalize_at.isoformat(),
            ),
        )
        from extensions.observation_lab.canonical import _append_lifecycle_event

        _append_lifecycle_event(
            db,
            observation_id=observation_id,
            generation=next_generation,
            to_status="shadow" if authority_mode == "observe_only" else "external_authority",
            action="register",
            reason="observation_registered",
            actor="observation_kernel",
            dedup_key=f"observation-register:{observation_id}:{next_generation}:{spec_hash}",
        )
    return _row(observation_id, next_generation)


def _phase(window: Mapping[str, Any], now: datetime) -> str:
    status = str(window["status"])
    if status != "active":
        return status
    if _handler(str(window["kind"]), str(window["handler_version"])) is None:
        return "handler_unavailable"
    try:
        collect_end = _utc(str(window["collect_until"]))
        finalize_at = _utc(str(window["finalize_not_before"]))
    except ValueError:
        return "time_unavailable"
    if now < collect_end:
        return "collecting"
    if now < finalize_at:
        return "settlement_grace"
    return "ready_to_finalize"


def status_window(
    observation_id: str | None = None,
    generation: int | None = None,
    *,
    slot_key: str | None = None,
    now: str | datetime | None = None,
    db=None,
) -> dict[str, Any]:
    if observation_id is not None and generation is not None:
        window = _row(observation_id, generation, db=db)
    elif slot_key is not None:
        window = active_window(slot_key=slot_key, db=db)
        if window is None:
            raise KeyError(slot_key)
    elif observation_id is not None:
        result = (db or get_lab_db()).execute(
            "SELECT * FROM observation_windows WHERE observation_id=? "
            "ORDER BY generation DESC LIMIT 1",
            (observation_id,),
        ).fetchone()
        if result is None:
            raise KeyError(observation_id)
        window = dict(result)
    else:
        raise ValueError("observation_id_or_slot_key_required")
    counts = Counter(
        str(row[0])
        for row in (db or get_lab_db()).execute(
            "SELECT admission FROM observation_units WHERE observation_id=? AND generation=?",
            (window["observation_id"], window["generation"]),
        ).fetchall()
    )
    window["phase"] = _phase(window, _utc(now))
    window["unit_counts"] = dict(sorted(counts.items()))
    window["latest_summary"] = (
        json.loads(window["last_summary_json"]) if window.get("last_summary_json") else None
    )
    window["handler_available"] = (
        _handler(str(window["kind"]), str(window["handler_version"])) is not None
    )
    return window


def capture_event(
    observation_id: str,
    generation: int,
    spec_hash: str,
    event: dict[str, Any],
    *,
    producer_commit: str | None = None,
    producer_runtime_session_id: str | None = None,
) -> dict[str, Any]:
    initial = _row(observation_id, generation)
    handler = _handler(str(initial["kind"]), str(initial["handler_version"]))
    if handler is None:
        raise ValueError(f"handler_unavailable:{initial['kind']}:{initial['handler_version']}")
    if handler.capture is None:
        raise ValueError("handler_capture_unavailable")
    progress = handler.capture(initial, dict(event))
    if not isinstance(progress, ObservationProgress):
        raise TypeError("handler_capture_must_return_observation_progress")
    captured_at = _utc().isoformat()
    inserted = 0
    idempotent = 0

    with lab_transaction(immediate=True) as db:
        current_row = db.execute(
            "SELECT * FROM observation_windows WHERE observation_id=? AND generation=?",
            (observation_id, int(generation)),
        ).fetchone()
        if current_row is None:
            raise KeyError(f"{observation_id}:{generation}")
        current = dict(current_row)
        if current["status"] != "active":
            raise ValueError(f"window_{current['status']}")
        if current["spec_hash"] != spec_hash:
            raise ValueError("observation_spec_hash_mismatch")
        started = _utc(str(current["started_at"]))
        collect_end = _utc(str(current["collect_until"]))
        for raw in progress.units:
            unit = dict(raw)
            if not unit.get("known_at"):
                raise ValueError("observation_unit_known_at_required")
            known_at = _utc(unit["known_at"])
            if not started <= known_at < collect_end:
                raise ValueError("unit_outside_half_open_interval")
            admission = str(unit.get("admission") or "")
            if admission not in _VALID_ADMISSION:
                raise ValueError("invalid_unit_admission")
            unit_key = str(unit.get("unit_key") or "")
            source_kind = str(unit.get("source_kind") or "")
            source_ref = str(unit.get("source_ref") or "")
            source_hash = str(unit.get("source_hash") or "")
            if not all((unit_key, source_kind, source_ref, source_hash)):
                raise ValueError("incomplete_observation_unit_identity")
            identity = (
                spec_hash,
                unit_key,
                source_kind,
                source_ref,
                source_hash,
                known_at.isoformat(),
                admission,
                unit.get("reason"),
            )
            unit_id = _hash({
                "observation_id": observation_id,
                "generation": int(generation),
                "unit_key": unit_key,
            })
            existing = db.execute(
                "SELECT * FROM observation_units WHERE unit_id=?",
                (unit_id,),
            ).fetchone()
            if existing is not None:
                stored = dict(existing)
                stored_identity = tuple(
                    stored[key]
                    for key in (
                        "spec_hash", "unit_key", "source_kind", "source_ref",
                        "source_hash", "known_at", "admission", "reason",
                    )
                )
                if stored_identity != identity:
                    raise ValueError("unit_identity_conflict")
                idempotent += 1
                continue
            db.execute(
                "INSERT INTO observation_units (unit_id,observation_id,generation,spec_hash,"
                "unit_key,source_kind,source_ref,source_hash,known_at,captured_at,producer_commit,"
                "producer_runtime_session_id,admission,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    unit_id,
                    observation_id,
                    int(generation),
                    spec_hash,
                    unit_key,
                    source_kind,
                    source_ref,
                    source_hash,
                    known_at.isoformat(),
                    captured_at,
                    producer_commit,
                    producer_runtime_session_id,
                    admission,
                    unit.get("reason"),
                ),
            )
            inserted += 1
        db.execute(
            "UPDATE observation_windows SET last_summary_json=?, last_error=NULL "
            "WHERE observation_id=? AND generation=? AND status='active'",
            (_json(progress.summary), observation_id, int(generation)),
        )
    return {
        "accepted": True,
        "inserted": inserted,
        "idempotent": idempotent,
        "summary": progress.summary,
    }


def _terminalize(observation_id: str, generation: int, status: str, reason: str) -> bool:
    reason = str(reason).strip()
    if status not in {"aborted", "invalidated"} or not reason:
        raise ValueError("invalid_terminal_transition")
    with lab_transaction(immediate=True) as db:
        result = db.execute(
            "UPDATE observation_windows SET status=?, terminal_at=?, terminal_reason=? "
            "WHERE observation_id=? AND generation=? AND status='active'",
            (status, _utc().isoformat(), reason, observation_id, int(generation)),
        )
        if result.rowcount:
            from extensions.observation_lab.canonical import _append_lifecycle_event

            _append_lifecycle_event(
                db,
                observation_id=observation_id,
                generation=int(generation),
                to_status=status,
                action=status,
                reason=reason,
                actor="observation_kernel",
                dedup_key=f"observation-{status}:{observation_id}:{generation}:{reason}",
                from_status="active",
            )
    return bool(result.rowcount)


def abort_window(observation_id: str, generation: int, *, reason: str) -> dict[str, Any]:
    from extensions.observation_lab.worker_lock import worker_cycle_exclusion

    with worker_cycle_exclusion():
        if not _terminalize(observation_id, generation, "aborted", reason):
            raise ValueError(f"window_{_row(observation_id, generation)['status']}")
        return status_window(observation_id, generation)


def invalidate_window(observation_id: str, generation: int, *, reason: str) -> dict[str, Any]:
    from extensions.observation_lab.worker_lock import worker_cycle_exclusion

    with worker_cycle_exclusion():
        if not _terminalize(observation_id, generation, "invalidated", reason):
            raise ValueError(f"window_{_row(observation_id, generation)['status']}")
        return status_window(observation_id, generation)


def _artifact_bytes(payload: dict[str, Any], *, compressed: bool) -> bytes:
    raw = _json(payload).encode("utf-8")
    return gzip.compress(raw, mtime=0) if compressed else raw


def _finalize_window(
    observation_id: str,
    generation: int,
    *,
    now: str | datetime | None = None,
    output: str | Path | None = None,
    progress: ObservationProgress | None = None,
) -> dict[str, Any]:
    window = _row(observation_id, generation)
    if window["status"] == "finalized":
        return status_window(observation_id, generation, now=now)
    if window["status"] != "active":
        raise ValueError(f"window_{window['status']}")
    at = _utc(now)
    if at < _utc(str(window["finalize_not_before"])):
        raise ValueError("window_finalize_not_due")
    handler = _handler(str(window["kind"]), str(window["handler_version"]))
    if handler is None:
        raise ValueError(f"handler_unavailable:{window['kind']}:{window['handler_version']}")
    readiness = progress or handler.advance(window)
    if readiness.invalidation_reason:
        invalidate_window(observation_id, generation, reason=readiness.invalidation_reason)
        raise ValueError("window_invalidated")
    if not readiness.can_finalize:
        raise ValueError("window_handler_not_ready")
    path = Path(output) if output is not None else (
        lab_db_path().parent
        / "artifacts"
        / f"{observation_id}-g{generation}.json.gz"
    )
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    unit_ids = tuple(
        row[0]
        for row in get_lab_db().execute(
            "SELECT unit_id FROM observation_units WHERE observation_id=? AND generation=? "
            "ORDER BY unit_id",
            (observation_id, int(generation)),
        ).fetchall()
    )
    domain_result = handler.finalize(window)
    if not isinstance(domain_result, dict):
        raise TypeError("handler_finalize_must_return_dict")
    provenance_rows = get_lab_db().execute(
        "SELECT DISTINCT producer_commit, producer_runtime_session_id FROM observation_units "
        "WHERE observation_id=? AND generation=?",
        (observation_id, int(generation)),
    ).fetchall()
    payload = {
        "observation_id": observation_id,
        "generation": int(generation),
        "kind": window["kind"],
        "handler_version": window["handler_version"],
        "spec_hash": window["spec_hash"],
        "evidence_cutoff": window["collect_until"],
        "finalized_at": at.isoformat(),
        "producer_provenance": {
            "commits": sorted(
                {row[0] for row in provenance_rows if row[0]}
                | ({str(window["producer_commit"])} if window.get("producer_commit") else set())
            ),
            "runtime_session_ids": sorted({row[1] for row in provenance_rows if row[1]}),
        },
        "domain_result": domain_result,
    }
    for key, value in domain_result.items():
        if key not in payload:
            payload[key] = value
    artifact = _artifact_bytes(payload, compressed=path.suffix == ".gz")
    digest = hashlib.sha256(artifact).hexdigest()
    staged.write_bytes(artifact)
    if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
        staged.unlink(missing_ok=True)
        raise RuntimeError("artifact_verification_failed")
    created = False
    try:
        if path.exists():
            existing = path.read_bytes()
            if hashlib.sha256(existing).hexdigest() == digest:
                staged.unlink(missing_ok=True)
            else:
                try:
                    envelope = json.loads(
                        gzip.decompress(existing) if path.suffix == ".gz" else existing
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    raise FileExistsError(path) from None
                identity_fields = (
                    "observation_id", "generation", "kind", "handler_version", "spec_hash"
                )
                referenced = get_lab_db().execute(
                    "SELECT 1 FROM observation_windows WHERE artifact_path=? LIMIT 1", (str(path),)
                ).fetchone()
                if (
                    not isinstance(envelope, dict) or
                    window.get("artifact_path") or window.get("artifact_sha256") or referenced
                    or any(envelope.get(field) != payload[field] for field in identity_fields)
                ):
                    raise FileExistsError(path)
                os.replace(staged, path)
                created = True
        else:
            os.replace(staged, path)
            created = True
        with lab_transaction(immediate=True) as db:
            current = db.execute(
                "SELECT status,spec_hash FROM observation_windows "
                "WHERE observation_id=? AND generation=?",
                (observation_id, int(generation)),
            ).fetchone()
            current_unit_ids = tuple(
                row[0]
                for row in db.execute(
                    "SELECT unit_id FROM observation_units WHERE observation_id=? AND generation=? "
                    "ORDER BY unit_id",
                    (observation_id, int(generation)),
                ).fetchall()
            )
            if (
                current is None
                or current["status"] != "active"
                or current["spec_hash"] != window["spec_hash"]
                or current_unit_ids != unit_ids
            ):
                raise RuntimeError("window_finalize_race")
            db.execute(
                "UPDATE observation_windows SET status='finalized',terminal_at=?,terminal_reason=?,"
                "last_advanced_at=?,last_summary_json=?,last_error=NULL,artifact_path=?,artifact_sha256=? "
                "WHERE observation_id=? AND generation=? AND status='active'",
                (
                    at.isoformat(),
                    "handler_ready",
                    at.isoformat(),
                    _json(readiness.summary),
                    str(path),
                    digest,
                    observation_id,
                    int(generation),
                ),
            )
            # The immutable Lab result is emitted in the same transaction as
            # the terminal lifecycle transition.  A missing Main import
            # cursor remains explicit in the source cut and never becomes a
            # fabricated complete measurement.
            from extensions.observation_lab.canonical import (
                record_observation_evaluation,
                record_observation_result,
            )

            cursor = db.execute(
                "SELECT contiguous_sequence, source_checkpoint_fingerprint "
                "FROM observation_import_cursors WHERE source_name='main'"
            ).fetchone()
            source_cutoff = _json({
                "main_import_contiguous_sequence": int(cursor[0]) if cursor else None,
                "source_checkpoint_fingerprint": (
                    str(cursor[1]) if cursor and cursor[1] else None
                ),
                "collect_until": window["collect_until"],
            })
            exposure_rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM observation_exposures WHERE observation_id=? AND generation=? "
                    "ORDER BY source_receipt_id",
                    (observation_id, int(generation)),
                ).fetchall()
            ]
            exposure_fingerprint = _hash(exposure_rows)
            outcome_fingerprint = _hash([
                {
                    "candidate_key": row.get("outcome_candidate_key"),
                    "outcome_source": row.get("outcome_source"),
                }
                for row in exposure_rows
                if row.get("outcome_candidate_key")
            ])
            verdict = str(domain_result.get("verdict") or "insufficient_data")
            if verdict not in {"positive", "negative", "neutral", "invalid", "insufficient_data"}:
                verdict = "insufficient_data"
            result_payload = {
                "status": "finalized",
                "source_cutoff": json.loads(source_cutoff),
                "exposure_count": len(exposure_rows),
                "outcome_count": sum(
                    1 for row in exposure_rows if row.get("outcome_candidate_key")
                ),
                "domain_result": domain_result,
            }
            evaluation_id = record_observation_evaluation(
                observation_id=observation_id,
                generation=int(generation),
                evaluation_kind="monitoring",
                source_cutoff=source_cutoff,
                input_fingerprint=_hash({
                    "spec_hash": window["spec_hash"],
                    "source_cutoff": source_cutoff,
                    "domain_result": domain_result,
                }),
                exposure_fingerprint=exposure_fingerprint,
                outcome_fingerprint=outcome_fingerprint,
                result=result_payload,
                verdict=verdict,
                disposition="finalized",
                db=db,
            )
            record_observation_result(
                observation_id=observation_id,
                generation=int(generation),
                spec_hash=str(window["spec_hash"]),
                source_cutoff=source_cutoff,
                verdict=verdict,
                disposition="finalized",
                result=result_payload,
                evaluation_id=evaluation_id,
                db=db,
            )
            from extensions.observation_lab.canonical import _append_lifecycle_event

            _append_lifecycle_event(
                db,
                observation_id=observation_id,
                generation=int(generation),
                to_status="finalized",
                action="finalize",
                reason="handler_ready",
                actor="observation_kernel",
                dedup_key=f"observation-finalize:{observation_id}:{generation}:{digest}",
                from_status="active",
            )
    except Exception:
        staged.unlink(missing_ok=True)
        if created:
            path.unlink(missing_ok=True)
        raise
    return status_window(observation_id, generation, now=at)


def finalize_window(
    observation_id: str,
    generation: int,
    *,
    now: str | datetime | None = None,
    output: str | Path | None = None,
    progress: ObservationProgress | None = None,
) -> dict[str, Any]:
    from extensions.observation_lab.worker_lock import worker_cycle_exclusion

    with worker_cycle_exclusion():
        return _finalize_window(
            observation_id, generation, now=now, output=output, progress=progress
        )


def advance_windows(*, limit: int = 10, now: str | datetime | None = None) -> dict[str, Any]:
    at = _utc(now)
    rows = get_lab_db().execute(
        "SELECT * FROM observation_windows WHERE status='active' "
        "ORDER BY COALESCE(last_advanced_at, started_at), observation_id, generation LIMIT ?",
        (max(0, int(limit)),),
    ).fetchall()
    results: dict[str, Any] = {}
    for raw in rows:
        window = dict(raw)
        key = f"{window['observation_id']}:{window['generation']}"
        handler = _handler(str(window["kind"]), str(window["handler_version"]))
        if handler is None:
            error = f"handler_unavailable:{window['kind']}:{window['handler_version']}"
            with lab_transaction(immediate=True) as db:
                db.execute(
                    "UPDATE observation_windows SET last_advanced_at=?,last_error=? "
                    "WHERE observation_id=? AND generation=? AND status='active'",
                    (at.isoformat(), error, window["observation_id"], window["generation"]),
                )
            results[key] = {"status": "unavailable", "error": error}
            continue
        try:
            progress = handler.advance(window)
            if progress.invalidation_reason:
                invalidate_window(
                    str(window["observation_id"]), int(window["generation"]),
                    reason=progress.invalidation_reason,
                )
                results[key] = {"status": "invalidated", "reason": progress.invalidation_reason}
                continue
            safety_action = (
                progress.summary.get("evaluation", {}).get("safety_action")
                if isinstance(progress.summary, dict)
                and isinstance(progress.summary.get("evaluation"), dict)
                else None
            )
            if safety_action:
                # The canonical evaluator already committed the Lab lifecycle
                # transition and Manifest revocations atomically.  Do not run
                # the generic kernel invalidation a second time.
                results[key] = {"status": "invalidated", "safety_action": safety_action}
                continue
            with lab_transaction(immediate=True) as db:
                db.execute(
                    "UPDATE observation_windows SET last_advanced_at=?,last_summary_json=?,last_error=NULL "
                    "WHERE observation_id=? AND generation=? AND status='active'",
                    (
                        at.isoformat(), _json(progress.summary),
                        window["observation_id"], window["generation"],
                    ),
                )
            if progress.can_finalize and at >= _utc(str(window["finalize_not_before"])):
                finalized = finalize_window(
                    str(window["observation_id"]), int(window["generation"]),
                    now=at, progress=progress,
                )
                results[key] = {"status": "finalized", "artifact_sha256": finalized["artifact_sha256"]}
            else:
                results[key] = {"status": "advanced", "summary": progress.summary}
        except Exception as exc:
            error = repr(exc)[:400]
            with lab_transaction(immediate=True) as db:
                db.execute(
                    "UPDATE observation_windows SET last_advanced_at=?,last_error=? "
                    "WHERE observation_id=? AND generation=? AND status='active'",
                    (at.isoformat(), error, window["observation_id"], window["generation"]),
                )
            results[key] = {"status": "error", "error": error}
    return {"advanced": len(rows), "results": results}


def active_retention_claims() -> dict[str, Any]:
    claims: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for raw in get_lab_db().execute(
        "SELECT * FROM observation_windows WHERE status='active' "
        "ORDER BY observation_id,generation"
    ).fetchall():
        window = dict(raw)
        key = f"{window['observation_id']}:{window['generation']}"
        handler = _handler(str(window["kind"]), str(window["handler_version"]))
        if handler is None:
            unavailable.append(key)
            continue
        try:
            claims.extend(handler.retention_claims(window))
        except Exception:
            unavailable.append(key)
    if unavailable:
        return {"available": False, "unavailable_windows": unavailable, "claims": ()}
    floors: dict[str, str] = {}
    for claim in claims:
        domain = str(claim["domain"])
        earliest = _utc(claim["earliest_at"]).isoformat()
        floors[domain] = min(floors.get(domain, earliest), earliest)
    return {"available": True, "unavailable_windows": [], "claims": floors}


def invalidate_active_for_restore(*, joint_cut_proven: bool) -> int:
    """Invalidate active windows after a restore whose joint cut is unproven."""
    from extensions.observation_lab.worker_lock import worker_cycle_exclusion
    from extensions.observation_lab.canonical import _append_lifecycle_event
    if joint_cut_proven:
        return 0

    with worker_cycle_exclusion():
        with lab_transaction(immediate=True) as db:
            rows = db.execute(
                "SELECT observation_id, generation FROM observation_windows "
                "WHERE status='active' ORDER BY observation_id, generation"
            ).fetchall()
            for row in rows:
                identity = f"restore-invalidate:{row['observation_id']}:{row['generation']}"
                _append_lifecycle_event(
                    db,
                    observation_id=str(row["observation_id"]),
                    generation=int(row["generation"]),
                    from_status="active",
                    to_status="invalidated",
                    action="restore_invalidate",
                    reason="restore_continuity_unproven",
                    actor="restore",
                    dedup_key=identity,
                    audit_identity=f"observation-restore:{identity}",
                )
            return len(rows)


__all__ = [
    "ObservationHandler",
    "ObservationProgress",
    "abort_window",
    "active_retention_claims",
    "active_window",
    "advance_windows",
    "capture_event",
    "finalize_window",
    "invalidate_active_for_restore",
    "invalidate_window",
    "register_handler",
    "registered_handlers",
    "start_window",
    "status_window",
]

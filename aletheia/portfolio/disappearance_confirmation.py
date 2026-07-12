"""Durable, fail-closed evidence for a locally open position disappearing.

This module never closes a position and never calls a broker.  It converts a
fresh, complete, account-scoped cached view into immutable observations.  A
candidate becoming ready means only that the later verified-exit protocol may
investigate it; readiness is not close or settlement authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any

from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
from aletheia.data.symbols import to_canonical
from aletheia.storage import repositories as repo
from aletheia.storage.models import (
    AuditEvent,
    PositionDisappearanceCandidate,
    PositionDisappearanceObservation,
)


MIN_INDEPENDENT_COMPLETE_OBSERVATIONS = 2
DIRECT_CONFIRMED_ABSENT = "confirmed_absent"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()}"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def logical_account_fingerprint(
    *, broker_id: str, account_environment: str, execution_mode: str,
) -> str:
    """Non-secret logical account identity for one configured broker/environment.

    Aletheia currently configures at most one credential set per execution mode and
    does not expose exchange account UIDs in UTA snapshots.  This fingerprint is
    therefore deliberately labelled logical; a future public account UID may replace
    it without ever hashing credential material.
    """
    return _digest({
        "kind": "logical_execution_account",
        "broker_id": broker_id,
        "account_environment": account_environment,
        "execution_mode": execution_mode,
    })


def build_view_evidence(
    snapshot: dict | None,
    *,
    broker_id: str,
    account_environment: str,
    execution_mode: str,
    complete: bool,
) -> dict:
    """Build a stable identity; re-reading one cache cannot count twice."""
    snapshot = snapshot or {}
    fetched_at = str(snapshot.get("fetched_at") or "")
    account_fingerprint = logical_account_fingerprint(
        broker_id=broker_id,
        account_environment=account_environment,
        execution_mode=execution_mode,
    )
    normalized_positions = sorted(
        (
            str(row.get("broker_id") or broker_id),
            to_canonical(str(row.get("symbol") or "")),
            str(row.get("market_type") or "usdt_futures"),
            str(row.get("side") or row.get("direction") or "").lower(),
            float(row.get("quantity") or 0),
        )
        for row in (snapshot.get("positions") or [])
    )
    payload = {
        "broker_id": broker_id,
        "account_environment": account_environment,
        "account_fingerprint": account_fingerprint,
        "execution_mode": execution_mode,
        "fetched_at": fetched_at,
        "positions_digest": _digest(normalized_positions),
    }
    view_complete = bool(complete and fetched_at)
    return {
        **payload,
        "view_complete": view_complete,
        "view_identity": _digest(payload) if view_complete else "unavailable",
        "semantics_version": OUTCOME_SEMANTICS_VERSION,
    }


def _candidate_identity(position: dict, evidence: dict) -> tuple[str, dict]:
    identity = {
        "position_id": str(position["id"]),
        "execution_mode": str(position["execution_mode"]),
        "broker_id": str(evidence["broker_id"]),
        "account_environment": str(evidence["account_environment"]),
        "account_fingerprint": str(evidence["account_fingerprint"]),
        "symbol": to_canonical(str(position["symbol"])),
        "market_type": str(position.get("market_type") or "unknown"),
        "direction": str(position.get("direction") or "").lower(),
    }
    return f"json:{_canonical_json(identity)}", identity


def disappearance_ready(candidate: dict) -> bool:
    return (
        int(candidate.get("confirmation_count") or 0)
        >= MIN_INDEPENDENT_COMPLETE_OBSERVATIONS
        or candidate.get("direct_query_state") == DIRECT_CONFIRMED_ABSENT
    )


def record_disappearance_observation(
    position: dict,
    evidence: dict,
    *,
    direct_query_state: str = "not_attempted",
) -> tuple[dict, bool]:
    """Persist one absence observation and return (candidate, inserted)."""
    if not evidence.get("view_complete"):
        raise ValueError("incomplete view cannot create disappearance evidence")
    required = (
        "view_identity", "broker_id", "account_environment",
        "account_fingerprint", "fetched_at",
    )
    if any(not evidence.get(key) for key in required):
        raise ValueError("scoped view identity is incomplete")
    identity_key, identity = _candidate_identity(position, evidence)
    observed_at = str(evidence["fetched_at"])
    created_at = _utcnow()
    candidate = repo.get_active_position_disappearance_candidate(identity_key)
    if candidate is not None and direct_query_state != DIRECT_CONFIRMED_ABSENT:
        current_ts = _parse_timestamp(observed_at)
        previous_ts = _parse_timestamp(str(candidate.get("last_observed_at") or ""))
        if current_ts is None or previous_ts is None or current_ts <= previous_ts:
            # A replayed or time-regressed cache is not independent evidence.
            return candidate, False

    try:
        with repo.transaction():
            if candidate is None:
                model = PositionDisappearanceCandidate(
                    position_id=identity["position_id"],
                    identity_key=identity_key,
                    broker_id=identity["broker_id"],
                    account_environment=identity["account_environment"],
                    account_fingerprint=identity["account_fingerprint"],
                    execution_mode=identity["execution_mode"],
                    symbol=identity["symbol"],
                    market_type=identity["market_type"],
                    direction=identity["direction"],
                    first_observed_at=observed_at,
                    last_observed_at=observed_at,
                    confirmation_count=0,
                    direct_query_state=direct_query_state,
                    semantics_version=OUTCOME_SEMANTICS_VERSION,
                    created_at=created_at,
                    updated_at=created_at,
                )
                repo.insert_position_disappearance_candidate(model)
                candidate = model.model_dump()
                repo.insert_audit_event(AuditEvent(
                    actor="system",
                    source="position_reconciler",
                    event_type="position_disappearance_candidate_created",
                    entity_type="position_disappearance_candidate",
                    entity_id=model.id,
                    after_state=_canonical_json(identity),
                    message="fresh complete scoped view did not contain local position",
                ))

            observation_hash = _digest({
                "candidate_id": candidate["id"],
                "view_identity": evidence["view_identity"],
                "position_absent": True,
                "direct_query_state": direct_query_state,
            })
            inserted = repo.insert_position_disappearance_observation(
                PositionDisappearanceObservation(
                    candidate_id=candidate["id"],
                    observation_hash=observation_hash,
                    view_identity=str(evidence["view_identity"]),
                    view_complete=True,
                    position_absent=True,
                    direct_query_state=direct_query_state,
                    evidence_json=_canonical_json(evidence),
                    observed_at=observed_at,
                )
            )
            if inserted:
                direct_state = (
                    direct_query_state
                    if direct_query_state != "not_attempted"
                    else candidate.get("direct_query_state") or "not_attempted"
                )
                repo.update_position_disappearance_candidate(
                    candidate["id"],
                    last_observed_at=observed_at,
                    confirmation_count=int(candidate.get("confirmation_count") or 0) + 1,
                    direct_query_state=direct_state,
                    updated_at=created_at,
                )
    except sqlite3.IntegrityError:
        # A concurrent scheduler retry may have created the same active identity.
        candidate = repo.get_active_position_disappearance_candidate(identity_key)
        if candidate is None:
            raise
        return candidate, False
    stored = repo.get_active_position_disappearance_candidate(identity_key)
    if stored is None:
        raise RuntimeError("disappearance candidate persistence failed")
    return stored, inserted


def resolve_reappeared_candidate(position: dict, evidence: dict) -> str | None:
    """Resolve only when a fresh scoped exchange row proves reappearance."""
    identity_key, _ = _candidate_identity(position, evidence)
    candidate = repo.get_active_position_disappearance_candidate(identity_key)
    if candidate is None:
        return None
    stamp = _utcnow()
    with repo.transaction():
        repo.update_position_disappearance_candidate(
            candidate["id"], status="reappeared", resolved_at=stamp, updated_at=stamp,
        )
        repo.insert_audit_event(AuditEvent(
            actor="system",
            source="position_reconciler",
            event_type="position_disappearance_resolved",
            entity_type="position_disappearance_candidate",
            entity_id=candidate["id"],
            before_state=_canonical_json({
                "status": "pending",
                "confirmation_count": candidate["confirmation_count"],
            }),
            after_state=_canonical_json({"status": "reappeared"}),
            message="fresh complete scoped exchange view contains the position again",
        ))
    return str(candidate["id"])


def process_disappearance_evidence(
    *, orphaned: list[Any], managed: list[Any], view_evidence: dict | None,
) -> dict:
    """Record current evidence without ever invoking close/accounting code."""
    summary = {
        "created": 0,
        "confirmed": 0,
        "observations": [],
        "resolved": [],
    }
    if not view_evidence or not view_evidence.get("view_complete"):
        return summary

    for item in managed:
        # all-managed degraded fallback has exchange=None and must not resolve.
        if not getattr(item, "exchange", None) or not getattr(item, "local", None):
            continue
        resolved = resolve_reappeared_candidate(item.local, view_evidence)
        if resolved:
            summary["resolved"].append(resolved)

    for item in orphaned:
        if not getattr(item, "local", None):
            continue
        before = repo.get_active_position_disappearance_candidate_for_position(
            str(item.local["id"])
        )
        candidate, inserted = record_disappearance_observation(
            item.local, view_evidence,
        )
        if before is None:
            summary["created"] += 1
        if disappearance_ready(candidate):
            summary["confirmed"] += 1
        summary["observations"].append({
            "candidate_id": candidate["id"],
            "position_id": candidate["position_id"],
            "inserted": inserted,
            "confirmation_count": candidate["confirmation_count"],
            "direct_query_state": candidate["direct_query_state"],
            "ready_for_verified_exit": disappearance_ready(candidate),
        })
    return summary

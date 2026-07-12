"""Durable incident state for reconciliation close/reopen churn.

The detector itself remains read-only in ``position_reconciler``.  This module
turns a systemic detector result into a restart-safe circuit breaker.  Recovery
is deliberately two-part: spaced clean observations establish evidence, then an
explicit operator action resolves the incident.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3

from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION
from aletheia.storage.models import AuditEvent, ReconciliationIntegrityIncident
from aletheia.storage import repositories as repo


INCIDENT_TYPE = "close_reopen_churn"
MIN_CLEAN_OBSERVATIONS = 2
MIN_CLEAN_OBSERVATION_SPACING = timedelta(minutes=5)


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fingerprint(detection: dict) -> str:
    identities = detection.get("churned_identities") or detection.get("churned_symbols") or []
    payload = {
        "incident_type": INCIDENT_TYPE,
        "identities": identities,
        "semantics_version": OUTCOME_SEMANTICS_VERSION,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def get_active_reconciliation_integrity_incident() -> dict | None:
    """Return the restart-safe global circuit-breaker state, if any."""
    return repo.get_active_reconciliation_integrity_incident(INCIDENT_TYPE)


def record_reconciliation_integrity_incident(
    detection: dict, *, now: datetime | None = None,
) -> tuple[dict, bool]:
    """Create or refresh one active incident without duplicating retries."""
    if not detection.get("is_thrashing"):
        raise ValueError("cannot record an incident from a clean detection")
    current = _now(now)
    stamp = current.isoformat()
    identities = detection.get("churned_identities") or []
    sources = detection.get("reopened_by_source") or {}
    fingerprint = _fingerprint(detection)
    active = get_active_reconciliation_integrity_incident()
    if active is not None:
        repo.update_reconciliation_integrity_incident(
            active["id"],
            fingerprint=fingerprint,
            window_minutes=int(detection.get("window_minutes") or 30),
            churn_count=int(detection.get("churn_count") or 0),
            identities_json=_canonical_json(identities),
            reopen_sources_json=_canonical_json(sources),
            last_detected_at=stamp,
            last_clean_observed_at=None,
            clean_observation_count=0,
            details_json=_canonical_json(detection),
            updated_at=stamp,
        )
        return repo.get_reconciliation_integrity_incident(active["id"]), False

    incident = ReconciliationIntegrityIncident(
        fingerprint=fingerprint,
        window_minutes=int(detection.get("window_minutes") or 30),
        churn_count=int(detection.get("churn_count") or 0),
        identities_json=_canonical_json(identities),
        reopen_sources_json=_canonical_json(sources),
        detected_at=stamp,
        last_detected_at=stamp,
        semantics_version=OUTCOME_SEMANTICS_VERSION,
        details_json=_canonical_json(detection),
        created_at=stamp,
        updated_at=stamp,
    )
    try:
        repo.insert_reconciliation_integrity_incident(incident)
        return repo.get_reconciliation_integrity_incident(incident.id), True
    except sqlite3.IntegrityError:
        # Another scheduler thread may have won the one-active-incident race.
        active = get_active_reconciliation_integrity_incident()
        if active is None:
            raise
        return active, False


def record_reconciliation_clean_observation(
    incident_id: str, *, now: datetime | None = None,
) -> dict:
    """Record a spaced clean observation; rapid retries cannot advance recovery."""
    incident = repo.get_reconciliation_integrity_incident(incident_id)
    if incident is None or incident.get("status") != "active":
        raise ValueError("active reconciliation integrity incident not found")
    current = _now(now)
    previous = _parse(incident.get("last_clean_observed_at"))
    if previous is not None and current - previous < MIN_CLEAN_OBSERVATION_SPACING:
        return incident
    count = int(incident.get("clean_observation_count") or 0) + 1
    stamp = current.isoformat()
    repo.update_reconciliation_integrity_incident(
        incident_id,
        last_clean_observed_at=stamp,
        clean_observation_count=count,
        updated_at=stamp,
    )
    return repo.get_reconciliation_integrity_incident(incident_id)


def resolve_reconciliation_integrity_incident(
    incident_id: str, *, actor: str, reason: str, now: datetime | None = None,
) -> dict:
    """Explicitly resolve only after the clean gate and a current clean check."""
    incident = repo.get_reconciliation_integrity_incident(incident_id)
    if incident is None or incident.get("status") != "active":
        raise ValueError("active reconciliation integrity incident not found")
    if int(incident.get("clean_observation_count") or 0) < MIN_CLEAN_OBSERVATIONS:
        raise ValueError("clean observation gate has not been met")
    from aletheia.portfolio.position_reconciler import detect_reconcile_thrash

    detection = detect_reconcile_thrash(
        window_minutes=int(incident.get("window_minutes") or 30)
    )
    if not detection.get("detector_available", True):
        raise ValueError("current reconcile detector is unavailable")
    if detection.get("is_thrashing"):
        raise ValueError("current reconcile detector is not clean")
    if not str(actor or "").strip() or not str(reason or "").strip():
        raise ValueError("actor and resolution reason are required")
    stamp = _now(now).isoformat()
    resolved_by = str(actor).strip()
    resolution_reason = str(reason).strip()
    with repo.transaction():
        repo.update_reconciliation_integrity_incident(
            incident_id,
            status="resolved",
            resolution_reason=resolution_reason,
            resolved_by=resolved_by,
            resolved_at=stamp,
            updated_at=stamp,
        )
        repo.insert_audit_event(AuditEvent(
            actor=resolved_by,
            source="reconciliation_integrity",
            event_type="reconciliation_integrity_incident_resolved",
            entity_type="reconciliation_integrity_incident",
            entity_id=incident_id,
            before_state=_canonical_json({"status": "active"}),
            after_state=_canonical_json({
                "status": "resolved",
                "clean_observation_count": incident["clean_observation_count"],
            }),
            message=resolution_reason,
        ))
    return repo.get_reconciliation_integrity_incident(incident_id)


def clean_observation_gate(incident: dict | None) -> dict:
    """Return honest operator-facing recovery progress."""
    count = int((incident or {}).get("clean_observation_count") or 0)
    return {
        "observed": count,
        "required": MIN_CLEAN_OBSERVATIONS,
        "met": count >= MIN_CLEAN_OBSERVATIONS,
        "explicit_resolution_required": True,
    }

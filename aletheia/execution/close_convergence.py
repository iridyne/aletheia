"""Pure close-claim state projection shared by execution and owner views.

The durable source of truth remains ``positions.pending_close_*`` plus the linked
Order.  This module only normalizes that fact for guards, reconciliation, and
operator-facing responses; it never places an order or changes portfolio state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


CLOSE_CLAIM_STATES = frozenset({
    "pending",
    "submitted",
    "partial",
    "partially_filled",
    "submission_unknown",
    "submission_rejected",
    "materialization_missing",
    "position_absence_unverified",
    "overfilled",
})

# Compatibility name used by the existing Position Manager and broker tests.
_OPEN_CLOSE_STATES = CLOSE_CLAIM_STATES
PENDING_CLOSE_STALE_HOURS = 4

_STATE_LABELS = {
    "pending": ("close_claimed", "close claimed; awaiting submission"),
    "submitted": ("close_submitted", "close submitted"),
    "partial": ("partial", "partial"),
    "partially_filled": ("partial", "partial"),
    "submission_unknown": ("evidence_unavailable", "evidence unavailable"),
    "materialization_missing": (
        "materialization_missing",
        "materialization missing",
    ),
    "position_absence_unverified": (
        "exchange_position_absent_verifying",
        "exchange position absent; verifying",
    ),
    "submission_rejected": (
        "manual_attention_required",
        "manual attention required",
    ),
    "overfilled": ("manual_attention_required", "manual attention required"),
    "error": ("manual_attention_required", "manual attention required"),
}

_IMMEDIATE_RECONCILIATION_STATES = frozenset({
    "submission_unknown",
    "submission_rejected",
    "materialization_missing",
    "position_absence_unverified",
    "overfilled",
    "error",
})
IMMEDIATE_RECONCILIATION_STATES = _IMMEDIATE_RECONCILIATION_STATES


def _parse_timestamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def pending_close_age_hours(
    requested_at: object,
    *,
    now: datetime | None = None,
) -> float | None:
    """Return a finite age, or ``None`` when the timestamp is not trustworthy."""
    requested = _parse_timestamp(requested_at)
    if requested is None:
        return None
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = (current.astimezone(timezone.utc) - requested).total_seconds() / 3600
    if age < 0:
        return None
    return age


def has_unresolved_close_claim(position: Mapping[str, Any]) -> bool:
    """Return whether a Position already owns a close claim that must not fork.

    A missing status on an existing claim is treated as unresolved.  This is a
    conservative restart/legacy guard; only the explicit pre-submission
    ``error`` path remains retryable because it has no exchange-side effect.
    """
    status = str(position.get("pending_close_status") or "").strip().lower()
    order_id = str(position.get("pending_close_order_id") or "").strip()
    return status in CLOSE_CLAIM_STATES or (bool(order_id) and not status)


def close_convergence_view(
    position: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Project one durable close claim into machine and owner vocabularies.

    ``close_convergence_state`` is stable machine vocabulary.  The separate
    ``close_convergence_owner_status`` is intentionally short, honest UI text.
    An old or malformed timestamp is treated as escalation-worthy rather than
    silently allowing a close claim to suppress protection forever.
    """
    position_status = str(position.get("status") or "").strip().lower()
    pending_status = str(position.get("pending_close_status") or "").strip().lower()
    order_id = str(position.get("pending_close_order_id") or "").strip() or None
    # A pre-submission ``error`` is deliberately retryable; it is an Order
    # history fact, not an unresolved exchange-side claim.  All other claim
    # detection uses the same guard as broker and protection paths.
    has_claim = has_unresolved_close_claim(position)
    requested_at = position.get("pending_close_requested_at")
    age_hours = pending_close_age_hours(requested_at, now=now)
    age_unknown = bool(has_claim and age_hours is None)

    if position_status == "closed" and not has_claim:
        state, owner_status = "closed", "closed"
        requires_reconciliation = False
        escalated = False
    elif not has_claim:
        state, owner_status = "still_open", "still open"
        requires_reconciliation = False
        escalated = False
    else:
        state, owner_status = _STATE_LABELS.get(
            pending_status,
            ("manual_attention_required", "manual attention required"),
        )
        stale = age_unknown or (
            age_hours is not None and age_hours >= PENDING_CLOSE_STALE_HOURS
        )
        requires_reconciliation = (
            pending_status in _IMMEDIATE_RECONCILIATION_STATES or stale
        )
        escalated = stale
        if stale:
            owner_status = "manual attention required"

    return {
        "pending_close": has_claim,
        "pending_close_order_id": order_id,
        "pending_close_status": pending_status or None,
        "pending_close_requested_at": requested_at,
        "close_convergence_state": state,
        "close_convergence_owner_status": owner_status,
        "close_convergence_requires_reconciliation": requires_reconciliation,
        "close_convergence_escalated": escalated,
        "close_convergence_age_hours": age_hours,
    }

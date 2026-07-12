"""Versioned clean-window gate for performance and Learning evidence.

The v1 anchor starts after the reconciler/accounting/reporting repairs. Earlier
history remains auditable and visible on raw account views, but it cannot prove
strategy edge or teach a behavior-changing consumer. A bounded incident interval
has its own reason so diagnosis can distinguish it from older pre-clean history.
"""
from __future__ import annotations

from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import OUTCOME_SEMANTICS_VERSION

# UTC. Outcome-integrity v1 classifier, close accounting, Risk split and honest
# reporting were all present at this boundary.
CLEAN_WINDOW_ANCHOR_ID = "outcome-integrity-v1-2026-07-12"
CLEAN_WINDOW_ANCHOR_ISO = "2026-07-12T07:10:00+00:00"
LEARNING_DATA_FLOOR_ISO = CLEAN_WINDOW_ANCHOR_ISO

# A bounded interval immediately before the v1 anchor is separately named so a
# report can distinguish the known reconciliation-integrity incident from older
# pre-clean history. Both remain excluded.
OUTCOME_INCIDENT_WINDOWS = (
    ("reconciliation-integrity-2026-07-12", "2026-07-12T00:00:00+00:00", CLEAN_WINDOW_ANCHOR_ISO),
)


def assess_outcome_window(row: dict) -> str:
    """Return eligible or a stable temporal/version exclusion reason."""
    version = row.get("outcome_semantics_version")
    if version != OUTCOME_SEMANTICS_VERSION:
        return "unknown_semantics_version"
    closed_at = _as_utc(row.get("closed_at"))
    if closed_at is None:
        return "missing_close_time"
    for _incident_id, start_raw, end_raw in OUTCOME_INCIDENT_WINDOWS:
        start = _as_utc(start_raw)
        end = _as_utc(end_raw)
        if start is not None and end is not None and start <= closed_at < end:
            return "incident_window"
    anchor = _as_utc(CLEAN_WINDOW_ANCHOR_ISO)
    if anchor is None or closed_at < anchor:
        return "pre_clean_window"
    return "eligible"


def outcome_window_metadata() -> dict[str, str]:
    return {
        "clean_window_anchor_id": CLEAN_WINDOW_ANCHOR_ID,
        "clean_window_anchor_iso": CLEAN_WINDOW_ANCHOR_ISO,
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
    }


def _as_utc(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def clean_window_cutoff(rolling_cutoff_iso: str) -> str:
    """Return the later of a rolling-window cutoff and the v1 clean-data anchor.

    Both inputs are ISO-8601 UTC timestamps. The returned string is used
    directly in SQL ``... >= ?`` comparisons against DB timestamps (also ISO
    UTC), so the result is whichever cutoff is more recent — never admitting
    pre-floor (dirty) data.
    """
    floor = LEARNING_DATA_FLOOR_ISO
    try:
        rc = datetime.fromisoformat(rolling_cutoff_iso)
        fl = datetime.fromisoformat(floor)
    except (ValueError, TypeError):
        # Malformed rolling cutoff → fail safe to the anchor.
        return floor
    return rolling_cutoff_iso if rc >= fl else floor

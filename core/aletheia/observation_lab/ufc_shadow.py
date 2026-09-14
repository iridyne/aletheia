"""Read-only historical UFC shadow-equivalence evidence."""

from __future__ import annotations

from typing import Any


UFC_PATH_ID = "universe_family_calibration"
_SKIPPED_EQUIVALENCE_NOTES = frozenset({
    "skipped",
    "no_stored_verdict_yet",
    "no_cohorts_yet",
    "current_policy_epoch_unavailable",
    "no_exact_current_cohort",
    "no_exact_current_verdict",
})


def decode_equivalence_result(row: dict[str, Any]) -> str:
    """Recover the three-state result from the backward-compatible row shape."""
    if row.get("notes") in _SKIPPED_EQUIVALENCE_NOTES:
        return "skipped"
    return "matched" if int(row.get("matched") or 0) == 1 else "mismatched"


def latest_equivalence_record(observation_id: str) -> dict | None:
    """Read-only: most recent equivalence pass for one Lab observation id."""
    from aletheia.observation_lab.db import readonly_lab_db

    with readonly_lab_db(allow_missing=True) as db:
        if db is None:
            return None
        row = db.execute(
            "SELECT * FROM ufc_shadow_equivalence WHERE observation_id=? "
            "ORDER BY checked_at DESC LIMIT 1",
            (observation_id,),
        ).fetchone()
    return dict(row) if row else None

"""Clean-window floor for negative-feedback learning data.

Round 1 (before 2026-06-25T15:22 UTC) produced known-dirty trade data:
direction over-concentration (short 17/23), broken attribution chains
(opportunity_id NULL), and a drawdown-brake bug that distorted sizing. If the
high-dimensional learners (insight_synthesizer / confidence_calibration /
reasoning_feedback) ingest that history, they fit biased conclusions —
empirically "every direction is contra" — and then steer future trades by it.

So every learning data-fetch filters to outcomes at or after this floor. The
floor is a one-time dirty-data quarantine, NOT a trading rule: once a rolling
window naturally starts after it, ``clean_window_cutoff`` returns the rolling
cutoff unchanged and the floor becomes a no-op.

See memory: aletheia-round1-cleanwindow-eval, aletheia-negfeedback-high-dimensional-evolve.
"""
from __future__ import annotations

from datetime import datetime

# UTC. Round 1 decision-chain + drawdown-brake fixes (70a7b64) landed ~15:22 UTC
# 2026-06-25; data at/after this point is the "clean window".
LEARNING_DATA_FLOOR_ISO = "2026-06-25T15:22:00+00:00"


def clean_window_cutoff(rolling_cutoff_iso: str) -> str:
    """Return the later of a rolling-window cutoff and the clean-data floor.

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
        # Malformed rolling cutoff → fail safe to the floor (never ingest
        # pre-floor dirty data).
        return floor
    return rolling_cutoff_iso if rc >= fl else floor

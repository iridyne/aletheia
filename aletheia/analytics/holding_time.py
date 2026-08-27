"""Shared holding-time availability semantics for analytics surfaces."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def holding_hours(opened_at: Any, closed_at: Any) -> float | None:
    """Return non-negative holding hours, or None when timing is not measurable."""
    if not opened_at or not closed_at:
        return None
    try:
        opened = _parse_timestamp(opened_at)
        closed = _parse_timestamp(closed_at)
    except (TypeError, ValueError, OverflowError):
        return None
    seconds = (closed - opened).total_seconds()
    if seconds < 0 or not math.isfinite(seconds):
        return None
    return seconds / 3600.0


def _parse_timestamp(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

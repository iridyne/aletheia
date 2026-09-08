"""Shared holding-time availability semantics for analytics surfaces."""

from __future__ import annotations
from aletheia.utils.dt import parse_utc

import math
from datetime import datetime, timezone
from typing import Any


def holding_hours(opened_at: Any, closed_at: Any) -> float | None:
    """Return non-negative holding hours, or None when timing is not measurable."""
    if not opened_at or not closed_at:
        return None
    # `parse_utc` returns None for malformed input rather than raising, so the
    # None check must be explicit. The previous local parser RAISED and this
    # function relied on the surrounding try/except; converging on the canonical
    # parser without adding this guard turned a malformed timestamp into
    # `TypeError: unsupported operand type(s) for -: 'NoneType' and 'NoneType'`
    # raised OUTSIDE the try, i.e. an unhandled crash in three analytics surfaces.
    opened = parse_utc(opened_at)
    closed = parse_utc(closed_at)
    if opened is None or closed is None:
        return None
    seconds = (closed - opened).total_seconds()
    if seconds < 0 or not math.isfinite(seconds):
        return None
    return seconds / 3600.0



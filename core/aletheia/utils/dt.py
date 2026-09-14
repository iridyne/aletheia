from __future__ import annotations

from datetime import datetime, timezone


def parse_utc(value: object) -> datetime | None:
    """Parse an ISO-8601 timestamp string to a UTC-aware datetime, or return None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

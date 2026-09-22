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


def parse_utc_instant(value) -> datetime | None:
    """Parse an ISO timestamp into a UTC-aware datetime, or None."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    # normalize trailing 'Z' (UTC) which fromisoformat <3.11 cannot read
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)

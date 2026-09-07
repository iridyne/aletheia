from __future__ import annotations

from datetime import datetime, timezone


def parse_utc(value: object) -> datetime | None:
    """Parse a timestamp into a UTC-aware datetime, or None when unusable.

    This is the contract shared by the seven parsers it replaces, established by
    probing each against the same inputs rather than by reading their bodies —
    a coarse read of the `tzinfo is None` branch previously mis-grouped 14 of 18.

    | input                        | result |
    |------------------------------|--------|
    | naive ISO string             | assumed UTC |
    | ISO string with any offset   | NORMALISED to UTC |
    | `datetime` instance          | accepted, normalised |
    | `"… "`-separated / `Z` suffix| accepted |
    | None / empty / malformed     | None |
    | non-string scalar (e.g. int) | None, not an exception |

    Two behaviours here are load-bearing and are the reason eleven sibling parsers
    were NOT folded in: five of them return a non-UTC offset unchanged, and four
    raise `AttributeError`/`TypeError` on a non-string input instead of returning
    None. Those are different contracts, not untidy copies.
    """
    if value in (None, ""):
        return None
    parsed = value if isinstance(value, datetime) else None
    if parsed is None:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

"""Pure utilities for deterministic profit-gradient evidence compilation."""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Mapping

from aletheia.config.secrets import is_secret_key
from aletheia.storage.hashing import sha256_json

__all__ = [
    "_finite",
    "_hash",
    "_iso",
    "_json_value",
    "_parse_utc",
    "_redact",
    "_time",
    "_unavailable",
]


def _parse_utc(value: Any, *, field: str = "cutoff_at") -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{field} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _finite(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def _time(value: Any) -> datetime | None:
    try:
        return _parse_utc(value, field="timestamp") if value not in (None, "") else None
    except ValueError:
        return None


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if is_secret_key(key)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _json_value(value: Any) -> Any:
    """Make source values safe for deterministic, NaN-free hashing."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_value(item) for item in value)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"__nonfinite__": repr(value)}
    if isinstance(value, datetime):
        return _iso(value)
    return value


def _hash(value: Any) -> str:
    normalized = _json_value(value)
    if not isinstance(normalized, (dict, list)):
        normalized = {"value": normalized}
    return sha256_json(normalized)


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, **extra}

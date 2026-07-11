"""Durable, identity-aware round-robin ordering for bounded agent loops."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TypeVar

from aletheia.storage.sqlite import get_db

T = TypeVar("T")


def rotate_from_next(items: Sequence[T], *, next_key: str | None, key) -> list[T]:
    """Return unique items beginning at the persisted next item when present."""
    unique: list[T] = []
    seen: set[str] = set()
    for item in items:
        item_key = key(item)
        if item_key in seen:
            continue
        seen.add(item_key)
        unique.append(item)
    if not unique or not next_key:
        return unique
    start = next((i for i, item in enumerate(unique) if key(item) == next_key), 0)
    return unique[start:] + unique[:start]


class DurableRoundRobin:
    """Store only the next complete identity; changing pools remain deterministic."""

    def __init__(self, state_key: str) -> None:
        self._state_key = state_key

    def next_key(self) -> str | None:
        row = get_db().execute(
            "SELECT value FROM settings WHERE key = ?", (self._state_key,)
        ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
            return value.get("next_key") if isinstance(value.get("next_key"), str) else None
        except (json.JSONDecodeError, TypeError):
            return None

    def advance_after(self, ordered_items: Sequence[T], *, completed_key: str, key) -> None:
        """Persist the successor only after an item completed its handling attempt."""
        ordered = rotate_from_next(ordered_items, next_key=None, key=key)
        if not ordered:
            return
        index = next((i for i, item in enumerate(ordered) if key(item) == completed_key), None)
        if index is None:
            return
        payload = json.dumps({"version": 1, "next_key": key(ordered[(index + 1) % len(ordered)])})
        get_db().execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (self._state_key, payload),
        )

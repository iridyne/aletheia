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
    """Store durable forward and prefix cursors for changing identity pools."""

    def __init__(self, state_key: str) -> None:
        self._state_key = state_key

    def _state(self) -> dict[str, object]:
        row = get_db().execute(
            "SELECT value FROM settings WHERE key = ?", (self._state_key,)
        ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row["value"])
        except (json.JSONDecodeError, TypeError, AttributeError):
            return {}
        return value if isinstance(value, dict) else {}

    def next_key(self) -> str | None:
        value = self._state().get("next_key")
        return value if isinstance(value, str) else None

    def prefix_key(self) -> str | None:
        """Return the independent cursor for identities before ``next_key``."""
        value = self._state().get("prefix_key")
        return value if isinstance(value, str) else None

    def _write_state(self, state: dict[str, object]) -> None:
        state = dict(state)
        state["version"] = 2
        get_db().execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (self._state_key, json.dumps(state, sort_keys=True)),
        )

    def _advance_cursor(
        self,
        field: str,
        ordered_items: Sequence[T],
        *,
        completed_key: str,
        key,
    ) -> None:
        ordered = rotate_from_next(ordered_items, next_key=None, key=key)
        if not ordered:
            return
        index = next(
            (i for i, item in enumerate(ordered) if key(item) == completed_key),
            None,
        )
        if index is None:
            return
        state = self._state()
        state[field] = key(ordered[(index + 1) % len(ordered)])
        self._write_state(state)

    def advance_after(self, ordered_items: Sequence[T], *, completed_key: str, key) -> None:
        """Persist the forward successor after an item handling attempt."""
        self._advance_cursor(
            "next_key",
            ordered_items,
            completed_key=completed_key,
            key=key,
        )

    def advance_prefix_after(
        self,
        ordered_items: Sequence[T],
        *,
        completed_key: str,
        key,
    ) -> None:
        """Persist the successor in the independent pre-cursor scan."""
        self._advance_cursor(
            "prefix_key",
            ordered_items,
            completed_key=completed_key,
            key=key,
        )

    def reset_prefix_key(self) -> None:
        """Restart the bounded prefix scan after its current range is exhausted."""
        state = self._state()
        if state.get("prefix_key") is None:
            return
        state["prefix_key"] = None
        self._write_state(state)

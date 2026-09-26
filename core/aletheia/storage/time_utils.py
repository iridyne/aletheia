"""Shared time utilities — single owner for utcnow_iso() / utcnow().

Having 18+ per-module copies of `def _utcnow() -> str: return datetime.now(...).isoformat()`
meant any caller that needed to test time-dependent behavior was patching a
different name. All callers now import from here; tests only need to patch
`aletheia.storage.time_utils.utcnow_iso` (or the module-level reference in
the caller, using the standard `monkeypatch.setattr(module, "utcnow_iso", ...)`)
instead of a different private name per file.

Usage::

    from aletheia.storage.time_utils import utcnow_iso, utcnow

    ts = utcnow_iso()          # "2026-09-26T10:00:00.123456+00:00"
    dt = utcnow()              # datetime(2026, 9, 26, tzinfo=timezone.utc)
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso() -> str:
    """Return the current UTC time as a timezone-aware ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)

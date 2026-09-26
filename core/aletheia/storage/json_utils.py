"""Shared canonical-JSON helpers — single owner for `json.dumps(sort_keys=True, ...)`.

Having 9 per-module copies of the same sorted-keys JSON serialiser meant
callers drifted: one used `default=str`, another added `allow_nan=False`,
a third had neither. This module provides one place with explicit variants
so callers pick the semantics they actually need.

Usage::

    from aletheia.storage.json_utils import (
        canonical_json,        # no extra flags  (most uses)
        canonical_json_safe,   # default=str     (for mixed-type values)
        canonical_json_strict, # allow_nan=False  (for cryptographic hashes)
    )
"""

from __future__ import annotations

import json


def canonical_json(value: object) -> str:
    """Sorted-keys compact JSON. No NaN guard, no str coercion.

    Use when the caller controls value types and NaN is impossible.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def canonical_json_safe(value: object) -> str:
    """Sorted-keys compact JSON with ``default=str``.

    Use when the value may contain non-serialisable objects (datetime,
    Decimal, custom dataclasses) and you want them stringified rather than
    raising.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def canonical_json_strict(value: object) -> str:
    """Sorted-keys compact JSON with ``allow_nan=False``.

    Use for cryptographic hashes or fingerprints where a NaN or Infinity
    should raise rather than produce a non-standard JSON string like
    ``NaN``. This is the behaviour required for `twin_persistence` and
    `backup/verify`.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

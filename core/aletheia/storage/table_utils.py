"""Shared SQLite table-inspection helper.

Having 8 per-module copies of the ``_table_exists`` one-liner meant each
analytics file wrote its own (sometimes with a try/except wrapper,
sometimes without). All callers now import from here.

Usage::

    from aletheia.storage.table_utils import table_exists

    if table_exists(db, "my_table"):
        ...
"""

from __future__ import annotations

from typing import Any


def table_exists(db: Any, table: str) -> bool:
    """Return True if *table* exists in the SQLite database *db*.

    Returns False if *db* is None (some callers pass an optional
    connection that may not yet be open).
    """
    if db is None:
        return False
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None

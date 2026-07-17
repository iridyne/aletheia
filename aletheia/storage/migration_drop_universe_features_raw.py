"""Phase 3 retention migration: drop the always-empty `raw` column.

`universe_feature_snapshots.raw` was declared as `TEXT DEFAULT '{}'` and no
writer has ever set a non-default value — every row carries the exact string
`'{}'`. On a table with millions of rows this becomes measurable dead-weight
bytes; dropping the column reclaims disk and eliminates the pretense that
per-row raw payload might exist.

Requires SQLite 3.35+ (`ALTER TABLE ... DROP COLUMN`). Idempotent: checks
`PRAGMA table_info` first, so a second invocation is a fast no-op.

This module lives as a flat file rather than under
`aletheia/storage/migrations/NNNN_*.py` because a `migrations/` subpackage
would shadow the existing `aletheia/storage/migrations.py` module and break
every consumer. The spec-recommended per-file migration numbering scheme is
not compatible with the current codebase layout; documented as spec_deviation.

Not wired into `run_migrations()` — Phase 4 backfill invokes this explicitly
so the schema change is coupled with the one-shot backfill + VACUUM step.
"""

from __future__ import annotations


def run(db=None) -> bool:
    """Drop the `raw` column from `universe_feature_snapshots` if present.

    Args:
        db: optional sqlite3 connection. Defaults to the current process-wide
            connection via `aletheia.storage.sqlite.get_db()`.

    Returns:
        True if the column was dropped by this invocation, False if it was
        already absent (or the table itself does not exist yet).
    """
    if db is None:
        from aletheia.storage.sqlite import get_db
        db = get_db()

    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='universe_feature_snapshots'"
    ).fetchone()
    if not exists:
        return False

    columns = {row["name"] for row in db.execute(
        "PRAGMA table_info(universe_feature_snapshots)"
    ).fetchall()}
    if "raw" not in columns:
        return False

    was_in_tx = db.in_transaction
    db.execute("ALTER TABLE universe_feature_snapshots DROP COLUMN raw")
    if not was_in_tx:
        db.commit()
    return True

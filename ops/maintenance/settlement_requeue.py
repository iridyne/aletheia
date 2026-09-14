"""Explicit historical observation requeue; no scheduler or trading authority."""

import json
from typing import Any

import typer

from aletheia.maintenance.settlement_requeue import _TABLES, _UNOBSERVED


def requeue_unobserved_failed_outcomes(*, dry_run: bool = True) -> dict[str, Any]:
    """Count, or restore, every failed row that never carried an observation.

    Args:
        dry_run: default. Reports the counts it would reclaim and writes nothing.

    Returns:
        One count per table plus ``dry_run`` and ``total``. In apply mode the
        counts are rows actually updated.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    db = get_db()
    result: dict[str, Any] = {"dry_run": dry_run}

    planned = {
        table: int(
            db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {_UNOBSERVED}"
            ).fetchone()["n"]
        )
        for table in _TABLES
    }
    if dry_run:
        result.update(planned)
        result["total"] = sum(planned.values())
        return result

    # One transaction over three small UPDATEs: the point of the repair is that the
    # queues come back consistent with each other, and a partial reclaim would leave
    # the canonical queue and its forward mirror disagreeing about which rows are
    # still owed a measurement. Via repo.transaction rather than a hand-rolled BEGIN
    # so the process-wide write lock is held for it, and so a caller that already
    # has a transaction open gets a savepoint instead of a nesting error.
    applied: dict[str, int] = {}
    with repo.transaction(immediate=True, savepoint=True):
        for table in _TABLES:
            cursor = db.execute(
                f"UPDATE {table} SET status = 'pending', settled_at = NULL "
                f"WHERE {_UNOBSERVED}"
            )
            applied[table] = int(cursor.rowcount or 0)

    result.update(applied)
    result["total"] = sum(applied.values())
    return result


def requeue_unobserved_settlements(
    apply: bool = typer.Option(
        False, "--apply",
        help="Return never-observed 'failed' rows to 'pending'. Default is dry-run.",
    ),
):
    """Reclaim settlement rows written off before any observation was taken.

    Targets exactly the rows whose status is 'failed' while both exit_price_usd and
    settlement_price_source are NULL — the signature of a terminal write that
    happened because the venue could not be reached, not because the row was
    unmeasurable. Resets status and settled_at only; never touches economics,
    deletes history, or reopens a row that carries a real verdict.
    """
    from aletheia.storage.migrations import assert_migrations_current

    assert_migrations_current()
    typer.echo(json.dumps(
        requeue_unobserved_failed_outcomes(dry_run=not apply),
        indent=2, ensure_ascii=False, sort_keys=True,
    ))

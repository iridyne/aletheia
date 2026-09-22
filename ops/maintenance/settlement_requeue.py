"""Reclaim settlement rows that were written off without ever being observed.

A settle loop that could not reach the venue used to mark its rows
``status='failed'``, a terminal state no job revisits. One Binance IP ban
(``418 -1003 ... IP banned until ...``) destroyed 335 counterfactual observations
that way in two cycles — every one with ``exit_price_usd IS NULL`` and
``settlement_price_source IS NULL``, while the 1m bars they would have been
measured from sat untouched in ``market_ohlcv``.

The write-off is now prevented at the source: an unreachable venue leaves the row
``pending`` (see ``universe.outcome_recorder.deferred_observation``). This module
is for the rows that were already destroyed before that landed. It is not a
scheduled job — it is a one-shot repair, safe to re-run.

The predicate is the whole design. ``status='failed'`` with BOTH
``exit_price_usd`` and ``settlement_price_source`` NULL is reachable only one way:
a terminal write that happened before any price arrived. Every other route to
``failed`` records what it saw —

* a rejected >10x observation carries the price it rejected
  (``repo.fail_forward_outcome`` and its siblings);
* ``fail_contaminated_forward_outcomes`` demotes rows that were already
  ``settled``, so their exit price is on the row.

— so this reclaims exactly the never-measured population and leaves every real
verdict alone. That is also what makes it terminate: a row it requeues either gets
measured, or gets failed again WITH the observation that failed it, and so is not
picked up a second time.

Never deletes history and never writes an economics column. It resets only
``status`` and ``settled_at``, returning the row to the state that means "still
owed an observation".
"""

from __future__ import annotations

from typing import Any


# Queues that share the one observation layer and therefore shared the defect.
_TABLES = (
    "filter_candidate_outcomes",
    "universe_forward_outcomes",
    "perp_structure_shadow_outcomes",
)

_UNOBSERVED = (
    "status = 'failed' "
    "AND exit_price_usd IS NULL "
    "AND settlement_price_source IS NULL"
)


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


def count_unobserved_failed_outcomes() -> dict[str, int]:
    """Per-table count of never-observed write-offs, for doctor and probes.

    A non-zero count is not self-healing: these rows are terminal until this
    module reclaims them.
    """
    from aletheia.storage.sqlite import get_db

    db = get_db()
    counts: dict[str, int] = {}
    for table in _TABLES:
        try:
            counts[table] = int(
                db.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE {_UNOBSERVED}"
                ).fetchone()["n"]
            )
        except Exception:
            counts[table] = 0
    return counts

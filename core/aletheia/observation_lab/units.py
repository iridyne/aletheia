"""ObservationUnit repository — sole writer of ``filter_shadow_frozen_units``.

Rows are immutable once inserted (INSERT OR IGNORE + UNIQUE key). Idempotent
sync is what lets the Lab restart safely and re-attempt captures without
duplicating cohort members.
"""

from __future__ import annotations

from typing import Any

from aletheia.observation_lab.db import get_lab_db


def insert_frozen_units(experiment_id: str, rows: list[dict[str, Any]]) -> int:
    """Idempotently freeze first-exposure units for one Observation.

    Returns the count that were newly inserted. Duplicates on
    ``(experiment_id, outcome_candidate_key)`` are silently skipped.
    """
    if not rows:
        return 0
    db = get_lab_db()
    inserted = 0
    for row in rows:
        payload = {"experiment_id": experiment_id, **row}
        cols = ", ".join(payload.keys())
        placeholders = ", ".join(["?"] * len(payload))
        cursor = db.execute(
            f"INSERT OR IGNORE INTO filter_shadow_frozen_units ({cols}) "
            f"VALUES ({placeholders})",
            list(payload.values()),
        )
        inserted += cursor.rowcount or 0
    return inserted


def list_frozen_units(
    experiment_id: str, *, decision: str | None = None
) -> list[dict[str, Any]]:
    where = "experiment_id = ?"
    params: list[Any] = [experiment_id]
    if decision is not None:
        where += " AND decision = ?"
        params.append(decision)
    rows = get_lab_db().execute(
        f"SELECT * FROM filter_shadow_frozen_units WHERE {where} "
        "ORDER BY first_evaluated_at, outcome_candidate_key",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def count_frozen_units(experiment_id: str) -> int:
    row = get_lab_db().execute(
        "SELECT COUNT(*) AS n FROM filter_shadow_frozen_units WHERE experiment_id=?",
        (experiment_id,),
    ).fetchone()
    return int(row["n"] if row else 0)

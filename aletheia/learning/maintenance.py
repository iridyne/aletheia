"""Bounded owner maintenance for historically unmeasurable experiments."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from aletheia.learning.registry import LEARNING_PATHS


def _unmeasurable_open_experiments() -> list[dict[str, Any]]:
    from aletheia.storage.sqlite import get_db

    supported = {
        path.path_id for path in LEARNING_PATHS if path.measurement_supported
    }
    rows = get_db().execute(
        "SELECT e.experiment_id, e.hypothesis_id, e.stage, h.path_id "
        "FROM learning_experiments e "
        "JOIN learning_hypotheses h ON h.hypothesis_id=e.hypothesis_id "
        "WHERE e.ended_at IS NULL ORDER BY h.path_id, e.experiment_id"
    ).fetchall()
    return [dict(row) for row in rows if row["path_id"] not in supported]


def _summary(rows: list[dict[str, Any]], *, dry_run: bool) -> dict[str, Any]:
    paths = Counter(str(row["path_id"]) for row in rows)
    return {
        "dry_run": dry_run,
        "experiments": len(rows),
        "hypotheses": len({str(row["hypothesis_id"]) for row in rows}),
        "paths": dict(sorted(paths.items())),
    }


def archive_unmeasurable_experiments(*, dry_run: bool = True) -> dict[str, Any]:
    """Soft-terminalize open experiments whose paths have no measurement adapter.

    The operation preserves every historical row and adds one lifecycle event per
    archived experiment. It defaults to a read-only report and is safe to re-run.
    """
    rows = _unmeasurable_open_experiments()
    if dry_run or not rows:
        return _summary(rows, dry_run=dry_run)

    from aletheia.learning.ledger import insert_lifecycle_event
    from aletheia.learning.models import LearningLifecycleEvent
    from aletheia.storage.repositories import transaction
    from aletheia.storage.sqlite import get_db

    now = datetime.now(timezone.utc).isoformat()
    lifecycle_event_ids: list[str] = []
    with transaction(immediate=True, savepoint=True):
        rows = _unmeasurable_open_experiments()
        db = get_db()
        for row in rows:
            event = LearningLifecycleEvent(
                dedup_key=(
                    "owner_maintenance:archive_unmeasurable:"
                    f"{row['experiment_id']}"
                ),
                hypothesis_id=str(row["hypothesis_id"]),
                experiment_id=str(row["experiment_id"]),
                from_stage=str(row["stage"]),
                action="archive_unmeasurable",
                reason="owner_maintenance:measurement_unsupported",
                audit_actor="human",
                audit_source="cli",
                created_at=now,
            )
            if insert_lifecycle_event(event):
                lifecycle_event_ids.append(event.event_id)
            db.execute(
                "UPDATE learning_experiments "
                "SET status='archived', ended_at=? "
                "WHERE experiment_id=? AND ended_at IS NULL",
                (now, row["experiment_id"]),
            )
            db.execute(
                "UPDATE learning_hypotheses "
                "SET status='archived', updated_at=? WHERE hypothesis_id=?",
                (now, row["hypothesis_id"]),
            )
    from aletheia.learning.lifecycle import reconcile_lifecycle_audits

    reconcile_lifecycle_audits(
        limit=min(max(len(lifecycle_event_ids), 1), 500),
        event_ids=lifecycle_event_ids,
    )
    return _summary(rows, dry_run=False)

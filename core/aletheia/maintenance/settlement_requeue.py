"""Read-only diagnostics and shared selection for unobserved settlement rows.

The explicitly invoked Ops repair owns requeue writes, not this Core reader.
"""

from __future__ import annotations

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


def count_unobserved_failed_outcomes() -> dict[str, int]:
    """Per-table count of never-observed write-offs, for doctor and probes.

    A non-zero count is not self-healing: these rows are terminal until this
    Ops repair reclaims them.
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

"""Exact, expand-only identity linking for runtime cost events."""

from __future__ import annotations

from typing import Any

from aletheia.economics.schema import ensure_economics_schema


def link_evaluation_cost_events(
    evaluation_id: str,
    *,
    opportunity_id: str | None = None,
    decision_id: str | None = None,
    proposal_id: str | None = None,
) -> int:
    """Attach durable entities to one exact evaluation's cost events.

    ``evaluation_id`` is bound before the first model call. The update only fills
    NULL identity columns on events carrying that exact id in structured metadata;
    it never uses time proximity, symbols, or stack inspection.
    """
    if not evaluation_id:
        return 0
    sets: list[str] = []
    params: list[Any] = []
    for column, value in (
        ("opportunity_id", opportunity_id),
        ("decision_id", decision_id),
        ("proposal_id", proposal_id),
    ):
        if value:
            sets.append(f"{column}=COALESCE({column}, ?)")
            params.append(str(value))
    if not sets:
        return 0
    try:
        db = ensure_economics_schema()
        params.append(str(evaluation_id))
        cur = db.execute(
            "UPDATE runtime_cost_events SET " + ",".join(sets)
            + " WHERE json_valid(metadata_json) "
            + "AND json_extract(metadata_json,'$.evaluation_id')=?",
            params,
        )
        return int(cur.rowcount or 0)
    except Exception:
        # Measurement linking cannot alter or block a decision lifecycle.
        return 0

"""Best-effort secondary audit for already-durable execution state."""

from __future__ import annotations

import logging
from collections.abc import Callable

from aletheia.files import jsonl

logger = logging.getLogger("aletheia.execution.post_commit_audit")


def write_post_commit_audit(
    event: dict,
    *,
    sink: Callable[[dict], object] | None = None,
) -> bool:
    """Write a secondary audit without invalidating committed trading truth.

    This helper is only for call sites where the Order/Position/Proposal state
    described by ``event`` is already durable. It deliberately catches only
    the audit sink call; DB and exchange operations must remain outside it and
    keep their original failure semantics.
    """
    audit_sink = sink or jsonl.write_audit
    try:
        audit_sink(event)
    except Exception as exc:
        after = event.get("after")
        persisted_status = after.get("status") if isinstance(after, dict) else None
        logger.warning(
            "Post-commit audit sink failed for %s %s status=%s (%s)",
            event.get("entity_type") or "entity",
            event.get("entity_id") or "unknown",
            persisted_status or "unknown",
            type(exc).__name__,
        )
        return False
    return True

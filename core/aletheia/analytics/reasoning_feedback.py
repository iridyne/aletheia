"""Validated persisted reasoning-feedback reads and optional producer binding."""
from __future__ import annotations

import json
import logging
from datetime import timedelta
import aletheia.analytics.execution_mode as _execution_mode
from aletheia.analytics.outcome_integrity import is_current_outcome_artifact

logger = logging.getLogger("aletheia.analytics.reasoning_feedback")
MAX_ACTIVE_FEEDBACK_AGE = timedelta(hours=24)
_feedback_builder = None


def configure_feedback_builder(builder) -> None:
    global _feedback_builder
    _feedback_builder = builder


def get_latest_reasoning_feedback(
    *, execution_mode: str | None = None,
) -> dict | None:
    """Read most-recent reasoning feedback for the selected execution mode.

    Only returns a persisted artifact whose ``execution_mode`` matches the
    resolved (or provided) selected mode (#1939).  Legacy mode-less artifacts
    are ignored so a mode switch cannot silently reuse the other cohort's
    Learning feedback.
    """
    if execution_mode is None:
        execution_mode = _execution_mode.resolve_selected_execution_mode()
    if not execution_mode:
        return None
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(item_type="reasoning_feedback", limit=5)
        for item in items:
            content = item.get("content")
            if not content:
                continue
            try:
                feedback = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            if feedback.get("execution_mode") != execution_mode:
                continue
            if feedback.get("cohort_complete") is not True or not feedback.get(
                "ordered_source_digest"
            ):
                continue
            if is_current_outcome_artifact(
                feedback, max_age=MAX_ACTIVE_FEEDBACK_AGE,
            ):
                return feedback
        return None
    except Exception as e:
        logger.debug("get_latest_reasoning_feedback failed: %s", e)
        return None

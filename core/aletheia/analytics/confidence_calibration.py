"""Persisted calibration reads, scorecard semantics and optional producer binding."""
from __future__ import annotations

import json
import logging
import math
from datetime import timedelta

import aletheia.analytics.execution_mode as _execution_mode
from aletheia.analytics.outcome_integrity import (
    is_current_outcome_artifact,
)

logger = logging.getLogger("aletheia.analytics.confidence_calibration")

MIN_CALIBRATION_SAMPLE = 5
MAX_ACTIVE_REPORT_AGE = timedelta(hours=24)

_calibration_builder = None


def configure_calibration_builder(builder) -> None:
    global _calibration_builder
    _calibration_builder = builder


def get_latest_calibration_report(
    *, execution_mode: str | None = None,
) -> dict | None:
    """Read most-recent calibration report for the selected execution mode.

    A persisted report is only returned when its ``execution_mode`` matches
    the resolved (or provided) selected mode (#1939).  Legacy mode-less
    reports are ignored so a mode switch cannot reuse the previous cohort's
    calibration under the new selected-mode identity.
    """
    if execution_mode is None:
        execution_mode = _execution_mode.resolve_selected_execution_mode()
    if not execution_mode:
        return None
    try:
        from aletheia.storage.repositories import list_knowledge_items
        items = list_knowledge_items(item_type="calibration_report", limit=5)
        for item in items:
            content = item.get("content")
            if not content:
                continue
            try:
                report = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                continue
            if report.get("execution_mode") != execution_mode:
                continue
            # Reports written before the explicit state field cannot distinguish
            # perfect ECE from the old no-reliable-bucket sentinel (ECE=0).
            if (
                report.get("ece") == 0
                and "calibration_state" not in report
                and "reliable_sample_count" not in report
            ):
                continue
            if is_current_outcome_artifact(
                report, max_age=MAX_ACTIVE_REPORT_AGE,
            ):
                return report
        return None
    except Exception as e:
        logger.debug("get_latest_calibration_report failed: %s", e)
        return None




def compute_calibration_score() -> float | None:
    """Return 0-100 score for go-live scorecard. 100=perfect, degrades with ECE."""
    report = get_latest_calibration_report()
    if not report:
        return None
    total = report.get("total_executed", 0)
    if total < MIN_CALIBRATION_SAMPLE:
        return None
    state = report.get("calibration_state")
    if state is not None and state != "available":
        return None
    reliable_n = report.get("reliable_sample_count")
    if reliable_n is not None:
        try:
            if int(reliable_n) <= 0:
                return None
        except (TypeError, ValueError):
            return None
    ece = report.get("ece")
    try:
        ece = float(ece)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ece):
        return None
    # A legacy zero without coverage metadata is ambiguous and must not grant
    # readiness points; non-zero legacy reports remain backward-compatible.
    if state is None and reliable_n is None and ece == 0.0:
        return None
    # ECE=0 → 100pts, ECE=0.20 → 60pts, ECE=0.50 → 0pts
    score = max(0.0, 100.0 - ece * 200.0)
    return round(min(100.0, score), 1)

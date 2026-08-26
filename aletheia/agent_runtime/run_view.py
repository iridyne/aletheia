from __future__ import annotations

import json


FAILED_AGENT_RUN_STATUSES = frozenset(
    {
        "failed",
        "error",
        "stale",
        "timed_out",
        "completed_with_errors",
        "rejected_queue_full",
        "cancelled",
    }
)
SUCCESS_AGENT_RUN_STATUSES = frozenset({"completed", "success"})


def parse_run_errors(raw: object) -> list[str]:
    if raw in (None, "", []):
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    try:
        decoded = json.loads(str(raw))
    except (TypeError, ValueError):
        return [str(raw).strip()] if str(raw).strip() else []
    if isinstance(decoded, list):
        return [str(item).strip() for item in decoded if str(item).strip()]
    if decoded is None:
        return []
    text = str(decoded).strip()
    return [text] if text else []


def summarize_run_errors(raw: object) -> str:
    return "; ".join(parse_run_errors(raw))


def is_failed_agent_run(status: object, errors: object = None) -> bool:
    normalized = str(status or "").strip().lower()
    if normalized in FAILED_AGENT_RUN_STATUSES:
        return True
    return normalized in SUCCESS_AGENT_RUN_STATUSES and bool(parse_run_errors(errors))


def finished_at(row: dict) -> str | None:
    return row.get("ended_at") or row.get("finished_at") or row.get("completed_at")

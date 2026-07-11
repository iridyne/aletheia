from __future__ import annotations

import json


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


def finished_at(row: dict) -> str | None:
    return row.get("ended_at") or row.get("finished_at") or row.get("completed_at")

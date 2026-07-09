"""Read-only candidate supply diagnostics.

This module only summarizes already-built executable/excluded candidate views
and evaluates recent supply snapshots. It does not run rankers, guards, LLMs,
risk, proposals, or execution.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

CANDIDATE_SUPPLY_FLOOR = 3
CANDIDATE_SUPPLY_REQUIRED_CYCLES = 2
EVENT_TYPE = "candidate_supply_snapshot"


def build_candidate_supply_snapshot(
    *,
    executable_candidates: list[dict[str, Any]] | None,
    excluded_candidates_summary: list[dict[str, Any]] | None,
    floor: int = CANDIDATE_SUPPLY_FLOOR,
    required_cycles: int = CANDIDATE_SUPPLY_REQUIRED_CYCLES,
) -> dict[str, Any]:
    executable = executable_candidates or []
    excluded = excluded_candidates_summary or []
    executable_count = len(executable)
    main_reason = _main_reason(excluded, executable_count)
    return {
        "executable_count": executable_count,
        "floor": max(1, int(floor or CANDIDATE_SUPPLY_FLOOR)),
        "below_floor": executable_count < max(1, int(floor or CANDIDATE_SUPPLY_FLOOR)),
        "required_consecutive_cycles": max(1, int(required_cycles or CANDIDATE_SUPPLY_REQUIRED_CYCLES)),
        "main_reason": main_reason,
        "excluded_candidates_summary": excluded,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def evaluate_candidate_supply_drought(
    events: list[dict[str, Any]] | None,
    *,
    floor: int = CANDIDATE_SUPPLY_FLOOR,
    required_cycles: int = CANDIDATE_SUPPLY_REQUIRED_CYCLES,
) -> dict[str, Any] | None:
    snapshots = _candidate_supply_snapshots(events or [])
    required = max(1, int(required_cycles or CANDIDATE_SUPPLY_REQUIRED_CYCLES))
    floor_i = max(1, int(floor or CANDIDATE_SUPPLY_FLOOR))
    if len(snapshots) < required:
        return None
    latest = snapshots[:required]
    if not all(int((item.get("payload") or {}).get("executable_count") or 0) < floor_i for item in latest):
        return None
    payloads = [item.get("payload") or {} for item in latest]
    main_reason = _dominant_recent_reason(payloads)
    newest = payloads[0]
    return {
        "event_type": "candidate_supply_drought",
        "executable_count": int(newest.get("executable_count") or 0),
        "floor": floor_i,
        "consecutive_cycles": required,
        "main_reason": main_reason,
        "latest_snapshot_event_id": snapshots[0].get("id"),
        "recent_snapshot_event_ids": [item.get("id") for item in latest if item.get("id")],
        "excluded_candidates_summary": newest.get("excluded_candidates_summary") or [],
    }


def latest_candidate_supply_snapshot(events: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    snapshots = _candidate_supply_snapshots(events or [])
    if not snapshots:
        return None
    return snapshots[0].get("payload") or {}


def _candidate_supply_snapshots(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        event for event in events
        if str(event.get("event_type") or "") == EVENT_TYPE
        and isinstance(event.get("payload"), dict)
    ]
    rows.sort(key=lambda event: str(event.get("created_at") or ""), reverse=True)
    return rows


def _main_reason(excluded: list[dict[str, Any]], executable_count: int) -> str:
    if not excluded:
        return "no_candidates" if executable_count <= 0 else "insufficient_executable_candidates"
    best = max(
        excluded,
        key=lambda row: int(row.get("count") or 0),
    )
    return str(best.get("reason") or "unknown")


def _dominant_recent_reason(payloads: list[dict[str, Any]]) -> str:
    counter: Counter[str] = Counter()
    for payload in payloads:
        reason = str(payload.get("main_reason") or "")
        if reason:
            counter[reason] += 1
    if counter:
        return counter.most_common(1)[0][0]
    return "unknown"

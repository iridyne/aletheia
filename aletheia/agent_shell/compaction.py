"""Operator session compaction into daily markdown summaries."""

from __future__ import annotations

from pathlib import Path

from aletheia.agent_shell.operator_recommendations import build_recommendations
from aletheia.agent_shell.operator_store import list_events, list_messages, list_sessions, list_tasks
from aletheia.config.loader import get_data_dir


def _summary_dir() -> Path:
    path = get_data_dir() / "knowledge" / "operator_memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _render_lines(items: list[str], empty_text: str) -> list[str]:
    if not items:
        return [f"- {empty_text}"]
    return [f"- {item}" for item in items]


def _event_summary(event: dict) -> str:
    payload = event.get("payload") or {}
    if isinstance(payload, dict):
        for key in ("summary", "message", "issue", "input", "response"):
            value = payload.get(key)
            if value:
                return str(value)
    related = ""
    if event.get("related_entity_type") and event.get("related_entity_id"):
        related = f" ({event['related_entity_type']}:{event['related_entity_id']})"
    return f"{event['event_type']}{related}"


def _build_sections(
    *,
    user_instructions: list[str],
    repair_items: list[str],
    proposal_items: list[str],
    strategy_items: list[str],
    unresolved: list[str],
    recommendations: list[str],
) -> dict[str, list[str]]:
    return {
        "major_user_instructions": user_instructions,
        "important_repair_decisions": repair_items,
        "proposal_outcomes": proposal_items,
        "strategy_state_changes": strategy_items,
        "unresolved_issues": unresolved,
        "follow_up_recommendations": recommendations,
    }


def _build_summary_excerpt(sections: dict[str, list[str]]) -> str:
    parts: list[str] = []
    major = sections["major_user_instructions"]
    follow_up = sections["follow_up_recommendations"]
    unresolved = sections["unresolved_issues"]
    proposals = sections["proposal_outcomes"]

    if major:
        parts.append(f"Recent compacted memory: user said {major[0]}")
    if unresolved:
        parts.append(f"Open issue: {unresolved[0]}")
    if proposals:
        parts.append(f"Latest proposal outcome: {proposals[0]}")
    if follow_up:
        parts.append(f"Follow-up recommendation: {follow_up[0]}")
    return " ".join(parts) if parts else "No significant operator memory captured in this daily summary."


def build_operator_daily_summary(*, date_str: str) -> dict:
    day_token = date_str.replace("-", "")
    path = _summary_dir() / f"daily_summary_{day_token}.md"

    sessions = [session for session in list_sessions(limit=500) if str(session.get("created_at", "")).startswith(date_str)]
    session_ids = [session["id"] for session in sessions]

    user_instructions: list[str] = []
    for session in sessions:
        user_input = session.get("user_input")
        if user_input and user_input != "[operator session]":
            user_instructions.append(str(user_input))
    for session_id in session_ids:
        for message in list_messages(session_id, limit=500):
            if not str(message.get("created_at", "")).startswith(date_str):
                continue
            if message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, dict) and content.get("text"):
                user_instructions.append(str(content["text"]))
            elif isinstance(content, str):
                user_instructions.append(content)

    events = [event for event in list_events(limit=500) if str(event.get("created_at", "")).startswith(date_str)]
    repair_items = [_event_summary(event) for event in events if str(event.get("event_type", "")).startswith("repair_")]
    proposal_items = [_event_summary(event) for event in events if str(event.get("event_type", "")).startswith("proposal_")]
    strategy_items = [_event_summary(event) for event in events if str(event.get("event_type", "")).startswith("strategy_")]

    unresolved = []
    for task in list_tasks(limit=500):
        if not str(task.get("created_at", "")).startswith(date_str):
            continue
        if task.get("status") in {"failed", "running"}:
            issue = task.get("error") or task.get("task_type")
            unresolved.append(str(issue))
    unresolved.extend(
        _event_summary(event)
        for event in events
        if event.get("event_type") in {"command_rejected", "operator_degraded", "heartbeat_stale"}
    )

    recommendations = [item["summary"] for item in build_recommendations(limit=10)]
    sections = _build_sections(
        user_instructions=user_instructions,
        repair_items=repair_items,
        proposal_items=proposal_items,
        strategy_items=strategy_items,
        unresolved=unresolved,
        recommendations=recommendations,
    )
    summary_excerpt = _build_summary_excerpt(sections)

    lines = [
        f"# Operator Daily Summary {date_str}",
        "",
        "## Major User Instructions",
        *_render_lines(user_instructions, "No major user instructions captured."),
        "",
        "## Important Repair Decisions",
        *_render_lines(repair_items, "No repair decisions captured."),
        "",
        "## Proposal Outcomes",
        *_render_lines(proposal_items, "No proposal outcomes captured."),
        "",
        "## Strategy-State Changes",
        *_render_lines(strategy_items, "No strategy-state changes captured."),
        "",
        "## Unresolved Issues",
        *_render_lines(unresolved, "No unresolved issues captured."),
        "",
        "## Follow-Up Recommendations",
        *_render_lines(recommendations, "No follow-up recommendations right now."),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "date": date_str,
        "path": path,
        "summary_excerpt": summary_excerpt,
        "sections": sections,
    }

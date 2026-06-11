"""Inspectable file artifacts for operator working memory."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from aletheia.config.loader import get_data_dir


def _safe_name(value: str, default: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-")
    return text or default


def _root_dir() -> Path:
    root = get_data_dir() / "agent_shell"
    for child in ("sessions", "tasks", "todos", "scratch", "extensions"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def session_log_path(session_id: str) -> Path:
    return _root_dir() / "sessions" / f"{_safe_name(session_id, 'session')}.jsonl"


def task_note_path(task_id: str) -> Path:
    return _root_dir() / "tasks" / f"{_safe_name(task_id, 'task')}.md"


def todo_note_path(todo_id: str, title: str) -> Path:
    slug = _safe_name(title, "todo")
    return _root_dir() / "todos" / f"{_safe_name(todo_id, 'todo')}-{slug}.md"


def scratch_note_path(note_id: str, title: str) -> Path:
    slug = _safe_name(title, "scratch")
    return _root_dir() / "scratch" / f"{_safe_name(note_id, 'scratch')}-{slug}.md"


def extension_manifest_path(extension_id: str, slash_command: str) -> Path:
    slug = _safe_name((slash_command or "").lstrip("/"), "extension")
    return _root_dir() / "extensions" / f"{_safe_name(extension_id, 'extension')}-{slug}.json"


def _reports_root_dir() -> Path:
    root = get_data_dir() / "reports"
    for child in ("daily", "post_trade", "system_health", "trade_review", "risk", "agent_performance"):
        (root / child).mkdir(parents=True, exist_ok=True)
    return root


def report_note_path(report_dir: str, title: str) -> Path:
    base = _reports_root_dir() / _safe_name(report_dir, "reports")
    base.mkdir(parents=True, exist_ok=True)
    slug = _safe_name(title, "report")
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return base / f"{timestamp}-{slug}.md"


def write_operator_report(
    *,
    report_dir: str,
    title: str,
    body: str,
    metadata: dict[str, object] | None = None,
) -> Path:
    path = report_note_path(report_dir, title)
    lines = [f"# {title}", ""]
    for key, value in (metadata or {}).items():
        label = str(key).replace("_", " ").title()
        lines.append(f"- {label}: {value}")
    if metadata:
        lines.append("")
    lines.extend(
        [
            "## Details",
            body.strip() or "No details recorded.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def append_session_log(session_id: str, record: dict) -> Path:
    path = session_log_path(session_id)
    payload = dict(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        handle.write("\n")
    return path


def write_task_note(task: dict) -> Path:
    path = task_note_path(str(task.get("id") or "task"))
    lines = [
        f"# Operator Task {task.get('task_type') or path.stem}",
        "",
        f"- Task ID: {task.get('id') or '—'}",
        f"- Status: {task.get('status') or '—'}",
        f"- Priority: {task.get('priority') or '—'}",
        f"- Created By: {task.get('created_by') or '—'}",
        f"- Assigned To: {task.get('assigned_to') or '—'}",
        f"- Created At: {task.get('created_at') or '—'}",
        f"- Started At: {task.get('started_at') or '—'}",
        f"- Completed At: {task.get('completed_at') or '—'}",
        f"- Error: {task.get('error') or '—'}",
        "",
        "## Input Data",
        "```json",
        json.dumps(task.get("input_data") or {}, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Output Data",
        "```json",
        json.dumps(task.get("output_data"), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_diagnostic_todo(*, todo_id: str, title: str, content: str, knowledge_item_id: str | None = None) -> Path:
    path = todo_note_path(todo_id, title)
    lines = [
        f"# {title}",
        "",
        f"- Todo ID: {todo_id}",
        f"- Knowledge Item ID: {knowledge_item_id or '—'}",
        "",
        "## Details",
        content.strip() or title.strip(),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_scratch_note(
    *,
    note_id: str,
    title: str,
    body: str,
    metadata: dict[str, object] | None = None,
) -> Path:
    path = scratch_note_path(note_id, title)
    lines = [f"# {title}", ""]
    for key, value in (metadata or {}).items():
        label = str(key).replace("_", " ").title()
        lines.append(f"- {label}: {value}")
    if metadata:
        lines.append("")
    lines.extend(
        [
            "## Details",
            body.strip() or "No details recorded.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_extension_manifest(extension: dict) -> Path:
    manifest = extension.get("manifest")
    manifest = dict(manifest) if isinstance(manifest, dict) else {}
    payload = {
        **manifest,
        "id": extension.get("id"),
        "session_id": extension.get("session_id"),
        "extension_name": extension.get("extension_name"),
        "slash_command": extension.get("slash_command"),
        "description": extension.get("description"),
        "target_command": extension.get("target_command"),
        "status": extension.get("status"),
        "created_by": extension.get("created_by"),
        "proposal_artifact_id": extension.get("proposal_artifact_id"),
        "latest_run_id": extension.get("latest_run_id"),
        "enabled_at": extension.get("enabled_at"),
        "enabled_by": extension.get("enabled_by"),
        "created_at": extension.get("created_at"),
        "updated_at": extension.get("updated_at"),
    }
    path = extension_manifest_path(
        str(extension.get("id") or "extension"),
        str(extension.get("slash_command") or extension.get("extension_name") or "extension"),
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path

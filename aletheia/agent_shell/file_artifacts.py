"""Inspectable file artifacts for operator working memory."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from aletheia.config.loader import get_data_dir

logger = logging.getLogger("aletheia.agent_shell.file_artifacts")


# Task types whose per-task .md note is agent-consumed telemetry, not human
# reference material. Skipping the file write for these avoids ~200 idle .md
# files/day that nothing reads. Failed / cancelled tasks are ALWAYS written
# regardless of type — those are the ones an operator actually reviews.
SKIP_TASK_NOTE_TYPES: frozenset[str] = frozenset(
    {
        "evaluate_strategy_state",
        "generate_review",
        "create_review_report",
        "diagnose_system_health",
        "set_symbol_observe_only",
    }
)

# Session log stems that must never be reaped (they are semantically persistent
# even when idle). Kept for the deprecation wrapper's default plus as an
# explicit exempt set used by the DB-JOIN pruner.
_PERSISTENT_SESSION_STEMS: frozenset[str] = frozenset({"operator-main"})

# Session kinds that, if referenced by a heartbeat's ``active_branch_id`` for
# their agent, must remain on disk regardless of the session row's age. These
# are the long-lived operator branches whose transcript is user-facing.
_PROTECTED_ACTIVE_BRANCH_KINDS: frozenset[str] = frozenset(
    {"review_session", "strategy_session", "diagnostic_session"}
)


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


def _sessions_dir() -> Path:
    return _root_dir() / "sessions"


def _tasks_dir() -> Path:
    return _root_dir() / "tasks"


def _fetch_session_prune_candidates(
    days: int,
    extra_exempt_stems: frozenset[str] | set[str] | None = None,
) -> tuple[set[str], set[str], frozenset[str]]:
    """Return (eligible_ids, all_session_ids, exempt_stems) from agent_shell_sessions.

    ``eligible_ids`` = sessions older than ``days`` and NOT exempt.
    ``all_session_ids`` = every id currently in agent_shell_sessions.
    ``exempt_stems`` = union of the persistent-stem set with any caller-supplied
    ``extra_exempt_stems`` (already ``_safe_name``-normalized). Callers use this
    to skip both aged-eligible and orphan deletion for stems the operator has
    explicitly pinned (e.g. ``cfg.operator.main_session_id``).

    Exemptions:
      - session id whose stem matches a protected persistent stem
        (e.g. ``operator-main``) or is in ``extra_exempt_stems``.
      - sessions with ``kind`` in ``_PROTECTED_ACTIVE_BRANCH_KINDS`` that are
        the current ``active_branch_id`` on any agent_shell_heartbeats row.
      - sessions that are the ``parent_session_id`` of a strictly younger
        session (its child is still in-window, so keep the ancestor's log
        for cross-reference).
    """
    from aletheia.storage.sqlite import get_db

    normalized_extra: frozenset[str] = frozenset(
        _safe_name(s, "session") for s in (extra_exempt_stems or ())
    )
    exempt_stems: frozenset[str] = _PERSISTENT_SESSION_STEMS | normalized_extra

    db = get_db()
    all_rows = db.execute("SELECT id FROM agent_shell_sessions").fetchall()
    all_ids = {str(r["id"]) for r in all_rows if r["id"]}

    cutoff_expr = f"datetime('now', '-{int(days)} days')"

    # Sessions the heartbeat table currently pins as an active branch and
    # whose kind we protect.
    active_branch_rows = db.execute(
        f"""
        SELECT DISTINCT s.id AS id
        FROM agent_shell_sessions s
        JOIN agent_shell_heartbeats h ON h.active_branch_id = s.id
        WHERE s.kind IN ({",".join("?" * len(_PROTECTED_ACTIVE_BRANCH_KINDS))})
        """,
        tuple(_PROTECTED_ACTIVE_BRANCH_KINDS),
    ).fetchall()
    active_branch_ids = {str(r["id"]) for r in active_branch_rows if r["id"]}

    # Sessions that are the parent of a strictly-younger session — keep them
    # so the child transcript still has its ancestor for context.
    parent_rows = db.execute(
        f"""
        SELECT DISTINCT p.id AS id
        FROM agent_shell_sessions p
        JOIN agent_shell_sessions c ON c.parent_session_id = p.id
        WHERE c.created_at > p.created_at
          AND c.created_at >= {cutoff_expr}
        """
    ).fetchall()
    parent_ids = {str(r["id"]) for r in parent_rows if r["id"]}

    exempt_ids = active_branch_ids | parent_ids

    old_rows = db.execute(
        f"SELECT id FROM agent_shell_sessions WHERE created_at < {cutoff_expr}"
    ).fetchall()
    eligible: set[str] = set()
    for row in old_rows:
        session_id = str(row["id"] or "")
        if not session_id:
            continue
        if session_id in exempt_ids:
            continue
        stem = _safe_name(session_id, "session")
        if stem in exempt_stems:
            continue
        eligible.add(session_id)
    return eligible, all_ids, exempt_stems


def prune_session_logs_db_join(
    days: int = 14,
    extra_exempt_stems: frozenset[str] | set[str] | tuple[str, ...] | None = None,
) -> int:
    """DB-JOIN prune of ``data/agent_shell/sessions/*.jsonl``.

    Rationale: the previous mtime-based sweep could reap files whose DB row was
    still live (any process rewriting the file bumps mtime, but conversely a
    session that had gone quiet for >days but whose row was still referenced —
    e.g. as an active branch — could vanish). Joining against
    ``agent_shell_sessions.created_at`` makes the on-disk retention match the
    DB retention exactly and adds an orphan sweep for files whose DB row is
    already gone. Fresh orphans (mtime within ``days``) are spared to avoid
    reaping a transcript whose DB row simply has not been committed yet —
    that race is common at session-open and would otherwise be user-visible.

    ``extra_exempt_stems`` is normalized through :func:`_safe_name` and unioned
    with the built-in persistent-stem set (``operator-main``) before any file
    is considered for deletion.

    Returns the number of files unlinked (aged + orphan combined).
    """
    sessions_dir = _sessions_dir()
    if not sessions_dir.is_dir():
        return 0

    eligible_ids, all_session_ids, exempt_stems = _fetch_session_prune_candidates(
        days, extra_exempt_stems
    )
    eligible_stems = {_safe_name(sid, "session") for sid in eligible_ids}
    valid_stems = {_safe_name(sid, "session") for sid in all_session_ids}
    orphan_cutoff = time.time() - int(days) * 86400

    deleted = 0
    for path in sessions_dir.glob("*.jsonl"):
        stem = path.stem
        if stem in exempt_stems:
            continue
        should_delete = False
        if stem in eligible_stems:
            should_delete = True
        elif stem not in valid_stems:
            # Orphan: no matching agent_shell_sessions row. Only reap when
            # the file itself is also older than the retention window so a
            # freshly-created transcript is not deleted before its DB row
            # is flushed.
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime < orphan_cutoff:
                should_delete = True
        if not should_delete:
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError as e:
            logger.debug("session log prune skipped %s: %s", path.name, e)
    return deleted


def prune_task_notes_db_join(
    days_completed: int = 7, days_failed: int = 30
) -> int:
    """DB-JOIN prune of ``data/agent_shell/tasks/*.md``.

    Reads ``agent_shell_tasks`` and unlinks the .md note for any task whose
    status is terminal (``completed`` / ``failed`` / ``cancelled``) and whose
    ``completed_at`` predates the per-status cutoff. Running / pending tasks —
    and tasks with a NULL ``completed_at`` — are never pruned. Also unlinks
    orphan files whose stem is not in the current task-id set.

    Returns the number of files unlinked (aged + orphan combined).
    """
    tasks_dir = _tasks_dir()
    if not tasks_dir.is_dir():
        return 0

    from aletheia.storage.sqlite import get_db

    db = get_db()
    all_rows = db.execute("SELECT id FROM agent_shell_tasks").fetchall()
    all_task_ids = {str(r["id"]) for r in all_rows if r["id"]}

    cutoff_completed = f"datetime('now', '-{int(days_completed)} days')"
    cutoff_failed = f"datetime('now', '-{int(days_failed)} days')"

    stale_rows = db.execute(
        f"""
        SELECT id FROM agent_shell_tasks
        WHERE completed_at IS NOT NULL
          AND (
                (status = 'completed' AND completed_at < {cutoff_completed})
             OR (status IN ('failed', 'cancelled') AND completed_at < {cutoff_failed})
          )
        """
    ).fetchall()
    stale_ids = {str(r["id"]) for r in stale_rows if r["id"]}
    stale_stems = {_safe_name(tid, "task") for tid in stale_ids}
    valid_stems = {_safe_name(tid, "task") for tid in all_task_ids}

    deleted = 0
    for path in tasks_dir.glob("*.md"):
        stem = path.stem
        should_delete = False
        if stem in stale_stems:
            should_delete = True
        elif stem not in valid_stems:
            should_delete = True
        if not should_delete:
            continue
        try:
            path.unlink()
            deleted += 1
        except OSError as e:
            logger.debug("task note prune skipped %s: %s", path.name, e)
    return deleted


def prune_session_logs(days: int = 14, keep: tuple[str, ...] = ("operator-main",)) -> int:
    """DEPRECATED — retained as a thin wrapper for legacy callers.

    Historical mtime-based sweep is replaced by :func:`prune_session_logs_db_join`
    which joins against ``agent_shell_sessions.created_at`` (and honours
    active-branch / parent-session exemptions). The ``keep`` tuple is forwarded
    as ``extra_exempt_stems`` so callers that pinned specific stems (e.g. the
    persistent operator-main transcript) continue to see those files preserved.
    """
    logger.warning(
        "prune_session_logs is deprecated; forwarding to prune_session_logs_db_join. "
        "keep=%r forwarded as extra_exempt_stems.",
        keep,
    )
    return prune_session_logs_db_join(days=days, extra_exempt_stems=tuple(keep))


def append_session_log(session_id: str, record: dict) -> Path:
    path = session_log_path(session_id)
    payload = dict(record)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str))
        handle.write("\n")
    return path


def write_task_note(task: dict) -> Path | None:
    """Write the operator-task .md note.

    Skips the write for task_types in :data:`SKIP_TASK_NOTE_TYPES` UNLESS the
    task is in a failed / cancelled terminal state — those are exactly the
    ones operators come back to review, so the file must exist regardless of
    type. Returns the written path, or ``None`` when the write was skipped.
    """
    task_type = str(task.get("task_type") or "").strip()
    status = str(task.get("status") or "").strip().lower()
    if task_type in SKIP_TASK_NOTE_TYPES and status not in ("failed", "cancelled"):
        return None
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

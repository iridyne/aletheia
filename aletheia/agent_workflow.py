"""Task Lite: machine-local development coordination for AI agents.

The workflow deliberately does not write task state, handoffs, test evidence, or
lease metadata into Git. Git/PR/CI remain authoritative for code and proof. Task
Lite only preserves enough local context to keep sibling agents and worktrees
from duplicating work.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_STATUSES = {"ready", "in_progress", "blocked", "done", "cancelled"}
_SECRET = re.compile(
    r"(?:api[_-]?key|secret|private[_-]?key|access[_-]?token|sk-[A-Za-z0-9]{16,})",
    re.I,
)
_RUNTIME_KEYS = {
    "gateway",
    "runtime",
    "database",
    "balance",
    "position",
    "credential",
    "pid",
}


@dataclass(frozen=True)
class Finding:
    severity: str
    message: str
    task_id: str | None = None


def repo_root(start: Path | None = None) -> Path:
    path = (start or Path.cwd()).resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".agent" / "project.yaml").is_file():
            return candidate
    raise FileNotFoundError(".agent/project.yaml not found; run from this repository")


def _yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return data


def _git(root: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode, (result.stdout or result.stderr).strip()


def _contains_forbidden(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            label = str(key).lower()
            full = f"{path}.{key}" if path else str(key)
            if _SECRET.search(label) or label in _RUNTIME_KEYS:
                findings.append(full)
            findings.extend(_contains_forbidden(nested, full))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            findings.extend(_contains_forbidden(nested, f"{path}[{index}]"))
    elif isinstance(value, str) and _SECRET.search(value):
        findings.append(path or "value")
    return findings


def _load_policy(root: Path) -> list[dict[str, Any]]:
    path = root / ".agent" / "policy.yaml"
    if not path.is_file():
        return []
    doc = _yaml(path)
    if doc.get("schema_version") != 1:
        raise ValueError("policy.yaml schema_version must be 1")
    entries = doc.get("trust_boundaries")
    if not isinstance(entries, list):
        raise ValueError("policy.yaml must contain a trust_boundaries list")
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise ValueError("each trust_boundaries entry needs an id")
        if not isinstance(entry.get("paths"), list) or not entry["paths"]:
            raise ValueError(f"trust_boundaries[{entry.get('id')}] needs paths")
        if not isinstance(entry.get("required_command_keys"), list):
            raise ValueError(
                f"trust_boundaries[{entry.get('id')}] needs required_command_keys"
            )
    return entries


def _path_matches(path: str, pattern: str) -> bool:
    if "**" in pattern:
        head, _, tail = pattern.partition("**")
        head = head.rstrip("/")
        tail = tail.lstrip("/")
        if head and not path.startswith(head + "/") and path != head:
            return False
        if tail and not fnmatch.fnmatch(path, "*" + tail):
            return False
        return True
    return fnmatch.fnmatch(path, pattern)


def _match_trust_boundaries(
    changed_paths: set[str], policy: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    hits: dict[str, dict[str, Any]] = {}
    for entry in policy:
        touched = sorted(
            {
                path
                for path in changed_paths
                for pattern in entry["paths"]
                if _path_matches(path, pattern)
            }
        )
        if touched:
            hits[str(entry["id"])] = {
                "paths": touched,
                "required_command_keys": list(entry.get("required_command_keys", [])),
            }
    return hits


def trust_boundary_hits_for_paths(
    changed_paths: set[str], root: Path | None = None
) -> dict[str, dict[str, Any]]:
    resolved = repo_root(root)
    try:
        policy = _load_policy(resolved)
    except (FileNotFoundError, ValueError):
        return {}
    return _match_trust_boundaries(changed_paths, policy)


def load_tasks(root: Path | None = None) -> list[dict[str, Any]]:
    """Load only machine-local Task Lite records.

    The historical ``.agent/tasks`` tree is intentionally not consulted. It is a
    frozen repository archive, not an active workflow source of truth.
    """
    resolved = repo_root(root)
    from aletheia import agent_tasks_db

    return agent_tasks_db.list_tasks(resolved)


def _validate_task_id(task_id: str) -> None:
    if not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id):
        raise ValueError(
            "task ID must start with TASK- and contain only letters, digits, _ or -"
        )


def create_task(
    task_id: str,
    *,
    title: str,
    intent: str,
    scope_boundary: str = "",
    scope_excluded: str = "",
    acceptance: list[str] | None = None,
    next_action: str = "",
    root: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Create one local task. No repository file is written."""
    _validate_task_id(task_id)
    if _contains_forbidden(
        {
            "title": title,
            "intent": intent,
            "scope": {
                "boundary": scope_boundary,
                "excluded": scope_excluded,
            },
            "acceptance": acceptance or [],
            "next_action": next_action,
        }
    ):
        raise ValueError("task contains secret-like or runtime-state metadata")
    resolved = repo_root(root)
    from aletheia import agent_tasks_db

    return agent_tasks_db.create_task(
        task_id,
        title=title,
        intent=intent,
        scope_boundary=scope_boundary,
        scope_excluded=scope_excluded,
        acceptance=acceptance,
        next_action=next_action,
        root=resolved,
    )


def claim_task(
    task_id: str,
    *,
    holder: str,
    hours: int = 8,
    root: Path | None = None,
) -> dict[str, Any]:
    """Atomically claim a local task for the current branch/worktree."""
    _validate_task_id(task_id)
    resolved = repo_root(root)
    _, branch = _git(resolved, "branch", "--show-current")
    if not branch:
        raise ValueError("cannot claim a task from detached HEAD")
    from aletheia import agent_tasks_db

    task = agent_tasks_db.claim_task_atomic(
        task_id, holder, branch, hours, resolved
    )
    if task is None:
        raise ValueError(
            f"task {task_id} is unavailable or this branch already has an active task"
        )
    return task


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    intent: str | None = None,
    scope_boundary: str | None = None,
    scope_excluded: str | None = None,
    acceptance: list[str] | None = None,
    next_action: str | None = None,
    blockers: list[str] | None = None,
    root: Path | None = None,
    **_: Any,
) -> dict[str, Any]:
    _validate_task_id(task_id)
    if status is not None and status not in _STATUSES:
        raise ValueError(f"unknown task status: {status}")
    resolved = repo_root(root)
    from aletheia import agent_tasks_db

    return agent_tasks_db.update_task(
        task_id,
        status=status,
        intent=intent,
        scope_boundary=scope_boundary,
        scope_excluded=scope_excluded,
        acceptance=acceptance,
        next_action=next_action,
        blockers=blockers,
        root=resolved,
    )


def complete_task(task_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Mark local coordination complete; CI remains the verification authority."""
    _validate_task_id(task_id)
    resolved = repo_root(root)
    from aletheia import agent_tasks_db

    task = agent_tasks_db.get_task(task_id, resolved)
    if task is None:
        raise ValueError(f"task not found: {task_id}")
    if task["status"] not in {"in_progress", "blocked"}:
        raise ValueError(
            f"only in-progress or blocked tasks may be completed (is {task['status']!r})"
        )
    return agent_tasks_db.complete_task(task_id, resolved)


def delete_task(task_id: str, *, root: Path | None = None) -> None:
    _validate_task_id(task_id)
    resolved = repo_root(root)
    from aletheia import agent_tasks_db

    if not agent_tasks_db.delete_task(task_id, resolved):
        raise ValueError(f"task not found: {task_id}")


def validate(
    root: Path | None = None,
    *,
    strict: bool = False,
    task_id: str | None = None,
    changed_since: str | None = None,
) -> list[Finding]:
    """Validate local Task Lite records and report trust-boundary hints.

    ``strict`` is retained for CLI compatibility. It does not turn task metadata
    into a Git or CI gate.
    """
    del strict
    resolved = repo_root(root)
    findings: list[Finding] = []
    try:
        project = _yaml(resolved / ".agent" / "project.yaml")
        if project.get("schema_version") != 1:
            findings.append(Finding("error", "invalid project schema version"))
        policy = _load_policy(resolved)
    except (FileNotFoundError, ValueError) as exc:
        return [Finding("error", str(exc))]

    tasks = load_tasks(resolved)
    if task_id is not None:
        tasks = [task for task in tasks if task.get("id") == task_id]
        if not tasks:
            findings.append(Finding("error", f"task not found: {task_id}", task_id))

    active_by_branch: dict[str, str] = {}
    for task in tasks:
        current_id = str(task.get("id", ""))
        if not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", current_id):
            findings.append(Finding("error", "invalid task ID", current_id or None))
        if task.get("status") not in _STATUSES:
            findings.append(
                Finding("error", f"unknown status {task.get('status')!r}", current_id)
            )
        forbidden = _contains_forbidden(task)
        if forbidden:
            findings.append(
                Finding(
                    "error",
                    "forbidden secret/runtime metadata: " + ", ".join(forbidden),
                    current_id,
                )
            )
        branch = task.get("branch")
        if task.get("status") == "in_progress" and isinstance(branch, str):
            if branch in active_by_branch:
                findings.append(
                    Finding(
                        "error",
                        f"duplicate active branch with {active_by_branch[branch]}",
                        current_id,
                    )
                )
            active_by_branch[branch] = current_id

    if changed_since is not None:
        code, changed = _git(
            resolved, "diff", "--name-only", f"{changed_since}...HEAD"
        )
        if code:
            findings.append(
                Finding("warning", f"cannot inspect changes since {changed_since}")
            )
        else:
            changed_paths = {line for line in changed.splitlines() if line}
            for boundary_id, meta in sorted(
                _match_trust_boundaries(changed_paths, policy).items()
            ):
                commands = ", ".join(meta["required_command_keys"]) or "focused tests"
                paths = ", ".join(meta["paths"][:3])
                findings.append(
                    Finding(
                        "warning",
                        f"trust-boundary {boundary_id!r} touched ({paths}); run {commands}. "
                        "Task Lite does not store or gate that proof.",
                    )
                )
    return findings


def bootstrap(root: Path | None = None) -> dict[str, Any]:
    resolved = repo_root(root)
    from aletheia import agent_tasks_db
    from aletheia.config.loader import get_data_dir
    from aletheia.runtime_paths import workflow_tasks_db

    agent_tasks_db.release_expired_claims(resolved)
    findings = validate(resolved)
    _, branch = _git(resolved, "branch", "--show-current")
    _, head = _git(resolved, "rev-parse", "HEAD")
    _, porcelain = _git(resolved, "status", "--porcelain")

    branch_tasks = agent_tasks_db.get_tasks_for_branch(branch, resolved) if branch else []
    active = [task for task in branch_tasks if task.get("status") == "in_progress"]
    selected = active[0] if len(active) == 1 else None
    task_source = "local_active" if selected else None
    if selected is None and not active:
        history = [
            task
            for task in branch_tasks
            if task.get("status") in {"done", "cancelled"}
        ]
        if history:
            selected = history[0]
            task_source = "local_history"

    return {
        "repository": str(resolved),
        "branch": branch,
        "head": head,
        "dirty": bool(porcelain),
        "task": selected,
        "task_source": task_source,
        "task_match_count": len(active),
        "next_action": selected.get("next_action") if selected else None,
        "evidence": {
            "freshness": "ci-owned",
            "commit": None,
            "command_key": None,
        },
        "findings": [finding.__dict__ for finding in findings],
        "runtime_next_command": "uv run aletheia doctor",
        "task_store": str(workflow_tasks_db(resolved, get_data_dir())),
    }


def recovery_test(root: Path | None = None) -> dict[str, Any]:
    payload = bootstrap(root)
    return {
        "recoverable": payload["task_match_count"] <= 1
        and not any(x["severity"] == "error" for x in payload["findings"]),
        "task_id": payload["task"].get("id") if payload["task"] else None,
        "task_source": payload["task_source"],
        "next_action": payload["next_action"],
        "workspace_dirty": payload["dirty"],
        "task_store": payload["task_store"],
        "runtime_facts_command": payload["runtime_next_command"],
        "findings": payload["findings"],
    }


def render_bootstrap(payload: dict[str, Any], *, json_out: bool = False) -> str:
    if json_out:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    task = payload["task"]
    lines = [
        "ALETHEIA TASK LITE",
        f"Branch: {payload['branch']}",
        f"HEAD: {payload['head']}",
        f"Workspace: {'dirty' if payload['dirty'] else 'clean'}",
        f"Local store: {payload['task_store']}",
    ]
    if task:
        prefix = "History: " if payload.get("task_source") == "local_history" else "Task: "
        lines.append(f"{prefix}{task['id']} — {task['title']}")
        lines.append(f"Status: {task['status']}")
        if task.get("next_action"):
            lines.append(f"Next: {task['next_action']}")
    else:
        lines.append(
            "Task: none for this branch. Create or claim a local task before non-trivial work."
        )
    for finding in payload["findings"]:
        lines.append(f"{finding['severity'].upper()}: {finding['message']}")
    lines.append("Verification: Git/PR/CI (not stored in Task Lite)")
    lines.append(f"Runtime facts: {payload['runtime_next_command']}")
    return "\n".join(lines)


# Compatibility guards for old automation. They fail clearly instead of silently
# recreating Git-coupled evidence or handoff behavior.
def verify_task(*_: Any, **__: Any) -> dict[str, Any]:
    raise ValueError(
        "Task Lite does not record verification evidence; run the focused command "
        "directly and rely on Git/PR/CI."
    )


def revalidate_completed_task(*_: Any, **__: Any) -> dict[str, Any]:
    raise ValueError("Task Lite has no completed-task evidence ledger to revalidate.")


def rebase_base(*_: Any, **__: Any) -> dict[str, Any]:
    raise ValueError("Task Lite does not bind tasks to Git base commits.")


def write_handoff(*_: Any, **__: Any) -> Path:
    raise ValueError("Task Lite does not write Git-tracked handoff files.")

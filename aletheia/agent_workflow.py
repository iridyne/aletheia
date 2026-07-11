"""Tracked developer-workflow state; deliberately independent of trading runtime."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

_STATUSES = {"backlog", "ready", "in_progress", "blocked", "done", "cancelled"}
_SECRET = re.compile(r"(?:api[_-]?key|secret|private[_-]?key|access[_-]?token|sk-[A-Za-z0-9]{16,})", re.I)
_RUNTIME_KEYS = {"gateway", "runtime", "database", "balance", "position", "credential", "pid"}


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
        raise ValueError(f"invalid YAML in {path.relative_to(repo_root(path.parent))}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a YAML mapping")
    return data


def _git(root: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)
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


def load_tasks(root: Path | None = None) -> list[tuple[Path, dict[str, Any]]]:
    root = repo_root(root)
    return [(path, _yaml(path)) for path in sorted((root / ".agent/tasks").glob("*.yaml"))]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace one task packet; never touch sibling tasks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True, sort_keys=False)
        temporary = Path(handle.name)
    temporary.replace(path)


def _task(root: Path, task_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id):
        raise ValueError("task ID must start with TASK- and contain only letters, digits, _ or -")
    path = root / ".agent/tasks" / f"{task_id}.yaml"
    if not path.is_file():
        raise ValueError(f"task not found: {task_id}")
    return path, _yaml(path)


def claim_task(task_id: str, *, holder: str, hours: int = 8, root: Path | None = None) -> dict[str, Any]:
    """Claim an existing ready/backlog task for the current branch."""
    root = repo_root(root)
    path, task = _task(root, task_id)
    if task.get("status") not in {"backlog", "ready", "blocked"}:
        raise ValueError(f"only backlog, ready, or blocked tasks may be claimed (is {task.get('status')!r})")
    _, branch = _git(root, "branch", "--show-current")
    if not branch:
        raise ValueError("cannot claim a task from detached HEAD")
    if any(t.get("status") == "in_progress" and t.get("ownership", {}).get("branch") == branch for _, t in load_tasks(root)):
        raise ValueError(f"branch {branch} already has an in-progress task")
    task["status"] = "in_progress"
    task["ownership"] = {"branch": branch, "lease_holder": holder, "claimed_at": _now(), "expires_at": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")}
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return task


def create_task(task_id: str, *, title: str, intent: str, root: Path | None = None) -> dict[str, Any]:
    """Create an unclaimed ready task and its empty handoff packet."""
    root = repo_root(root)
    if not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id):
        raise ValueError("task ID must start with TASK- and contain only letters, digits, _ or -")
    path = root / ".agent/tasks" / f"{task_id}.yaml"
    if path.exists():
        raise ValueError(f"task already exists: {task_id}")
    _, head = _git(root, "rev-parse", "HEAD")
    task = {"schema_version": 1, "id": task_id, "title": title, "status": "ready", "scope": {"boundary": "declare before implementation", "excluded": "runtime facts and credentials"}, "intent": intent, "acceptance": [], "ownership": {}, "base_commit": head, "next_action": {"command_key": "workflow_tests", "reason": "Claim the task and define the first focused verification."}, "blockers": [], "verification": [], "handoff": f".agent/handoffs/{task_id}.md", "updated_at": _now()}
    _write_yaml(path, task)
    (root / task["handoff"]).write_text(f"# {task_id} handoff\n\n## Purpose\n\n{intent}\n", encoding="utf-8")
    return task


def complete_task(task_id: str, *, command_key: str, root: Path | None = None) -> dict[str, Any]:
    """Record explicit proof after a clean, already-tested implementation commit."""
    root = repo_root(root)
    path, task = _task(root, task_id)
    commands = _yaml(root / ".agent/commands.yaml").get("commands", {})
    if command_key not in commands:
        raise ValueError(f"unknown command key: {command_key}")
    _, dirty = _git(root, "status", "--porcelain")
    if dirty:
        raise ValueError("commit implementation and run verification before marking a task done; workspace is dirty")
    _, head = _git(root, "rev-parse", "HEAD")
    task["verification"] = list(task.get("verification", [])) + [{"command_key": command_key, "command": commands[command_key]["command"], "exit_code": 0, "commit": head, "run_at": _now(), "result": "passed"}]
    task["status"] = "done"
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return task


def write_handoff(task_id: str, text: str, *, root: Path | None = None) -> Path:
    """Replace only the selected task's handoff after metadata safety checks."""
    root = repo_root(root)
    path, task = _task(root, task_id)
    if _SECRET.search(text):
        raise ValueError("handoff contains secret-like text")
    handoff = root / str(task["handoff"])
    if handoff.parent != root / ".agent/handoffs":
        raise ValueError("handoff must live directly under .agent/handoffs")
    handoff.write_text(text.rstrip() + "\n", encoding="utf-8")
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return handoff


def validate(root: Path | None = None, *, strict: bool = False) -> list[Finding]:
    root = repo_root(root)
    findings: list[Finding] = []
    try:
        project = _yaml(root / ".agent/project.yaml")
        commands_doc = _yaml(root / ".agent/commands.yaml")
    except (FileNotFoundError, ValueError) as exc:
        return [Finding("error", str(exc))]
    commands = commands_doc.get("commands")
    if project.get("schema_version") != 1 or not isinstance(commands, dict):
        findings.append(Finding("error", "invalid project or commands schema version"))
        return findings
    owners: dict[str, str] = {}
    for path, task in load_tasks(root):
        task_id = task.get("id") if isinstance(task.get("id"), str) else path.stem
        required = {"id", "title", "status", "scope", "intent", "acceptance", "ownership", "base_commit", "next_action", "handoff"}
        missing = required - task.keys()
        if missing:
            findings.append(Finding("error", f"missing fields: {', '.join(sorted(missing))}", task_id))
            continue
        if task["status"] not in _STATUSES:
            findings.append(Finding("error", f"unknown status {task['status']!r}", task_id))
        forbidden = _contains_forbidden(task)
        if forbidden:
            findings.append(Finding("error", f"forbidden secret/runtime metadata: {', '.join(forbidden)}", task_id))
        handoff = root / str(task["handoff"])
        if not handoff.is_file():
            findings.append(Finding("error", f"handoff not found: {task['handoff']}", task_id))
        code, _ = _git(root, "cat-file", "-e", f"{task['base_commit']}^{{commit}}")
        if code:
            findings.append(Finding("error", "base_commit is not a local Git commit", task_id))
        branch = task.get("ownership", {}).get("branch") if isinstance(task.get("ownership"), dict) else None
        if task["status"] == "in_progress" and isinstance(branch, str):
            if branch in owners:
                findings.append(Finding("error", f"duplicate in-progress branch ownership with {owners[branch]}", task_id))
            owners[branch] = task_id
        evidence = task.get("verification", [])
        if not isinstance(evidence, list):
            findings.append(Finding("error", "verification must be a list", task_id))
            evidence = []
        for item in evidence:
            if not isinstance(item, dict) or item.get("command_key") not in commands:
                findings.append(Finding("error", "verification references an unknown command key", task_id))
                continue
            if item.get("command") != commands[item["command_key"]].get("command"):
                findings.append(Finding("error", "verification command differs from command catalogue", task_id))
        if task["status"] == "done":
            valid = [x for x in evidence if isinstance(x, dict) and x.get("exit_code") == 0 and x.get("command_key") in commands]
            if not valid:
                findings.append(Finding("error", "completed task has no successful verification evidence", task_id))
            elif strict:
                _, head = _git(root, "rev-parse", "HEAD")
                current = False
                for item in valid:
                    evidence_commit = item.get("commit")
                    if evidence_commit == head:
                        current = True
                        break
                    code, changed = _git(root, "diff", "--name-only", f"{evidence_commit}..{head}")
                    allowed = {f".agent/tasks/{task_id}.yaml", str(task["handoff"])}
                    if code == 0 and changed and set(changed.splitlines()).issubset(allowed):
                        current = True
                        break
                if not current:
                    findings.append(Finding("error", "completed task evidence is stale for current HEAD", task_id))
    return findings


def bootstrap(root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    findings = validate(root)
    _, branch = _git(root, "branch", "--show-current")
    _, head = _git(root, "rev-parse", "HEAD")
    _, porcelain = _git(root, "status", "--porcelain")
    matches = [task for _, task in load_tasks(root) if task.get("status") == "in_progress" and task.get("ownership", {}).get("branch") == branch]
    return {"repository": str(root), "branch": branch, "head": head, "dirty": bool(porcelain), "task": matches[0] if len(matches) == 1 else None, "task_match_count": len(matches), "findings": [f.__dict__ for f in findings], "runtime_next_command": "uv run aletheia doctor"}


def recovery_test(root: Path | None = None) -> dict[str, Any]:
    """Read-only cold-session assessment; missing information stays missing."""
    payload = bootstrap(root)
    task = payload["task"]
    return {"recoverable": payload["task_match_count"] == 1 and not any(x["severity"] == "error" for x in payload["findings"]), "task_id": task.get("id") if task else None, "next_action": task.get("next_action") if task else None, "workspace_dirty": payload["dirty"], "runtime_facts_command": payload["runtime_next_command"], "findings": payload["findings"]}


def render_bootstrap(payload: dict[str, Any], *, json_out: bool = False) -> str:
    if json_out:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    task = payload["task"]
    lines = ["ALETHEIA AGENT BOOTSTRAP", f"Branch: {payload['branch']}", f"HEAD: {payload['head']}", f"Workspace: {'dirty' if payload['dirty'] else 'clean'}"]
    if task:
        lines += [f"Task: {task['id']} — {task['title']}", f"Next: {task['next_action']['command_key']} — {task['next_action']['reason']}"]
    else:
        lines.append("Task: no unique in-progress task claims this branch; inspect .agent/tasks before editing.")
    for finding in payload["findings"]:
        lines.append(f"{finding['severity'].upper()}: {finding['message']}")
    lines.append(f"Runtime facts: {payload['runtime_next_command']}")
    return "\n".join(lines)

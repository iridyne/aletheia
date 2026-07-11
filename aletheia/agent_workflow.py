"""Tracked developer-workflow state; deliberately independent of trading runtime."""

from __future__ import annotations

import fnmatch
import json
import hashlib
import re
import shlex
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
_HANDOFF_HEADINGS = (
    "Purpose",
    "Changes made",
    "Uncommitted work",
    "Verification",
    "Remaining work",
    "Exact next action",
    "Blockers",
    "Risks",
)
_TINY_HANDOFF_HEADINGS = (
    "Purpose",
    "Changes made",
    "Verification",
)
_PACKET_TYPES = frozenset({"standard", "tiny"})
_LEGAL_TRANSITIONS = {
    "backlog": {"ready", "cancelled"},
    "ready": {"blocked", "cancelled"},
    "in_progress": {"blocked", "cancelled"},
    # A blocked task can be resumed on the same branch (in_progress) or
    # released back to the ready pool. Previously the only escape was
    # `cancelled`, which forced re-claiming through the claim_task side door.
    "blocked": {"in_progress", "ready", "cancelled"},
    "done": set(),
    "cancelled": set(),
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


def _write_text(path: Path, text: str) -> None:
    """Atomically replace one handoff file in the same directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def _task(root: Path, task_id: str) -> tuple[Path, dict[str, Any]]:
    if not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id):
        raise ValueError("task ID must start with TASK- and contain only letters, digits, _ or -")
    path = root / ".agent/tasks" / f"{task_id}.yaml"
    if not path.is_file():
        raise ValueError(f"task not found: {task_id}")
    return path, _yaml(path)


def _commands(root: Path) -> dict[str, Any]:
    commands = _yaml(root / ".agent/commands.yaml").get("commands")
    if not isinstance(commands, dict):
        raise ValueError("commands catalogue must contain a commands mapping")
    return commands


def _load_policy(root: Path) -> list[dict[str, Any]]:
    """Read `.agent/policy.yaml` and return the trust-boundary list (empty when absent)."""
    path = root / ".agent/policy.yaml"
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
        if not isinstance(entry.get("required_command_keys"), list) or not entry["required_command_keys"]:
            raise ValueError(f"trust_boundaries[{entry.get('id')}] needs required_command_keys")
    return entries


def _match_trust_boundaries(
    changed_paths: set[str], policy: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Return {boundary_id: {"paths": [...], "required_command_keys": [...]}} for touched boundaries."""
    hits: dict[str, dict[str, Any]] = {}
    for entry in policy:
        touched = sorted({
            path for path in changed_paths
            for pattern in entry["paths"]
            if _path_matches(path, pattern)
        })
        if touched:
            hits[entry["id"]] = {
                "paths": touched,
                "required_command_keys": list(entry["required_command_keys"]),
            }
    return hits


def _path_matches(path: str, pattern: str) -> bool:
    """Match a path against a policy glob. `**` means any depth."""
    if "**" in pattern:
        # Split around the first `**`; require prefix and suffix to match.
        head, _, tail = pattern.partition("**")
        head = head.rstrip("/")
        tail = tail.lstrip("/")
        if head and not path.startswith(head + "/") and path != head:
            return False
        if tail and not fnmatch.fnmatch(path, "*" + tail):
            return False
        return True
    return fnmatch.fnmatch(path, pattern)


def _dirty_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError(f"cannot inspect workspace: {result.stderr.strip()}")
    entries = [entry for entry in result.stdout.split("\0") if entry]
    paths: set[str] = set()
    index = 0
    while index < len(entries):
        entry = entries[index]
        status = entry[:2]
        paths.add(entry[3:])
        if status[0] in {"R", "C"}:
            index += 1
            if index < len(entries):
                paths.add(entries[index])
        index += 1
    return paths


def _workflow_paths(task: dict[str, Any]) -> set[str]:
    return {f".agent/tasks/{task['id']}.yaml", str(task["handoff"])}


def _assert_only_workflow_metadata_dirty(root: Path, task: dict[str, Any]) -> None:
    unexpected = sorted(_dirty_paths(root) - _workflow_paths(task))
    if unexpected:
        raise ValueError(
            "commit implementation before verification; non-workflow files are dirty: "
            + ", ".join(unexpected)
        )


def _handoff_headings_for(task: dict[str, Any] | None) -> tuple[str, ...]:
    """Return the required heading tuple for a task. Tiny packets require 3;
    everything else (unset / standard / unknown values) requires all 8, keeping
    backwards compatibility with legacy packets that have no packet_type field."""
    if isinstance(task, dict) and task.get("packet_type") == "tiny":
        return _TINY_HANDOFF_HEADINGS
    return _HANDOFF_HEADINGS


def _missing_handoff_headings(text: str, task: dict[str, Any] | None = None) -> list[str]:
    headings = {line[3:].strip() for line in text.splitlines() if line.startswith("## ")}
    required = _handoff_headings_for(task)
    return [heading for heading in required if heading not in headings]


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


def create_task(
    task_id: str,
    *,
    title: str,
    intent: str,
    packet_type: str = "standard",
    root: Path | None = None,
) -> dict[str, Any]:
    """Create an unclaimed ready task and its empty handoff packet.

    Pass `packet_type="tiny"` for a small fix that would otherwise degrade
    the 8-heading handoff into filler; the tiny template requires only
    Purpose / Changes made / Verification.
    """
    root = repo_root(root)
    if not re.fullmatch(r"TASK-[A-Za-z0-9_-]+", task_id):
        raise ValueError("task ID must start with TASK- and contain only letters, digits, _ or -")
    if packet_type not in _PACKET_TYPES:
        raise ValueError(f"packet_type must be one of {sorted(_PACKET_TYPES)}, got {packet_type!r}")
    path = root / ".agent/tasks" / f"{task_id}.yaml"
    if path.exists():
        raise ValueError(f"task already exists: {task_id}")
    _, head = _git(root, "rev-parse", "HEAD")
    task = {"schema_version": 1, "handoff_version": 1, "id": task_id, "title": title, "status": "ready", "packet_type": packet_type, "scope": {"boundary": "declare before implementation", "excluded": "runtime facts and credentials"}, "intent": intent, "acceptance": [], "ownership": {}, "base_commit": head, "next_action": {"command_key": "workflow_tests", "reason": "Claim the task and define the first focused verification."}, "blockers": [], "verification": [], "handoff": f".agent/handoffs/{task_id}.md", "updated_at": _now()}
    _write_yaml(path, task)
    if packet_type == "tiny":
        _write_text(root / task["handoff"],
            f"# {task_id} handoff\n\n"
            f"## Purpose\n\n{intent}\n\n"
            "## Changes made\n\n- None yet.\n\n"
            "## Verification\n\n- Not run yet.\n",
        )
        return task
    _write_text(root / task["handoff"],
        f"# {task_id} handoff\n\n"
        f"## Purpose\n\n{intent}\n\n"
        "## Changes made\n\n- None yet.\n\n"
        "## Uncommitted work\n\n- Task packet and this handoff.\n\n"
        "## Verification\n\n- Not run yet.\n\n"
        "## Remaining work\n\n- Complete the acceptance conditions.\n\n"
        "## Exact next action\n\n- Claim the task and define the first focused verification.\n\n"
        "## Blockers\n\n- None.\n\n"
        "## Risks\n\n- None identified.\n",
    )
    return task


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    next_command_key: str | None = None,
    next_reason: str | None = None,
    blockers: list[str] | None = None,
    acceptance: list[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Deliberately update one task while enforcing state and command policy."""
    root = repo_root(root)
    path, task = _task(root, task_id)
    commands = _commands(root)
    if status is not None and status != task.get("status"):
        if status == "done":
            raise ValueError("use task complete to enter done state")
        if status not in _LEGAL_TRANSITIONS.get(str(task.get("status")), set()):
            raise ValueError(f"illegal task transition: {task.get('status')} -> {status}")
        task["status"] = status
    if next_command_key is not None:
        if next_command_key not in commands:
            raise ValueError(f"unknown command key: {next_command_key}")
        if not next_reason or not next_reason.strip():
            raise ValueError("next action reason is required with a command key")
        task["next_action"] = {"command_key": next_command_key, "reason": next_reason.strip()}
    elif next_reason is not None:
        raise ValueError("next command key is required with a next action reason")
    if blockers is not None:
        task["blockers"] = blockers
    if acceptance is not None:
        task["acceptance"] = acceptance
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return task


def rebase_base(task_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Reset an active task's base_commit to current HEAD, recording the rebase.

    After a rebase or squash-merge the original base_commit can become
    unreachable from HEAD even though every intended change is still there.
    `validate()` reports that as a warning; this call resolves it explicitly.
    """
    root = repo_root(root)
    path, task = _task(root, task_id)
    if task.get("status") not in {"in_progress", "blocked"}:
        raise ValueError(
            f"only in-progress or blocked tasks may rebase base_commit (is {task.get('status')!r})"
        )
    _, head = _git(root, "rev-parse", "HEAD")
    if not head:
        raise ValueError("cannot resolve current HEAD")
    task["base_commit"] = head
    task["rebased_at"] = _now()
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return task


def verify_task(task_id: str, *, command_key: str, root: Path | None = None) -> dict[str, Any]:
    """Run one registered command and record evidence only after a real success."""
    root = repo_root(root)
    path, task = _task(root, task_id)
    if task.get("status") not in {"in_progress", "blocked"}:
        raise ValueError(f"only in-progress or blocked tasks may be verified (is {task.get('status')!r})")
    commands = _commands(root)
    if command_key not in commands:
        raise ValueError(f"unknown command key: {command_key}")
    command = commands[command_key].get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError(f"registered command is empty: {command_key}")
    _assert_only_workflow_metadata_dirty(root, task)
    _, head = _git(root, "rev-parse", "HEAD")
    result = subprocess.run(shlex.split(command), cwd=root, check=False)
    if result.returncode:
        raise ValueError(f"verification command failed with exit code {result.returncode}: {command_key}")
    evidence = {
        "command_key": command_key,
        "command": command,
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
        "exit_code": result.returncode,
        "commit": head,
        "run_at": _now(),
        "result": "passed",
        "recorded_by": "agent_workflow.verify/v1",
    }
    task["verification"] = list(task.get("verification", [])) + [evidence]
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return task


def complete_task(task_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """Complete a task only with current tool-produced evidence and a full handoff."""
    root = repo_root(root)
    path, task = _task(root, task_id)
    if task.get("status") not in {"in_progress", "blocked"}:
        raise ValueError(f"only in-progress or blocked tasks may be completed (is {task.get('status')!r})")
    commands = _commands(root)
    if "workflow_bootstrap" not in commands:
        raise ValueError("commands catalogue must define workflow_bootstrap")
    _assert_only_workflow_metadata_dirty(root, task)
    _, head = _git(root, "rev-parse", "HEAD")
    current = [
        item for item in task.get("verification", [])
        if isinstance(item, dict)
        and item.get("exit_code") == 0
        and item.get("result") == "passed"
        and item.get("recorded_by") == "agent_workflow.verify/v1"
        and item.get("commit") == head
        and item.get("command_key") in commands
        and item.get("command") == commands[item["command_key"]].get("command")
    ]
    if not current:
        raise ValueError("task completion requires successful verification at current HEAD; run task verify first")
    handoff = root / str(task["handoff"])
    missing = _missing_handoff_headings(handoff.read_text(encoding="utf-8"), task)
    if missing:
        raise ValueError("handoff is missing required headings: " + ", ".join(missing))
    task["status"] = "done"
    task["next_action"] = {
        "command_key": "workflow_bootstrap",
        "reason": "Task complete; run bootstrap and create or claim a new task before changing code.",
    }
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return task


def write_handoff(task_id: str, text: str, *, root: Path | None = None) -> Path:
    """Replace only the selected task's handoff after metadata safety checks."""
    root = repo_root(root)
    path, task = _task(root, task_id)
    if _SECRET.search(text):
        raise ValueError("handoff contains secret-like text")
    if task.get("handoff_version") == 1:
        missing = _missing_handoff_headings(text, task)
        if missing:
            raise ValueError("handoff is missing required headings: " + ", ".join(missing))
    handoff = root / str(task["handoff"])
    if handoff.parent != root / ".agent/handoffs":
        raise ValueError("handoff must live directly under .agent/handoffs")
    _write_text(handoff, text.rstrip() + "\n")
    task["updated_at"] = _now()
    _write_yaml(path, task)
    return handoff


def validate(
    root: Path | None = None,
    *,
    strict: bool = False,
    task_id: str | None = None,
    changed_since: str | None = None,
) -> list[Finding]:
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
    tasks = load_tasks(root)
    if task_id is not None:
        tasks = [(path, task) for path, task in tasks if task.get("id") == task_id]
        if not tasks:
            findings.append(Finding("error", f"task not found: {task_id}", task_id))
    for path, task in tasks:
        task_id = task.get("id") if isinstance(task.get("id"), str) else path.stem
        required = {"id", "title", "status", "scope", "intent", "acceptance", "ownership", "base_commit", "next_action", "blockers", "verification", "handoff"}
        missing = required - task.keys()
        if missing:
            findings.append(Finding("error", f"missing fields: {', '.join(sorted(missing))}", task_id))
            continue
        if task["status"] not in _STATUSES:
            findings.append(Finding("error", f"unknown status {task['status']!r}", task_id))
        if task.get("id") != path.stem:
            findings.append(Finding("error", "task ID does not match its filename", task_id))
        expected_handoff = f".agent/handoffs/{task_id}.md"
        if task.get("handoff") != expected_handoff:
            findings.append(Finding("error", "handoff path does not match task ID", task_id))
        if not isinstance(task.get("acceptance"), list) or not isinstance(task.get("blockers"), list):
            findings.append(Finding("error", "acceptance and blockers must be lists", task_id))
        forbidden = _contains_forbidden(task)
        if forbidden:
            findings.append(Finding("error", f"forbidden secret/runtime metadata: {', '.join(forbidden)}", task_id))
        handoff = root / str(task["handoff"])
        if not handoff.is_file():
            findings.append(Finding("error", f"handoff not found: {task['handoff']}", task_id))
        elif task.get("handoff_version") == 1:
            missing_headings = _missing_handoff_headings(handoff.read_text(encoding="utf-8"), task)
            if missing_headings:
                findings.append(Finding("error", "handoff is missing required headings: " + ", ".join(missing_headings), task_id))
        if task.get("packet_type") is not None and task["packet_type"] not in _PACKET_TYPES:
            findings.append(Finding("error", f"unknown packet_type: {task['packet_type']!r}", task_id))
        code, _ = _git(root, "cat-file", "-e", f"{task['base_commit']}^{{commit}}")
        if code:
            findings.append(Finding("error", "base_commit is not a local Git commit", task_id))
        else:
            code, _ = _git(root, "merge-base", "--is-ancestor", str(task["base_commit"]), "HEAD")
            if code:
                # After a rebase or squash-merge, the original branch tip can
                # become an orphan even though every intended change is still
                # reachable. Surface it as a warning so `check --strict` doesn't
                # jam long-lived branches — `task rebase-base` clears it.
                findings.append(Finding("warning", "base_commit is not reachable from current HEAD; run 'agent workflow task rebase-base' to reset it", task_id))
        next_action = task.get("next_action")
        if not isinstance(next_action, dict) or next_action.get("command_key") not in commands:
            findings.append(Finding("error", "next action references an unknown command key", task_id))
        elif not isinstance(next_action.get("reason"), str) or not next_action["reason"].strip():
            findings.append(Finding("error", "next action reason is missing", task_id))
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
            expected_digest = hashlib.sha256(str(item.get("command", "")).encode("utf-8")).hexdigest()
            if task.get("handoff_version") == 1 and (
                item.get("recorded_by") != "agent_workflow.verify/v1"
                or item.get("command_sha256") != expected_digest
            ):
                findings.append(Finding("error", "verification was not recorded by the workflow verifier", task_id))
        if task["status"] == "done":
            valid = [
                x for x in evidence
                if isinstance(x, dict)
                and x.get("exit_code") == 0
                and x.get("result", "passed") == "passed"
                and x.get("command_key") in commands
                and x.get("command") == commands[x["command_key"]].get("command")
            ]
            if not valid:
                findings.append(Finding("error", "completed task has no successful verification evidence", task_id))
            elif strict:
                for item in valid:
                    code, _ = _git(root, "merge-base", "--is-ancestor", str(item.get("commit", "")), "HEAD")
                    if code == 0:
                        break
                else:
                    findings.append(Finding("error", "completed task evidence is not reachable from current HEAD", task_id))
            if not isinstance(next_action, dict) or next_action.get("command_key") != "workflow_bootstrap":
                findings.append(Finding("error", "completed task next action must return to workflow_bootstrap", task_id))
    if changed_since is not None:
        code, changed = _git(root, "diff", "--name-only", f"{changed_since}...HEAD")
        if code:
            findings.append(Finding("error", f"cannot inspect changes since {changed_since}"))
        else:
            changed_paths = {line for line in changed.splitlines() if line}
            governed = {
                path for path in changed_paths
                if path.startswith(("aletheia/", "frontend/", "tests/", "docs/", "scripts/", ".github/", ".agent/schemas/"))
                or path in {".agent/commands.yaml", ".agent/project.yaml", ".agent/policy.yaml"}
                or path in {"AGENTS.md", "CURRENT_TASK.md", "README.md", "pyproject.toml", "uv.lock"}
            }
            if governed:
                if not any(path.startswith(".agent/tasks/") for path in changed_paths):
                    findings.append(Finding("error", "governed changes require a tracked task packet update"))
                if not any(path.startswith(".agent/handoffs/") for path in changed_paths):
                    findings.append(Finding("error", "governed changes require a tracked handoff update"))
            try:
                policy = _load_policy(root)
            except (FileNotFoundError, ValueError) as exc:
                findings.append(Finding("error", f"trust-boundary policy is invalid: {exc}"))
                policy = []
            hits = _match_trust_boundaries(changed_paths, policy)
            if hits:
                _, head = _git(root, "rev-parse", "HEAD")
                touched_task_ids = {
                    path.split("/")[-1].removesuffix(".yaml")
                    for path in changed_paths
                    if path.startswith(".agent/tasks/") and path.endswith(".yaml")
                }
                candidate_tasks = [
                    (task_path, task)
                    for task_path, task in load_tasks(root)
                    if not touched_task_ids or task.get("id") in touched_task_ids
                ]
                if not candidate_tasks:
                    for boundary_id, meta in sorted(hits.items()):
                        findings.append(Finding(
                            "error",
                            f"trust-boundary {boundary_id!r} touched but no task packet accompanies "
                            f"the change; add evidence with one of: {', '.join(meta['required_command_keys'])}",
                        ))
                for boundary_id, meta in sorted(hits.items()):
                    satisfied_by: str | None = None
                    for _, task in candidate_tasks:
                        for item in task.get("verification", []):
                            if not isinstance(item, dict):
                                continue
                            if item.get("exit_code") != 0:
                                continue
                            if item.get("result", "passed") != "passed":
                                continue
                            if item.get("command_key") not in meta["required_command_keys"]:
                                continue
                            commit = str(item.get("commit", ""))
                            code, _ = _git(root, "merge-base", "--is-ancestor", commit, head)
                            if code == 0:
                                satisfied_by = str(task.get("id"))
                                break
                        if satisfied_by:
                            break
                    if not satisfied_by:
                        paths_summary = ", ".join(meta["paths"][:3])
                        if len(meta["paths"]) > 3:
                            paths_summary += ", ..."
                        findings.append(Finding(
                            "error",
                            f"trust-boundary {boundary_id!r} touched ({paths_summary}) requires "
                            f"passing evidence for one of: {', '.join(meta['required_command_keys'])} "
                            f"recorded at a commit reachable from HEAD",
                        ))
    return findings


def trust_boundary_hits_for_paths(
    changed_paths: set[str], root: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Public helper: report which trust-boundary groups a path set would trigger.

    Intended for use by scripts (pre-commit hook, ad-hoc audits). Returns an
    empty dict when the policy is missing or malformed rather than raising, so
    the hook can print a hint and continue.
    """
    resolved = repo_root(root)
    try:
        policy = _load_policy(resolved)
    except (FileNotFoundError, ValueError):
        return {}
    return _match_trust_boundaries(changed_paths, policy)


def bootstrap(root: Path | None = None) -> dict[str, Any]:
    root = repo_root(root)
    findings = validate(root)
    _, branch = _git(root, "branch", "--show-current")
    _, head = _git(root, "rev-parse", "HEAD")
    _, porcelain = _git(root, "status", "--porcelain")
    branch_tasks = [task for _, task in load_tasks(root) if task.get("ownership", {}).get("branch") == branch]
    matches = [task for task in branch_tasks if task.get("status") == "in_progress"]
    selected = matches[0] if len(matches) == 1 else None
    task_source = "active_branch" if selected else None
    if selected is None and not matches:
        completed = [task for task in branch_tasks if task.get("status") in {"done", "cancelled"}]
        if completed:
            selected = max(completed, key=lambda task: str(task.get("updated_at", "")))
            task_source = "branch_history"
        elif not branch_tasks:
            completed = [task for _, task in load_tasks(root) if task.get("status") in {"done", "cancelled"}]
            if completed:
                selected = max(completed, key=lambda task: str(task.get("updated_at", "")))
                task_source = "global_history"
    evidence = {"freshness": "missing", "commit": None, "command_key": None}
    if selected:
        successful = [
            item for item in selected.get("verification", [])
            if isinstance(item, dict) and item.get("exit_code") == 0 and item.get("result", "passed") == "passed"
        ]
        if successful:
            latest = successful[-1]
            evidence = {
                "freshness": "current" if latest.get("commit") == head else "historical",
                "commit": latest.get("commit"),
                "command_key": latest.get("command_key"),
            }
            code, _ = _git(root, "merge-base", "--is-ancestor", str(latest.get("commit", "")), "HEAD")
            if code:
                evidence["freshness"] = "unreachable"
    next_action = selected.get("next_action") if selected else None
    return {"repository": str(root), "branch": branch, "head": head, "dirty": bool(porcelain), "task": selected, "task_source": task_source, "task_match_count": len(matches), "next_action": next_action, "evidence": evidence, "findings": [f.__dict__ for f in findings], "runtime_next_command": "uv run aletheia doctor"}


def recovery_test(root: Path | None = None) -> dict[str, Any]:
    """Read-only cold-session assessment; missing information stays missing."""
    payload = bootstrap(root)
    task = payload["task"]
    return {"recoverable": task is not None and payload["task_match_count"] <= 1 and not any(x["severity"] == "error" for x in payload["findings"]), "task_id": task.get("id") if task else None, "task_source": payload["task_source"], "next_action": payload["next_action"], "evidence": payload["evidence"], "workspace_dirty": payload["dirty"], "runtime_facts_command": payload["runtime_next_command"], "findings": payload["findings"]}


def render_bootstrap(payload: dict[str, Any], *, json_out: bool = False) -> str:
    if json_out:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    task = payload["task"]
    lines = ["ALETHEIA AGENT BOOTSTRAP", f"Branch: {payload['branch']}", f"HEAD: {payload['head']}", f"Workspace: {'dirty' if payload['dirty'] else 'clean'}"]
    if task:
        prefix = "History (no active task): " if payload.get("task_source") in {"branch_history", "global_history"} else "Task: "
        lines += [
            f"{prefix}{task['id']} — {task['title']}",
            f"Evidence: {payload['evidence']['freshness']}",
            f"Next: {payload['next_action']['command_key']} — {payload['next_action']['reason']}",
        ]
    else:
        lines.append("Task: no unique in-progress task claims this branch; inspect .agent/tasks before editing.")
    for finding in payload["findings"]:
        lines.append(f"{finding['severity'].upper()}: {finding['message']}")
    lines.append(f"Runtime facts: {payload['runtime_next_command']}")
    return "\n".join(lines)

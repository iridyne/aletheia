"""Git operations for self-upgrade, scoped to the project source root.

Mirrors the subprocess idiom of aletheia/files/git.py::_git — every function
degrades gracefully (returns a dict or sentinel) and never raises into callers.
Operates on the repository that contains the installed source, NOT the data dir.
"""

import logging
import shutil
import subprocess

from aletheia.config.loader import _PROJECT_ROOT
from aletheia.gateway.pidfile import _no_window_kwargs

logger = logging.getLogger("aletheia.update.git")

REPO_ROOT = _PROJECT_ROOT


def git_available() -> bool:
    return shutil.which("git") is not None


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        **_no_window_kwargs(),
    )


def is_repo() -> bool:
    if not git_available():
        return False
    r = _git("rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def current_commit() -> str:
    r = _git("rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def current_branch() -> str:
    r = _git("rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def remote_commit(branch: str = "master") -> str:
    r = _git("rev-parse", f"origin/{branch}")
    return r.stdout.strip() if r.returncode == 0 else ""


def fetch(branch: str = "master") -> dict:
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}
    r = _git("fetch", "origin", branch)
    if r.returncode != 0:
        return {"ok": False, "reason": "fetch_failed", "stderr": r.stderr.strip()}
    return {"ok": True}


def behind_count(branch: str = "master") -> int:
    """How many commits HEAD is behind origin/<branch>. -1 on error."""
    r = _git("rev-list", "--count", f"HEAD..origin/{branch}")
    if r.returncode != 0:
        return -1
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1


def is_worktree_dirty() -> bool:
    """True if there are uncommitted changes (refuse to pull over them)."""
    r = _git("status", "--porcelain")
    if r.returncode != 0:
        return True  # unknown -> treat as dirty, the safe default
    return bool(r.stdout.strip())


def changed_files(old: str, new: str) -> list[str]:
    r = _git("diff", "--name-only", f"{old}..{new}")
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def pull(branch: str = "master") -> dict:
    """Fast-forward only merge from origin. Rejects force-pushes and diverged history."""
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}
    r = _git("fetch", "origin", branch)
    if r.returncode != 0:
        return {"ok": False, "reason": "fetch_failed", "stderr": r.stderr.strip()}
    r = _git("merge", "--ff-only", f"origin/{branch}")
    if r.returncode != 0:
        return {"ok": False, "reason": "ff_only_rejected", "stderr": r.stderr.strip()}
    return {"ok": True, "output": r.stdout.strip()}


def reset_hard(commit: str) -> dict:
    """Hard-reset the source repo to a specific commit (rollback after a bad pull).

    Only ever called inside a human-initiated apply_update, targeting the recorded
    pre-pull commit. Never raises.
    """
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}
    r = _git("reset", "--hard", commit)
    if r.returncode != 0:
        return {"ok": False, "reason": "reset_failed", "stderr": r.stderr.strip()}
    return {"ok": True, "output": r.stdout.strip()}

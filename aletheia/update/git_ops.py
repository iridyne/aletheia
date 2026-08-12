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


def ahead_count(branch: str = "master") -> int:
    """How many commits HEAD is ahead of origin/<branch> (unpushed). -1 on error."""
    r = _git("rev-list", "--count", f"origin/{branch}..HEAD")
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


def fetch_head() -> str:
    """Return the SHA recorded in FETCH_HEAD after the most recent fetch."""
    r = _git("rev-parse", "FETCH_HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def pull(branch: str = "master") -> dict:
    """Fast-forward to the exact fetched SHA, not the mutable remote ref.

    Invariant: the SHA captured from FETCH_HEAD immediately after the fetch is
    the only SHA referenced by the ahead-count check, the merge target, and the
    post-merge equality guard.  A concurrent fetch that advances origin/<branch>
    between any two of those steps cannot cause HEAD to land on an unvalidated
    commit, because git merge --ff-only FETCH_HEAD is resolved once at the time
    of the call against the FETCH_HEAD written by *this* fetch, not against the
    mutable origin/<branch> pointer.
    """
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}
    r = _git("fetch", "origin", branch)
    if r.returncode != 0:
        return {"ok": False, "reason": "fetch_failed", "stderr": r.stderr.strip()}

    # Capture the immutable SHA from FETCH_HEAD immediately after the fetch so
    # all subsequent checks and the merge target all reference the same commit.
    target = fetch_head()
    if not target:
        return {"ok": False, "reason": "remote_state_unavailable"}

    ahead = ahead_count(branch)
    if ahead < 0:
        return {"ok": False, "reason": "remote_state_unavailable"}
    if ahead:
        return {"ok": False, "reason": "local_ahead", "ahead_count": ahead}

    # Merge to the exact SHA we validated, not the mutable origin/<branch>.
    r = _git("merge", "--ff-only", target)
    if r.returncode != 0:
        return {"ok": False, "reason": "ff_only_rejected", "stderr": r.stderr.strip()}
    current = current_commit()
    if current != target:
        return {
            "ok": False,
            "reason": "source_not_at_remote_head",
            "current_commit": current,
            "remote_commit": target,
        }
    return {"ok": True, "output": r.stdout.strip(), "remote_commit": target}


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

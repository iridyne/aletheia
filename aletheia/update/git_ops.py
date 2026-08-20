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


def _https_fallback_url() -> str | None:
    """Return HTTPS equivalent when origin is an SSH URL, else None."""
    r = _git("remote", "get-url", "origin")
    if r.returncode != 0:
        return None
    url = r.stdout.strip()
    if not url.startswith("git@"):
        return None
    try:
        host, path = url[4:].split(":", 1)
        return f"https://{host}/{path}"
    except ValueError:
        return None


def _fetch_with_https_fallback(*args: str) -> subprocess.CompletedProcess:
    """git fetch, retrying with HTTPS when the SSH remote fails."""
    r = _git("fetch", *args)
    if r.returncode == 0:
        return r
    fallback = _https_fallback_url()
    if not fallback:
        return r
    fb_args = tuple(fallback if a == "origin" else a for a in args)
    r2 = _git("fetch", *fb_args)
    return r2 if r2.returncode == 0 else r


def fetch(branch: str = "master") -> dict:
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}
    r = _fetch_with_https_fallback("origin", branch)
    if r.returncode != 0:
        return {"ok": False, "reason": "fetch_failed", "stderr": r.stderr.strip()}
    return {"ok": True}


def fetch_release_tags() -> dict:
    """Fetch the master history and tags used by the source-runtime updater."""
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}
    r = _fetch_with_https_fallback("origin", "master", "--tags")
    if r.returncode != 0:
        return {"ok": False, "reason": "fetch_failed", "stderr": r.stderr.strip()}
    return {"ok": True}


def tag_names() -> list[str]:
    """Return local tag names after a successful fetch, or an empty list on error."""
    r = _git("tag", "--list")
    return [line.strip() for line in r.stdout.splitlines() if line.strip()] if r.returncode == 0 else []


def remote_tag_names() -> set[str] | None:
    """Return stable tag names currently advertised by ``origin``.

    Local tags may outlive a remote deletion or be created by an operator, so
    they are never sufficient authority for automatic deployment.
    """
    r = _git("ls-remote", "--tags", "--refs", "origin")
    if r.returncode != 0:
        return None
    prefix = "refs/tags/"
    return {
        ref[len(prefix):]
        for line in r.stdout.splitlines()
        if len((parts := line.split())) == 2
        for ref in [parts[1]]
        if ref.startswith(prefix)
    }


def tag_commit(tag: str) -> str:
    """Resolve either a lightweight or annotated tag to its commit SHA."""
    r = _git("rev-list", "-n", "1", tag)
    return r.stdout.strip() if r.returncode == 0 else ""


def is_ancestor(older: str, newer: str) -> bool:
    r = _git("merge-base", "--is-ancestor", older, newer)
    return r.returncode == 0


def version_at_ref(ref: str) -> str:
    """Read the product version from a Git object without checking it out."""
    r = _git("show", f"{ref}:aletheia/__init__.py")
    return r.stdout if r.returncode == 0 else ""


def merge_fast_forward(target: str) -> dict:
    """Advance the source checkout to a previously pinned commit only."""
    r = _git("merge", "--ff-only", target)
    if r.returncode != 0:
        return {"ok": False, "reason": "ff_only_rejected", "stderr": r.stderr.strip()}
    actual = current_commit()
    if actual != target:
        return {"ok": False, "reason": "source_not_at_target", "current_commit": actual}
    return {"ok": True, "output": r.stdout.strip()}


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




def pull(branch: str = "master") -> dict:
    """Fast-forward to the exact SHA returned by this operation's fetch."""
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}

    # Fetch into an operation-owned ref. FETCH_HEAD and origin/<branch> are
    # repository-wide mutable state and can be overwritten by another fetch
    # between subprocess calls.
    target_ref = f"refs/aletheia/update/{branch}"
    r = _fetch_with_https_fallback("--no-write-fetch-head", "origin", f"{branch}:{target_ref}")
    if r.returncode != 0:
        return {"ok": False, "reason": "fetch_failed", "stderr": r.stderr.strip()}
    r = _git("rev-parse", "--verify", target_ref)
    target = r.stdout.strip() if r.returncode == 0 else ""
    if not target:
        return {"ok": False, "reason": "remote_state_unavailable"}

    r = _git("rev-list", "--count", f"{target}..HEAD")
    try:
        ahead = int(r.stdout.strip()) if r.returncode == 0 else -1
    except ValueError:
        ahead = -1
    if ahead < 0:
        return {"ok": False, "reason": "remote_state_unavailable"}
    if ahead:
        return {"ok": False, "reason": "local_ahead", "ahead_count": ahead}

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

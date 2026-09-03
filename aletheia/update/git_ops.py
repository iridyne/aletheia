"""Git operations for self-upgrade, scoped to the project source root.

Mirrors the subprocess idiom of aletheia/files/git.py::_git — every function
degrades gracefully (returns a dict or sentinel) and never raises into callers.
Operates on the repository that contains the installed source, NOT the data dir.
"""

import logging
import os
from pathlib import Path
import shutil
import subprocess

from aletheia.config.loader import _PROJECT_ROOT
from aletheia.gateway.pidfile import _no_window_kwargs

logger = logging.getLogger("aletheia.update.git")

REPO_ROOT = _PROJECT_ROOT
_GIT_TIMEOUT_SEC = 60
_GIT_TIMEOUT_RETURN_CODE = 124
_GIT_OS_ERROR_RETURN_CODE = 127
_GIT_MUTATION_TIMEOUT_REASON = "git_mutation_timeout_reconciled"
_GIT_MUTATION_UNKNOWN_REASON = "git_mutation_state_unknown"
_GIT_OPERATION_PATHS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "BISECT_HEAD",
    "rebase-merge",
    "rebase-apply",
    "sequencer",
    "index.lock",
)


def git_available() -> bool:
    return shutil.which("git") is not None


def _git(*args) -> subprocess.CompletedProcess:
    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT_SEC,
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            command,
            124,
            "",
            f"git command exceeded {_GIT_TIMEOUT_SEC}s",
        )
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def is_repo() -> bool:
    if not git_available():
        return False
    r = _git("rev-parse", "--is-inside-work-tree")
    return r.returncode == 0 and r.stdout.strip() == "true"


def current_commit() -> str:
    r = _git("rev-parse", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def _is_interrupted(r: subprocess.CompletedProcess) -> bool:
    return r.returncode == _GIT_OS_ERROR_RETURN_CODE or (
        r.returncode == _GIT_TIMEOUT_RETURN_CODE
        and (r.stderr or "").strip() == f"git command exceeded {_GIT_TIMEOUT_SEC}s"
    )


def _mutation_postconditions(target: str, *, before: str | None = None) -> dict:
    actual = current_commit()
    if not actual or actual != target:
        return {
            "ok": False,
            "current_commit": actual,
            "detail": "HEAD did not reach the requested target",
        }
    if before is not None:
        if not before:
            return {"ok": False, "current_commit": actual, "detail": "pre-mutation HEAD unavailable"}
        ancestry = _git("merge-base", "--is-ancestor", before, target)
        if ancestry.returncode != 0:
            return {
                "ok": False,
                "current_commit": actual,
                "detail": "target is not a descendant of the pre-mutation HEAD",
            }
    status = _git("status", "--porcelain")
    if status.returncode != 0:
        return {"ok": False, "current_commit": actual, "detail": "worktree state unavailable"}
    if status.stdout.strip():
        return {"ok": False, "current_commit": actual, "detail": "worktree is not clean"}
    git_dir_result = _git("rev-parse", "--git-dir")
    if git_dir_result.returncode != 0 or not git_dir_result.stdout.strip():
        return {"ok": False, "current_commit": actual, "detail": "Git operation state unavailable"}
    git_dir = Path(git_dir_result.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = REPO_ROOT / git_dir
    for marker in _GIT_OPERATION_PATHS:
        marker_path = git_dir / marker
        if marker_path.exists():
            return {
                "ok": False,
                "current_commit": actual,
                "detail": f"Git operation state remains active: {marker}",
            }
    return {"ok": True, "current_commit": actual}


def _reconcile_interrupted_mutation(
    target: str,
    result: subprocess.CompletedProcess,
    *,
    before: str | None = None,
) -> dict:
    proof = _mutation_postconditions(target, before=before)
    if proof["ok"]:
        return {
            "ok": True,
            "reason": _GIT_MUTATION_TIMEOUT_REASON,
            "current_commit": proof["current_commit"],
        }
    return {
        "ok": False,
        "reason": _GIT_MUTATION_UNKNOWN_REASON,
        "current_commit": proof.get("current_commit", ""),
        "stderr": (result.stderr or "").strip(),
        "detail": proof.get("detail", "mutation postconditions unavailable"),
    }


def _merge_to_target(target: str, *, before: str) -> dict:
    r = _git("merge", "--ff-only", target)
    if r.returncode != 0:
        if _is_interrupted(r):
            return _reconcile_interrupted_mutation(target, r, before=before)
        return {"ok": False, "reason": "ff_only_rejected", "stderr": r.stderr.strip()}
    actual = current_commit()
    if actual != target:
        return {"ok": False, "reason": "source_not_at_target", "current_commit": actual}
    return {"ok": True, "output": r.stdout.strip(), "current_commit": actual}


def current_branch() -> str:
    r = _git("rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def remote_commit(branch: str = "master") -> str:
    r = _git("rev-parse", f"origin/{branch}")
    return r.stdout.strip() if r.returncode == 0 else ""


def _fetch_with_https_fallback(*args: str) -> subprocess.CompletedProcess:
    """git fetch, retrying via HTTPS URL-rewrite when the SSH remote fails.

    Uses GIT_CONFIG_* env vars so the rewrite applies transparently to the
    named remote without modifying any on-disk git config, and sets
    GIT_TERMINAL_PROMPT=0 to prevent the fallback from hanging on auth prompts.
    """
    r = _git("fetch", *args)
    if r.returncode == 0:
        return r
    url_r = _git("remote", "get-url", "origin")
    if url_r.returncode != 0 or not url_r.stdout.strip().startswith("git@"):
        return r
    try:
        host, _ = url_r.stdout.strip()[4:].split(":", 1)
    except ValueError:
        return r
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = f"url.https://{host}/.insteadOf"
    env["GIT_CONFIG_VALUE_0"] = f"git@{host}:"
    fallback_command = ["git", "fetch", *args]
    try:
        r2 = subprocess.run(
            fallback_command,
            cwd=str(REPO_ROOT), capture_output=True, text=True,
            env=env,
            timeout=_GIT_TIMEOUT_SEC,
            **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        r2 = subprocess.CompletedProcess(
            fallback_command,
            124,
            "",
            f"git command exceeded {_GIT_TIMEOUT_SEC}s",
        )
    except OSError as exc:
        r2 = subprocess.CompletedProcess(fallback_command, 127, "", str(exc))
    if r2.returncode == 0:
        return r2
    combined = "; ".join(filter(None, [r.stderr.strip(), r2.stderr.strip()]))
    return subprocess.CompletedProcess(r2.args, r2.returncode, r2.stdout, combined)


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


def tag_names() -> list[str] | None:
    """Return local tag names, or ``None`` when Git cannot enumerate tags."""
    r = _git("tag", "--list")
    return [line.strip() for line in r.stdout.splitlines() if line.strip()] if r.returncode == 0 else None


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
    return _merge_to_target(target, before=current_commit())


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


def changed_files(old: str, new: str) -> list[str] | None:
    """Return changed paths, or ``None`` when Git cannot classify the diff."""
    r = _git("diff", "--name-only", f"{old}..{new}")
    if r.returncode != 0:
        return None
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def tree_hash(commit: str, path: str) -> str:
    r = _git("rev-parse", f"{commit}:{path}")
    return r.stdout.strip() if r.returncode == 0 else ""




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

    merged = _merge_to_target(target, before=current_commit())
    if not merged.get("ok"):
        return merged
    return {**merged, "remote_commit": target}


def reset_hard(commit: str) -> dict:
    """Hard-reset the source repo to a specific commit (rollback after a bad pull).

    Only ever called inside a human-initiated apply_update, targeting the recorded
    pre-pull commit. Never raises.
    """
    if not git_available():
        return {"ok": False, "reason": "git_unavailable"}
    r = _git("reset", "--hard", commit)
    if r.returncode != 0:
        if _is_interrupted(r):
            return _reconcile_interrupted_mutation(commit, r)
        return {"ok": False, "reason": "reset_failed", "stderr": r.stderr.strip()}
    return {"ok": True, "output": r.stdout.strip()}

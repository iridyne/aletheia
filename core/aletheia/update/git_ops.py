"""Read-only source identity and the shared Git subprocess primitive."""
import shutil
import subprocess
from aletheia.config.loader import _PROJECT_ROOT
from aletheia.gateway.pidfile import _no_window_kwargs

REPO_ROOT = _PROJECT_ROOT
_GIT_TIMEOUT_SEC = 60

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


def current_branch() -> str:
    r = _git("rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else ""


def remote_commit(branch: str = "master") -> str:
    r = _git("rev-parse", f"origin/{branch}")
    return r.stdout.strip() if r.returncode == 0 else ""


def tag_names() -> list[str] | None:
    """Return local tag names, or ``None`` when Git cannot enumerate tags."""
    r = _git("tag", "--list")
    return [line.strip() for line in r.stdout.splitlines() if line.strip()] if r.returncode == 0 else None


def tag_commit(tag: str) -> str:
    """Resolve either a lightweight or annotated tag to its commit SHA."""
    r = _git("rev-list", "-n", "1", tag)
    return r.stdout.strip() if r.returncode == 0 else ""


def is_ancestor(older: str, newer: str) -> bool:
    r = _git("merge-base", "--is-ancestor", older, newer)
    return r.returncode == 0


def version_at_ref(ref: str) -> str:
    """Read the product version from a Git object without checking it out."""
    # Pre-layout release/rollback refs retain their historical Git-object path.
    for path in ("core/aletheia/__init__.py", "aletheia/__init__.py"):
        r = _git("show", f"{ref}:{path}")
        if r.returncode == 0:
            return r.stdout
    return ""


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

"""Read-only jj view of the source checkout, for developer machines only.

The production updater stays on ``git_ops``: Fedora and tarball runtimes carry no jj.
In a colocated checkout git HEAD is detached at ``@-``, so branch and ahead/behind
questions are answered here instead.
"""
import shutil
import subprocess

from aletheia.gateway.pidfile import _no_window_kwargs
from aletheia.update import git_ops

_TIMEOUT_SEC = 60


def _jj(*args: str) -> subprocess.CompletedProcess:
    command = ["jj", "--color=never", *args]
    try:
        return subprocess.run(
            command, cwd=str(git_ops.REPO_ROOT), capture_output=True, text=True,
            timeout=_TIMEOUT_SEC, **_no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, "", f"jj command exceeded {_TIMEOUT_SEC}s")
    except OSError as exc:
        return subprocess.CompletedProcess(command, 127, "", str(exc))


def is_jj_repo() -> bool:
    return shutil.which("jj") is not None and (git_ops.REPO_ROOT / ".jj").is_dir()


def current_commit() -> str:
    r = _jj("log", "-r", "@-", "--no-graph", "-T", "commit_id")
    return r.stdout.strip() if r.returncode == 0 else ""


def current_bookmark() -> str:
    r = _jj("log", "-r", "@-", "--no-graph", "-T", 'local_bookmarks.map(|b| b.name()).join("\\n")')
    return r.stdout.split("\n", 1)[0].strip() if r.returncode == 0 else ""


def is_dirty() -> bool:
    """True when the working-copy commit ``@`` holds changes."""
    r = _jj("diff", "--summary", "-r", "@")
    return r.returncode == 0 and bool(r.stdout.strip())


def _count(revset: str) -> int:
    r = _jj("log", "-r", revset, "--no-graph", "-T", '"x\\n"')
    return len(r.stdout.split()) if r.returncode == 0 else -1


def ahead_count(bookmark: str) -> int:
    """Commits in ``@-`` not on ``<bookmark>@origin`` (unpushed). -1 on error."""
    return _count(f"{bookmark}@origin..@-")


def behind_count(bookmark: str) -> int:
    """Commits on ``<bookmark>@origin`` not in ``@-``. -1 on error."""
    return _count(f"@-..{bookmark}@origin")

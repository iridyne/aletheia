"""Detect and remove legacy Aletheia auto-restart git hooks.

Gateway lifecycle is owner-controlled. Git operations must never restart the
runtime implicitly. Older checkouts installed three managed hooks, so startup
and ``doctor --fix`` use this module only as a narrow migration cleanup.

Foreign hooks are never changed. A file is removable only when it is the
standalone Aletheia-managed script, with the managed block immediately after
the shebang and the matching end marker at EOF.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_HOOK_NAMES = ("post-merge", "post-checkout", "post-rewrite")
_MARKER_PREFIX = "# >>> aletheia-auto-restart"
_END = "# <<< aletheia-auto-restart <<<"
_STAMP_NAME = "aletheia-auto-restart.last"


def _git(repo_dir: Path, *args: str) -> str | None:
    """Run a git command in repo_dir; return stripped stdout, or None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except Exception:
        return None


def _repo_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _hooks_dir(repo_dir: Path) -> Path | None:
    """Return the shared hooks directory, including from a linked worktree."""
    common = _git(repo_dir, "rev-parse", "--git-common-dir")
    if not common:
        return None
    common_path = Path(common)
    if not common_path.is_absolute():
        common_path = (repo_dir / common_path).resolve()
    return common_path / "hooks"


def _is_standalone_managed_hook(body: str) -> bool:
    lines = body.splitlines()
    return (
        len(lines) >= 3
        and lines[0] == "#!/bin/sh"
        and lines[1].startswith(_MARKER_PREFIX)
        and lines[-1] == _END
    )


def git_hooks_status(repo_dir: Path | None = None) -> dict:
    """Report legacy managed hooks without changing repository state."""
    repo_dir = repo_dir or _repo_dir()
    hooks_dir = _hooks_dir(repo_dir)
    if hooks_dir is None:
        return {
            "installed": [],
            "missing": list(_HOOK_NAMES),
            "foreign": [],
            "hooks_dir": None,
            "error": "not a git repository",
        }

    installed: list[str] = []
    missing: list[str] = []
    foreign: list[str] = []
    for name in _HOOK_NAMES:
        path = hooks_dir / name
        if not path.exists():
            missing.append(name)
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            foreign.append(name)
            continue
        (installed if _is_standalone_managed_hook(body) else foreign).append(name)
    return {
        "installed": installed,
        "missing": missing,
        "foreign": foreign,
        "hooks_dir": str(hooks_dir),
        "error": None,
    }


def remove_managed_git_hooks(repo_dir: Path | None = None) -> list[str]:
    """Remove only standalone legacy Aletheia hooks; best-effort, idempotent."""
    repo_dir = repo_dir or _repo_dir()
    actions: list[str] = []
    try:
        hooks_dir = _hooks_dir(repo_dir)
        if hooks_dir is None:
            return []
        for name in _HOOK_NAMES:
            path = hooks_dir / name
            if not path.exists():
                continue
            try:
                body = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not _is_standalone_managed_hook(body):
                continue
            try:
                path.unlink()
                actions.append(f"removed {name} auto-restart hook")
            except OSError as exc:
                actions.append(f"{name}: removal failed — {exc}")

        stamp = hooks_dir.parent / _STAMP_NAME
        try:
            stamp.unlink(missing_ok=True)
        except OSError as exc:
            actions.append(f"restart debounce stamp removal failed — {exc}")
    except Exception as exc:
        actions.append(f"git-hook cleanup error: {exc}")
    return actions

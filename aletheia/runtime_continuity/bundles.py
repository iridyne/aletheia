"""Candidate runtime bundle materialization.

The current update flow pulls directly into the source worktree. Runtime
continuity needs a non-destructive candidate bundle, so this module creates a
detached git worktree under data/runtime_bundles/<runtime_id>.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from aletheia.config.loader import _PROJECT_ROOT, get_data_dir
from aletheia.gateway.pidfile import _no_window_kwargs


class RuntimeBundleError(RuntimeError):
    pass


def _git(args: list[str], *, cwd: Path | None = None) -> dict:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd or _PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            **_no_window_kwargs(),
        )
    except FileNotFoundError as exc:
        raise RuntimeBundleError("git_unavailable") from exc
    return {
        "ok": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }


def runtime_bundles_dir() -> Path:
    path = get_data_dir() / "runtime_bundles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def materialize_candidate_worktree(
    *,
    runtime_id: str,
    commit: str,
    branch: str = "master",
) -> Path:
    """Create or reuse a detached candidate worktree for commit."""
    target = runtime_bundles_dir() / runtime_id
    if target.exists():
        return target

    fetched = _git(["fetch", "origin", branch])
    if not fetched.get("ok"):
        raise RuntimeBundleError(f"fetch_failed:{fetched.get('stderr') or fetched.get('stdout')}")

    added = _git(["worktree", "add", "--detach", str(target), commit])
    if not added.get("ok"):
        raise RuntimeBundleError(f"worktree_add_failed:{added.get('stderr') or added.get('stdout')}")
    return target

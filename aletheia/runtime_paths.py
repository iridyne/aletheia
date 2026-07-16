"""Platform-independent paths for Aletheia's per-user runtime state."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

RUNTIME_DATA_DIR_ENV = "ALETHEIA_DATA_DIR"


def _user_home() -> Path:
    """Indirection kept small so tests never depend on the real user profile."""
    return Path.home()


def default_runtime_home() -> Path:
    """Return the platform-neutral default runtime root for the current user."""
    return (_user_home() / ".aletheia").resolve()


def runtime_home() -> Path:
    """Resolve the primary runtime root, honoring an explicit absolute override."""
    raw = os.environ.get(RUNTIME_DATA_DIR_ENV, "").strip()
    if not raw:
        return default_runtime_home()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise RuntimeError(f"{RUNTIME_DATA_DIR_ENV} must be an absolute path")
    return candidate.resolve()


def preview_root() -> Path:
    """Root for isolated development instances; never the primary runtime root."""
    return default_runtime_home() / "instances" / "dev-preview"


def checkout_id(project_root: Path) -> str:
    """Stable, path-derived identifier preventing worktree preview collisions."""
    payload = str(project_root.expanduser().resolve()).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def preview_instance_dir(project_root: Path, port: int) -> Path:
    return preview_root() / checkout_id(project_root) / str(port)


def _git_common_dir(project_root: Path) -> Path:
    """Resolve the shared Git directory for a checkout and all of its worktrees.

    Worktree ``.git`` files point at ``<common>/.git/worktrees/<name>``. Using the
    common directory as the identity keeps Task Lite coordination machine-local
    while allowing sibling worktrees from the same clone to see the same tasks.
    """
    dot_git = project_root.expanduser().resolve() / ".git"
    if dot_git.is_dir():
        return dot_git.resolve()
    if dot_git.is_file():
        first_line = dot_git.read_text(encoding="utf-8").splitlines()[0].strip()
        if first_line.lower().startswith("gitdir:"):
            raw = first_line.split(":", 1)[1].strip()
            git_dir = Path(raw)
            if not git_dir.is_absolute():
                git_dir = (project_root / git_dir).resolve()
            else:
                git_dir = git_dir.resolve()
            if git_dir.parent.name == "worktrees":
                return git_dir.parent.parent
            return git_dir
    # Non-Git test sandboxes remain isolated by their checkout path.
    return project_root.expanduser().resolve()


def workflow_repository_id(project_root: Path) -> str:
    """Machine-local repository identity shared by sibling Git worktrees."""
    payload = str(_git_common_dir(project_root)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def workflow_tasks_db(project_root: Path, runtime_root: Path) -> Path:
    """Local Task Lite store, shared by worktrees but never committed to Git."""
    return (
        runtime_root
        / "development"
        / "agent-tasks"
        / workflow_repository_id(project_root)
        / "tasks.db"
    )


def workflow_claims_db(project_root: Path, runtime_root: Path) -> Path:
    """Backward-compatible alias for the former claim-only database path API."""
    return workflow_tasks_db(project_root, runtime_root)

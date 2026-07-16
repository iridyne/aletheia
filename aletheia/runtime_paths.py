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

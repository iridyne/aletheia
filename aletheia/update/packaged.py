"""Packaged runtime identity and read-only state primitives."""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

HOST_ROOT_ENV = "ALETHEIA_HOST_ROOT"
PACKAGED_ENV = "ALETHEIA_PACKAGED"

class PackagedUpdateError(RuntimeError):
    """Fail-closed packaged update error with a stable reason code."""

    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


def is_packaged_mode() -> bool:
    return os.environ.get(PACKAGED_ENV, "").strip() == "1" or bool(getattr(sys, "frozen", False))


def host_root() -> Path:
    explicit = os.environ.get(HOST_ROOT_ENV, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise PackagedUpdateError("host_root_not_absolute")
        return path
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Aletheia"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if not base:
            raise PackagedUpdateError("local_app_data_unavailable")
        return Path(base) / "Aletheia"
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(xdg) if xdg else home / ".local" / "share") / "Aletheia"


def _paths() -> dict[str, Path]:
    root = host_root()
    return {
        "root": root,
        "releases": root / "releases",
        "downloads": root / "downloads",
        "preflight": root / "preflight",
        "current": root / "current.json",
        "state": root / "update-state.json",
        "request": root / "update-request.json",
        "lock": root / "update-stage.lock",
        "feed_url": root / "update-feed-url.txt",
        "public_key": root / "update-public-key.pem",
    }


def platform_id() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    system = {"darwin": "darwin", "windows": "windows", "linux": "linux"}.get(system, system)
    machine = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "amd64": "x86_64",
        "x86_64": "x86_64",
    }.get(machine, machine)
    return f"{system}-{machine}"


def _read_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if not required:
            return {}
        raise PackagedUpdateError("state_file_missing", str(path))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackagedUpdateError("state_file_invalid", f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackagedUpdateError("state_file_invalid", f"{path}: expected object")
    return value


def current_selection() -> dict[str, Any]:
    selection = _read_json(_paths()["current"])
    if selection.get("schema_version") != 1 or not isinstance(selection.get("current"), str):
        raise PackagedUpdateError("current_selection_invalid")
    return selection


def read_update_state() -> dict[str, Any]:
    path = _paths()["state"]
    state = _read_json(path, required=False)
    if not state:
        selection = _read_json(_paths()["current"], required=False)
        return {
            "schema_version": 1,
            "mode": "packaged",
            "phase": "idle",
            "status": "idle",
            "current_release": selection.get("current"),
            "updated_at": None,
        }
    return state


def _current_release_git_sha(selection: dict[str, Any]) -> str | None:
    """Return the current immutable payload SHA when its manifest is safe to inspect.

    Release IDs are normally validated by the stable host before it launches a payload.
    Update checks still treat an absent, malformed, or escaped local manifest as unknown
    rather than trusting it to suppress a signed update.
    """

    current = selection.get("current")
    if not isinstance(current, str) or not current or Path(current).name != current:
        return None
    release = _paths()["releases"] / current
    if release.is_symlink() or not release.is_dir():
        return None
    try:
        manifest = _read_json(release / "release-manifest.json")
    except PackagedUpdateError:
        return None
    git_sha = manifest.get("git_sha")
    return git_sha if isinstance(git_sha, str) and git_sha else None

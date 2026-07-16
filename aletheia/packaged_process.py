"""Child-process command construction for source and frozen Aletheia runtimes.

A PyInstaller executable cannot be treated as a generic Python interpreter. In a
source checkout the gateway may launch a script or ``python -m uvicorn``. In a frozen
payload every child process must re-enter the same executable through an explicit,
private dispatch mode handled by ``scripts/release/pyinstaller_entry.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen_runtime() -> bool:
    """Return whether this process is executing from a frozen application payload."""

    return bool(getattr(sys, "frozen", False))


def gateway_runner_command(
    *,
    script: Path,
    real_python: str,
    no_browser: bool = False,
) -> list[str]:
    """Return the supervisor command for source or packaged execution."""

    if is_frozen_runtime():
        command = [sys.executable, "__packaged_gateway_runner"]
    else:
        command = [real_python, str(script)]
    if no_browser:
        command.append("--no-browser")
    return command


def uvicorn_command(
    app: str,
    *,
    real_python: str,
    host: str,
    port: int,
    log_level: str,
    loop: str = "none",
) -> list[str]:
    """Return an uvicorn child command without assuming the payload is Python."""

    prefix = (
        [sys.executable, "__packaged_uvicorn"]
        if is_frozen_runtime()
        else [real_python, "-m", "uvicorn"]
    )
    return [
        *prefix,
        app,
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        log_level,
        "--loop",
        loop,
    ]


def packaged_child_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    """Preserve the packaged-mode marker for frozen child processes."""

    env = dict(base or os.environ)
    if is_frozen_runtime():
        env["ALETHEIA_PACKAGED"] = "1"
    return env

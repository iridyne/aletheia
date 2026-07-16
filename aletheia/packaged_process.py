"""Child-process command construction for source and frozen Aletheia runtimes.

A PyInstaller executable cannot be treated as a generic Python interpreter. In a
source checkout the gateway may launch a script or ``python -m uvicorn``. In a frozen
payload every child process must re-enter the same executable through an explicit,
private dispatch mode handled by ``scripts/release/pyinstaller_entry.py``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence


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


def translate_frozen_command(
    args: Sequence[object],
    *,
    executable: str | None = None,
) -> list[object]:
    """Translate Python-style child commands into frozen executable re-entry modes.

    The function is pure and intentionally narrow: unrelated subprocess commands are
    returned unchanged. It is directly tested because a regression here makes the
    packaged CLI appear healthy while every background gateway start fails.
    """

    translated = list(args)
    if len(translated) < 2:
        return translated
    executable = executable or sys.executable
    try:
        invoked = Path(os.fspath(translated[0])).resolve()
        current = Path(executable).resolve()
    except (TypeError, ValueError, OSError):
        return translated
    if invoked != current:
        return translated

    second = os.fspath(translated[1])
    if Path(second).name == "_gateway_runner.py":
        return [executable, "__packaged_gateway_runner", *translated[2:]]
    if len(translated) >= 3 and second == "-m" and os.fspath(translated[2]) == "uvicorn":
        return [executable, "__packaged_uvicorn", *translated[3:]]
    return translated


def install_frozen_subprocess_adapter() -> None:
    """Install the frozen child-process adapter once for this process.

    Existing gateway code keeps its well-tested source-mode process lifecycle. In a
    frozen process only, this adapter rewrites the two Python-specific spawn shapes at
    the final Popen boundary. ``subprocess.run`` also benefits because it delegates to
    ``Popen``. The captured original function prevents recursive re-entry.
    """

    if not is_frozen_runtime() or getattr(subprocess.Popen, "_aletheia_frozen_adapter", False):
        return

    original_popen = subprocess.Popen

    def adapted_popen(args, *popen_args, **popen_kwargs):
        translated = args
        if isinstance(args, (list, tuple)):
            translated = translate_frozen_command(args)
        if isinstance(popen_kwargs.get("env"), dict):
            popen_kwargs["env"] = packaged_child_environment(popen_kwargs["env"])
        return original_popen(translated, *popen_args, **popen_kwargs)

    adapted_popen._aletheia_frozen_adapter = True  # type: ignore[attr-defined]
    subprocess.Popen = adapted_popen  # type: ignore[assignment]

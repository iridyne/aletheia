"""Shared, non-shell subprocess harness for locally authenticated agent CLIs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
import platform
import subprocess
import threading
from typing import Mapping, Sequence

from aletheia.gateway.pidfile import _no_window_kwargs


_ALLOWED_ENV_KEYS = {
    "ALL_PROXY",
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "NO_PROXY",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "all_proxy",
    "http_proxy",
    "https_proxy",
    "no_proxy",
}


def cli_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return the minimal host environment needed to locate and run local CLIs."""
    values = os.environ if source is None else source
    return {key: value for key, value in values.items() if key in _ALLOWED_ENV_KEYS}


@dataclass(frozen=True)
class CLIResult:
    stdout: bytes
    stderr: bytes
    returncode: int


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        await proc.wait()
    except (AttributeError, ProcessLookupError, TypeError):
        return


def _run_cli_sync(
    argv: Sequence[str],
    *,
    stdin: bytes | None,
    timeout: float,
    cancelled: threading.Event,
    proc_holder: dict[str, subprocess.Popen],
) -> CLIResult:
    proc = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE if stdin is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=cli_environment(),
        **_no_window_kwargs(),
    )
    proc_holder["proc"] = proc
    if cancelled.is_set():
        proc.kill()
    try:
        stdout, stderr = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        proc.communicate()
        raise asyncio.TimeoutError from exc
    return CLIResult(
        stdout=stdout or b"",
        stderr=stderr or b"",
        returncode=proc.returncode if proc.returncode is not None else 1,
    )


async def _run_cli_in_thread(
    argv: Sequence[str],
    *,
    stdin: bytes | None,
    timeout: float,
) -> CLIResult:
    cancelled = threading.Event()
    proc_holder: dict[str, subprocess.Popen] = {}
    try:
        return await asyncio.to_thread(
            _run_cli_sync,
            argv,
            stdin=stdin,
            timeout=timeout,
            cancelled=cancelled,
            proc_holder=proc_holder,
        )
    except asyncio.CancelledError:
        cancelled.set()
        proc = proc_holder.get("proc")
        if proc is not None and proc.poll() is None:
            proc.kill()
        raise


async def run_cli(
    argv: Sequence[str],
    *,
    stdin: bytes | None = None,
    timeout: float,
) -> CLIResult:
    """Run a trusted argv without shell interpolation and reap on cancellation."""
    if not argv:
        raise ValueError("CLI argv must not be empty")
    if platform.system() == "Windows":
        return await _run_cli_in_thread(argv, stdin=stdin, timeout=timeout)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=cli_environment(),
        **_no_window_kwargs(),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(stdin),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        await _kill_process(proc)
        raise
    except asyncio.CancelledError:
        await _kill_process(proc)
        raise
    return CLIResult(
        stdout=stdout or b"",
        stderr=stderr or b"",
        returncode=proc.returncode if proc.returncode is not None else 1,
    )

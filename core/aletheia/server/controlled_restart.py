"""Controlled Gateway restart (#1454): an intentional restart is not a crash.

A manual dashboard restart or a self-heal restart asks uvicorn for its normal
graceful shutdown (the same SIGTERM handling a whole-gateway stop uses), so the
lifespan runs its full cleanup and records the Runtime Session by the real
shutdown result. The lifespan's last step then exits with ``RESTART_EXIT_CODE``;
the supervisor treats that code as a controlled restart, outside the crash budget
and backoff.

If the graceful shutdown hangs, the supervisor's existing port-lost watchdog
kills the worker; that exit is not 42 and stays a budgeted crash.
"""

from __future__ import annotations

import os
import signal
import threading

RESTART_EXIT_CODE = 42

_requested = threading.Event()
# Indirection so tests can observe the exit without ending the test process.
_os_exit = os._exit


def request_controlled_restart() -> None:
    """Start uvicorn's graceful shutdown and mark it as a controlled restart."""
    _requested.set()
    signal.raise_signal(signal.SIGTERM)


def restart_requested() -> bool:
    return _requested.is_set()


def exit_if_restart_requested() -> None:
    """Final lifespan step: hand the controlled-restart outcome to the supervisor."""
    if not _requested.is_set():
        return
    _os_exit(RESTART_EXIT_CODE)

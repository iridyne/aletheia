"""Gentle, honest terminal progress for blocking SQLite migrations."""

from __future__ import annotations

import threading
import time

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from aletheia.storage.migrations import MigrationProgressEvent


def _duration(seconds: float) -> str:
    value = max(0, int(round(seconds)))
    minutes, remainder = divmod(value, 60)
    return f"{minutes}m {remainder:02d}s" if minutes else f"{remainder}s"


class MigrationProgressDisplay:
    """A step bar with live elapsed time and a qualified ETA range."""

    def __init__(self, *, console: Console | None = None) -> None:
        self._console = console or Console()
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[eta]}"),
            console=self._console,
            transient=False,
            refresh_per_second=4,
        )
        self._task_id: int | None = None
        self._current_started = 0.0
        self._estimated_remaining: tuple[float, float] | None = None
        self._estimate_basis: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> "MigrationProgressDisplay":
        self._progress.start()
        self._thread = threading.Thread(
            target=self._refresh_eta,
            name="aletheia-migration-progress",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, _exc, _tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        with self._lock:
            if exc_type is not None and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description="Database migration failed",
                    eta="see diagnostic message below",
                )
        self._progress.stop()

    def __call__(self, event: MigrationProgressEvent) -> None:
        with self._lock:
            if self._task_id is None:
                self._task_id = self._progress.add_task(
                    "Preparing database migration",
                    total=max(1, event.step_count),
                    completed=0,
                    eta="ETA pending",
                )
            if event.phase == "started":
                self._current_started = time.monotonic()
                self._estimated_remaining = event.estimated_remaining_sec
                self._estimate_basis = event.estimate_basis
                self._progress.update(
                    self._task_id,
                    completed=max(0, event.step_index - 1),
                    description=(
                        f"Step {event.step_index}/{event.step_count}: "
                        f"{event.description}"
                    ),
                    eta=self._eta_text(),
                )
            elif event.phase == "completed":
                self._current_started = 0.0
                self._estimated_remaining = event.estimated_remaining_sec
                self._estimate_basis = None
                suffix = (
                    f" ({_duration(event.duration_sec)})"
                    if event.duration_sec is not None
                    else ""
                )
                self._progress.update(
                    self._task_id,
                    completed=event.step_index,
                    description=(
                        f"Completed {event.step_index}/{event.step_count}: "
                        f"{event.description}{suffix}"
                    ),
                    eta=self._eta_text(),
                )

    def _eta_text(self) -> str:
        if self._estimated_remaining is None:
            return "ETA unavailable; elapsed is exact"
        elapsed = (
            time.monotonic() - self._current_started
            if self._current_started
            else 0.0
        )
        low = max(0.0, self._estimated_remaining[0] - elapsed)
        high = max(0.0, self._estimated_remaining[1] - elapsed)
        if high <= 0 and not self._current_started:
            estimate = "complete"
        elif high <= 0:
            estimate = "estimate exceeded; still working"
        else:
            estimate = f"ETA {_duration(low)}-{_duration(high)}"
        if self._estimate_basis:
            estimate += f" ({self._estimate_basis})"
        return estimate

    def _refresh_eta(self) -> None:
        while not self._stop.wait(0.25):
            with self._lock:
                if self._task_id is not None and self._current_started:
                    self._progress.update(self._task_id, eta=self._eta_text())

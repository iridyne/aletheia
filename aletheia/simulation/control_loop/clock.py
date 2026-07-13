"""A minimal UTC virtual clock. No sleeps and no wall-clock progression."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class VirtualClock:
    current: datetime

    def __post_init__(self) -> None:
        if self.current.tzinfo is None:
            self.current = self.current.replace(tzinfo=timezone.utc)
        self.current = self.current.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, hours: float = 0, minutes: float = 0) -> datetime:
        if hours < 0 or minutes < 0:
            raise ValueError("virtual time cannot move backwards")
        self.current += timedelta(hours=hours, minutes=minutes)
        return self.current

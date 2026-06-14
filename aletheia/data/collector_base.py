"""Base protocol for data collectors."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DataCollector(Protocol):
    """Structural interface every domain collector implements."""

    @property
    def domain(self) -> str:
        """Domain name for logging and staleness tracking."""
        ...

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        """Collect data for the given symbols. Returns partial results on failure."""
        ...

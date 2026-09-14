"""Bounded optional Telegram transport binding, configured by entrypoints."""

from collections.abc import Awaitable, Callable
from typing import Protocol


class TelegramTransport(Protocol):
    allowed_chat_ids: set[int]

    def configure(self) -> bool: ...
    async def send_message(self, chat_id: int, text: str, *, parse_mode: str | None = "HTML") -> None: ...
    async def run(self, poll_interval: float = 2.5) -> None: ...
    def stop(self) -> None: ...


_factory: Callable[[], TelegramTransport] | None = None
_health: Callable[[], Awaitable[dict]] | None = None


def configure_transport(
    factory: Callable[[], TelegramTransport] | None,
    health: Callable[[], Awaitable[dict]] | None,
) -> None:
    global _factory, _health
    _factory, _health = factory, health


def create_transport() -> TelegramTransport | None:
    return _factory() if _factory is not None else None


async def transport_health() -> dict:
    if _health is None:
        return {"reachable": False, "error": "adapter_unavailable"}
    return await _health()

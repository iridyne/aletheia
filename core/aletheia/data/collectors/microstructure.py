"""Normalized orderbook acquisition binding; no wire access or persistence."""

from collections.abc import Awaitable, Callable
from typing import Any

from aletheia.data.symbols import InstrumentId

_orderbook_collect: Callable[..., Awaitable[dict[str, Any]]] | None = None


def configure_orderbook_collector(collect: Callable[..., Awaitable[dict[str, Any]]] | None) -> None:
    global _orderbook_collect
    _orderbook_collect = collect


async def collect_orderbooks(symbols: list[InstrumentId | str], *, venue: str | None = None) -> dict[str, Any]:
    if _orderbook_collect is None:
        return {"snapshots": [], "errors": ["provider_setup:RuntimeError"]}
    return await _orderbook_collect(symbols, venue=venue)

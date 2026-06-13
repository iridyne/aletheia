"""Exchange adapter protocol for gateway recovery/reconciliation.

Defines the interface that real and mock exchange adapters must implement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class RemoteOrder:
    venue_order_id: str
    client_order_id: str | None = None
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    quantity: str = ""
    price: str | None = None
    status: str = "open"  # open | filled | cancelled | expired


@dataclass(slots=True)
class RemoteFill:
    venue_trade_id: str
    venue_order_id: str | None = None
    client_order_id: str | None = None
    symbol: str = ""
    side: str = ""
    quantity: str = ""
    price: str = ""
    fee: str | None = None
    fee_currency: str | None = None
    filled_at: str = ""


@dataclass(slots=True)
class RemotePosition:
    symbol: str
    side: str  # long | short
    quantity: str
    avg_entry_price: str | None = None
    unrealized_pnl: str | None = None


class ExchangeAdapter(Protocol):
    """Protocol for exchange API interactions during reconciliation."""

    async def fetch_open_orders(self) -> list[RemoteOrder]: ...
    async def fetch_positions(self) -> list[RemotePosition]: ...
    async def fetch_recent_fills(self, since: str | None = None) -> list[RemoteFill]: ...


class MockExchangeAdapter:
    """Mock adapter for testing reconciliation without a real exchange."""

    def __init__(
        self,
        open_orders: list[RemoteOrder] | None = None,
        positions: list[RemotePosition] | None = None,
        recent_fills: list[RemoteFill] | None = None,
    ):
        self.open_orders = open_orders or []
        self.positions = positions or []
        self.recent_fills = recent_fills or []

    async def fetch_open_orders(self) -> list[RemoteOrder]:
        return self.open_orders

    async def fetch_positions(self) -> list[RemotePosition]:
        return self.positions

    async def fetch_recent_fills(self, since: str | None = None) -> list[RemoteFill]:
        if since:
            return [f for f in self.recent_fills if f.filled_at > since]
        return self.recent_fills

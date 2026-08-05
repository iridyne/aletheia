"""IBroker Protocol — unified interface for read-only account queries."""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class AccountInfo:
    equity_usdt: float
    available_usdt: float
    frozen_usdt: float
    unrealized_pnl: float
    asset_breakdown: list[dict] = field(default_factory=list)


@dataclass
class BrokerPosition:
    symbol: str
    side: str
    quantity: float
    entry_price: float
    unrealized_pnl: float
    leverage: int = 1
    mark_price: float = 0.0
    opened_at_ms: int | None = None  # exchange-reported position open time


@runtime_checkable
class IBroker(Protocol):
    @property
    def broker_id(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    async def get_account(self) -> AccountInfo: ...

    async def get_positions(self) -> list[BrokerPosition]: ...

    async def test_connection(self) -> bool: ...

    async def close(self) -> None: ...

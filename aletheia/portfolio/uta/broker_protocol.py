"""IBroker Protocol — unified interface for read-only account queries."""

from dataclasses import dataclass, field
import math
from typing import Protocol, runtime_checkable


def parse_exchange_leverage(raw: object) -> tuple[int | None, str]:
    """Normalize an exchange leverage field without inventing a 1x fallback."""
    if raw is None or raw == "":
        return None, "unavailable"
    if isinstance(raw, bool):
        return None, "invalid"
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, "invalid"
    if not math.isfinite(value) or value <= 0 or not value.is_integer():
        return None, "invalid"
    return int(value), "exchange_reported"


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
    leverage: int | None = None
    mark_price: float = 0.0
    opened_at_ms: int | None = None  # exchange-reported position open time
    leverage_source: str | None = None
    market_type: str = "usdt_futures"

    def __post_init__(self) -> None:
        if self.leverage_source is None:
            self.leverage_source = (
                "exchange_reported" if self.leverage is not None else "unavailable"
            )


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

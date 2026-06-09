"""Market-data provider protocol + value models (Ticker, Candle).

Pure models — no I/O, no third-party deps. ``CCXTProvider`` (ccxt_provider.py)
is the concrete implementation; tests supply lightweight fakes that satisfy the
``MarketDataProvider`` Protocol structurally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aletheia.data.symbols import InstrumentId


@dataclass
class Ticker:
    """A point-in-time ticker snapshot.

    ``high_24h`` / ``low_24h`` / ``provider`` are optional — not every venue or
    fake supplies them (see test fakes that omit the 24h extremes).
    """

    symbol: str
    last_price: float
    volume_24h: float
    change_pct_24h: float
    provider: str | None = None
    high_24h: float | None = None
    low_24h: float | None = None


@dataclass
class Candle:
    """A single OHLCV bar. ``timestamp`` is unix milliseconds (CCXT convention)."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@runtime_checkable
class MarketDataProvider(Protocol):
    """Structural interface every market-data source implements.

    ``get_ohlcv`` returns candles oldest→newest. Both fetch methods accept a
    canonical string symbol or an ``InstrumentId``. ``close`` releases the
    underlying session and must be idempotent.
    """

    async def get_ticker(self, symbol: InstrumentId | str) -> Ticker: ...

    async def get_ohlcv(
        self,
        symbol: InstrumentId | str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
    ) -> list[Candle]: ...

    async def close(self) -> None: ...

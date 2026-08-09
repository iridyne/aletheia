"""Market-data provider protocol + value models (Ticker, Candle).

Pure models — no I/O, no third-party deps. ``CCXTProvider`` (ccxt_provider.py)
is the concrete implementation; tests supply lightweight fakes that satisfy the
``MarketDataProvider`` Protocol structurally.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Literal, Protocol, runtime_checkable

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
    """A single OHLCV bar. ``timestamp`` is unix milliseconds (CCXT convention).

    ``is_closed`` is False only for the trailing bar that is still forming on the
    exchange — set by the fetch layer when ``candle.timestamp + timeframe_ms > now``.
    Historical candles and candles read back from the DB default to True.
    """

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool = True
    provider: str | None = None
    exchange: str | None = None


OHLCVOutcome = Literal["complete", "partial", "empty", "failed", "unsupported"]


@dataclass(frozen=True)
class OHLCVAttempt:
    provider: str
    outcome: OHLCVOutcome
    accepted: int = 0
    rejected: int = 0
    reason: str | None = None


def classify_ohlcv_outcome(*, accepted: int, rejected: int, requested: int) -> OHLCVOutcome:
    if accepted == 0:
        return "empty"
    if rejected or (requested > 0 and accepted < requested):
        return "partial"
    return "complete"


class OHLCVResult(list[Candle]):
    """List-compatible candles with request-scoped fallback and quality facts."""

    def __init__(
        self,
        candles: Iterable[Candle] = (),
        *,
        requested: int,
        rejected: int = 0,
        attempts: Iterable[OHLCVAttempt] = (),
    ) -> None:
        super().__init__(candles)
        self.requested = requested
        self.accepted = len(self)
        self.rejected = rejected
        self.attempts = tuple(attempts)
        self.outcome = classify_ohlcv_outcome(
            accepted=self.accepted,
            rejected=rejected,
            requested=requested,
        )


def parse_ohlcv_candle(
    row: object,
    *,
    provider: str,
    exchange: str,
    volume_index: int | None = 5,
) -> Candle | None:
    """Parse one provider row, rejecting non-finite or impossible OHLCV facts."""
    if not isinstance(row, (list, tuple)):
        return None
    required = 5 if volume_index is None else volume_index + 1
    if len(row) < required:
        return None
    try:
        timestamp_value = float(row[0])
        open_value = float(row[1])
        high_value = float(row[2])
        low_value = float(row[3])
        close_value = float(row[4])
        volume_value = 0.0 if volume_index is None else float(row[volume_index])
    except (TypeError, ValueError, OverflowError):
        return None
    values = (timestamp_value, open_value, high_value, low_value, close_value, volume_value)
    if not all(math.isfinite(value) for value in values):
        return None
    if timestamp_value <= 0 or not timestamp_value.is_integer():
        return None
    if min(open_value, high_value, low_value, close_value) <= 0 or volume_value < 0:
        return None
    if high_value < max(open_value, close_value) or low_value > min(open_value, close_value):
        return None
    return Candle(
        timestamp=int(timestamp_value),
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=volume_value,
        provider=provider,
        exchange=exchange,
    )


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
    ) -> OHLCVResult: ...

    async def close(self) -> None: ...

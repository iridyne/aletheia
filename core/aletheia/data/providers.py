"""Market-data provider protocol + value models (Ticker, Candle).

Normalized contracts with no concrete adapter imports or third-party deps.
Composition binds optional listing reads and fresh provider creation; tests can
replace those side effects without importing concrete adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Awaitable, Callable
import math
from typing import Iterable, Literal, Protocol, runtime_checkable

from aletheia.data.symbols import InstrumentId


_listing_reader: Callable[[str, bool], Awaitable[frozenset[str]]] | None = None
_provider_factory: Callable[..., MarketDataProvider] | None = None
_macro_reader: Callable[[], Awaitable[str]] | None = None
_prediction_history_reader: Callable[..., Awaitable[list[dict]]] | None = None


def configure_macro_reader(reader: Callable[[], Awaitable[str]] | None) -> None:
    global _macro_reader
    _macro_reader = reader


async def read_macro_context() -> str:
    return await _macro_reader() if _macro_reader is not None else ""


def configure_prediction_history_reader(reader: Callable[..., Awaitable[list[dict]]] | None) -> None:
    global _prediction_history_reader
    _prediction_history_reader = reader


async def read_prediction_history(token_id: str, *, interval: str) -> list[dict] | None:
    """None means no adapter; an installed source may honestly return no points."""
    if _prediction_history_reader is None:
        return None
    return await _prediction_history_reader(token_id, interval=interval)


class MarketDataObservationError(RuntimeError):
    """Bounded acquisition diagnostics without a provider exception message."""

    def __init__(self, phase: str, cause: Exception) -> None:
        self.phase = phase if phase in {
            "provider_setup", "load_markets", "market_metadata", "fetch_order_book", "parse",
        } else "parse"
        self.exception_type = type(cause).__name__
        super().__init__(f"{self.phase}:{self.exception_type}")


def configure_market_data_factory(factory: Callable[..., MarketDataProvider] | None) -> None:
    global _provider_factory
    _provider_factory = factory


def get_market_data_provider(
    exchange: str | None = None,
    *,
    exact_venue: bool = False,
    fallback_venue: str | None = None,
) -> MarketDataProvider:
    """Create a request-owned provider; exact reads forbid cross-source fallback.

    ``fallback_venue`` only preserves the adapter's ordinary-read construction
    policy; optional observation capabilities remain primary-only when
    ``exact_venue`` is true.
    """
    if exact_venue and not exchange:
        raise ValueError("exact market data requires a venue")
    if _provider_factory is None:
        raise RuntimeError("market data provider unavailable")
    return _provider_factory(
        exchange, exact_venue=exact_venue, fallback_venue=fallback_venue,
    )


def configure_listing_reader(
    reader: Callable[[str, bool], Awaitable[frozenset[str]]] | None,
) -> None:
    global _listing_reader
    _listing_reader = reader


async def read_market_listing(venue: str, *, spot_only: bool) -> frozenset[str]:
    """Read confirmed listing facts; unavailable is never confirmed emptiness."""
    if _listing_reader is None:
        raise RuntimeError("market listing reader unavailable")
    listed = await _listing_reader(venue, spot_only)
    if not isinstance(listed, frozenset) or not listed or any(
        not isinstance(symbol, str) or not symbol.strip() for symbol in listed
    ):
        raise ValueError("market listing result is empty or invalid")
    return listed


# Canonical timeframe → milliseconds. Single source of truth for bar durations.
# Covers the Bitget/Binance/OKX granularities the charts UI exposes for parity
# with mainstream exchange terminals; agent reasoning still uses the smaller
# trading.instruments.TIMEFRAMES subset. An unlisted timeframe (e.g. "2m",
# "1M") yields 0 from timeframe_ms(); the OHLCV route treats that as
# unsupported when paginating.
_TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


def timeframe_ms(timeframe: str) -> int:
    """Milliseconds in one bar of ``timeframe``; 0 for an unsupported timeframe."""
    return _TF_MS.get(timeframe, 0)


@dataclass
class MarketInstrument:
    """Active USDT spot/perpetual listing facts, never registry admission."""

    venue: str
    symbol: str
    base_asset: str
    quote_asset: str
    market_type: str
    background: dict
    min_notional: float | None = None
    price_precision: int | None = None
    amount_precision: int | None = None
    is_test_or_dated: bool = False
    is_tokenized_equity: bool = False
    contract_size: float | None = None


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

    def __post_init__(self) -> None:
        self.last_price = _ticker_number(
            self.last_price, "last_price", positive=True,
        )
        self.volume_24h = _ticker_number(
            self.volume_24h, "volume_24h", non_negative=True,
        )
        self.change_pct_24h = _ticker_number(self.change_pct_24h, "change_pct_24h")
        if self.high_24h is not None:
            self.high_24h = _ticker_number(self.high_24h, "high_24h", positive=True)
        if self.low_24h is not None:
            self.low_24h = _ticker_number(self.low_24h, "low_24h", positive=True)
        if (
            self.high_24h is not None
            and self.low_24h is not None
            and self.high_24h < self.low_24h
        ):
            raise ValueError("ticker high_24h must be >= low_24h")


def require_exact_ticker_venue(ticker: Ticker, venue: str) -> Ticker:
    """Reject a ticker whose physical provider differs from the requested venue."""
    if not isinstance(ticker, Ticker):
        raise ValueError("ticker payload is not canonical")
    expected = str(venue or "").strip().lower()
    actual = str(ticker.provider or "").strip().lower()
    if not expected or actual != expected:
        raise ValueError(
            f"ticker provider {actual or 'unknown'} does not match venue "
            f"{expected or 'unknown'}"
        )
    return ticker


def _ticker_number(
    value: object,
    field: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> float:
    if isinstance(value, bool):
        raise ValueError(f"ticker {field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"ticker {field} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"ticker {field} must be finite")
    if positive and number <= 0:
        raise ValueError(f"ticker {field} must be positive")
    if non_negative and number < 0:
        raise ValueError(f"ticker {field} must be non-negative")
    return number


def _required_ticker_field(payload: dict, *names: str, field: str) -> object:
    """Return the first present ticker fact without coercing falsey values."""
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    raise ValueError(f"ticker {field} is missing")


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
        source: str = "provider_fetch",
    ) -> None:
        super().__init__(candles)
        self.requested = requested
        self.accepted = len(self)
        self.rejected = rejected
        self.attempts = tuple(attempts)
        self.source = source
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

    Sources may additionally expose ``get_ohlcv_observation(symbol, timeframe,
    limit, since, use_cache) -> OHLCVResult`` as an async capability. It must
    use the exact primary venue, perform no cross-venue fallback, persist no
    market history and change no venue health. Observation consumers require
    this capability explicitly; ordinary OHLCV support does not imply it, and
    its absence is unavailable rather than permission to use a writable read.

    The optional async ``get_taker_buy_ratio(symbol) -> float | None`` reads
    the existing primary venue's 1h buy/sell ratio without writes or fallback.
    Unsupported reads return None; acquisition errors propagate to the caller.
    Core owns whether a feature observation warrants this optional request.

    The optional async ``get_funding_observation(symbol, *, ticker)`` and
    ``get_open_interest_observation(symbol)`` capabilities return normalized,
    source-timestamped observation mappings. They perform no persistence or
    fallback; Core owns freshness admission, stored fallback and feature writes.

    The optional async ``get_orderbook_observation(symbol, *, market_type)``
    capability returns one normalized exact-market orderbook mapping without
    persistence or fallback. Core owns freshness reuse, diagnostics and the
    sole orderbook repository write.
    Acquisition failures may raise ``MarketDataObservationError`` with a bounded
    stage and exception type, never the external exception message.

    The optional async ``get_tickers_observation(symbols, *, market_type)``
    capability returns the existing feature-collection ticker mappings from the
    exact primary venue. It performs no persistence or cross-venue fallback;
    Core retains prefilter, volume and feature semantics.

    The optional async ``get_market_instruments(market_type=None)`` capability
    returns active USDT spot/perpetual listing facts as ``list[MarketInstrument]``
    from the exact primary venue, without persistence or cross-venue fallback.
    Dated futures are not perpetual instruments and are excluded by the adapter.
    ``MarketInstrument.contract_size`` is normalized metadata for contract
    volume conversion. Core retains product admission, configured exclusions
    and registry lifecycle ownership.

    """

    async def get_ticker(self, symbol: InstrumentId | str) -> Ticker: ...

    async def get_ohlcv(
        self,
        symbol: InstrumentId | str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
        *,
        use_cache: bool = True,
    ) -> OHLCVResult: ...

    async def close(self) -> None: ...

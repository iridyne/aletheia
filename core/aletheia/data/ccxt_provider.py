"""Public market-data composition and canonical timeframe/venue checks.

Concrete providers are bound by existing launch entrypoints. Every factory call
returns fresh loop-local adapters; Core never imports a concrete implementation.
"""

from __future__ import annotations

from typing import Callable

from aletheia.data.providers import MarketDataProvider, Ticker

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


_native_provider_factory: Callable[..., MarketDataProvider] | None = None


def configure_native_provider_factory(factory: Callable[..., MarketDataProvider] | None) -> None:
    global _native_provider_factory
    _native_provider_factory = factory


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


_spot_fallback_factory: Callable[[], list[MarketDataProvider]] | None = None


def configure_spot_fallback_factory(factory: Callable[[], list[MarketDataProvider]] | None) -> None:
    global _spot_fallback_factory
    _spot_fallback_factory = factory


def get_market_data_provider(
    exchange: str | None = None,
    *,
    native_ccxt: bool = False,
    fallback_exchange: str | None = None,
):
    """Construct a FRESH market-data provider (never cached).

    Primary defaults to ``market_data.ccxt_exchange`` (overridable via ``exchange``).
    Fallback defaults to ``market_data.ccxt_fallback`` and can be explicitly pinned via
    ``fallback_exchange`` for venue-specific sources such as xStocks.

    When ``enable_crypto_fallback`` is True (default), returns a FallbackChainProvider
    wrapping CCXTProvider plus the bound public spot adapters. Callers that
    require CCXT-native batch methods can request ``native_ccxt=True``; they still
    enter through this factory and receive a fresh provider with configured fallback.
    """
    from aletheia.config.loader import load_config

    md = load_config().market_data
    primary = exchange or md.ccxt_exchange
    ccxt = _native_provider_factory(
        exchange=primary,
        fallback_exchange=fallback_exchange or md.ccxt_fallback,
    ) if _native_provider_factory is not None else None

    if native_ccxt or not getattr(md, "enable_crypto_fallback", True):
        if ccxt is None:
            raise RuntimeError("ccxt_market_data_extension_unavailable")
        return ccxt

    from aletheia.data.sources.chain import FallbackChainProvider

    fallbacks = _spot_fallback_factory() if _spot_fallback_factory is not None else []
    if ccxt is None and not fallbacks:
        raise RuntimeError("ccxt_market_data_extension_unavailable")
    return FallbackChainProvider(([ccxt] if ccxt is not None else []) + fallbacks)

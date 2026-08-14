"""CCXT-backed market-data provider — the single source of public OHLCV/tickers.

Why a per-call factory (``get_market_data_provider``) instead of a cached singleton:
ccxt.async_support binds its aiohttp session to the event loop that created it. The
APScheduler jobs each run on a FRESH event loop (``asyncio.run`` per job), so a cached
provider from a previous loop raises "Event loop is closed" on the next fetch. Every
caller therefore constructs a provider, uses it, and ``await``s ``close()`` in a finally.

Primary/fallback: each fetch tries the primary exchange, and on failure retries once on
the configured fallback. Venue-pinned xStock listing checks still enter through
``get_market_data_provider(native_ccxt=True, fallback_exchange=exchange)`` so they keep
the managed session and malformed-market guard without substituting another venue.

Documented direct-construction sites (routing through the factory would either be wrong
or lack the CCXT-native surface they need - 2026-07-07 audit, #49):

- xStocks (ON-series): builds ``CCXTProvider(exchange="bitget", fallback_exchange="bitget")``
  because only Bitget lists those tokens and the config fallback (bybit) would not find them.
- ``universe/registry.py::sync_registry`` and ``universe/features.py::collect_features``: use
  ``CCXTProvider(exchange=md.ccxt_exchange, fallback_exchange=md.ccxt_fallback)`` — same
  config resolution as the factory, but the factory returns a ``FallbackChainProvider``
  whose surface lacks ``_ensure_primary`` + ``load_markets`` these callers need. #91 fix
  aligned the fallback exchange resolution with the factory so the two agree.

Any NEW direct construction site should be added here with its reason; a bare
``CCXTProvider(...)`` outside this list is a review-flagged deviation.

Symbols enter as canonical ``BTC/USDT`` strings or ``InstrumentId`` objects and are
resolved to the CCXT unified symbol (with perp settle suffix when needed) at the boundary.
OHLCV fetches are best-effort persisted to ``market_ohlcv`` so charts have history even
when a later fetch fails; persistence failures never propagate.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aletheia.data.providers import (
    Candle,
    OHLCVAttempt,
    OHLCVResult,
    Ticker,
    classify_ohlcv_outcome,
    parse_ohlcv_candle,
)
from aletheia.data.symbols import InstrumentId, to_canonical
from aletheia.data.venue_health import (
    VenueCoolingDownError,
    is_venue_available,
    is_venue_refusal,
    record_venue_failure,
    record_venue_success,
)

logger = logging.getLogger("aletheia.ccxt_provider")

# Canonical timeframe → milliseconds. Single source of truth for bar durations.
# Scoped to the timeframes the system actually uses (trading.instruments.TIMEFRAMES
# + 1m). An unlisted timeframe (e.g. "3m") yields 0 from timeframe_ms(), which the
# OHLCV route treats as unsupported when paginating — keep this set tight on purpose.
_TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def timeframe_ms(timeframe: str) -> int:
    """Milliseconds in one bar of ``timeframe``; 0 for an unsupported timeframe."""
    return _TF_MS.get(timeframe, 0)


def _resolve_ccxt_symbol(symbol: InstrumentId | str) -> tuple[str, str, str]:
    """Return (ccxt_symbol, canonical_symbol, market_type) for a fetch target."""
    if isinstance(symbol, InstrumentId):
        return symbol.to_ccxt_symbol(), symbol.symbol, symbol.market_type
    canon = to_canonical(symbol)
    return canon, canon, "spot"


def _install_market_id_guard(exchange: Any, exchange_name: str) -> Any:
    """Drop malformed CCXT market rows before `set_markets()` sorts by id.

    Some OKX responses can include a market entry with `id=None`. CCXT then
    builds `markets_by_id` with mixed `None` and `str` keys and Python raises
    while sorting them. One bad row should not stop the entire market feed.
    """
    if getattr(exchange, "_aletheia_market_id_guard", False):
        return exchange

    original_set_markets = exchange.set_markets

    def guarded_set_markets(markets, currencies=None):
        dropped = 0
        if isinstance(markets, list):
            filtered = []
            for market in markets:
                if isinstance(market, dict) and market.get("id") is None:
                    dropped += 1
                    continue
                filtered.append(market)
            markets = filtered
        if dropped:
            logger.warning(
                "ccxt %s load_markets: dropped %d malformed market row(s) with empty id",
                exchange_name,
                dropped,
            )
        return original_set_markets(markets, currencies)

    exchange.set_markets = guarded_set_markets
    exchange._aletheia_market_id_guard = True
    return exchange


class CCXTProvider:
    """Market data via ccxt.async_support with a primary + fallback exchange."""

    def __init__(self, exchange: str, fallback_exchange: str) -> None:
        self._primary_name = exchange
        self._fallback_name = fallback_exchange
        # Lazily constructed so __init__ does no I/O and binds no event loop —
        # the exchange clients are created on first fetch, on the active loop.
        self._primary = None
        self._fallback = None
        # Injected aiohttp sessions we own and must close (one per ccxt client).
        self._sessions: list = []

    def _client(self, name: str):
        # Build via the managed factory (aletheia.net.make_ccxt), which injects a
        # proxy-aware aiohttp session using aiohttp's OS-resolver (getaddrinfo)
        # instead of the c-ares AsyncResolver that aiohttp auto-selects when aiodns
        # is installed — on Windows/locked-down hosts c-ares can't read DNS config
        # and every fetch dies with ClientConnectorDNSError. We own the injected
        # session (own_session=False) and close it in close().
        from aletheia.net import make_ccxt

        exchange, session = make_ccxt(name)
        self._sessions.append(session)
        return _install_market_id_guard(exchange, name)

    def _ensure_primary(self):
        if self._primary is None:
            self._primary = self._client(self._primary_name)
        return self._primary

    def _ensure_fallback(self):
        if self._fallback is None:
            self._fallback = self._client(self._fallback_name)
        return self._fallback

    async def get_ticker(self, symbol: InstrumentId | str) -> Ticker:
        ccxt_symbol, canon, market_type = _resolve_ccxt_symbol(symbol)
        # For perp instruments ccxt requires defaultType="swap" so that Bitget/Bybit
        # route fetch_ticker to the futures endpoint instead of the spot
        # instruments-info endpoint.  We set and restore per-call to avoid
        # mutating shared state across concurrent calls on the same exchange client.
        _is_perp = market_type == "perp"
        last_err: Exception | None = None
        for name, ensure in (
            (self._primary_name, self._ensure_primary),
            (self._fallback_name, self._ensure_fallback),
        ):
            if not is_venue_available(name):
                last_err = VenueCoolingDownError(f"market-data venue cooling down: {name}")
                continue
            try:
                exchange = ensure()
                _prev_type = exchange.options.get("defaultType") if _is_perp else None
                if _is_perp:
                    exchange.options["defaultType"] = "swap"
                try:
                    raw = await exchange.fetch_ticker(ccxt_symbol)
                finally:
                    if _is_perp:
                        if _prev_type is None:
                            exchange.options.pop("defaultType", None)
                        else:
                            exchange.options["defaultType"] = _prev_type
                record_venue_success(name)
                return Ticker(
                    symbol=canon,
                    last_price=float(raw.get("last") or raw.get("close") or 0.0),
                    volume_24h=float(raw.get("quoteVolume") or raw.get("baseVolume") or 0.0),
                    change_pct_24h=float(raw.get("percentage") or 0.0),
                    high_24h=_opt_float(raw.get("high")),
                    low_24h=_opt_float(raw.get("low")),
                    provider=name,
                )
            except Exception as e:  # noqa: BLE001 — try fallback, then re-raise
                last_err = e
                if is_venue_refusal(e):
                    record_venue_failure(name, e)
                logger.warning("ticker fetch failed on %s for %s: %s", name, ccxt_symbol, e)
        raise last_err if last_err else RuntimeError("ticker fetch failed")

    async def get_ohlcv(
        self,
        symbol: InstrumentId | str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
        *,
        _record_venue_health: bool = True,
        _persist_result: bool = True,
    ) -> OHLCVResult:
        ccxt_symbol, canon, mkt = _resolve_ccxt_symbol(symbol)

        from aletheia.data.cache import get_cache
        cache = get_cache()
        if since is None:
            cached = cache.get(
                canon, timeframe, limit,
                market_type=mkt, exchange=self._primary_name,
            )
            if cached is not None:
                return cached if isinstance(cached, OHLCVResult) else OHLCVResult(
                    cached, requested=limit,
                )

        last_err: Exception | None = None
        attempts: list[OHLCVAttempt] = []
        for name, ensure in (
            (self._primary_name, self._ensure_primary),
            (self._fallback_name, self._ensure_fallback),
        ):
            if since is None and name != self._primary_name:
                cached = cache.get(
                    canon, timeframe, limit,
                    market_type=mkt, exchange=name,
                )
                if cached is not None:
                    return cached if isinstance(cached, OHLCVResult) else OHLCVResult(
                        cached, requested=limit,
                    )
            if not is_venue_available(name):
                last_err = VenueCoolingDownError(f"market-data venue cooling down: {name}")
                continue
            try:
                raw = await ensure().fetch_ohlcv(
                    ccxt_symbol, timeframe=timeframe, since=since, limit=limit
                )
                if _record_venue_health:
                    record_venue_success(name)
                parsed = [
                    parse_ohlcv_candle(row, provider="ccxt", exchange=name)
                    for row in raw
                ]
                candles = [candle for candle in parsed if candle is not None]
                rejected = len(parsed) - len(candles)
                outcome = classify_ohlcv_outcome(
                    accepted=len(candles), rejected=rejected, requested=limit,
                )
                attempts.append(OHLCVAttempt(
                    provider=name,
                    outcome=outcome,
                    accepted=len(candles),
                    rejected=rejected,
                ))
                if rejected:
                    logger.warning(
                        "ohlcv quality rejected %d/%d row(s) from %s for %s",
                        rejected,
                        len(parsed),
                        name,
                        ccxt_symbol,
                    )
                if not candles:
                    continue
                # Mark the trailing bar as open when its close time is still in
                # the future. CCXT always returns the currently-forming bar as
                # the last element; treat it as unconfirmed so callers can
                # exclude it from indicator calculations.
                if candles:
                    tf_ms = _TF_MS.get(timeframe, 0)
                    if tf_ms > 0 and candles[-1].timestamp + tf_ms > int(time.time() * 1000):
                        candles[-1].is_closed = False
                # SQLite's writer lane is synchronous by contract. Waiting for it
                # on the scheduler event loop stalls unrelated async jobs and API
                # work during bursty OHLCV refreshes, so keep the write awaited but
                # move the blocking wait to a worker thread.
                if _persist_result:
                    await asyncio.to_thread(
                        self._persist,
                        canon,
                        timeframe,
                        candles,
                        name,
                        market_type=mkt,
                    )
                result = OHLCVResult(
                    candles,
                    requested=limit,
                    rejected=sum(attempt.rejected for attempt in attempts),
                    attempts=attempts,
                )
                if since is None:
                    cache.set(
                        canon, timeframe, limit, result,
                        market_type=mkt, exchange=name,
                    )
                return result
            except Exception as e:  # noqa: BLE001 — try fallback, then re-raise
                last_err = e
                attempts.append(OHLCVAttempt(
                    provider=name,
                    outcome="failed",
                    reason=type(e).__name__,
                ))
                if _record_venue_health and is_venue_refusal(e):
                    record_venue_failure(name, e)
                logger.warning("ohlcv fetch failed on %s for %s: %s", name, ccxt_symbol, e)
        if attempts and any(attempt.outcome == "empty" for attempt in attempts):
            return OHLCVResult(
                requested=limit,
                rejected=sum(attempt.rejected for attempt in attempts),
                attempts=attempts,
            )
        if last_err:
            last_err.ohlcv_attempts = tuple(attempts)  # type: ignore[attr-defined]
            raise last_err
        raise RuntimeError("ohlcv fetch failed")

    async def get_ohlcv_observation(
        self,
        symbol: InstrumentId | str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
    ) -> OHLCVResult:
        """Fetch OHLCV without changing venue health or market-data history."""

        return await self.get_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=limit,
            since=since,
            _record_venue_health=False,
            _persist_result=False,
        )

    @staticmethod
    def _persist(
        canon: str,
        timeframe: str,
        candles: list[Candle],
        exchange: str,
        market_type: str = "spot",
    ) -> None:
        """Best-effort persist confirmed bars; never raise into the fetch path."""
        closed_candles = [candle for candle in candles if candle.is_closed]
        if not closed_candles:
            return
        try:
            from aletheia.storage.repositories import upsert_ohlcv_candles

            upsert_ohlcv_candles(
                symbol=canon, timeframe=timeframe, candles=closed_candles,
                provider="ccxt", exchange=exchange, market_type=market_type,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("ohlcv persist skipped for %s/%s: %s", canon, timeframe, e)

    async def close(self) -> None:
        """Close exchange clients AND the aiohttp sessions we injected. Idempotent.

        ccxt does not close an injected session (own_session=False), so the
        sessions in self._sessions are ours to close — otherwise aiohttp logs
        "Unclosed client session" and leaks connectors across scheduler loops.

        Each close is wrapped in a 5s timeout so a stuck TCP teardown (proxy /
        half-open connections) cannot block the entire cancel-cleanup path —
        the root cause of §19 "Unclosed client session" leaks after timeout.
        """
        import asyncio as _aio

        for attr in ("_primary", "_fallback"):
            client = getattr(self, attr)
            if client is not None:
                try:
                    await _aio.wait_for(client.close(), timeout=5.0)
                except (Exception, _aio.CancelledError):  # noqa: BLE001
                    pass
                setattr(self, attr, None)
        for session in self._sessions:
            try:
                await _aio.wait_for(session.close(), timeout=5.0)
            except (Exception, _aio.CancelledError):  # noqa: BLE001
                pass
        self._sessions = []


def _opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def require_exact_ticker_venue(ticker: Ticker, venue: str) -> Ticker:
    """Reject a ticker whose physical provider differs from the requested venue."""
    expected = str(venue or "").strip().lower()
    actual = str(ticker.provider or "").strip().lower()
    if not expected or actual != expected:
        raise ValueError(
            f"ticker provider {actual or 'unknown'} does not match venue "
            f"{expected or 'unknown'}"
        )
    return ticker


def get_market_data_provider(
    exchange: str | None = None,
    *,
    native_ccxt: bool = False,
    fallback_exchange: str | None = None,
):
    """Construct a FRESH market-data provider (never cached — see module docstring).

    Primary defaults to ``market_data.ccxt_exchange`` (overridable via ``exchange``).
    Fallback defaults to ``market_data.ccxt_fallback`` and can be explicitly pinned via
    ``fallback_exchange`` for venue-specific sources such as xStocks.

    When ``enable_crypto_fallback`` is True (default), returns a FallbackChainProvider
    wrapping [CCXTProvider, CoinGeckoProvider, BinancePublicProvider]. Callers that
    require CCXT-native batch methods can request ``native_ccxt=True``; they still
    enter through this factory and receive a fresh provider with configured fallback.
    """
    from aletheia.config.loader import load_config

    md = load_config().market_data
    primary = exchange or md.ccxt_exchange
    ccxt = CCXTProvider(
        exchange=primary,
        fallback_exchange=fallback_exchange or md.ccxt_fallback,
    )

    if native_ccxt or not getattr(md, "enable_crypto_fallback", True):
        return ccxt

    from aletheia.data.sources.binance_public import BinancePublicProvider
    from aletheia.data.sources.chain import FallbackChainProvider
    from aletheia.data.sources.coingecko import CoinGeckoProvider

    return FallbackChainProvider([ccxt, CoinGeckoProvider(), BinancePublicProvider()])

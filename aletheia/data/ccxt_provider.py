"""CCXT-backed market-data provider — the single source of public OHLCV/tickers.

Why a per-call factory (``get_market_data_provider``) instead of a cached singleton:
ccxt.async_support binds its aiohttp session to the event loop that created it. The
APScheduler jobs each run on a FRESH event loop (``asyncio.run`` per job), so a cached
provider from a previous loop raises "Event loop is closed" on the next fetch. Every
caller therefore constructs a provider, uses it, and ``await``s ``close()`` in a finally.

Primary/fallback: each fetch tries the primary exchange, and on failure retries once on
the configured fallback. xStocks (ON-series) are the one site that constructs
``CCXTProvider(exchange="bitget", fallback_exchange="bitget")`` directly, because only
Bitget lists those tokens and the config fallback (bybit) would not find them.

Symbols enter as canonical ``BTC/USDT`` strings or ``InstrumentId`` objects and are
resolved to the CCXT unified symbol (with perp settle suffix when needed) at the boundary.
OHLCV fetches are best-effort persisted to ``market_ohlcv`` so charts have history even
when a later fetch fails; persistence failures never propagate.
"""

from __future__ import annotations

import logging

from aletheia.data.providers import Candle, Ticker
from aletheia.data.symbols import InstrumentId, to_canonical

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
        import aiohttp
        import ccxt.async_support as ccxt
        from aiohttp.resolver import ThreadedResolver

        if not hasattr(ccxt, name):
            raise ValueError(f"unknown ccxt exchange: {name}")
        # Force aiohttp's OS-resolver (getaddrinfo) instead of the c-ares
        # AsyncResolver that aiohttp auto-selects when aiodns is installed. On
        # Windows/locked-down hosts c-ares can't read DNS config and every fetch
        # dies with ClientConnectorDNSError ("Could not contact DNS servers"),
        # even though plain urllib (OS resolver) reaches the same host fine. We
        # inject our own session so ccxt uses it (own_session=False), which means
        # WE are responsible for closing it — done in close().
        session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=ThreadedResolver()))
        self._sessions.append(session)
        return getattr(ccxt, name)({"enableRateLimit": True, "session": session})

    def _ensure_primary(self):
        if self._primary is None:
            self._primary = self._client(self._primary_name)
        return self._primary

    def _ensure_fallback(self):
        if self._fallback is None:
            self._fallback = self._client(self._fallback_name)
        return self._fallback

    async def get_ticker(self, symbol: InstrumentId | str) -> Ticker:
        ccxt_symbol, canon, _ = _resolve_ccxt_symbol(symbol)
        last_err: Exception | None = None
        for name, ensure in (
            (self._primary_name, self._ensure_primary),
            (self._fallback_name, self._ensure_fallback),
        ):
            try:
                raw = await ensure().fetch_ticker(ccxt_symbol)
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
                logger.warning("ticker fetch failed on %s for %s: %s", name, ccxt_symbol, e)
        raise last_err if last_err else RuntimeError("ticker fetch failed")

    async def get_ohlcv(
        self,
        symbol: InstrumentId | str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
    ) -> list[Candle]:
        ccxt_symbol, canon, _ = _resolve_ccxt_symbol(symbol)
        last_err: Exception | None = None
        for name, ensure in (
            (self._primary_name, self._ensure_primary),
            (self._fallback_name, self._ensure_fallback),
        ):
            try:
                raw = await ensure().fetch_ohlcv(
                    ccxt_symbol, timeframe=timeframe, since=since, limit=limit
                )
                candles = [
                    Candle(
                        timestamp=int(row[0]),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                    for row in raw
                ]
                self._persist(canon, timeframe, candles, name)
                return candles
            except Exception as e:  # noqa: BLE001 — try fallback, then re-raise
                last_err = e
                logger.warning("ohlcv fetch failed on %s for %s: %s", name, ccxt_symbol, e)
        raise last_err if last_err else RuntimeError("ohlcv fetch failed")

    @staticmethod
    def _persist(canon: str, timeframe: str, candles: list[Candle], exchange: str) -> None:
        """Best-effort persist to market_ohlcv; never raise into the fetch path."""
        if not candles:
            return
        try:
            from aletheia.storage.repositories import upsert_ohlcv_candles

            upsert_ohlcv_candles(
                symbol=canon, timeframe=timeframe, candles=candles,
                provider="ccxt", exchange=exchange,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("ohlcv persist skipped for %s/%s: %s", canon, timeframe, e)

    async def close(self) -> None:
        """Close exchange clients AND the aiohttp sessions we injected. Idempotent.

        ccxt does not close an injected session (own_session=False), so the
        sessions in self._sessions are ours to close — otherwise aiohttp logs
        "Unclosed client session" and leaks connectors across scheduler loops.
        """
        for attr in ("_primary", "_fallback"):
            client = getattr(self, attr)
            if client is not None:
                try:
                    await client.close()
                except Exception as e:  # noqa: BLE001
                    logger.debug("provider close error: %s", e)
                setattr(self, attr, None)
        for session in self._sessions:
            try:
                await session.close()
            except Exception as e:  # noqa: BLE001
                logger.debug("session close error: %s", e)
        self._sessions = []


def _opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_market_data_provider(exchange: str | None = None) -> CCXTProvider:
    """Construct a FRESH market-data provider (never cached — see module docstring).

    Primary defaults to ``market_data.ccxt_exchange`` (overridable via ``exchange``);
    the fallback ALWAYS comes from ``market_data.ccxt_fallback``, never the override.
    """
    from aletheia.config.loader import load_config

    md = load_config().market_data
    primary = exchange or md.ccxt_exchange
    return CCXTProvider(exchange=primary, fallback_exchange=md.ccxt_fallback)

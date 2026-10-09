"""CCXT-backed market-data provider — the single source of public OHLCV/tickers.

Why a per-call factory (``get_market_data_provider``) instead of a cached singleton:
ccxt.async_support binds its aiohttp session to the event loop that created it. The
APScheduler jobs each run on a FRESH event loop (``asyncio.run`` per job), so a cached
provider from a previous loop raises "Event loop is closed" on the next fetch. Every
caller therefore constructs a provider, uses it, and ``await``s ``close()`` in a finally.
The one process-level cache is ``_MARKETS_CACHE``: plain market-metadata dicts (no
client, session or loop-bound object) that let the orderbook observation skip a
repeated cold ``load_markets`` inside its five-second budget.

Primary/fallback: each fetch tries the primary exchange, and on failure retries once on
the configured fallback. Venue-pinned xStock listing checks still enter through
``get_market_data_provider(native_ccxt=True, fallback_exchange=exchange)`` so they keep
the managed session and malformed-market guard without substituting another venue.
The optional ``get_ohlcv_observation`` capability is deliberately primary-only and
does not enter this fallback loop.

The remaining documented direct-construction site is an intentional venue-native
exception (routing through the generic factory would select the configured fallback):

- xStocks (ON-series): builds ``CCXTProvider(exchange="bitget", fallback_exchange="bitget")``
  because only Bitget lists those tokens and the config fallback (bybit) would not find them.

Registry sync and feature volume conversion use the existing Core factory's
exact-venue selection and normalized ``get_market_instruments`` capability.
They load primary metadata only; the historical configured fallback client was
never read by either path.

Any NEW direct construction site should be added here with its reason; a bare
``CCXTProvider(...)`` outside this list is a review-flagged deviation.

Symbols enter as canonical ``BTC/USDT`` strings or ``InstrumentId`` objects and are
resolved to the CCXT unified symbol (with perp settle suffix when needed) at the boundary.
OHLCV fetches are best-effort persisted to ``market_ohlcv`` so charts have history even
when a later fetch fails; persistence failures never propagate.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import math
import time
from typing import Any

from aletheia.data import providers
from aletheia.data.providers import (
    Candle,
    MarketInstrument,
    OHLCVAttempt,
    OHLCVResult,
    Ticker,
    _required_ticker_field,
    classify_ohlcv_outcome,
    parse_ohlcv_candle,
)
from aletheia.data.symbols import (
    InstrumentId,
    build_instrument_background,
    to_canonical,
    to_ccxt_perp,
)
from aletheia.data.venue_health import (
    VenueCoolingDownError,
    is_venue_available,
    is_venue_refusal,
    record_venue_failure,
    record_venue_success,
)

logger = logging.getLogger("aletheia.ccxt_provider")

_FEATURE_TICKER_CHUNK_SIZE = 100


def _precision_to_decimals(p) -> int | None:
    """Normalize CCXT decimal-place or fractional tick-size precision."""
    if p is None:
        return None
    try:
        if isinstance(p, float) and 0 < p < 1:
            return max(0, int(round(-math.log10(p))))
        return int(p)
    except (ValueError, OverflowError):
        return None


def _resolve_ccxt_symbol(symbol: InstrumentId | str) -> tuple[str, str, str]:
    """Return (ccxt_symbol, canonical_symbol, market_type) for a fetch target."""
    if isinstance(symbol, InstrumentId):
        return symbol.to_ccxt_symbol(), symbol.symbol, symbol.market_type
    canon = to_canonical(symbol)
    return canon, canon, "spot"


def _cached_ohlcv_result(cached: object, requested: int) -> OHLCVResult:
    if isinstance(cached, OHLCVResult):
        return OHLCVResult(
            cached,
            requested=cached.requested,
            rejected=cached.rejected,
            attempts=cached.attempts,
            source="memory_cache",
        )
    return OHLCVResult(cached, requested=requested, source="memory_cache")


def _install_market_id_guard(exchange: Any, exchange_name: str) -> Any:
    """Drop malformed CCXT market rows before `set_markets()` sorts by id.

    Some OKX responses can include a market entry with `id=None`. CCXT then
    builds `markets_by_id` with mixed `None` and `str` keys and Python raises
    while sorting them. One bad row should not stop the entire market feed.

    ``set_markets()`` is the only place CCXT replaces ``exchange.markets``;
    ``load_markets()`` serves its cached result without calling it again. So
    this is also where the snapshot gets its acquisition time, stored with the
    snapshot so a reader never pairs one snapshot with another's time (#3786).
    """
    if getattr(exchange, "_aletheia_market_id_guard", False):
        return exchange

    original_set_markets = exchange.set_markets

    def guarded_set_markets(markets, currencies=None, *, acquired_at=None):
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
        result = original_set_markets(markets, currencies)
        exchange._candidate_markets_snapshot = (
            getattr(exchange, "markets", None) or {},
            acquired_at or datetime.now(timezone.utc).isoformat(),
        )
        return result

    exchange.set_markets = guarded_set_markets
    exchange._aletheia_market_id_guard = True
    return exchange


# Market-metadata snapshots shared by fresh observation clients. Only plain dicts
# are kept, never a client or session (those are loop-bound; see module docstring).
# Order rules and venue listing reads call ``load_markets`` directly and never read it.
_MARKETS_CACHE: dict[tuple[str, bool, str], tuple[float, dict, dict, str]] = {}
_MARKETS_CACHE_TTL_S = 3600.0


async def _load_markets_cached(exchange: Any) -> None:
    """Load markets, restoring a recent snapshot for the same class/sandbox/options.

    The restore keeps the snapshot's original acquisition time (#3786).
    """
    key = None
    if not getattr(exchange, "markets", None):
        key = (
            type(exchange).__name__,
            bool(getattr(exchange, "isSandboxModeEnabled", False)),
            json.dumps(getattr(exchange, "options", {}), sort_keys=True, default=repr),
        )
        hit = _MARKETS_CACHE.get(key)
        if hit is not None and time.monotonic() - hit[0] < _MARKETS_CACHE_TTL_S:
            exchange.set_markets(hit[1], hit[2], acquired_at=hit[3])
            return
    await exchange.load_markets()
    snapshot = getattr(exchange, "_candidate_markets_snapshot", None)
    if key is not None and snapshot and snapshot[0]:
        _MARKETS_CACHE[key] = (
            time.monotonic(), snapshot[0], dict(getattr(exchange, "currencies", None) or {}), snapshot[1],
        )


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

    def _client(self, name: str, **options):
        # Build via the managed factory (aletheia.net.make_ccxt), which injects a
        # proxy-aware aiohttp session using aiohttp's OS-resolver (getaddrinfo)
        # instead of the c-ares AsyncResolver that aiohttp auto-selects when aiodns
        # is installed — on Windows/locked-down hosts c-ares can't read DNS config
        # and every fetch dies with ClientConnectorDNSError. We own the injected
        # session (own_session=False) and close it in close().
        from aletheia.net import make_ccxt

        exchange, session = make_ccxt(name, **options)
        self._sessions.append(session)
        return _install_market_id_guard(exchange, name)

    def _ensure_primary(self, market_type: str | None = None):
        if self._primary_name == "binance" and market_type in {"perp", "futures"}:
            if getattr(self, "_primary_perp", None) is None:
                self._primary_perp = self._client(
                    "binanceusdm", options={"defaultType": "swap"}
                )
            return self._primary_perp
        if self._primary is None:
            self._primary = self._client(self._primary_name)
        return self._primary

    def _ensure_fallback(self, market_type: str | None = None):
        if self._fallback_name == "binance" and market_type in {"perp", "futures"}:
            if getattr(self, "_fallback_perp", None) is None:
                self._fallback_perp = self._client(
                    "binanceusdm", options={"defaultType": "swap"}
                )
            return self._fallback_perp
        if self._fallback is None:
            self._fallback = self._client(self._fallback_name)
        return self._fallback

    @contextmanager
    def _market_type_context(self, exchange, market_type: str):
        """Temporarily select the physical derivatives API for one request."""
        if market_type not in {"perp", "futures"}:
            yield exchange
            return
        had_default_type = "defaultType" in exchange.options
        previous_default_type = exchange.options.get("defaultType")
        exchange.options["defaultType"] = "swap"
        try:
            yield exchange
        finally:
            if had_default_type:
                exchange.options["defaultType"] = previous_default_type
            else:
                exchange.options.pop("defaultType", None)

    async def get_ticker(self, symbol: InstrumentId | str) -> Ticker:
        ccxt_symbol, canon, market_type = _resolve_ccxt_symbol(symbol)
        last_err: Exception | None = None
        attempts = (
            (self._primary_name, self._ensure_primary),
            (self._fallback_name, self._ensure_fallback),
        )
        for index, (name, ensure) in enumerate(attempts):
            if index and name == self._primary_name:
                continue
            if not is_venue_available(name):
                last_err = VenueCoolingDownError(f"market-data venue cooling down: {name}")
                continue
            try:
                exchange = ensure(market_type)
                with self._market_type_context(exchange, market_type):
                    raw = await exchange.fetch_ticker(ccxt_symbol)
                ticker = Ticker(
                    symbol=canon,
                    last_price=_required_ticker_field(
                        raw, "last", "close", field="last_price",
                    ),
                    volume_24h=_required_ticker_field(
                        raw, "quoteVolume", "baseVolume", field="volume_24h",
                    ),
                    change_pct_24h=_required_ticker_field(
                        raw, "percentage", field="change_pct_24h",
                    ),
                    high_24h=raw.get("high"),
                    low_24h=raw.get("low"),
                    provider=name,
                )
                record_venue_success(name)
                return ticker
            except Exception as e:  # noqa: BLE001 — try fallback, then re-raise
                last_err = e
                if is_venue_refusal(e):
                    record_venue_failure(name, e)
                logger.warning("ticker fetch failed on %s for %s: %s", name, ccxt_symbol, e)
        raise last_err if last_err else RuntimeError("ticker fetch failed")

    async def get_tickers_observation(
        self, symbols: list[str], *, market_type: str = "spot",
    ) -> dict[str, dict]:
        """Fetch feature tickers from the exact primary market without fallback."""
        exchange = self._ensure_primary(market_type)
        is_perp = market_type in {"perp", "futures", "swap", "usdt_futures"}
        with self._market_type_context(exchange, market_type):
            if not getattr(exchange, "markets", None):
                await exchange.load_markets()
        requested: list[str] = []
        seen: set[str] = set()
        for symbol in symbols:
            if not isinstance(symbol, str) or not symbol.isascii():
                continue
            ccxt_symbol = to_ccxt_perp(symbol) if is_perp else to_canonical(symbol)
            if ccxt_symbol in seen or ccxt_symbol not in (getattr(exchange, "markets", {}) or {}):
                continue
            seen.add(ccxt_symbol)
            requested.append(ccxt_symbol)

        tickers: dict[str, dict] = {}
        with self._market_type_context(exchange, market_type):
            for offset in range(0, len(requested), _FEATURE_TICKER_CHUNK_SIZE):
                chunk = requested[offset:offset + _FEATURE_TICKER_CHUNK_SIZE]
                try:
                    raw = await exchange.fetch_tickers(chunk)
                    if not isinstance(raw, dict):
                        raise TypeError("ticker batch is not a mapping")
                    for symbol in chunk:
                        ticker = raw.get(symbol)
                        if isinstance(ticker, dict) and ticker:
                            tickers[symbol] = dict(ticker)
                except Exception as exc:  # noqa: BLE001 — preserve per-chunk failure semantics
                    logger.warning(
                        "Ticker chunk %d-%d failed: %s",
                        offset,
                        offset + len(chunk),
                        exc,
                    )
        return tickers

    async def get_ohlcv(
        self,
        symbol: InstrumentId | str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
        *,
        use_cache: bool = True,
        _record_venue_health: bool = True,
        _persist_result: bool = True,
    ) -> OHLCVResult:
        ccxt_symbol, canon, mkt = _resolve_ccxt_symbol(symbol)

        from aletheia.data.cache import get_cache
        cache = get_cache()
        if use_cache and since is None:
            cached = cache.get(
                canon, timeframe, limit,
                market_type=mkt, exchange=self._primary_name,
            )
            if cached is not None:
                return _cached_ohlcv_result(cached, limit)

        last_err: Exception | None = None
        attempts: list[OHLCVAttempt] = []
        for index, (name, ensure) in enumerate((
            (self._primary_name, self._ensure_primary),
            (self._fallback_name, self._ensure_fallback),
        )):
            if index and name == self._primary_name:
                continue
            if use_cache and since is None and name != self._primary_name:
                cached = cache.get(
                    canon, timeframe, limit,
                    market_type=mkt, exchange=name,
                )
                if cached is not None:
                    return _cached_ohlcv_result(cached, limit)
            if not is_venue_available(name):
                last_err = VenueCoolingDownError(f"market-data venue cooling down: {name}")
                continue
            try:
                exchange = ensure(mkt)
                with self._market_type_context(exchange, mkt):
                    raw = await exchange.fetch_ohlcv(
                        ccxt_symbol, timeframe=timeframe, since=since, limit=limit
                    )
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
                if _record_venue_health:
                    record_venue_success(name)
                # Mark the trailing bar as open when its close time is still in
                # the future. CCXT always returns the currently-forming bar as
                # the last element; treat it as unconfirmed so callers can
                # exclude it from indicator calculations.
                if candles:
                    tf_ms = providers.timeframe_ms(timeframe)
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
                    source="provider_fetch",
                )
                if use_cache and since is None:
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
                source="provider_fetch",
            )
        if last_err:
            last_err.ohlcv_attempts = tuple(attempts)  # type: ignore[attr-defined]
            raise last_err
        raise RuntimeError("ohlcv fetch failed")

    async def get_taker_buy_ratio(self, symbol: str) -> float | None:
        """Read the existing venue-native 1h ratio; never persist or fall back."""
        exchange = self._ensure_primary("perp")
        ex_id = getattr(exchange, "id", "")
        if ex_id == "binance":
            taker_data = await asyncio.wait_for(
                exchange.fapipublic_get_takerlongshortratio({
                    "symbol": symbol.replace("/", ""), "period": "1h", "limit": 1,
                }),
                timeout=5.0,
            )
            if taker_data and len(taker_data) > 0:
                ratio = float(taker_data[0].get("buySellRatio", 0))
                if ratio > 0:
                    return ratio
        elif ex_id == "okx":
            resp = await asyncio.wait_for(
                exchange.public_get_rubik_stat_taker_volume({
                    "ccy": symbol.split("/")[0], "instType": "CONTRACTS", "period": "1H",
                }),
                timeout=5.0,
            )
            rows = (resp or {}).get("data") or []
            if rows:
                sell_vol = float(rows[0][1])
                buy_vol = float(rows[0][2])
                if sell_vol > 0:
                    return buy_vol / sell_vol
        return None

    async def get_funding_observation(
        self, symbol: str, *, ticker: dict | None = None,
    ) -> dict | None:
        """Acquire and normalize one exact-venue funding observation."""
        from aletheia.data.derivatives_facts import (
            finite,
            funding_interval_seconds,
            instrument_from_market,
            normalize_funding,
            provider_source_time,
        )

        exchange = self._ensure_primary("perp")
        exchange_id = getattr(exchange, "id", self._primary_name)
        perp_symbol = to_ccxt_perp(symbol)
        markets, markets_acquired_at = getattr(exchange, "_candidate_markets_snapshot", None) or (
            getattr(exchange, "markets", {}) or {}, None,
        )
        market = markets.get(perp_symbol) or {}
        if exchange_id == "bitget":
            info = (ticker or {}).get("info") or {}
            raw = {
                "fundingRate": info.get("fundingRate"),
                "markPrice": info.get("markPrice"),
                "timestamp": (ticker or {}).get("timestamp"),
            }
        else:
            raw = await asyncio.wait_for(
                exchange.fetch_funding_rate(perp_symbol), timeout=5.0,
            )
        if not isinstance(raw, dict):
            return None

        received_at = datetime.now(timezone.utc)
        source_observed_at, source_time_status = provider_source_time(raw, received_at)
        interval_raw = raw
        interval_source = "funding_response.interval"
        if exchange_id == "bitget":
            hours = (market.get("info") or {}).get("fundInterval")
            interval_raw = {"interval": f"{hours}h"}
            interval_source = "bitget.contracts.fundInterval"
        evidence = normalize_funding(
            raw,
            instrument=instrument_from_market(symbol, market),
            provider="ccxt",
            venue=exchange_id,
            acquired_at=received_at,
            interval_seconds=funding_interval_seconds(interval_raw),
        )
        return {
            "funding_rate": finite(raw.get("fundingRate")),
            "observed_at": source_observed_at,
            "source_time_status": source_time_status,
            "received_at": received_at.isoformat(),
            "evidence": evidence,
            "interval_source": interval_source,
            "interval_raw": interval_raw.get("interval"),
            "interval_acquired_at": (
                markets_acquired_at if exchange_id == "bitget" else received_at.isoformat()
            ),
        }

    async def get_open_interest_observation(self, symbol: str) -> dict | None:
        """Acquire and normalize one exact-venue open-interest observation."""
        from aletheia.data.derivatives_facts import (
            ccxt_metadata,
            normalize_open_interest,
            provider_source_time,
        )

        exchange = self._ensure_primary("perp")
        perp_symbol = to_ccxt_perp(symbol)
        raw = await asyncio.wait_for(
            exchange.fetch_open_interest(perp_symbol), timeout=5.0,
        )
        if not isinstance(raw, dict):
            return None
        received_at = datetime.now(timezone.utc)
        source_observed_at, source_time_status = provider_source_time(raw, received_at)
        market = (getattr(exchange, "markets", {}) or {}).get(perp_symbol) or {}
        venue = self._primary_name
        metadata = json.loads(
            ccxt_metadata(symbol, venue, market, raw, family="open_interest")
        )
        fact = normalize_open_interest(
            raw,
            instrument=metadata["instrument"],
            provider="ccxt",
            venue=venue,
            acquired_at=received_at,
            value_unit=metadata.get("oi_unit"),
        )
        return {
            "observed_at": source_observed_at,
            "source_time_status": source_time_status,
            "received_at": received_at.isoformat(),
            "fact": fact,
        }

    async def get_orderbook_observation(
        self, symbol: str, *, market_type: str = "spot",
    ) -> dict | None:
        """Acquire and normalize one exact-market orderbook without writing it."""
        from extensions.data.collectors.microstructure import _contract_size, _parse_orderbook

        phase = "provider_setup"
        try:
            exchange = self._ensure_primary(market_type)
            ccxt_symbol = to_ccxt_perp(symbol) if market_type in {"perp", "futures"} else to_canonical(symbol)
            with self._market_type_context(exchange, market_type):
                phase = "load_markets"
                await _load_markets_cached(exchange)
                phase = "market_metadata"
                market = exchange.market(ccxt_symbol)
                is_perp = market_type in {"perp", "futures"}
                if (
                    market.get("symbol") != ccxt_symbol
                    or bool(market.get("contract")) != is_perp
                    or (not is_perp and not market.get("spot"))
                ):
                    return None
                contract_size = _contract_size(market)
                started = time.monotonic()
                phase = "fetch_order_book"
                async with asyncio.timeout(5):
                    book = await exchange.fetch_order_book(ccxt_symbol, limit=20)
            phase = "parse"
            if not isinstance(book, dict) or book.get("symbol") not in (None, ccxt_symbol):
                return None
            snapshot = _parse_orderbook(
                to_canonical(symbol),
                self._primary_name,
                book,
                int((time.monotonic() - started) * 1000),
                contract_size=contract_size,
                market_type=market_type,
            )
            return snapshot.model_dump() if snapshot is not None else None
        except Exception as exc:
            raise providers.MarketDataObservationError(phase, exc) from None

    async def get_market_instruments(
        self, market_type: str | None = None,
    ) -> list[MarketInstrument]:
        requested_market_type = market_type
        exchange = (
            self._ensure_primary(requested_market_type)
            if requested_market_type is not None
            else self._ensure_primary()
        )
        with self._market_type_context(exchange, requested_market_type or "spot"):
            await exchange.load_markets()
        result = []
        for symbol_key, info in exchange.markets.items():
            if not info.get("active", True) or info.get("option") or info.get("future"):
                continue
            quote = (info.get("quote") or "").upper()
            base = (info.get("base") or "").upper()
            if quote != "USDT" or not base:
                continue
            instrument_market_type = "perp" if info.get("swap") else "spot"
            if requested_market_type and instrument_market_type != requested_market_type:
                continue
            native_id = (info.get("id") or "").upper()
            precision = info.get("precision") or {}
            result.append(MarketInstrument(
                venue=self._primary_name,
                symbol=f"{base}/{quote}",
                base_asset=base,
                quote_asset=quote,
                market_type=instrument_market_type,
                background=build_instrument_background(
                    symbol=str(info.get("symbol") or symbol_key),
                    venue=self._primary_name,
                    market_type=instrument_market_type,
                    market_info=info,
                ),
                min_notional=((info.get("limits") or {}).get("cost") or {}).get("min"),
                price_precision=_precision_to_decimals(precision.get("price")),
                amount_precision=_precision_to_decimals(precision.get("amount")),
                contract_size=_opt_float(info.get("contractSize")),
                is_test_or_dated="TEST" in native_id or "_" in native_id,
                is_tokenized_equity=str((info.get("info") or {}).get("instCategory") or "") == "3",
            ))
        return result

    async def get_ohlcv_observation(
        self,
        symbol: InstrumentId | str,
        timeframe: str = "1h",
        limit: int = 100,
        since: int | None = None,
        *,
        use_cache: bool = True,
    ) -> OHLCVResult:
        """Fetch fresh primary-venue OHLCV without fallback or durable writes."""
        ccxt_symbol, canon, mkt = _resolve_ccxt_symbol(symbol)

        from aletheia.data.cache import get_cache

        cache = get_cache()
        if use_cache and since is None:
            cached = cache.get(
                canon, timeframe, limit,
                market_type=mkt, exchange=self._primary_name,
            )
            if cached is not None:
                return _cached_ohlcv_result(cached, limit)

        if not is_venue_available(self._primary_name):
            exc = VenueCoolingDownError(
                f"market-data venue cooling down: {self._primary_name}"
            )
            exc.ohlcv_attempts = (OHLCVAttempt(  # type: ignore[attr-defined]
                provider=self._primary_name,
                outcome="failed",
                reason=type(exc).__name__,
            ),)
            raise exc

        exchange = self._ensure_primary(mkt)
        try:
            with self._market_type_context(exchange, mkt):
                raw = await exchange.fetch_ohlcv(
                    ccxt_symbol, timeframe=timeframe, since=since, limit=limit,
                )
            parsed = [
                parse_ohlcv_candle(row, provider="ccxt", exchange=self._primary_name)
                for row in raw
            ]
            candles = [candle for candle in parsed if candle is not None]
            rejected = len(parsed) - len(candles)
            attempt = OHLCVAttempt(
                provider=self._primary_name,
                outcome=classify_ohlcv_outcome(
                    accepted=len(candles), rejected=rejected, requested=limit,
                ),
                accepted=len(candles),
                rejected=rejected,
            )
            if candles:
                tf_ms = providers.timeframe_ms(timeframe)
                if tf_ms > 0 and candles[-1].timestamp + tf_ms > int(time.time() * 1000):
                    candles[-1].is_closed = False
            result = OHLCVResult(
                candles,
                requested=limit,
                rejected=rejected,
                attempts=(attempt,),
                source="provider_fetch",
            )
            if use_cache and since is None:
                cache.set(
                    canon, timeframe, limit, result,
                    market_type=mkt, exchange=self._primary_name,
                )
            return result
        except Exception as exc:  # noqa: BLE001 — preserve one exact-venue attempt
            attempt = OHLCVAttempt(
                provider=self._primary_name,
                outcome="failed",
                reason=type(exc).__name__,
            )
            exc.ohlcv_attempts = (attempt,)  # type: ignore[attr-defined]
            raise

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

    async def get_public_market_capabilities(
        self,
        *,
        sandbox: bool = False,
        extra_options: dict | None = None,
        exchange_name: str | None = None,
    ) -> dict[str, frozenset[str]]:
        """Read active USDT spot/perp symbols without fallback or persistence.

        The sandbox client is request-local and never changes this provider's
        ordinary clients. No credentials or private endpoints are involved.
        ``extra_options`` merges into the CCXT client options (e.g. a profile's
        ``listing_options`` selecting an API category) so the listing read
        matches the exact profile that will submit orders.
        ``exchange_name`` overrides the primary venue name when the execution
        profile uses a different CCXT class (e.g. ``binanceusdm`` for testnet).
        """
        from aletheia.net import close_ccxt, make_ccxt

        exchange, session = make_ccxt(
            exchange_name or self._primary_name,
            options={"defaultType": "swap", **(extra_options or {})},
        )
        try:
            if sandbox:
                exchange.set_sandbox_mode(True)
            await exchange.load_markets()
            symbols: dict[str, set[str]] = {"usdt_futures": set(), "spot": set()}
            for info in exchange.markets.values():
                if not info.get("active", True):
                    continue
                if (info.get("quote") or "").upper() != "USDT":
                    continue
                if info.get("spot"):
                    market_type = "spot"
                elif info.get("swap") and not info.get("future"):
                    market_type = "usdt_futures"
                else:
                    continue
                canonical = to_canonical(f"{info.get('base', '')}/USDT")
                symbols[market_type].add(canonical)
            return {key: frozenset(value) for key, value in symbols.items()}
        finally:
            if not await close_ccxt(exchange, session):
                raise RuntimeError("ccxt_cleanup_failed")

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

        closed: set[int] = set()
        for attr in ("_primary", "_fallback", "_primary_perp", "_fallback_perp"):
            client = getattr(self, attr, None)
            if client is not None:
                if id(client) in closed:
                    setattr(self, attr, None)
                    continue
                try:
                    await _aio.wait_for(client.close(), timeout=5.0)
                except _aio.CancelledError:
                    pass
                except Exception:
                    logger.debug("ccxt_provider: client close timed out or failed during teardown", exc_info=True)
                closed.add(id(client))
                setattr(self, attr, None)
        for session in self._sessions:
            try:
                await _aio.wait_for(session.close(), timeout=5.0)
            except _aio.CancelledError:
                pass
            except Exception:
                logger.debug("ccxt_provider: session close timed out or failed during teardown", exc_info=True)
        self._sessions = []


async def read_market_listing(venue: str, spot_only: bool) -> frozenset[str]:
    """Normalize exact-venue public listings; own and close the fresh client."""
    provider = get_market_data_provider(
        exchange=venue, native_ccxt=True, fallback_exchange=venue,
    )
    try:
        exchange = provider._ensure_primary()
        if spot_only:
            exchange.options["defaultType"] = "spot"
        markets = await exchange.load_markets()
        if not isinstance(markets, dict) or not markets:
            raise ValueError(f"load_markets returned empty/invalid result for {venue}")
        # Preserve the two consumers' existing active predicates, including 0:
        # spot-only uses identity; the all-market cache uses equality.
        listed = frozenset(
            to_canonical(str(symbol))
            for symbol, market in markets.items()
            if isinstance(market, dict)
            and (
                market.get("spot") is True and market.get("active") is not False
                if spot_only else market.get("active") != False  # noqa: E712
            )
        )
        if not listed:
            raise ValueError(f"no active {'spot ' if spot_only else ''}markets found for {venue}")
        return listed
    finally:
        await provider.close()


def _opt_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("ticker optional numeric field is invalid") from exc


def create_market_data_provider(
    exchange: str | None = None,
    *,
    exact_venue: bool = False,
    fallback_venue: str | None = None,
):
    """Bind standard Core reads to the existing fresh concrete provider policy."""
    if exact_venue:
        if not exchange:
            raise ValueError("exact market data requires a venue")
        return get_market_data_provider(
            exchange,
            native_ccxt=True,
            fallback_exchange=fallback_venue or exchange,
        )
    return get_market_data_provider(exchange)


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

    from extensions.data.sources.binance_public import BinancePublicProvider
    from extensions.data.sources.chain import FallbackChainProvider
    from extensions.data.sources.coingecko import CoinGeckoProvider

    return FallbackChainProvider([ccxt, CoinGeckoProvider(), BinancePublicProvider()])

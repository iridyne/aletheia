"""Public CCXT transport, wire normalization and loop-local client ownership."""

from __future__ import annotations

from extensions.exchanges.ccxt.derivatives_metadata import (
    ccxt_metadata, funding_interval_seconds, instrument_from_market, provider_source_time,
)

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any

from aletheia.data import market_history
from aletheia.data.models import OrderbookSnapshot, OpenInterestSnapshot, FundingRateSnapshot
from aletheia.data.derivatives_facts import DerivativesFact
from aletheia.data.providers import (
    OHLCVAttempt,
    OHLCVResult,
    Ticker,
    _required_ticker_field,
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

from aletheia.data.ccxt_provider import timeframe_ms

logger = logging.getLogger("aletheia.ccxt_provider")


def _to_perp_symbol(spot_symbol: str, exchange) -> str | None:
    """Convert canonical spot BTC/USDT to the perp symbol on this exchange."""
    # CCXT perp: BTC/USDT:USDT
    perp = f"{spot_symbol}:USDT"
    if perp in (exchange.markets or {}):
        return perp
    return None


def _parse_price(symbol, venue, raw, *, market, acquired_at):
    from aletheia.data.derivatives_facts import (
        price_snapshot,
    )

    instrument = instrument_from_market(symbol, market)
    if raw.get('symbol') is not None:
        if raw['symbol'] != f"{instrument['canonical_symbol']}:{instrument['settle_asset']}":
            return None
    source, status = provider_source_time(raw, acquired_at)
    return price_snapshot(
        symbol, venue, instrument=instrument, acquired_at=acquired_at, provider='ccxt',
        mark_price=raw.get('markPrice'), reference_price=raw.get('indexPrice'),
        source_observed_at=source, source_time_status=status,
    )


def _parse_funding(
    symbol: str, exchange: str, raw: dict, *, market: dict | None = None, metadata_reader=ccxt_metadata
) -> FundingRateSnapshot | None:
    from aletheia.data.derivatives_facts import finite
    rate = finite(raw.get("fundingRate"))
    if rate is None:
        return None
    from aletheia.data.derivatives_facts import annualize_funding
    annualized = annualize_funding(rate, funding_interval_seconds(raw))
    next_time = raw.get("fundingDatetime") or raw.get("nextFundingDatetime")
    raw_mark = raw.get("markPrice")
    mark = finite(raw_mark) if raw_mark is not None else None
    if raw_mark is not None and (mark is None or mark <= 0):
        return None
    acquired_at = datetime.now(timezone.utc)
    source_observed_at, source_time_status = provider_source_time(raw, acquired_at)

    try:
        return FundingRateSnapshot(
            symbol=symbol,
            exchange=exchange,
            funding_rate=rate,
            funding_rate_annualized=annualized,
            derivatives_metadata_json=metadata_reader(symbol, exchange, market or {}, raw, family="funding", acquired_at=acquired_at.isoformat()),
            next_funding_time=str(next_time) if next_time else None,
            mark_price=mark,
            source_observed_at=source_observed_at,
            acquired_at=acquired_at.isoformat(),
            source_time_status=source_time_status,
            observed_at=source_observed_at or acquired_at.isoformat(),
        )
    except ValueError:
        return None


def _normalize_oi_fact(raw, *, acquired_at, **kwargs):
    from aletheia.data.derivatives_facts import normalize_open_interest

    source, status = provider_source_time(raw, acquired_at)
    return normalize_open_interest(
        amount=raw.get("openInterestAmount"), reported_value=raw.get("openInterestValue"),
        source_observed_at=source, source_time_status=status, acquired_at=acquired_at, **kwargs,
    )


def _parse_oi(
    symbol: str,
    exchange: str,
    raw: dict,
    lsr: float | None = None,
    top_position_ratio: float | None = None,
    *, market: dict | None = None, metadata_reader=ccxt_metadata,
) -> OpenInterestSnapshot | None:
    from aletheia.data.derivatives_facts import open_interest_usd, finite
    metadata = metadata_reader(symbol, exchange, market or {}, raw, family='open_interest')
    proof = json.loads(metadata)
    oi_usd, reason = open_interest_usd(amount=raw.get("openInterestAmount"), reported_value=raw.get("openInterestValue"),
        instrument=proof['instrument'], value_unit=proof['oi_unit'])
    if reason or oi_usd is None:
        return None
    # A CCXT amount is not a proved base quantity; do not relabel contracts as coins.
    oi_coin = None

    # Binance positioning ratios are optional enrichment.  Invalid enrichment
    # must degrade to unavailable, never discard an otherwise valid venue OI
    # snapshot (MS-C01).
    lsr = finite(lsr)
    if lsr is not None and lsr < 0:
        lsr = None
    top_position_ratio = finite(top_position_ratio)
    if top_position_ratio is not None and top_position_ratio < 0:
        top_position_ratio = None
    acquired_at = datetime.now(timezone.utc)
    source_observed_at, source_time_status = provider_source_time(raw, acquired_at)

    try:
        return OpenInterestSnapshot(
            symbol=symbol,
            exchange=exchange,
            open_interest_usd=oi_usd,
            open_interest_coin=oi_coin,
            derivatives_metadata_json=metadata,
            oi_change_1h_pct=None,
            long_short_ratio=lsr,
            top_trader_position_ratio=top_position_ratio,
            source_observed_at=source_observed_at,
            acquired_at=acquired_at.isoformat(),
            source_time_status=source_time_status,
            observed_at=source_observed_at or acquired_at.isoformat(),
        )
    except ValueError:
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


def _normalize_ohlcv(raw, *, venue: str, symbol: str, limit: int) -> OHLCVResult:
    parsed = [parse_ohlcv_candle(row, provider="ccxt", exchange=venue) for row in raw]
    candles = [candle for candle in parsed if candle is not None]
    rejected = len(parsed) - len(candles)
    if rejected:
        logger.warning("ohlcv quality rejected %d/%d row(s) from %s for %s",
                       rejected, len(parsed), venue, symbol)
    return OHLCVResult(candles, requested=limit, rejected=rejected, attempts=[OHLCVAttempt(
        provider=venue,
        outcome=classify_ohlcv_outcome(accepted=len(candles), rejected=rejected, requested=limit),
        accepted=len(candles), rejected=rejected,
    )])


async def _read_funding(exchange, symbol, ticker):
    raw = await exchange.fetch_funding_rate(symbol)
    return raw or {}, (raw or {}).get("interval"), "funding_response.interval", None


async def _read_comparison_funding(exchange, markets, fetch):
    return await fetch(exchange.fetch_funding_rates,
                       [market["symbol"] for market in markets.values()]) or {}


class CCXTProvider:
    """Market data via ccxt.async_support with a primary + fallback exchange."""

    def __init__(self, exchange: str, fallback_exchange: str, *, funding_reader=_read_funding,
                 taker_reader=None, comparison_funding_reader=_read_comparison_funding,
                 comparison_oi_reader=None, comparison_prices_from_funding=False,
                 primary_perp_exchange: str | None = None,
                 fallback_perp_exchange: str | None = None, metadata_reader=ccxt_metadata) -> None:
        self._metadata_reader = metadata_reader
        self._primary_name = exchange
        self._fallback_name = fallback_exchange
        self._primary_perp_name = primary_perp_exchange
        self._fallback_perp_name = fallback_perp_exchange
        self._funding_reader = funding_reader
        self._taker_reader = taker_reader
        self._comparison_funding_reader = comparison_funding_reader
        self._comparison_oi_reader = comparison_oi_reader
        self._comparison_prices_from_funding = comparison_prices_from_funding
        # Lazily constructed so __init__ does no I/O and binds no event loop —
        # the exchange clients are created on first fetch, on the active loop.
        self._primary = None
        self._fallback = None
        # Injected aiohttp sessions we own and must close (one per ccxt client).
        self._sessions: list = []

    async def prepare_live_derivatives(self) -> tuple[str, bool]:
        """Load primary perp markets and report batch acquisition capability."""
        exchange = self._ensure_primary("perp")
        await exchange.load_markets()
        has = getattr(exchange, "has", {}) or {}
        return self._primary_name, bool(
            has.get("fetchOpenInterests") and has.get("fetchFundingRates")
        )

    async def get_live_derivatives(self, target, *, batched, lsr_map, top_position_map):
        """Acquire normalized live snapshots; the caller owns selection and writes."""

        exchange = self._ensure_primary("perp")
        primary_name = self._primary_name
        funding, oi, errors = [], [], []
        funding_map: dict = {}
        oi_map: dict = {}
        if batched:
            try:
                funding_map = await exchange.fetch_funding_rates()
            except Exception as e:
                errors.append(f"funding_batch: {e}")
                logger.debug("batch funding fetch failed: %s", e)
            try:
                oi_map = await exchange.fetch_open_interests()
            except Exception as e:
                errors.append(f"oi_batch: {e}")
                logger.debug("batch OI fetch failed: %s", e)

        for sym in target:
            perp_sym = _to_perp_symbol(sym, exchange)
            if not perp_sym:
                continue

            fr = None
            raw_oi = None
            if batched:
                fr = funding_map.get(perp_sym)
                raw_oi = oi_map.get(perp_sym)
            else:
                try:
                    fr = await exchange.fetch_funding_rate(perp_sym)
                except Exception as e:
                    errors.append(f"funding {sym}: {e}")
                    logger.debug("funding fetch failed for %s: %s", sym, e)
                try:
                    raw_oi = await exchange.fetch_open_interest(perp_sym)
                except Exception as e:
                    errors.append(f"oi {sym}: {e}")
                    logger.debug("OI fetch failed for %s: %s", sym, e)

            market = exchange.markets.get(perp_sym) or {}
            if fr:
                snap = _parse_funding(sym, primary_name, fr, market=market, metadata_reader=self._metadata_reader)
                if snap:
                    snap.derivatives_metadata_json = self._metadata_reader(sym, primary_name, market, fr, family='funding', acquired_at=snap.acquired_at)
                    funding.append(snap)
                    if snap.source_time_status != "provided":
                        errors.append(
                            f"funding_source_time_{snap.source_time_status} {sym}"
                        )
                else:
                    errors.append(f"funding_invalid {sym}")
            # LSR is folded into the same OI dataclass — no follow-up UPDATE.
            if raw_oi:
                snap = _parse_oi(
                    sym, primary_name, raw_oi,
                    lsr=(lsr_map.get(sym) or {}).get('value'),
                    top_position_ratio=(top_position_map.get(sym) or {}).get('value'),
                    market=market, metadata_reader=self._metadata_reader,
                )
                if snap:
                    snap.derivatives_metadata_json = self._metadata_reader(sym, primary_name, market, raw_oi,
                        family='open_interest', ratios=dict(long_short_ratio=lsr_map.get(sym),
                            top_trader_position_ratio=top_position_map.get(sym)))
                    oi.append(snap)
                    if snap.source_time_status != "provided":
                        errors.append(
                            f"open_interest_source_time_{snap.source_time_status} {sym}"
                        )
                else:
                    errors.append(f"open_interest_invalid {sym}")

        return {"funding": funding, "open_interest": oi, "errors": errors}

    async def prepare_derivatives_history(self, symbols):
        exchange = self._ensure_primary("perp")
        await exchange.load_markets()
        return self._primary_name, [
            sym for sym in dict.fromkeys(symbols) if _to_perp_symbol(sym, exchange)
        ]

    async def get_funding_history(self, sym):
        from aletheia.data.derivatives_facts import finite

        exchange = self._ensure_primary("perp")
        perp_sym = _to_perp_symbol(sym, exchange)
        errors, snapshots = [], []
        try:
            history = await exchange.fetch_funding_rate_history(perp_sym, limit=24)
        except Exception as exc:
            return [], [f"funding_history {sym}: {exc}"]
        valid = []
        for item in history:
            ts, rate = finite(item.get("timestamp")), finite(item.get("fundingRate"))
            if ts is None or ts <= 0 or rate is None:
                if not errors:
                    errors.append(f"funding_history_invalid {sym}")
                continue
            valid.append((int(ts), rate, item))
        for ts, rate, raw in sorted(valid, key=lambda item: item[0]):
            snapshot = _parse_funding(sym, self._primary_name,
                {**raw, "fundingRate": rate, "timestamp": ts},
                market=(getattr(exchange, "markets", {}) or {}).get(perp_sym), metadata_reader=self._metadata_reader)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots, errors

    async def get_open_interest_history(self, sym):
        from aletheia.data.derivatives_facts import finite, open_interest_usd

        exchange = self._ensure_primary("perp")
        perp_sym = _to_perp_symbol(sym, exchange)
        errors, snapshots = [], []
        try:
            history = await exchange.fetch_open_interest_history(perp_sym, "1h", limit=24)
        except Exception as exc:
            logger.warning("OI history not available for %s on %s (%s: %s) — skipping backfill",
                           sym, self._primary_name, type(exc).__name__, exc)
            return [], [f"oi_history {sym}: {type(exc).__name__}: {exc}"]
        for item in history:
            raw_ts = item.get("timestamp")
            ts = finite(raw_ts)
            if ts is None or ts <= 0:
                if raw_ts is not None and not errors:
                    errors.append(f"oi_history_invalid {sym}")
                continue
            metadata = self._metadata_reader(sym, self._primary_name,
                exchange.markets.get(perp_sym) or {}, item, family="open_interest")
            proof = json.loads(metadata)
            value, reason = open_interest_usd(amount=item.get("openInterestAmount"), reported_value=item.get("openInterestValue"),
                instrument=proof["instrument"],
                                              value_unit=proof["oi_unit"])
            if reason or value is None:
                if not errors:
                    errors.append(f"oi_history_invalid {sym}")
                continue
            observed = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
            snapshots.append(OpenInterestSnapshot(
                symbol=sym, exchange=self._primary_name, open_interest_usd=value,
                open_interest_coin=None, oi_change_1h_pct=None,
                long_short_ratio=None, top_trader_position_ratio=None,
                observed_at=observed, source_observed_at=observed,
                acquired_at=datetime.now(timezone.utc).isoformat(),
                source_time_status="provided", derivatives_metadata_json=metadata,
            ))
        return snapshots, errors

    async def get_derivatives_comparison(self, symbols, *, owns_primary, acquired):
        from aletheia.data.derivatives_facts import (
            comparison_row,
            project_price_snapshot, project_snapshot,
        )

        venue = self._primary_name
        states = {s: {"listed": None, "funding": "deferred",
                     "open_interest": "deferred", "price": "deferred"} for s in symbols}
        result = dict(funding=[], open_interest=[], prices=[], errors=[], coverage=states)
        async def fetch(method, *args, **kwargs):
            try:
                return await asyncio.wait_for(method(*args, **kwargs), 3.0)
            except Exception as exc:
                result['errors'].append(f'{venue}:{method.__name__}:{type(exc).__name__}')
                return None

        try:
            async with asyncio.timeout(10.0):
                exchange = self._ensure_primary('perp')
                await exchange.load_markets()
                markets = {}
                for symbol in symbols:
                    market = exchange.markets.get(f"{symbol}:{symbol.split('/')[-1]}")
                    if market is None:
                        states[symbol] = dict(listed=False, funding='not_listed', open_interest='not_listed', price='not_listed')
                    elif not (market.get('swap') and market.get('linear') and market.get('active') is not False
                              and market.get('settle') in {'USDT', 'USDC'}):
                        states[symbol] = dict(listed=True, funding='unsupported', open_interest='unsupported', price='unsupported')
                    else:
                        states[symbol]['listed'] = True
                        markets[symbol] = market
                # Four per-symbol requests rotate over the complete unranked workset.
                names = list(markets)
                offset = (int(acquired.timestamp() // 600) * 4) % max(1, len(names))
                selected = (names[offset:] + names[:offset])[:4]
                unified = [m['symbol'] for m in markets.values()]
                funding_map = {}
                if owns_primary:
                    for symbol in names:
                        states[symbol].update(funding='owned_primary', open_interest='owned_primary')
                elif names:
                    if exchange.has.get('fetchFundingRates'):
                        funding_map = await self._comparison_funding_reader(exchange, markets, fetch)
                        funding_names = names
                    else:
                        funding_names = selected
                        for symbol in selected:
                            raw = await fetch(exchange.fetch_funding_rate, markets[symbol]['symbol'])
                            if raw:
                                funding_map[markets[symbol]['symbol']] = raw
                    for symbol in funding_names:
                        raw = funding_map.get(markets[symbol]['symbol'])
                        snap = _parse_funding(symbol, venue, {**raw, 'markPrice': None}, market=markets[symbol], metadata_reader=self._metadata_reader) if raw else None
                        if snap:
                            snap.derivatives_metadata_json = self._metadata_reader(symbol, venue, markets[symbol], raw, family='funding', acquired_at=snap.acquired_at)
                            result['funding'].append(comparison_row(snap.model_dump()))
                            states[symbol]['funding'] = project_snapshot(snap.model_dump(), 'funding')[0]['availability']
                        else:
                            states[symbol]['funding'] = 'invalid' if raw else 'provider_error'
                    if exchange.has.get('fetchOpenInterests'):
                        oi_map = await fetch(exchange.fetch_open_interests, unified) or {}
                        oi_names = names
                    elif self._comparison_oi_reader is not None and exchange.has.get('fetchOpenInterestHistory'):
                        # The history endpoint proves USD notional; current amount-only OI does not.
                        oi_map = await self._comparison_oi_reader(exchange, markets, selected, fetch)
                        oi_names = selected
                    else:
                        oi_map, oi_names = {}, []
                        for symbol in names:
                            states[symbol]['open_interest'] = 'oi_unit_unproven'
                    for symbol in oi_names:
                        raw = oi_map.get(markets[symbol]['symbol'])
                        metadata = self._metadata_reader(symbol, venue, markets[symbol], raw or {}, family='open_interest')
                        proved = json.loads(metadata)['oi_unit'] == 'USD'
                        fact = _normalize_oi_fact(raw, instrument=instrument_from_market(symbol, markets[symbol]),
                            provider='ccxt', venue=venue, acquired_at=datetime.now(timezone.utc),
                            value_unit='USD') if raw and proved else None
                        if fact and fact['availability'] == 'available':
                            snap = OpenInterestSnapshot(symbol=symbol, exchange=venue, open_interest_usd=fact['value'],
                                source_observed_at=fact['source_observed_at'], acquired_at=fact['acquired_at'],
                                source_time_status='provided' if fact['source_observed_at'] else 'missing',
                                observed_at=fact['source_observed_at'] or fact['acquired_at'],
                                derivatives_metadata_json=metadata)
                            result['open_interest'].append(comparison_row(snap.model_dump()))
                            states[symbol]['open_interest'] = project_snapshot(snap.model_dump(), 'open_interest')[0]['availability']
                        else:
                            states[symbol]['open_interest'] = fact['availability'] if fact else ('oi_unit_unproven' if raw else 'provider_error')
                # Binance funding already carries both price parents from one response.
                if self._comparison_prices_from_funding and funding_map:
                    price_map, price_names = funding_map, names
                elif names and exchange.has.get('fetchMarkPrices'):
                    price_map = await fetch(exchange.fetch_mark_prices, unified) or {}
                    price_names = names
                else:
                    price_map, price_names = {}, selected
                    for symbol in selected:
                        if exchange.has.get('fetchMarkPrice'):
                            raw = await fetch(exchange.fetch_mark_price, markets[symbol]['symbol'])
                            if raw:
                                price_map[markets[symbol]['symbol']] = raw
                        else:
                            states[symbol]['price'] = 'unsupported'
                for symbol in price_names:
                    raw = price_map.get(markets[symbol]['symbol'])
                    row = _parse_price(symbol, venue, raw, market=markets[symbol],
                        acquired_at=datetime.now(timezone.utc)) if raw else None
                    if row:
                        result['prices'].append(row)
                        projected = project_price_snapshot(row)
                        states[symbol]['price'] = next((f['availability'] for f in projected if f['availability'] != 'unavailable'), 'unavailable')
                    elif states[symbol]['price'] != 'unsupported':
                        states[symbol]['price'] = 'invalid' if raw else 'provider_error'
        except Exception as exc:
            result['errors'].append(f'{venue}:{type(exc).__name__}')
            for state in states.values():
                state['error'] = type(exc).__name__
                if state['listed'] is None:
                    state.update(funding='provider_error', open_interest='provider_error', price='provider_error')

        return result

    def _client(self, name: str, **options):
        exchange, session = create_client(name, **options)
        self._sessions.append(session)
        return exchange

    def _ensure_primary(self, market_type: str | None = None):
        if self._primary_perp_name and market_type in {"perp", "futures"}:
            if getattr(self, "_primary_perp", None) is None:
                self._primary_perp = self._client(
                    self._primary_perp_name, options={"defaultType": "swap"}
                )
            return self._primary_perp
        if self._primary is None:
            self._primary = self._client(self._primary_name)
        return self._primary

    def _ensure_fallback(self, market_type: str | None = None):
        if self._fallback_perp_name and market_type in {"perp", "futures"}:
            if getattr(self, "_fallback_perp", None) is None:
                self._fallback_perp = self._client(
                    self._fallback_perp_name, options={"defaultType": "swap"}
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

    async def prepare_primary_market(self, market_type: str) -> None:
        """Load one discovery market on this provider's owned primary client."""
        if market_type not in {"spot", "perp", "futures"}:
            raise ValueError("unsupported discovery market type")
        exchange = self._ensure_primary(market_type)
        exchange.options["defaultType"] = "spot" if market_type == "spot" else "swap"
        if market_type != "spot":
            exchange._candidate_markets_acquired_at = None
        await exchange.load_markets()
        if market_type != "spot":
            exchange._candidate_markets_acquired_at = datetime.now(timezone.utc).isoformat()

    async def get_primary_taker_ratio(self, instrument: InstrumentId) -> float | None:
        if instrument.exchange != self._primary_name or instrument.market_type not in {"perp", "futures"}:
            raise ValueError("Taker primary instrument mismatch")
        if self._taker_reader is None:
            return None
        return await self._taker_reader(self._ensure_primary(instrument.market_type), instrument)

    async def get_primary_funding(self, instrument: InstrumentId, ticker: dict) -> dict:
        """Normalize configured primary funding input without owning candidate policy."""
        from aletheia.data.derivatives_facts import finite, normalize_funding

        if instrument.exchange != self._primary_name or instrument.market_type not in {"perp", "futures"}:
            raise ValueError("Funding primary instrument mismatch")
        exchange = self._ensure_primary(instrument.market_type)
        symbol = instrument.to_ccxt_symbol()
        raw, interval, interval_source, interval_at = await self._funding_reader(exchange, symbol, ticker)
        acquired_at = datetime.now(timezone.utc)
        source_time, source_status = provider_source_time(raw, acquired_at)
        market = (getattr(exchange, "markets", {}) or {}).get(symbol) or {}
        evidence = normalize_funding(
            raw.get("fundingRate"), instrument=instrument_from_market(instrument.symbol, market),
            provider="ccxt", venue=instrument.exchange, acquired_at=acquired_at,
            source_observed_at=source_time, source_time_status=source_status,
            interval_seconds=funding_interval_seconds({"interval": interval}),
        )
        evidence.update(interval_raw=interval, interval_source=interval_source,
                        interval_acquired_at=(acquired_at.isoformat() if interval_source == "funding_response.interval" else interval_at))
        return {"venue": instrument.exchange, "symbol": instrument.symbol,
                "funding_rate": finite(raw.get("fundingRate")),
                "source_observed_at": source_time, "evidence": evidence}

    async def get_primary_open_interest(self, instrument: InstrumentId) -> DerivativesFact:
        """Return source-proved OI from the loaded primary, without persistence."""

        if instrument.exchange != self._primary_name or instrument.market_type not in {"perp", "futures"}:
            raise ValueError("OI primary instrument mismatch")
        exchange = self._ensure_primary(instrument.market_type)
        symbol = instrument.to_ccxt_symbol()
        raw = await exchange.fetch_open_interest(symbol)
        acquired_at = datetime.now(timezone.utc)
        market = (getattr(exchange, "markets", {}) or {}).get(symbol) or {}
        proof = json.loads(self._metadata_reader(instrument.symbol, instrument.exchange, market, raw or {}, family="open_interest"))
        return _normalize_oi_fact(
            raw or {}, instrument=proof["instrument"], provider="ccxt",
            venue=instrument.exchange, acquired_at=acquired_at, value_unit=proof["oi_unit"],
        )

    async def get_primary_ohlcv(self, instrument: InstrumentId, *, timeframe: str, limit: int) -> OHLCVResult:
        """Read the loaded primary only; caller owns timeout, cutoff and persistence."""
        if instrument.exchange != self._primary_name:
            raise ValueError("OHLCV primary venue mismatch")
        exchange = self._ensure_primary(instrument.market_type)
        symbol = instrument.to_ccxt_symbol()
        raw = await exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        return _normalize_ohlcv(raw, venue=instrument.exchange, symbol=symbol, limit=limit)

    async def get_orderbook(self, instrument: InstrumentId) -> OrderbookSnapshot | None:
        """Read one discovery snapshot using the already-loaded primary client."""
        from aletheia.data.orderbook import contract_size_from_market, parse_orderbook

        if instrument.exchange != self._primary_name:
            return None
        exchange = self._ensure_primary(instrument.market_type)
        symbol = instrument.to_ccxt_symbol()
        market = exchange.market(symbol)
        is_perp = instrument.market_type in {"perp", "futures"}
        if (market.get("symbol") != symbol
                or bool(market.get("contract")) != is_perp
                or (not is_perp and not market.get("spot"))):
            return None
        size = contract_size_from_market(market)
        started = time.monotonic()
        async with asyncio.timeout(5):
            book = await exchange.fetch_order_book(symbol, limit=20)
        if book.get("symbol") not in (None, symbol):
            return None
        return parse_orderbook(
            instrument.symbol, instrument.exchange, book,
            int((time.monotonic() - started) * 1000),
            contract_size=size, market_type=instrument.market_type,
        )

    async def get_tickers(self, symbols: list[str], *, market_type: str = "spot") -> dict:
        """Return canonical (market type, symbol) keys, never CCXT wire symbols."""
        from extensions.exchanges.ccxt.tickers import _chunked_fetch_tickers

        instruments = [InstrumentId.from_parts(self._primary_name, symbol, market_type) for symbol in symbols]
        wire = {instrument.to_ccxt_symbol(): instrument for instrument in instruments}
        rows = await _chunked_fetch_tickers(self._ensure_primary(market_type), list(wire))
        return {(market_type, wire[symbol].symbol): row for symbol, row in rows.items() if symbol in wire}

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
                candles = _normalize_ohlcv(raw, venue=name, symbol=ccxt_symbol, limit=limit)
                attempts.extend(candles.attempts)
                if not candles:
                    continue
                if _record_venue_health:
                    record_venue_success(name)
                # Mark the trailing bar as open when its close time is still in
                # the future. CCXT always returns the currently-forming bar as
                # the last element; treat it as unconfirmed so callers can
                # exclude it from indicator calculations.
                if candles:
                    tf_ms = timeframe_ms(timeframe)
                    if tf_ms > 0 and candles[-1].timestamp + tf_ms > int(time.time() * 1000):
                        candles[-1].is_closed = False
                # SQLite's writer lane is synchronous by contract. Waiting for it
                # on the scheduler event loop stalls unrelated async jobs and API
                # work during bursty OHLCV refreshes, so keep the write awaited but
                # move the blocking wait to a worker thread.
                if _persist_result:
                    try:
                        await asyncio.to_thread(
                            market_history.persist_closed_candles,
                            canon,
                            timeframe,
                            candles,
                            name,
                            market_type=mkt,
                            provider="ccxt",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("ohlcv persist skipped for %s/%s: %s", canon, timeframe, exc)
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
            use_cache=False,
            _record_venue_health=False,
            _persist_result=False,
        )

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
                except (Exception, _aio.CancelledError):  # noqa: BLE001
                    pass
                closed.add(id(client))
                setattr(self, attr, None)
        for session in self._sessions:
            try:
                await _aio.wait_for(session.close(), timeout=5.0)
            except (Exception, _aio.CancelledError):  # noqa: BLE001
                pass
        self._sessions = []


def create_client(name: str, **options) -> tuple[Any, Any]:
    from aletheia.net import make_ccxt

    exchange, session = make_ccxt(name, **options)
    return install_market_id_guard(exchange, name), session


def install_market_id_guard(exchange: Any, exchange_name: str) -> Any:
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

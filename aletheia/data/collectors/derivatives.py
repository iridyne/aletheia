"""Derivatives collector — funding rate, open interest, liquidations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from aletheia.net import httpx_client
from aletheia.data.models import (
    FundingRateSnapshot,
    LiquidationEvent,
    OpenInterestSnapshot,
)

logger = logging.getLogger("aletheia.data.collectors.derivatives")

# Fallback liquidation source: Binance Futures REST.
# Used ONLY when the primary configured exchange does not support fetch_liquidations
# via CCXT unified API. When active, liquidation exchange metadata will read "binance"
# regardless of the configured primary — callers should treat it as market-wide signal,
# not as confirmation of events on the configured primary exchange.
_BINANCE_LIQUIDATIONS_URL = "https://fapi.binance.com/fapi/v1/forceOrders"


class _CCXTLiquidationsUnsupported(Exception):
    """Raised when the exchange's CCXT driver lacks fetch_liquidations support."""


class DerivativesCollector:
    domain = "derivatives"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        from aletheia.data.ccxt_provider import get_market_data_provider

        provider = get_market_data_provider()
        funding: list[FundingRateSnapshot] = []
        oi: list[OpenInterestSnapshot] = []
        liquidations: list[LiquidationEvent] = []
        errors: list[str] = []
        primary_name = provider._primary_name

        try:
            exchange = provider._ensure_primary()
            await exchange.load_markets()

            for sym in symbols:
                perp_sym = _to_perp_symbol(sym, exchange)
                if not perp_sym:
                    continue

                # Funding rate
                try:
                    fr = await exchange.fetch_funding_rate(perp_sym)
                    snap = _parse_funding(sym, primary_name, fr)
                    if snap:
                        funding.append(snap)
                except Exception as e:
                    errors.append(f"funding {sym}: {e}")
                    logger.debug("funding fetch failed for %s: %s", sym, e)

                # Open interest
                try:
                    raw_oi = await exchange.fetch_open_interest(perp_sym)
                    snap = _parse_oi(sym, primary_name, raw_oi)
                    if snap:
                        oi.append(snap)
                except Exception as e:
                    errors.append(f"oi {sym}: {e}")
                    logger.debug("OI fetch failed for %s: %s", sym, e)

            # Liquidations: try primary exchange via CCXT first; fall back to Binance REST.
            try:
                ccxt_liqs = await _fetch_liquidations_ccxt(exchange, primary_name, symbols)
                liquidations.extend(ccxt_liqs)
                logger.debug(
                    "liquidations via ccxt/%s: %d events", primary_name, len(ccxt_liqs)
                )
            except _CCXTLiquidationsUnsupported:
                if primary_name != "binance":
                    logger.debug(
                        "liquidations: %s does not support fetch_liquidations via CCXT; "
                        "falling back to Binance Futures REST — liquidation records will "
                        "carry exchange='binance', not '%s'",
                        primary_name,
                        primary_name,
                    )
                try:
                    binance_liqs = await _fetch_liquidations_binance_rest(symbols)
                    liquidations.extend(binance_liqs)
                except Exception as e:
                    errors.append(f"liquidations: {e}")
                    logger.debug("liquidation fetch (binance fallback) failed: %s", e)
            except Exception as e:
                errors.append(f"liquidations: {e}")
                logger.debug("liquidation fetch failed: %s", e)

        finally:
            await provider.close()

        return {
            "funding": funding,
            "open_interest": oi,
            "liquidations": liquidations,
            "errors": errors,
        }


def _to_perp_symbol(spot_symbol: str, exchange) -> str | None:
    """Convert canonical spot BTC/USDT to the perp symbol on this exchange."""
    # CCXT perp: BTC/USDT:USDT
    perp = f"{spot_symbol}:USDT"
    if perp in (exchange.markets or {}):
        return perp
    return None


def _parse_funding(
    symbol: str, exchange: str, raw: dict
) -> FundingRateSnapshot | None:
    rate = raw.get("fundingRate")
    if rate is None:
        return None
    rate = float(rate)
    annualized = rate * 3 * 365  # 8h intervals → annual
    next_time = raw.get("fundingDatetime") or raw.get("nextFundingDatetime")
    mark = raw.get("markPrice")

    return FundingRateSnapshot(
        symbol=symbol,
        exchange=exchange,
        funding_rate=rate,
        funding_rate_annualized=round(annualized, 4),
        next_funding_time=str(next_time) if next_time else None,
        mark_price=float(mark) if mark else None,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def _parse_oi(
    symbol: str, exchange: str, raw: dict
) -> OpenInterestSnapshot | None:
    oi_val = raw.get("openInterestAmount") or raw.get("openInterestValue")
    if oi_val is None:
        return None

    oi_usd = float(oi_val)
    # Some exchanges return in coin, some in USD. CCXT unified uses openInterestValue (USD).
    info = raw.get("info", {})
    oi_coin = None
    if "sumOpenInterest" in info:
        oi_coin = float(info["sumOpenInterest"])

    # Compute 1h change pct against previous snapshot
    oi_change_1h_pct = None
    try:
        from aletheia.storage.repositories import latest_open_interest
        prev = latest_open_interest(symbol)
        if prev and prev.get("open_interest_usd"):
            prev_oi = float(prev["open_interest_usd"])
            if prev_oi > 0:
                oi_change_1h_pct = round((oi_usd - prev_oi) / prev_oi * 100, 2)
    except Exception:
        pass

    return OpenInterestSnapshot(
        symbol=symbol,
        exchange=exchange,
        open_interest_usd=oi_usd,
        open_interest_coin=oi_coin,
        oi_change_1h_pct=oi_change_1h_pct,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


async def _fetch_liquidations_ccxt(
    exchange, exchange_name: str, symbols: list[str]
) -> list[LiquidationEvent]:
    """Fetch recent liquidations from the primary exchange via CCXT unified API.

    Raises _CCXTLiquidationsUnsupported if the exchange driver does not implement
    fetch_liquidations, allowing the caller to fall back to Binance REST.
    """
    if not exchange.has.get("fetchLiquidations"):
        raise _CCXTLiquidationsUnsupported(exchange_name)

    results: list[LiquidationEvent] = []
    now = datetime.now(timezone.utc)
    since_ms = int((now - timedelta(hours=1)).timestamp() * 1000)

    for sym in symbols[:10]:  # cap to avoid rate limits
        perp_sym = _to_perp_symbol(sym, exchange)
        if not perp_sym:
            continue
        try:
            raw_list = await exchange.fetch_liquidations(perp_sym, since=since_ms, limit=20)
            for item in raw_list:
                price = float(item.get("price") or 0)
                qty = float(item.get("amount") or item.get("quantity") or 0)
                side_raw = (item.get("side") or "").lower()
                # CCXT unified: side is the position side being liquidated
                side = side_raw if side_raw in ("long", "short") else "short"
                ts = item.get("timestamp")
                event_str = (
                    datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
                    if ts
                    else now.isoformat()
                )
                results.append(LiquidationEvent(
                    symbol=sym,
                    exchange=exchange_name,
                    side=side,
                    quantity_usd=price * qty,
                    price=price,
                    event_time=event_str,
                    observed_at=now.isoformat(),
                ))
        except Exception as e:
            logger.debug("ccxt liquidations fetch failed for %s: %s", sym, e)

    return results


async def _fetch_liquidations_binance_rest(
    symbols: list[str],
) -> list[LiquidationEvent]:
    """Fetch recent liquidations from Binance Futures REST API (fallback).

    This endpoint is exchange-specific. When the configured primary exchange is not
    Binance, returned events carry exchange='binance' and represent Binance market
    conditions, not the configured primary.
    """
    results: list[LiquidationEvent] = []
    now = datetime.now(timezone.utc)

    async with httpx_client(timeout=10) as client:
        for sym in symbols[:10]:  # cap to avoid rate limits
            binance_sym = sym.replace("/", "")
            try:
                resp = await client.get(
                    _BINANCE_LIQUIDATIONS_URL,
                    params={"symbol": binance_sym, "limit": 20},
                )
                if resp.status_code != 200:
                    continue
                for item in resp.json():
                    side = "long" if item.get("side", "").upper() == "SELL" else "short"
                    price = float(item.get("price", 0))
                    qty = float(item.get("origQty", 0))
                    event_time = item.get("time", 0)
                    if event_time:
                        et = datetime.fromtimestamp(event_time / 1000, tz=timezone.utc)
                        if (now - et) > timedelta(hours=1):
                            continue
                        event_str = et.isoformat()
                    else:
                        event_str = now.isoformat()

                    results.append(LiquidationEvent(
                        symbol=sym,
                        exchange="binance",
                        side=side,
                        quantity_usd=price * qty,
                        price=price,
                        event_time=event_str,
                        observed_at=now.isoformat(),
                    ))
            except Exception:
                continue

    return results

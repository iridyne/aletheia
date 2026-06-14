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

_BINANCE_LIQUIDATIONS_URL = "https://fapi.binance.com/fapi/v1/forceOrders"


class DerivativesCollector:
    domain = "derivatives"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        from aletheia.data.ccxt_provider import get_market_data_provider

        provider = get_market_data_provider()
        funding: list[FundingRateSnapshot] = []
        oi: list[OpenInterestSnapshot] = []
        liquidations: list[LiquidationEvent] = []
        errors: list[str] = []

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
                    snap = _parse_funding(sym, provider._primary_name, fr)
                    if snap:
                        funding.append(snap)
                except Exception as e:
                    errors.append(f"funding {sym}: {e}")
                    logger.debug("funding fetch failed for %s: %s", sym, e)

                # Open interest
                try:
                    raw_oi = await exchange.fetch_open_interest(perp_sym)
                    snap = _parse_oi(sym, provider._primary_name, raw_oi)
                    if snap:
                        oi.append(snap)
                except Exception as e:
                    errors.append(f"oi {sym}: {e}")
                    logger.debug("OI fetch failed for %s: %s", sym, e)
        finally:
            await provider.close()

        # Liquidations — separate HTTP call (Binance REST)
        try:
            liqs = await _fetch_liquidations(symbols)
            liquidations.extend(liqs)
        except Exception as e:
            errors.append(f"liquidations: {e}")
            logger.debug("liquidation fetch failed: %s", e)

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

    return OpenInterestSnapshot(
        symbol=symbol,
        exchange=exchange,
        open_interest_usd=oi_usd,
        open_interest_coin=oi_coin,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


async def _fetch_liquidations(
    symbols: list[str],
) -> list[LiquidationEvent]:
    """Fetch recent liquidations from Binance Futures API."""
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
                        # Only keep liquidations from last hour
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

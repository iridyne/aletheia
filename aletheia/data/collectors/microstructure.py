"""Microstructure collector — orderbook spread, depth, and imbalance."""

from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timezone
from typing import Any

from aletheia.data.models import OrderbookSnapshot

logger = logging.getLogger("aletheia.data.collectors.microstructure")


class MicrostructureCollector:
    domain = "microstructure"

    async def collect(self, symbols: list[str], *, venue: str | None = None) -> dict[str, Any]:
        from extensions.data.ccxt_provider import get_market_data_provider

        try:
            provider = get_market_data_provider(exchange=venue, native_ccxt=True)
        except Exception as exc:
            return {"snapshots": [], "errors": [f"provider_setup:{type(exc).__name__}"]}
        snapshots: list[OrderbookSnapshot] = []
        errors: list[str] = []

        try:
            from aletheia.data.symbols import InstrumentId

            for sym in symbols:
                phase = "provider_setup"
                try:
                    if isinstance(sym, InstrumentId):
                        fetch_sym = sym.to_ccxt_symbol()
                        canonical_sym = sym.symbol
                        market_type = sym.market_type
                    else:
                        fetch_sym = sym
                        canonical_sym = sym
                        market_type = "spot"

                    exchange = provider._ensure_primary(market_type)
                    with provider._market_type_context(exchange, market_type):
                        phase = "load_markets"
                        await exchange.load_markets()
                        phase = "market_metadata"
                        market = exchange.market(fetch_sym)
                        contract_size = _contract_size(market)
                        t0 = time.time()
                        phase = "fetch_order_book"
                        ob = await exchange.fetch_order_book(fetch_sym, limit=20)
                    latency_ms = int((time.time() - t0) * 1000)

                    phase = "parse"
                    snap = _parse_orderbook(
                        canonical_sym,
                        provider._primary_name,
                        ob,
                        latency_ms,
                        contract_size=contract_size,
                        market_type=market_type,
                    )
                    if snap:
                        snapshots.append(snap)
                    else:
                        errors.append("parse:invalid_orderbook")
                except Exception as exc:
                    errors.append(f"{phase}:{type(exc).__name__}")
        finally:
            await provider.close()

        return {"snapshots": snapshots, "errors": errors}


def _parse_orderbook(
    symbol: str,
    exchange: str,
    ob: dict,
    latency_ms: int,
    *,
    contract_size: float = 1.0,
    market_type: str = "spot",
) -> OrderbookSnapshot | None:
    bids = ob.get("bids", [])
    asks = ob.get("asks", [])
    if not bids or not asks:
        return None

    try:
        bid_levels = [_validated_level(level) for level in bids]
        ask_levels = [_validated_level(level) for level in asks]
        if any(level is None for level in (*bid_levels, *ask_levels)):
            return None
        if not _is_ordered(bid_levels, descending=True) or not _is_ordered(
            ask_levels, descending=False
        ):
            return None
        best_bid = bid_levels[0][0]
        best_ask = ask_levels[0][0]
        if best_ask < best_bid:
            return None

        mid = (best_bid + best_ask) / 2
        spread_bps = ((best_ask - best_bid) / mid) * 10_000
        threshold_10bps = mid * 0.001  # 10 bps from mid

        bid_depth = _depth_within(
            bid_levels, mid - threshold_10bps, side="bid", contract_size=contract_size
        )
        ask_depth = _depth_within(
            ask_levels, mid + threshold_10bps, side="ask", contract_size=contract_size
        )

        bid_vol = sum(level[1] for level in bid_levels[:10])
        ask_vol = sum(level[1] for level in ask_levels[:10])
        total_vol = bid_vol + ask_vol
        imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else None
        derived = (mid, spread_bps, bid_depth, ask_depth, bid_vol, ask_vol, total_vol)
        if (
            not math.isfinite(mid)
            or mid <= 0
            or not all(math.isfinite(value) and value >= 0 for value in derived[1:])
        ):
            return None
        if imbalance is not None and (
            not math.isfinite(imbalance) or not -1 <= imbalance <= 1
        ):
            return None

        return OrderbookSnapshot(
            symbol=symbol,
            exchange=exchange,
            market_type=market_type,
            bid_price=best_bid,
            ask_price=best_ask,
            spread_bps=round(spread_bps, 2),
            bid_depth_10bps_usd=round(bid_depth, 2),
            ask_depth_10bps_usd=round(ask_depth, 2),
            total_depth_10bps_usd=round(bid_depth + ask_depth, 2),
            imbalance_ratio=round(imbalance, 4) if imbalance is not None else None,
            observed_at=datetime.now(timezone.utc).isoformat(),
            fetch_latency_ms=latency_ms,
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _validated_level(level: object) -> tuple[float, float] | None:
    try:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            return None
        price = float(level[0])
        quantity = float(level[1])
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    if not math.isfinite(quantity) or quantity < 0:
        return None
    return price, quantity


def _is_ordered(levels: list[tuple[float, float] | None], *, descending: bool) -> bool:
    prices = [level[0] for level in levels if level is not None]
    pairs = zip(prices, prices[1:])
    return all(left >= right if descending else left <= right for left, right in pairs)


def _depth_within(
    levels: list,
    price_threshold: float,
    side: str,
    *,
    contract_size: float = 1.0,
) -> float:
    """Sum USD value of levels within price threshold from mid."""
    if not math.isfinite(price_threshold) or not math.isfinite(contract_size) or contract_size <= 0:
        raise ValueError("invalid orderbook depth parameters")
    total_usd = 0.0
    for level in levels:
        parsed = _validated_level(level)
        if parsed is None:
            raise ValueError("invalid orderbook level")
        price, qty = parsed
        if side == "bid" and price < price_threshold:
            break
        if side == "ask" and price > price_threshold:
            break
        total_usd += price * qty * contract_size
        if not math.isfinite(total_usd):
            raise ValueError("non-finite orderbook depth")
    return total_usd


def _contract_size(market: dict[str, Any]) -> float:
    """Return base units per amount; spot amounts already use base units."""
    if not market.get("contract"):
        return 1.0
    try:
        size = float(market.get("contractSize"))
    except (TypeError, ValueError):
        raise ValueError("invalid contract size")
    if not math.isfinite(size) or size <= 0:
        raise ValueError("invalid contract size")
    return size

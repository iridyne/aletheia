"""Microstructure collector — orderbook spread, depth, and imbalance."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from aletheia.data.models import OrderbookSnapshot

logger = logging.getLogger("aletheia.data.collectors.microstructure")


class MicrostructureCollector:
    domain = "microstructure"

    async def collect(self, symbols: list[str]) -> dict[str, Any]:
        from aletheia.data.ccxt_provider import get_market_data_provider

        provider = get_market_data_provider()
        snapshots: list[OrderbookSnapshot] = []
        errors: list[str] = []

        try:
            from aletheia.data.symbols import to_ccxt_perp, InstrumentId

            exchange = provider._ensure_primary()
            try:
                await exchange.load_markets()
            except Exception as e:
                errors.append(f"load_markets failed: {e}")
                logger.warning("microstructure: load_markets failed, skipping tick: %s", e)
                return {"snapshots": snapshots, "errors": errors}

            for sym in symbols:
                try:
                    # InstrumentId-aware: set defaultType and ccxt symbol per market_type
                    if isinstance(sym, InstrumentId):
                        is_perp = sym.market_type == "perp"
                        fetch_sym = sym.to_ccxt_symbol()
                        canonical_sym = sym.symbol
                    else:
                        is_perp = False
                        fetch_sym = sym
                        canonical_sym = sym

                    exchange.options["defaultType"] = "swap" if is_perp else "spot"

                    t0 = time.time()
                    ob = await exchange.fetch_order_book(fetch_sym, limit=20)
                    latency_ms = int((time.time() - t0) * 1000)

                    snap = _parse_orderbook(canonical_sym, provider._primary_name, ob, latency_ms)
                    if snap:
                        snapshots.append(snap)
                except Exception as e:
                    errors.append(f"{sym}: {e}")
                    logger.debug("orderbook fetch failed for %s: %s", sym, e)
        finally:
            await provider.close()

        return {"snapshots": snapshots, "errors": errors}


def _parse_orderbook(
    symbol: str, exchange: str, ob: dict, latency_ms: int
) -> OrderbookSnapshot | None:
    bids = ob.get("bids", [])
    asks = ob.get("asks", [])
    if not bids or not asks:
        return None

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    if best_bid <= 0 or best_ask <= 0:
        return None

    mid = (best_bid + best_ask) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10_000

    threshold_10bps = mid * 0.001  # 10 bps from mid

    bid_depth = _depth_within(bids, mid - threshold_10bps, side="bid")
    ask_depth = _depth_within(asks, mid + threshold_10bps, side="ask")

    bid_vol = sum(float(b[1]) for b in bids[:10])
    ask_vol = sum(float(a[1]) for a in asks[:10])
    total_vol = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else None

    return OrderbookSnapshot(
        symbol=symbol,
        exchange=exchange,
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


def _depth_within(levels: list, price_threshold: float, side: str) -> float:
    """Sum USD value of levels within price threshold from mid."""
    total_usd = 0.0
    for price_str, qty_str in levels:
        price = float(price_str)
        qty = float(qty_str)
        if side == "bid" and price < price_threshold:
            break
        if side == "ask" and price > price_threshold:
            break
        total_usd += price * qty
    return total_usd

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
                        is_perp = sym.market_type in {"perp", "futures"}
                        fetch_sym = sym.to_ccxt_symbol()
                        canonical_sym = sym.symbol
                        market_type = sym.market_type
                    else:
                        is_perp = False
                        fetch_sym = sym
                        canonical_sym = sym
                        market_type = "spot"

                    exchange.options["defaultType"] = "swap" if is_perp else "spot"
                    market = exchange.market(fetch_sym)
                    contract_size = _contract_size(market)

                    t0 = time.time()
                    ob = await exchange.fetch_order_book(fetch_sym, limit=20)
                    latency_ms = int((time.time() - t0) * 1000)

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
                except Exception as e:
                    errors.append(f"{sym}: {e}")
                    logger.debug("orderbook fetch failed for %s: %s", sym, e)
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

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    if best_bid <= 0 or best_ask <= 0:
        return None

    mid = (best_bid + best_ask) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10_000

    threshold_10bps = mid * 0.001  # 10 bps from mid

    bid_depth = _depth_within(
        bids, mid - threshold_10bps, side="bid", contract_size=contract_size
    )
    ask_depth = _depth_within(
        asks, mid + threshold_10bps, side="ask", contract_size=contract_size
    )

    bid_vol = sum(float(level[1]) for level in bids[:10])
    ask_vol = sum(float(level[1]) for level in asks[:10])
    total_vol = bid_vol + ask_vol
    imbalance = (bid_vol - ask_vol) / total_vol if total_vol > 0 else None

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


def _depth_within(
    levels: list,
    price_threshold: float,
    side: str,
    *,
    contract_size: float = 1.0,
) -> float:
    """Sum USD value of levels within price threshold from mid."""
    total_usd = 0.0
    for level in levels:
        price = float(level[0])
        qty = float(level[1])
        if side == "bid" and price < price_threshold:
            break
        if side == "ask" and price > price_threshold:
            break
        total_usd += price * qty * contract_size
    return total_usd


def _contract_size(market: dict[str, Any]) -> float:
    """Return base units per amount; spot amounts already use base units."""
    if not market.get("contract"):
        return 1.0
    try:
        size = float(market.get("contractSize") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return size if size > 0 else 1.0

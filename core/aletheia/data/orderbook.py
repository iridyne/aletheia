"""Shared orderbook geometry and normalized USD-depth projection; no transport or writes."""

import math
from datetime import datetime, timezone

from aletheia.data.models import OrderbookSnapshot


def contract_size_from_market(market: dict) -> float:
    """Return base units per amount; spot amounts already use base units."""
    if not market.get("contract"):
        return 1.0
    if isinstance(market.get("contractSize"), bool):
        raise ValueError("invalid contract size")
    try:
        size = float(market.get("contractSize"))
    except (TypeError, ValueError):
        raise ValueError("invalid contract size")
    if not math.isfinite(size) or size <= 0:
        raise ValueError("invalid contract size")
    return size


def parse_orderbook(
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
        if isinstance(level[0], bool) or isinstance(level[1], bool):
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

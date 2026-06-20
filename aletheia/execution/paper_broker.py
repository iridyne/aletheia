"""Paper broker — simulates order fills for paper/demo trading."""

from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, update_order, insert_fill, insert_position, update_position, list_positions,
)


def _apply_slippage(price: float, direction: str, slippage_bps: float) -> float:
    """Apply adverse slippage: worse fill for the trader."""
    slip = slippage_bps / 10_000.0
    if direction in ("long", "buy"):
        return round(price * (1 + slip), 8)
    return round(price * (1 - slip), 8)


def execute_paper_order(
    proposal_id: str,
    risk_decision_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str,
    quantity: float,
    price: float,
    execution_mode: str = "paper_internal",
    strategy_id: str | None = None,
    stop_price: float | None = None,
) -> dict:
    """Execute a paper order: create order, fill, and position.

    The protective stop_price (from the proposal) and a take-profit derived from
    config TP% are recorded on a newly-opened position so the position_manager can
    act on them. Averaging into an existing position preserves its original stop/TP.
    """
    from aletheia.config.loader import load_config
    cfg = load_config()
    slippage_bps = cfg.trading.slippage_bps
    fee_rate = cfg.trading.execution_fee_pct / 100.0

    fill_price = _apply_slippage(price, direction, slippage_bps)

    order = Order(
        proposal_id=proposal_id,
        risk_decision_id=risk_decision_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=fill_price,
        execution_mode=execution_mode,
        status="filled",
    )
    insert_order(order)

    fill = Fill(
        order_id=order.id,
        fill_price=fill_price,
        fill_quantity=quantity,
        fee=quantity * fill_price * fee_rate,
    )
    insert_fill(fill)

    # Check if we have an existing position to update
    filled_take_profit: float | None = None
    existing = _find_open_position(symbol, market_type, direction, execution_mode, strategy_id)
    if existing:
        new_qty = existing["quantity"] + quantity
        new_entry = (existing["entry_price"] * existing["quantity"] + fill_price * quantity) / new_qty
        update_kwargs = {"quantity": new_qty, "entry_price": new_entry, "current_price": fill_price}
        if stop_price is not None:
            old_stop = existing.get("stop_price")
            if old_stop is not None:
                update_kwargs["stop_price"] = max(stop_price, old_stop) if direction in ("long", "buy") else min(stop_price, old_stop)
            else:
                update_kwargs["stop_price"] = stop_price
        update_position(existing["id"], **update_kwargs)
        position_id = existing["id"]
    else:
        take_profit_price = _take_profit_for(direction, fill_price)
        filled_take_profit = take_profit_price
        # BTC benchmark: record BTC price at entry for alpha/beta separation
        btc_entry_price = _btc_price_now()
        # Regime tag at entry time
        regime_tag = _current_regime()
        # Capture entry-time market context for post-trade review
        entry_market_summary = None
        try:
            import json as _json
            from aletheia.data.context_builder import build_market_context_from_db
            ctx = build_market_context_from_db([symbol])
            entry_market_summary = _json.dumps({
                "regime": regime_tag,
                "price": fill_price,
                "data_completeness": ctx.data_completeness,
                "tradability": ctx.tradability.get(symbol, {}),
                "leverage": ctx.leverage.get(symbol, {}),
            }, default=str)[:2000]
        except Exception:
            pass
        position = Position(
            symbol=symbol,
            market_type=market_type,
            direction=direction,
            quantity=quantity,
            entry_price=fill_price,
            current_price=fill_price,
            execution_mode=execution_mode,
            strategy_id=strategy_id,
            proposal_id=proposal_id,
            order_id=order.id,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            btc_entry_price=btc_entry_price,
            regime_tag=regime_tag,
            entry_market_summary=entry_market_summary,
        )
        insert_position(position)
        position_id = position.id

    result = {
        "order_id": order.id,
        "fill_id": fill.id,
        "position_id": position_id,
        "fill_price": fill_price,
        "fill_quantity": quantity,
    }
    try:
        from aletheia.notifications import notify_trade_fill
        notify_trade_fill(
            symbol, direction, quantity, fill_price, position_id, execution_mode,
            stop_price=stop_price,
            take_profit_price=filled_take_profit,
        )
    except Exception:
        pass
    return result


def _btc_price_now() -> float | None:
    """Fetch current BTC/USDT price for benchmark tracking."""
    try:
        from aletheia.trading.market_data import latest_snapshot
        snap = latest_snapshot("BTC/USDT")
        return float(snap["price"]) if snap else None
    except Exception:
        return None


def _current_regime() -> str:
    """Read current macro regime tag."""
    try:
        from aletheia.storage.repositories import latest_macro_snapshot
        macro = latest_macro_snapshot()
        if macro and macro.get("risk_regime"):
            return macro["risk_regime"]
    except Exception:
        pass
    return "unknown"


def _take_profit_for(direction: str, entry_price: float) -> float | None:
    """Derive a take-profit price from config TP% and direction."""
    from aletheia.config.loader import load_config
    pct = load_config().trading.default_take_profit_pct
    if not pct or entry_price <= 0:
        return None
    if direction in ("long", "buy"):
        return round(entry_price * (1 + pct / 100.0), 8)
    return round(entry_price * (1 - pct / 100.0), 8)


def _find_open_position(
    symbol: str,
    market_type: str,
    direction: str,
    execution_mode: str,
    strategy_id: str | None,
) -> dict | None:
    positions = list_positions(status="open")
    for p in positions:
        if (p["symbol"] == symbol and p["market_type"] == market_type
                and p["direction"] == direction and p["execution_mode"] == execution_mode):
            if strategy_id is not None and p.get("strategy_id") not in (None, "", strategy_id):
                continue
            return p
    return None

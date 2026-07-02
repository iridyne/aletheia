"""Paper broker — simulates order fills for paper/demo trading."""

from typing import Any
import json

from aletheia.storage.models import Order, Fill, Position
from aletheia.storage.repositories import (
    insert_order, update_order, insert_fill, insert_position, update_position, list_positions,
)
from aletheia.execution.position_enrichment import liquidation_price_for as _liq_price


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
    leverage: int | None = None,
    opportunity_id: str | None = None,
    trade_school: str = "short_term",
    attribution_snapshot: dict[str, Any] | str | None = None,
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
        # Averaging in shifts the entry → the liquidation level moves too. Recompute
        # it from the leverage already on the position (leverage itself is unchanged).
        existing_lev = existing.get("leverage")
        if existing_lev and existing_lev > 1:
            update_kwargs["liquidation_price"] = _liq_price(new_entry, direction, existing_lev, symbol)
        if stop_price is not None:
            old_stop = existing.get("stop_price")
            if old_stop is not None:
                update_kwargs["stop_price"] = max(stop_price, old_stop) if direction in ("long", "buy") else min(stop_price, old_stop)
            else:
                update_kwargs["stop_price"] = stop_price
        # Accumulate entry-fee and slippage across averaging-in legs: the standing
        # position must reflect the full cost basis of every leg so realized PnL
        # after a close isn't overstated. Standalone NULL/0.0 from legacy rows → 0.
        new_entry_fee = round(fill_price * quantity * fee_rate, 8)
        new_slippage_cost = round(abs(fill_price - price) * quantity, 8)
        existing_entry_fee = existing.get("entry_fee")
        existing_slippage = existing.get("slippage_cost")
        update_kwargs["entry_fee"] = round((existing_entry_fee or 0.0) + new_entry_fee, 8)
        update_kwargs["slippage_cost"] = round((existing_slippage or 0.0) + new_slippage_cost, 8)
        update_position(existing["id"], **update_kwargs)
        position_id = existing["id"]
    else:
        take_profit_price = _take_profit_for(direction, fill_price)
        filled_take_profit = take_profit_price
        attribution = _paper_position_attribution(symbol, fill_price, attribution_snapshot)
        btc_entry_price = attribution["btc_entry_price"]
        regime_tag = attribution["regime_tag"]
        entry_market_summary = attribution["entry_market_summary"]
        # Decision explainability: pull reasoning from the originating proposal
        decision_reasoning = None
        try:
            from aletheia.storage.repositories import _select_one
            proposal = _select_one("trade_proposals", proposal_id)
            if proposal:
                decision_reasoning = proposal.get("agent_rationale")
        except Exception:
            pass
        # Cost visibility: record entry-leg fee and slippage at open time
        entry_fee_val = round(fill_price * quantity * fee_rate, 8)
        slippage_cost_val = round(abs(fill_price - price) * quantity, 8)
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
            opportunity_id=opportunity_id,
            leverage=leverage,
            liquidation_price=_liq_price(fill_price, direction, leverage, symbol),
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            btc_entry_price=btc_entry_price,
            regime_tag=regime_tag,
            entry_market_summary=entry_market_summary,
            decision_reasoning=decision_reasoning,
            entry_fee=entry_fee_val,
            slippage_cost=slippage_cost_val,
            trade_school=trade_school,
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


def capture_paper_attribution_snapshot(symbol: str) -> dict[str, Any]:
    """Capture proposal-time attribution inputs for replay-stable paper fills."""
    regime_tag = _current_regime()
    snapshot: dict[str, Any] = {
        "btc_entry_price": _btc_price_now(),
        "regime_tag": regime_tag,
    }
    try:
        from aletheia.data.context_builder import build_market_context_from_db
        ctx = build_market_context_from_db([symbol])
        snapshot["market_context"] = {
            "data_completeness": ctx.data_completeness,
            "tradability": ctx.tradability.get(symbol, {}),
            "leverage": ctx.leverage.get(symbol, {}),
        }
    except Exception:
        pass
    return snapshot


def _paper_position_attribution(
    symbol: str,
    fill_price: float,
    attribution_snapshot: dict[str, Any] | str | None,
) -> dict[str, Any]:
    snapshot = _coerce_attribution_snapshot(attribution_snapshot)
    if snapshot:
        regime_tag = str(snapshot.get("regime_tag") or "unknown")
        return {
            "btc_entry_price": _float_or_none(snapshot.get("btc_entry_price")),
            "regime_tag": regime_tag,
            "entry_market_summary": _entry_market_summary_from_snapshot(
                snapshot, fill_price, regime_tag
            ),
        }

    regime_tag = _current_regime()
    return {
        "btc_entry_price": _btc_price_now(),
        "regime_tag": regime_tag,
        "entry_market_summary": _live_entry_market_summary(symbol, fill_price, regime_tag),
    }


def _coerce_attribution_snapshot(value: dict[str, Any] | str | None) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _entry_market_summary_from_snapshot(
    snapshot: dict[str, Any],
    fill_price: float,
    regime_tag: str,
) -> str | None:
    explicit = snapshot.get("entry_market_summary")
    if isinstance(explicit, str):
        return explicit[:2000]
    if isinstance(explicit, dict):
        return json.dumps(explicit, default=str)[:2000]

    context = snapshot.get("market_context")
    if not isinstance(context, dict):
        return None
    return json.dumps({
        "regime": regime_tag,
        "price": fill_price,
        "data_completeness": context.get("data_completeness"),
        "tradability": context.get("tradability", {}),
        "leverage": context.get("leverage", {}),
    }, default=str)[:2000]


def _live_entry_market_summary(symbol: str, fill_price: float, regime_tag: str) -> str | None:
    try:
        from aletheia.data.context_builder import build_market_context_from_db
        ctx = build_market_context_from_db([symbol])
        return json.dumps({
            "regime": regime_tag,
            "price": fill_price,
            "data_completeness": ctx.data_completeness,
            "tradability": ctx.tradability.get(symbol, {}),
            "leverage": ctx.leverage.get(symbol, {}),
        }, default=str)[:2000]
    except Exception:
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _current_regime() -> str:
    """Current MARKET regime stamped on a new paper Position (attribution + learning).

    A1 fix: reads market_regimes.state first (real market regime) so regime_tag
    feeds insight_synthesizer's regime_playbook with a true dimension; falls back
    to macro risk_regime, then 'unknown'. Mirrors position_enrichment.current_regime
    so paper and demo/live brokers stamp the same regime source. Never raises.
    """
    try:
        from aletheia.storage.repositories import get_latest_market_regime
        mr = get_latest_market_regime()
        if mr and mr.get("state"):
            return mr["state"]
    except Exception:
        pass
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

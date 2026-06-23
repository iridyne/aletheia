"""Position manager — closes open PAPER positions by stop-loss / take-profit / max-hold.

This is the keystone that turns open positions into realized PnL, which in turn
activates the daily-loss breaker (risk/engine.py §8) and the strategy promotion
gates. It only updates the position projection + writes an audit event — it never
places an exchange order, so it lives in portfolio/ (not the change-controlled
execution/).

Governance:
- LIVE positions (bitget_live_limited) are NEVER auto-closed (future + human-only).
- The kill switch does NOT block closing — closing reduces risk.
- No latest price → skip the position (never fabricate an exit).
- The scheduler job that calls this still honors runtime.paused.
"""

import logging
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.portfolio.pnl import close_position, _latest_price
from aletheia.storage.repositories import list_positions

logger = logging.getLogger("aletheia.position_manager")

_PAPER_MODES = {"paper_internal", "bitget_demo", "binance_testnet", "okx_demo"}


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def _effective_stop(position: dict, cfg) -> float | None:
    stop = position.get("stop_price")
    if stop:
        return float(stop)
    pct = cfg.trading.default_stop_loss_pct
    entry = position["entry_price"]
    if not pct or entry <= 0:
        return None
    if position["direction"] in ("long", "buy"):
        return round(entry * (1 - pct / 100.0), 8)
    return round(entry * (1 + pct / 100.0), 8)


def _effective_take_profit(position: dict, cfg) -> float | None:
    tp = position.get("take_profit_price")
    if tp:
        return float(tp)
    pct = cfg.trading.default_take_profit_pct
    entry = position["entry_price"]
    if not pct or entry <= 0:
        return None
    if position["direction"] in ("long", "buy"):
        return round(entry * (1 + pct / 100.0), 8)
    return round(entry * (1 - pct / 100.0), 8)


def _get_atr_pct(symbol: str) -> float | None:
    """Return latest ATR% for a symbol from universe_feature_snapshots, or None.

    universe_feature_snapshots uses asset_id (FK → universe_registry) + ts,
    not symbol + created_at. We join through universe_registry to filter by symbol.
    """
    try:
        from aletheia.storage.sqlite import get_db
        db = get_db()
        row = db.execute(
            "SELECT ufs.atr_pct FROM universe_feature_snapshots ufs "
            "JOIN universe_registry ur ON ufs.asset_id = ur.id "
            "WHERE ur.symbol = ? "
            "ORDER BY ufs.ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row:
            val = row[0] if not hasattr(row, "get") else row.get("atr_pct")
            if val and float(val) > 0:
                return float(val)
    except Exception:
        pass
    return None


def _maybe_update_trailing_stop(position: dict, price: float, cfg) -> None:
    """Update stop_price for trailing stop and breakeven logic. Writes DB on change.

    Called before _decide_exit() so the updated stop is evaluated this cycle.
    No-op when trail_enabled=False.
    """
    if not cfg.trading.trail_enabled:
        return
    direction = position["direction"]
    is_long = direction in ("long", "buy")
    entry = float(position["entry_price"])
    current_stop = position.get("stop_price")
    if current_stop is not None:
        current_stop = float(current_stop)

    # 1. Breakeven move: when profit > breakeven_trigger_pct, floor stop at entry
    if current_stop is not None and entry > 0:
        profit_pct = ((price - entry) / entry * 100.0) if is_long else ((entry - price) / entry * 100.0)
        if profit_pct >= cfg.trading.breakeven_trigger_pct:
            if is_long and current_stop < entry:
                from aletheia.storage.repositories import update_position
                update_position(position["id"], stop_price=entry)
                position["stop_price"] = entry
                current_stop = entry
            elif not is_long and current_stop > entry:
                from aletheia.storage.repositories import update_position
                update_position(position["id"], stop_price=entry)
                position["stop_price"] = entry
                current_stop = entry

    # 2. Trailing stop: keep stop within trail_distance of best seen price
    high_water = position.get("trail_high_water")
    high_water = float(high_water) if high_water is not None else price

    atr_pct = _get_atr_pct(position.get("symbol", ""))
    if atr_pct:
        trail_distance = price * (atr_pct / 100.0) * cfg.trading.trail_atr_mult
    else:
        trail_distance = price * (cfg.trading.trail_pct / 100.0)

    updates: dict = {}
    if is_long:
        new_hw = max(high_water, price)
        new_stop = new_hw - trail_distance
        if current_stop is None or new_stop > current_stop:
            updates["stop_price"] = round(new_stop, 8)
            position["stop_price"] = updates["stop_price"]
        if new_hw != high_water:
            updates["trail_high_water"] = round(new_hw, 8)
    else:
        new_hw = min(high_water, price)
        new_stop = new_hw + trail_distance
        if current_stop is None or new_stop < current_stop:
            updates["stop_price"] = round(new_stop, 8)
            position["stop_price"] = updates["stop_price"]
        if new_hw != high_water:
            updates["trail_high_water"] = round(new_hw, 8)

    if updates:
        from aletheia.storage.repositories import update_position
        update_position(position["id"], **updates)
        # Sync the exchange TPSL bracket when the stop price changes
        new_stop = updates.get("stop_price")
        if new_stop and cfg.trading.exchange_tpsl_enabled and position.get("execution_mode") in ("bitget_demo", "bitget_live_limited"):
            try:
                from aletheia.execution.bitget_broker import replace_exchange_stop
                replace_exchange_stop(position["id"], float(new_stop))
            except Exception as e:
                logger.warning("exchange stop sync failed pos=%s: %s", position["id"], e)


def _decide_exit(position: dict, price: float, cfg) -> tuple[str, float] | None:
    """Return (reason, exit_price) if the position should close, else None."""
    is_long = position["direction"] in ("long", "buy")
    stop = _effective_stop(position, cfg)
    tp = _effective_take_profit(position, cfg)

    if stop is not None:
        if (is_long and price <= stop) or (not is_long and price >= stop):
            return "stop_loss", stop
    if tp is not None:
        if (is_long and price >= tp) or (not is_long and price <= tp):
            return "take_profit", tp

    max_hold = cfg.trading.max_position_hold_hours
    if max_hold and max_hold > 0:
        opened = _parse_dt(position.get("opened_at"))
        if opened is not None:
            age_hours = (datetime.now(timezone.utc) - opened).total_seconds() / 3600.0
            if age_hours >= max_hold:
                return "max_hold", price
    return None


def _sync_exchange_closed(cfg) -> int:
    """Detect positions the exchange bracket already closed and mark them locally.

    For bitget_demo positions: fetches live exchange positions and closes any local
    'open' position that the exchange no longer holds. Only runs when
    exchange_tpsl_enabled=True to avoid spurious calls in pure-paper mode.
    Returns count of positions synced.
    """
    if not cfg.trading.exchange_tpsl_enabled:
        return 0
    demo_positions = [p for p in list_positions(status="open") if p.get("execution_mode") == "bitget_demo"]
    if not demo_positions:
        return 0

    try:
        from aletheia.exchanges.bitget.client import BitgetClient
        from aletheia.data.symbols import to_bitget
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        client = BitgetClient(mode=cfg.exchange.mode)
        try:
            asyncio.get_running_loop()
            with ThreadPoolExecutor(max_workers=1) as pool:
                exchange_pos = pool.submit(asyncio.run, client.get_positions()).result()
        except RuntimeError:
            exchange_pos = asyncio.run(client.get_positions())
    except Exception as e:
        logger.warning("_sync_exchange_closed: get_positions failed: %s", e)
        return 0

    # Build set of (canonical_symbol, hold_side) that exchange still holds
    exchange_open: set = set()
    for ep in exchange_pos:
        qty = float(ep.quantity) if hasattr(ep, "quantity") else 0.0
        if qty > 0:
            from aletheia.data.symbols import to_canonical
            exchange_open.add((to_canonical(ep.symbol), ep.side))

    synced = 0
    for p in demo_positions:
        hold_side = "long" if p["direction"] in ("long", "buy") else "short"
        if (p["symbol"], hold_side) not in exchange_open:
            exit_price = _latest_price(p["symbol"]) or float(p.get("current_price") or p["entry_price"])
            try:
                close_position(p["id"], exit_price=float(exit_price), reason="exchange_bracket_triggered")
                synced += 1
            except Exception as e:
                logger.warning("_sync_exchange_closed: close failed %s: %s", p["id"], e)
    return synced


def evaluate_open_positions() -> dict:
    """Evaluate all open paper positions and close any that hit an exit rule.

    Checks price-based rules (stop/TP/max-hold), RuleSet exit signals,
    and market-aware exit intelligence (when enabled).
    """
    cfg = load_config()
    closed = 0
    checked = 0
    market_exits = 0

    # Check for exchange-bracket-triggered closes before the local polling loop
    _sync_exchange_closed(cfg)

    # Collect RuleSet exit signals once (avoids redundant bar fetches per position)
    ruleset_exit_position_ids: set[str] = set()
    try:
        from aletheia.trading.ruleset_scanner import scan_active_rulesets
        for sig in scan_active_rulesets():
            if sig.signal_type == "exit" and sig.position_id:
                ruleset_exit_position_ids.add(sig.position_id)
    except Exception as e:
        logger.warning("ruleset exit scan failed: %s", e)

    # Build market signal index once per cycle (batch query, not per-position)
    market_signal_index: dict = {}
    if cfg.trading.market_aware_exits.enabled:
        try:
            from aletheia.portfolio.market_signal_reader import get_market_signals_for_positions
            all_open = list_positions(status="open")
            syms = list({p["symbol"] for p in all_open if p.get("execution_mode") in _PAPER_MODES})
            if syms:
                market_signal_index = get_market_signals_for_positions(
                    syms, max_signal_age_min=cfg.trading.market_aware_exits.max_signal_age_min,
                )
        except Exception as e:
            logger.warning("market_signal_index build failed: %s", e)

    for p in list_positions(status="open"):
        if p.get("execution_mode") not in _PAPER_MODES:
            continue
        checked += 1
        price = _latest_price(p["symbol"])
        if price is None:
            continue

        _maybe_update_trailing_stop(p, float(price), cfg)

        decision = _decide_exit(p, float(price), cfg)
        if decision is None and p["id"] in ruleset_exit_position_ids:
            decision = ("ruleset_exit_signal", float(price))

        # Market-aware exit: only when mechanical exits didn't fire
        if decision is None and cfg.trading.market_aware_exits.enabled:
            signal = market_signal_index.get(p["symbol"])
            if signal is not None:
                try:
                    from aletheia.portfolio.exit_rules import evaluate_market_exit_rules
                    action = evaluate_market_exit_rules(p, float(price), signal, cfg)
                    if action.action == "urgent_exit":
                        decision = ("market_signal_exit", float(price))
                        market_exits += 1
                        logger.info("market_signal_exit: %s reason=%s", p["symbol"], action.reason)
                    elif action.action == "tighten_stop" and action.new_stop_price is not None:
                        from aletheia.storage.repositories import update_position
                        update_position(
                            p["id"],
                            stop_price=action.new_stop_price,
                            last_market_signal_action="tighten_stop",
                            last_market_signal_reason=action.reason[:200],
                            last_market_signal_at=datetime.now(timezone.utc).isoformat(),
                        )
                        p["stop_price"] = action.new_stop_price
                        logger.info("market_tighten_stop: %s new_stop=%.6f reason=%s",
                                    p["symbol"], action.new_stop_price, action.reason)
                except Exception as e:
                    logger.warning("market_aware_exit failed %s: %s", p["id"], e)

        if decision is None:
            continue
        reason, exit_price = decision
        try:
            if p.get("execution_mode") == "bitget_demo":
                from aletheia.execution.bitget_broker import close_demo_position
                close_demo_position(p["id"], reason=reason)
            elif p.get("execution_mode") == "binance_testnet":
                from aletheia.execution.binance_broker import close_testnet_position
                close_testnet_position(p["id"], reason=reason)
            elif p.get("execution_mode") in ("okx_demo", "okx_live_limited"):
                from aletheia.execution.okx_broker import close_okx_position
                close_okx_position(p["id"], reason=reason)
            else:
                close_position(p["id"], exit_price=exit_price, reason=reason)
            closed += 1
        except Exception as e:
            logger.warning("close failed for %s: %s", p["id"], e)
    return {"checked": checked, "closed": closed, "market_exits": market_exits}

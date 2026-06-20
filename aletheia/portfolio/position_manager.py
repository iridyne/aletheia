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


def evaluate_open_positions() -> dict:
    """Evaluate all open paper positions and close any that hit an exit rule.

    Checks both price-based rules (stop/TP/max-hold) and RuleSet exit signals
    from the strategy's compiled RuleSet (if any).
    """
    cfg = load_config()
    closed = 0
    checked = 0

    # Collect RuleSet exit signals once (avoids redundant bar fetches per position)
    ruleset_exit_position_ids: set[str] = set()
    try:
        from aletheia.trading.ruleset_scanner import scan_active_rulesets
        for sig in scan_active_rulesets():
            if sig.signal_type == "exit" and sig.position_id:
                ruleset_exit_position_ids.add(sig.position_id)
    except Exception as e:
        logger.warning("ruleset exit scan failed: %s", e)

    for p in list_positions(status="open"):
        # Never auto-close live positions (future + human-only).
        if p.get("execution_mode") not in _PAPER_MODES:
            continue
        checked += 1
        price = _latest_price(p["symbol"])
        if price is None:
            continue  # no honest exit price — skip, never fabricate

        # Determine exit reason: price-based first, then RuleSet signal
        decision = _decide_exit(p, float(price), cfg)
        if decision is None and p["id"] in ruleset_exit_position_ids:
            decision = ("ruleset_exit_signal", float(price))

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
    return {"checked": checked, "closed": closed}

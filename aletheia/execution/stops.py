"""Shared protective-stop helpers for execution brokers.

The three real-exchange brokers (okx/binance/bitget) are structural clones of one
another. A protective stop must (a) be persisted on the Position row at open and
(b) tighten — never loosen — when averaging into an existing position. Keeping that
logic here gives all brokers ONE source of truth so the historical "stop dropped on
the floor at open" bug cannot re-appear via copy-paste. Mirrors paper_broker.
"""

from __future__ import annotations


def merge_protective_stop(
    direction: str,
    new_stop: float | None,
    old_stop: float | None,
) -> float | None:
    """Return the more protective of two stops when averaging into a position.

    For a long, the higher stop is tighter (closer to price → less downside);
    for a short, the lower stop is tighter. If either side is None, prefer the
    one that exists (a stop beats no stop). Returns None only when both are None.
    """
    if new_stop is None:
        return old_stop
    if old_stop is None:
        return new_stop
    if direction in ("long", "buy"):
        return max(new_stop, old_stop)
    return min(new_stop, old_stop)


def place_exchange_protective_orders(position: dict) -> dict:
    """Place exchange-side reduce-only SL/TP for an EXISTING position (e.g. an
    adopted one), when enabled and supported. Broker-agnostic dispatch, non-fatal.

    Gating (both must hold, else no-op returning {}):
      1. cfg.trading.exchange_tpsl_enabled is True (the global feature switch;
         defaults False → this is dormant until the operator opts in).
      2. execution_mode is a DEMO mode. Live is deliberately excluded: the
         reconciler auto-adopts, so placing an exchange order on a live position
         would be an AUTONOMOUS LIVE ORDER (forbidden by the safety rules). Live
         adopted positions keep their DB stop/tp for human/position_manager use.

    Adoption parity: entry paths already persist brackets at open. This closes the
    gap where adopt_position persisted DB stop/tp but never placed the exchange-side
    bracket, leaving adopted positions without native protection when the gateway is
    down. Returns placed ids ({} if none). Bitget-demo parity is a trivial follow-up
    (adopt is OKX-only today — the active paper broker is okx_demo).
    """
    from aletheia.config.loader import load_config

    cfg = load_config()
    if not cfg.trading.exchange_tpsl_enabled:
        return {}

    mode = position.get("execution_mode") or ""
    stop = position.get("stop_price")
    tp = position.get("take_profit_price")
    if not stop and not tp:
        return {}

    if mode == "okx_demo":
        from aletheia.execution.okx_broker import place_okx_protective_orders
        return place_okx_protective_orders(
            position_id=position["id"],
            symbol=position["symbol"],
            market_type=position.get("market_type") or "usdt_futures",
            direction=position["direction"],
            quantity=float(position.get("quantity") or 0),
            stop_price=stop,
            take_profit_price=tp,
            execution_mode=mode,
        )
    return {}

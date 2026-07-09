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


def replace_exchange_protective_stop(position_id: str, new_stop_price: float) -> None:
    """Broker-agnostic dispatch: replace the exchange-side protective SL for a
    position whose trailed stop just tightened.

    #139 fix: the trail loop previously only reached Bitget — okx_demo and
    binance_testnet positions could see their DB stop_price tighten while the
    exchange bracket stayed at the OLD level, so a gateway outage would let the
    original (looser) stop close them. The trail feature is gated behind the
    default-off `exchange_tpsl_enabled` flag; when the operator opts in, all
    three brokers must honour it consistently. Non-fatal.

    Gating repeats the same rules as `place_exchange_protective_orders`:
      1. cfg.trading.exchange_tpsl_enabled True (default False)
      2. execution_mode is a DEMO/TESTNET mode (never an autonomous live order).
    """
    from aletheia.config.loader import load_config
    from aletheia.storage import repositories as _repo

    row = _repo._select_one("positions", position_id)
    if row is None:
        return
    cfg = load_config()
    if not cfg.trading.exchange_tpsl_enabled:
        return

    mode = row.get("execution_mode") or ""
    if mode in ("bitget_demo", "bitget_live_limited"):
        from aletheia.execution.bitget_broker import replace_exchange_stop
        replace_exchange_stop(position_id, new_stop_price)
    elif mode == "okx_demo":
        from aletheia.execution.okx_broker import replace_okx_exchange_stop
        replace_okx_exchange_stop(position_id, new_stop_price)
    elif mode == "binance_testnet":
        # Binance USDT-M client currently exposes only place_order (no
        # place_stop / cancel_algo yet). The DB stop_price is still tightened by
        # the caller — the exchange bracket just can't be replaced until the
        # client grows a stop-order surface. Log so this doesn't fail silently
        # if a user opts into exchange_tpsl_enabled on a Binance testnet account.
        import logging as _log
        _log.getLogger("aletheia.execution.stops").warning(
            "Binance trailed-stop exchange replacement not implemented "
            "(position %s); DB stop_price tightened but exchange bracket unchanged.",
            position_id,
        )
    # Live modes (okx_live_limited / binance_live_limited): deliberately no-op —
    # placing an exchange order from an autonomous trail update is forbidden.

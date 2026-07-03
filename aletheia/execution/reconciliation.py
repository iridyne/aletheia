"""Order reconciliation (guide §7 execution/reconciliation.py, §20.2).

Compares locally-tracked LIVE orders against the exchange's reported open orders.
A live order we marked 'submitted' that no longer appears open on the exchange is
treated as filled, and the divergence is audited. Paper/demo orders are ignored
(they have no exchange counterpart). Reconciliation never places or cancels
orders — it only converges local bookkeeping.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from aletheia.config.loader import load_config
from aletheia.storage.repositories import list_orders, update_order
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.reconciliation")

# Bitget live is the only broker with a wired get_open_orders() reconciliation
# path. OKX/Binance live orders are detected and ALERTED (not silently ignored)
# until those clients gain get_open_orders — see CR-EX-01 below.
_LIVE_MODE = "bitget_live_limited"
_UNRECONCILED_LIVE_MODES = ("okx_live_limited", "binance_live_limited")
_OPEN_LOCAL_STATES = {"submitted", "pending", "partial"}


def _run_coro(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def reconcile() -> dict:
    """Reconcile local live orders with the exchange. Returns a small summary."""
    cfg = load_config()
    all_orders = list_orders(limit=500)

    # CR-EX-01: OKX/Binance live orders have no get_open_orders() reconciliation
    # path yet. Rather than silently leaving them stuck in 'submitted' forever
    # (under-counting live exposure — a fund-safety hole), detect and ALERT so the
    # gap is visible the moment live trading is enabled on those brokers. Full
    # reconciliation is tracked in BACKEND_ISSUES §16.2 (needs OKXClient/
    # BinanceClient.get_open_orders).
    unreconciled = [
        o for o in all_orders
        if o.get("execution_mode") in _UNRECONCILED_LIVE_MODES
        and o.get("status") in _OPEN_LOCAL_STATES
        and o.get("exchange_order_id")
    ]
    if unreconciled:
        by_mode: dict[str, int] = {}
        for o in unreconciled:
            by_mode[o["execution_mode"]] = by_mode.get(o["execution_mode"], 0) + 1
        logger.warning("reconcile: %d live orders on un-reconciled brokers: %s", len(unreconciled), by_mode)
        write_audit({
            "actor": "system", "source": "reconciliation",
            "event_type": "infra_event", "entity_type": "order",
            "message": (
                f"CR-EX-01: {len(unreconciled)} live orders on brokers without a "
                f"reconciliation path ({by_mode}) — exposure may be under-counted. "
                "Needs OKX/Binance get_open_orders (BACKEND_ISSUES §16.2)."
            ),
        })

    # Only meaningful in live mode; in demo/paper there is nothing to reconcile.
    live_orders = [
        o for o in all_orders
        if o.get("execution_mode") == _LIVE_MODE
        and o.get("status") in _OPEN_LOCAL_STATES
        and o.get("exchange_order_id")
    ]
    if not live_orders:
        return {"checked": 0, "filled": 0, "errors": [], "unreconciled_live": len(unreconciled)}

    from aletheia.exchanges.bitget.client import BitgetClient
    client = BitgetClient(mode=cfg.exchange.mode)

    errors: list[str] = []
    open_ids: dict[str, set[str]] = {"spot": set(), "usdt_futures": set()}
    failed_markets: set[str] = set()
    for market_type in ("spot", "usdt_futures"):
        try:
            rows = _run_coro(client.get_open_orders(market_type=market_type))
            for r in rows:
                oid = r.get("orderId") or r.get("order_id")
                if oid:
                    open_ids[market_type].add(str(oid))
        except Exception as e:
            errors.append(f"{market_type}: {e}")
            failed_markets.add(market_type)
            logger.warning("reconcile: fetch open orders (%s) failed: %s", market_type, e)

    # If ALL markets failed, don't infer anything.
    if failed_markets == {"spot", "usdt_futures"}:
        return {"checked": len(live_orders), "filled": 0, "errors": errors}

    all_open_ids = open_ids["spot"] | open_ids["usdt_futures"]
    filled = 0
    for o in live_orders:
        # Skip orders whose market_type had a fetch error — don't infer fills.
        order_market = o.get("market_type", "spot")
        if order_market in failed_markets:
            continue
        if str(o["exchange_order_id"]) not in all_open_ids:
            update_order(o["id"], status="filled", filled_at=datetime.now(timezone.utc).isoformat())
            write_audit({
                "actor": "system", "source": "reconciliation",
                "event_type": "order_reconciled_filled", "entity_type": "order",
                "entity_id": o["id"],
                "before": {"status": o["status"]}, "after": {"status": "filled"},
                "message": f"Order {o['exchange_order_id']} no longer open on exchange; marked filled",
            })
            filled += 1

    return {"checked": len(live_orders), "filled": filled, "errors": errors,
            "unreconciled_live": len(unreconciled)}

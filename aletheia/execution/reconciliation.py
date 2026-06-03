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
from datetime import datetime

from aletheia.config.loader import load_config
from aletheia.storage.repositories import list_orders, update_order
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.reconciliation")

_LIVE_MODE = "bitget_live_limited"
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
    # Only meaningful in live mode; in demo/paper there is nothing to reconcile.
    live_orders = [
        o for o in list_orders(limit=500)
        if o.get("execution_mode") == _LIVE_MODE
        and o.get("status") in _OPEN_LOCAL_STATES
        and o.get("exchange_order_id")
    ]
    if not live_orders:
        return {"checked": 0, "filled": 0, "errors": []}

    from aletheia.exchanges.bitget.client import BitgetClient
    client = BitgetClient(mode=cfg.exchange.mode)

    errors: list[str] = []
    open_ids: set[str] = set()
    for market_type in ("spot", "usdt_futures"):
        try:
            rows = _run_coro(client.get_open_orders(market_type=market_type))
            for r in rows:
                oid = r.get("orderId") or r.get("order_id")
                if oid:
                    open_ids.add(str(oid))
        except Exception as e:
            errors.append(f"{market_type}: {e}")
            logger.warning("reconcile: fetch open orders (%s) failed: %s", market_type, e)

    # If we couldn't fetch open orders at all, don't infer fills (avoid false positives).
    if errors and not open_ids:
        return {"checked": len(live_orders), "filled": 0, "errors": errors}

    filled = 0
    for o in live_orders:
        if str(o["exchange_order_id"]) not in open_ids:
            update_order(o["id"], status="filled", filled_at=datetime.utcnow().isoformat())
            write_audit({
                "actor": "system", "source": "reconciliation",
                "event_type": "order_reconciled_filled", "entity_type": "order",
                "entity_id": o["id"],
                "before": {"status": o["status"]}, "after": {"status": "filled"},
                "message": f"Order {o['exchange_order_id']} no longer open on exchange; marked filled",
            })
            filled += 1

    return {"checked": len(live_orders), "filled": filled, "errors": errors}

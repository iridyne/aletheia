"""Order reconciliation (guide §7 execution/reconciliation.py, §20.2).

Compares locally-tracked LIVE orders against the exchange's reported open orders
and private fill history.  Absence from the open-order list is not proof of a
fill: cancelled, rejected, and history-query failures have the same appearance.
Paper/demo orders are ignored (they have no exchange counterpart).
Reconciliation never places or cancels orders — it only converges local
bookkeeping when the exchange provides direct evidence.
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
# ``partial`` is retained only for rows written by older builds.  Brokers now
# persist the exchange-normalized ``partially_filled`` spelling.
_OPEN_LOCAL_STATES = {
    "submitted", "pending", "partial", "partially_filled", "submission_unknown",
}


def _filled_quantity_by_order(rows) -> dict[str, float]:
    """Return cumulative, exchange-reported fill quantity by order id."""
    if isinstance(rows, dict):
        rows = rows.get("fillList") or rows.get("list") or []
    if not isinstance(rows, list):
        return {}
    quantities: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        order_id = row.get("orderId") or row.get("order_id") or row.get("ordId")
        if not order_id:
            continue
        raw_quantity = (
            row.get("fillQuantity") or row.get("fillQty") or row.get("baseVolume")
            or row.get("size") or row.get("fillSize")
        )
        try:
            quantity = float(raw_quantity)
        except (TypeError, ValueError):
            quantity = 0.0
        key = str(order_id)
        quantities[key] = quantities.get(key, 0.0) + max(quantity, 0.0)
    return quantities


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
        return {
            "checked": 0, "filled": 0, "inconclusive": 0, "errors": [],
            "unreconciled_live": len(unreconciled),
        }

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
        return {
            "checked": len(live_orders), "filled": 0,
            "inconclusive": len(live_orders), "errors": errors,
            "unreconciled_live": len(unreconciled),
        }

    filled = 0
    inconclusive = 0
    fill_quantities_by_market: dict[str, dict[str, float]] = {}
    failed_fill_markets: set[str] = set()
    for o in live_orders:
        # Skip orders whose market_type had a fetch error — don't infer fills.
        order_market = o.get("market_type", "spot")
        if order_market in failed_markets:
            continue
        exchange_order_id = str(o["exchange_order_id"])
        if exchange_order_id in open_ids.get(order_market, set()):
            continue

        # Open-order absence has several terminal meanings.  Fetch private fills
        # once per market and only converge to filled when that response carries
        # this exact exchange order id.
        if order_market not in fill_quantities_by_market and order_market not in failed_fill_markets:
            try:
                rows = _run_coro(client.get_fills(market_type=order_market))
                fill_quantities_by_market[order_market] = _filled_quantity_by_order(rows)
            except Exception:
                failed_fill_markets.add(order_market)
                errors.append(f"{order_market}: private fill history unavailable")

        reported_fill_quantity = fill_quantities_by_market.get(order_market, {}).get(
            exchange_order_id, 0.0,
        )
        requested_quantity = float(o.get("quantity") or 0.0)
        quantity_tolerance = max(1e-12, requested_quantity * 1e-9)
        if (
            requested_quantity > 0
            and reported_fill_quantity + quantity_tolerance >= requested_quantity
        ):
            update_order(
                o["id"], status="filled",
                filled_at=datetime.now(timezone.utc).isoformat(),
            )
            write_audit({
                "actor": "system", "source": "reconciliation",
                "event_type": "order_reconciled_filled", "entity_type": "order",
                "entity_id": o["id"],
                "before": {"status": o["status"]}, "after": {"status": "filled"},
                "message": (
                    f"Order {exchange_order_id} is absent from open orders and has "
                    "complete matching private fill evidence; marked filled"
                ),
            })
            filled += 1
            continue

        inconclusive += 1
        write_audit({
            "actor": "system", "source": "reconciliation",
            "event_type": "order_reconciliation_inconclusive", "entity_type": "order",
            "entity_id": o["id"],
            "before": {"status": o["status"]}, "after": {"status": o["status"]},
            "message": (
                f"Order {exchange_order_id} is absent from open orders but has no "
                "matching private fill evidence; local status left unresolved"
            ),
        })

    return {"checked": len(live_orders), "filled": filled, "inconclusive": inconclusive,
            "errors": errors,
            "unreconciled_live": len(unreconciled)}

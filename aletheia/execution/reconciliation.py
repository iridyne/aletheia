"""Order reconciliation (guide §7 execution/reconciliation.py, §20.2).

Compares locally-tracked exchange orders against the venue's reported open orders
and private fill history.  Absence from the open-order list is not proof of a
fill: cancelled, rejected, and history-query failures have the same appearance.
Local paper orders are ignored. Bitget demo orders are exchange-side records and
therefore use the same evidence and materialization path as limited live orders.
Reconciliation never places or cancels orders — it only converges local
bookkeeping when the exchange provides direct evidence.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from aletheia.data.symbols import to_bitget, to_canonical
from aletheia.execution.exchange_materialization import (
    MaterializationIntegrityError,
    durable_private_fill_id,
    evidence_for_order,
    funding_request_for_complete_close,
    materialize_bitget_close,
    materialize_bitget_entry,
)
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.repositories import update_order
from aletheia.storage.sqlite import get_db
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.reconciliation")

# Each execution mode selects its own account explicitly; using the current
# config mode could reconcile a live Order against a demo/testnet account after
# an operator configuration change.
_BITGET_ACCOUNT_MODES = {
    "bitget_live_limited": "live",
    "bitget_demo": "demo",
}
_MULTIBROKER_ACCOUNT_MODES = (
    "okx_demo",
    "okx_live_limited",
    "binance_testnet",
    "binance_live_limited",
)
# ``partial`` is retained only for rows written by older builds.  Brokers now
# persist the exchange-normalized ``partially_filled`` spelling.
_OPEN_LOCAL_STATES = (
    "submitted", "pending", "partial", "partially_filled", "submission_unknown",
    "materialization_missing",
)


def _list_unresolved_core_orders() -> list[dict]:
    """Read every unresolved core Order; reconciliation must not truncate risk."""
    placeholders = ",".join("?" for _ in _OPEN_LOCAL_STATES)
    rows = get_db().execute(
        f"""
        SELECT *
        FROM orders
        WHERE status IN ({placeholders})
        ORDER BY created_at ASC, rowid ASC
        """,
        _OPEN_LOCAL_STATES,
    ).fetchall()
    return [dict(row) for row in rows]


def _local_fill_chain_materialized(order: dict) -> bool:
    """Confirm that an exchange fill already exists in the local causal chain.

    Reconciliation cannot safely manufacture Fill economics or a Position.  It
    may only close an unresolved Order status after another real execution path
    has durably written the corresponding Fill and entry/close Position state.
    """
    db = get_db()
    local_fill = db.execute(
        "SELECT COALESCE(SUM(fill_quantity), 0) AS quantity FROM fills WHERE order_id=?",
        (order["id"],),
    ).fetchone()
    local_quantity = float(local_fill["quantity"] or 0.0)
    requested_quantity = float(order.get("quantity") or 0.0)
    tolerance = max(1e-12, requested_quantity * 1e-9)
    if (
        requested_quantity <= 0
        or abs(local_quantity - requested_quantity) > tolerance
    ):
        return False

    # New rows carry direct Position identity.  A close must have reached the
    # terminal Position state, while an entry/averaging leg only needs its
    # confirmed quantity represented in that Position.  This avoids guessing a
    # close target from proposal + symbol, which is ambiguous after averaging.
    position_id = order.get("position_id")
    if position_id:
        linked_position = db.execute(
            "SELECT status, quantity FROM positions WHERE id=? LIMIT 1",
            (position_id,),
        ).fetchone()
        if linked_position is None:
            return False
        if order.get("close_reason") is not None:
            return linked_position["status"] == "closed"
        return (
            float(linked_position["quantity"] or 0.0) + tolerance
            >= local_quantity
        )

    # Entry materialization keeps a permanent direct order backlink.
    entry_position = db.execute(
        "SELECT quantity FROM positions WHERE order_id=? LIMIT 1", (order["id"],),
    ).fetchone()
    if entry_position is not None:
        return float(entry_position["quantity"] or 0.0) + tolerance >= local_quantity

    # A close still in its handoff window keeps pending_close_order_id.  Seeing a
    # Fill while that Position remains open is not complete materialization.
    pending_close = db.execute(
        "SELECT status FROM positions WHERE pending_close_order_id=? LIMIT 1",
        (order["id"],),
    ).fetchone()
    if pending_close is not None:
        return pending_close["status"] == "closed"

    # Successful close cleanup clears pending_close_order_id.  The close order
    # still shares the entry proposal and instrument identity, while the Position
    # permanently identifies its distinct entry Order.
    proposal_id = order.get("proposal_id")
    if not proposal_id:
        return False
    closed_position = db.execute(
        """
        SELECT 1
        FROM positions
        WHERE proposal_id=?
          AND symbol=?
          AND market_type=?
          AND direction=?
          AND execution_mode=?
          AND status='closed'
          AND COALESCE(order_id, '') <> ?
        LIMIT 1
        """,
        (
            proposal_id, order.get("symbol"), order.get("market_type"),
            order.get("direction"), order.get("execution_mode"), order["id"],
        ),
    ).fetchone()
    return closed_position is not None


def _list_bitget_demo_filled_entry_gaps() -> list[dict]:
    """Select historical filled entry gaps without widening the normal scan.

    This query is intentionally unbounded and deliberately separate from
    ``_OPEN_LOCAL_STATES``.  Only native Bitget demo entries with a durable venue
    id are eligible; adopted observations and close Orders remain read-only.
    """
    rows = get_db().execute(
        """
        SELECT o.*
        FROM orders AS o
        WHERE o.status='filled'
          AND o.execution_mode='bitget_demo'
          AND o.source='native'
          AND COALESCE(o.exchange_order_id, '') <> ''
          AND o.close_reason IS NULL
          AND NOT (
              COALESCE(o.position_id, '') <> ''
              AND COALESCE(o.client_order_id, '') = ('close-' || o.position_id)
          )
          AND NOT EXISTS (
              SELECT 1
              FROM positions AS p
              WHERE p.pending_close_order_id=o.id
          )
          AND (
              CAST(o.quantity AS REAL) <= 0
              OR ABS(
                  COALESCE((
                      SELECT SUM(f.fill_quantity)
                      FROM fills AS f
                      WHERE f.order_id=o.id
                  ), 0) - CAST(o.quantity AS REAL)
              ) > MAX(1e-12, ABS(CAST(o.quantity AS REAL)) * 1e-9)
              OR NOT EXISTS (
                  SELECT 1
                  FROM positions AS p
                  WHERE (p.id=o.position_id OR p.order_id=o.id)
                    AND CAST(p.quantity AS REAL)
                        + MAX(1e-12, ABS(CAST(o.quantity AS REAL)) * 1e-9)
                        >= COALESCE((
                            SELECT SUM(f.fill_quantity)
                            FROM fills AS f
                            WHERE f.order_id=o.id
                        ), 0)
              )
          )
        ORDER BY o.created_at ASC, o.rowid ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _has_compatible_open_position(order: dict) -> bool:
    row = get_db().execute(
        """
        SELECT 1
        FROM positions
        WHERE status='open' AND symbol=? AND market_type=? AND direction=?
          AND execution_mode=?
        LIMIT 1
        """,
        (
            order.get("symbol"),
            order.get("market_type"),
            order.get("direction"),
            order.get("execution_mode"),
        ),
    ).fetchone()
    return row is not None


def _complete_exact_gap_evidence(order: dict, rows) -> bool:
    """Require every exact-query row to carry complete matching economics."""
    raw_rows = _response_rows(rows)
    exchange_order_id = str(order.get("exchange_order_id") or "").strip()
    if not exchange_order_id or not raw_rows:
        return False
    if any(str(row.get("orderId") or "") != exchange_order_id for row in raw_rows):
        return False
    matched_id, evidence = evidence_for_order(order, raw_rows)
    # The parser drops rows with missing fill id, price, quantity, fee, or
    # exchange time.  Requiring a one-to-one result makes any such omission
    # inconclusive instead of silently materializing only the usable subset.
    if matched_id != exchange_order_id or len(evidence) != len(raw_rows):
        return False
    requested = _finite_float(order.get("quantity"), positive=True)
    if requested is None:
        return False
    if any(
        not math.isfinite(item.price)
        or not math.isfinite(item.quantity)
        or not math.isfinite(item.fee)
        or item.price <= 0
        or item.quantity <= 0
        or item.fee < 0
        or item.filled_at_ms <= 0
        for item in evidence
    ):
        return False
    quantity = sum(item.quantity for item in evidence)
    return _numbers_match(quantity, requested)


def _finite_float(
    value,
    *,
    positive: bool = False,
    nonnegative: bool = False,
) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def _numbers_match(left: float, right: float) -> bool:
    tolerance = max(1e-12, max(abs(left), abs(right)) * 1e-9)
    return abs(left - right) <= tolerance


def _stored_money_matches(left: float, right: float) -> bool:
    # Position fees are intentionally normalized to eight decimals by the PnL
    # writer, while reconciliation evidence retains the raw private sum.
    tolerance = max(5e-9, max(abs(left), abs(right)) * 1e-9)
    return abs(left - right) <= tolerance


def _evidence_fee_in_quote(
    *,
    fee: float,
    fee_currency: str,
    fill_price: float,
    symbol: str,
) -> float | None:
    """Recompute the quote fee using the materializer's direct-fill rules."""
    canonical = to_canonical(symbol)
    base, separator, quote = canonical.partition("/")
    currency = str(fee_currency or "").upper()
    if not separator or not base or not quote or not currency:
        return None
    if currency == quote.upper():
        return fee
    if currency == base.upper():
        return fee * fill_price
    return None


def _timestamp_ms(value) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    try:
        return int(round(parsed.timestamp() * 1000))
    except (OSError, OverflowError, ValueError):
        return None


def _closed_pending_chain_complete(
    position: dict,
    order: dict | None,
    fills: list[dict],
) -> bool:
    """Prove a stale pending marker from the complete durable close chain."""
    if order is None or not fills:
        return False
    if (
        position.get("status") != "closed"
        or position.get("execution_mode") != "bitget_demo"
        or order.get("execution_mode") != "bitget_demo"
        or order.get("source") != "native"
        or order.get("status") != "filled"
        or position.get("pending_close_order_id") != order.get("id")
        or order.get("position_id") != position.get("id")
        or not str(order.get("exchange_order_id") or "").strip()
        or not order.get("close_reason")
        or position.get("close_reason") != order.get("close_reason")
        or not bool(position.get("exit_fill_verified"))
        or position.get("exit_price_source") != "broker_fill"
        or order.get("client_order_id") != f"close-{position.get('id')}"
    ):
        return False
    for field in (
        "proposal_id",
        "symbol",
        "market_type",
        "direction",
        "execution_mode",
    ):
        if order.get(field) != position.get(field):
            return False

    order_quantity = _finite_float(order.get("quantity"), positive=True)
    position_quantity = _finite_float(position.get("quantity"), positive=True)
    exit_price = _finite_float(position.get("current_price"), positive=True)
    exit_fee = _finite_float(position.get("exit_fee"), nonnegative=True)
    if None in (order_quantity, position_quantity, exit_price, exit_fee):
        return False
    if not _numbers_match(order_quantity, position_quantity):
        return False

    try:
        evidence = json.loads(position.get("reconciliation_evidence") or "")
    except (TypeError, ValueError):
        return False
    if not isinstance(evidence, dict):
        return False
    if (
        evidence.get("source") != "bitget_private_close_fills"
        or evidence.get("source_endpoint") != "private_fills"
        or evidence.get("broker_id") != "bitget_demo"
        or evidence.get("account_environment") != "demo"
        or evidence.get("execution_mode") != "bitget_demo"
        or str(evidence.get("exchange_order_id") or "")
        != str(order.get("exchange_order_id"))
        or evidence.get("client_order_id") != order.get("client_order_id")
    ):
        return False

    evidence_fills = evidence.get("fills")
    fill_ids = evidence.get("fill_ids")
    durable_fill_ids = evidence.get("durable_fill_ids")
    if (
        not isinstance(evidence_fills, list)
        or not evidence_fills
        or not isinstance(fill_ids, list)
        or not isinstance(durable_fill_ids, list)
        or len(evidence_fills) != len(fill_ids)
        or len(evidence_fills) != len(durable_fill_ids)
        or len(evidence_fills) != len(fills)
    ):
        return False

    local_by_id = {str(fill.get("id") or ""): fill for fill in fills}
    if len(local_by_id) != len(fills) or "" in local_by_id:
        return False
    expected_remote_ids: list[str] = []
    expected_durable_ids: list[str] = []
    total_quantity = 0.0
    total_notional = 0.0
    total_fee_in_quote = 0.0
    latest_fill_ms: int | None = None
    for item in evidence_fills:
        if not isinstance(item, dict):
            return False
        remote_fill_id = str(
            item.get("remote_fill_id") or item.get("fill_id") or ""
        ).strip()
        if not remote_fill_id or str(item.get("fill_id") or "") != remote_fill_id:
            return False
        try:
            durable_fill_id = durable_private_fill_id(
                "bitget_demo", remote_fill_id
            )
        except MaterializationIntegrityError:
            return False
        if str(item.get("durable_fill_id") or "") != durable_fill_id:
            return False
        local = local_by_id.get(durable_fill_id)
        if local is None or local.get("fee_source") != "reconciled":
            return False

        item_price = _finite_float(item.get("price"), positive=True)
        item_quantity = _finite_float(item.get("quantity"), positive=True)
        item_fee = _finite_float(item.get("fee"), nonnegative=True)
        local_price = _finite_float(local.get("fill_price"), positive=True)
        local_quantity = _finite_float(local.get("fill_quantity"), positive=True)
        local_fee = _finite_float(local.get("fee"), nonnegative=True)
        item_fee_currency = str(item.get("fee_currency") or "").upper()
        local_fee_currency = str(local.get("fee_currency") or "").upper()
        item_time_ms = _timestamp_ms(item.get("filled_at"))
        local_time_ms = _timestamp_ms(local.get("filled_at"))
        if None in (
            item_price,
            item_quantity,
            item_fee,
            local_price,
            local_quantity,
            local_fee,
            item_time_ms,
            local_time_ms,
        ):
            return False
        if (
            not _numbers_match(item_price, local_price)
            or not _numbers_match(item_quantity, local_quantity)
            or not _numbers_match(item_fee, local_fee)
            or not item_fee_currency
            or item_fee_currency != local_fee_currency
            or item_time_ms != local_time_ms
        ):
            return False
        fee_in_quote = _evidence_fee_in_quote(
            fee=item_fee,
            fee_currency=item_fee_currency,
            fill_price=item_price,
            symbol=str(order.get("symbol") or ""),
        )
        if fee_in_quote is None:
            return False
        expected_remote_ids.append(remote_fill_id)
        expected_durable_ids.append(durable_fill_id)
        total_quantity += item_quantity
        total_notional += item_quantity * item_price
        total_fee_in_quote += fee_in_quote
        latest_fill_ms = max(latest_fill_ms or item_time_ms, item_time_ms)

    if (
        len(set(expected_remote_ids)) != len(expected_remote_ids)
        or len(set(expected_durable_ids)) != len(expected_durable_ids)
        or fill_ids != expected_remote_ids
        or durable_fill_ids != expected_durable_ids
        or set(local_by_id) != set(expected_durable_ids)
    ):
        return False
    evidence_quantity = _finite_float(evidence.get("fill_quantity"), positive=True)
    evidence_fee = _finite_float(evidence.get("fee_in_quote"), nonnegative=True)
    if evidence_quantity is None or evidence_fee is None:
        return False
    if (
        not _numbers_match(total_quantity, order_quantity)
        or not _numbers_match(total_quantity, position_quantity)
        or not _numbers_match(total_quantity, evidence_quantity)
        or not _numbers_match(total_notional / total_quantity, exit_price)
        or not _numbers_match(total_fee_in_quote, evidence_fee)
        or not _stored_money_matches(evidence_fee, exit_fee)
        or _timestamp_ms(order.get("filled_at")) != latest_fill_ms
        or _timestamp_ms(position.get("closed_at")) != latest_fill_ms
    ):
        return False
    return True


def _clear_verified_closed_pending() -> list[str]:
    """Clear only local stale markers backed by a complete close chain."""
    candidate_ids = [
        str(row["id"])
        for row in get_db().execute(
            """
            SELECT id
            FROM positions
            WHERE status='closed'
              AND execution_mode='bitget_demo'
              AND COALESCE(pending_close_order_id, '') <> ''
            ORDER BY closed_at ASC, rowid ASC
            """
        ).fetchall()
    ]
    cleared_ids: list[str] = []
    for position_id in candidate_ids:
        cleared = False
        close_order_id = ""
        with repo.transaction(immediate=True):
            db = get_db()
            row = db.execute(
                "SELECT * FROM positions WHERE id=? LIMIT 1", (position_id,)
            ).fetchone()
            if row is None:
                continue
            position = dict(row)
            close_order_id = str(position.get("pending_close_order_id") or "")
            order_row = db.execute(
                "SELECT * FROM orders WHERE id=? LIMIT 1", (close_order_id,)
            ).fetchone()
            order = dict(order_row) if order_row is not None else None
            fill_rows = [
                dict(fill)
                for fill in db.execute(
                    "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                    (close_order_id,),
                ).fetchall()
            ]
            if not _closed_pending_chain_complete(position, order, fill_rows):
                continue
            cursor = db.execute(
                """
                UPDATE positions
                SET pending_close_order_id=NULL,
                    pending_close_status=NULL,
                    pending_close_requested_at=NULL
                WHERE id=? AND status='closed' AND pending_close_order_id=?
                """,
                (position_id, close_order_id),
            )
            cleared = cursor.rowcount == 1
            if cleared:
                repo.insert_audit_event(AuditEvent(
                    actor="system",
                    source="reconciliation",
                    event_type="position_pending_close_reconciled",
                    entity_type="position",
                    entity_id=position_id,
                    before_state=json.dumps(
                        {"pending_close_order_id": close_order_id},
                        sort_keys=True,
                    ),
                    after_state=json.dumps(
                        {"pending_close_order_id": None},
                        sort_keys=True,
                    ),
                    message=(
                        "Cleared stale pending-close bookkeeping after the durable "
                        "close Order, Fill evidence, and closed Position agreed."
                    ),
                ))
        if not cleared:
            continue
        cleared_ids.append(position_id)
        audit_payload = {
            "actor": "system",
            "source": "reconciliation",
            "event_type": "position_pending_close_reconciled",
            "entity_type": "position",
            "entity_id": position_id,
            "reason": "verified_closed_chain_stale_pending_cleared",
            "before": {"pending_close_order_id": close_order_id},
            "after": {"pending_close_order_id": None},
            "message": (
                "Cleared stale pending-close bookkeeping after the durable close "
                "Order, Fill evidence, and closed Position agreed."
            ),
        }
        try:
            write_audit(audit_payload)
        except Exception as exc:
            # The database event committed atomically with the state change and
            # remains authoritative. A secondary file-sink outage must not make
            # reconciliation retry a cleanup that already succeeded.
            logger.error(
                "pending-close cleanup JSONL audit failed for %s (%s)",
                position_id,
                type(exc).__name__,
            )
    return cleared_ids


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
_LIVE_MODE = "bitget_live_limited"
_CCXT_LIVE_MODES = ("okx_live_limited", "binance_live_limited")

def resolve_orders_missing_from_exchange(
    local_orders: list[dict],
    exchange_open_ids: set[str],
    modes: tuple[str, ...] = _CCXT_LIVE_MODES,
) -> list[str]:
    """Pure CR-EX-01 core: given locally-open orders (for the given execution modes) and
    the set of order ids the exchange still reports OPEN, return the exchange_order_ids
    that should converge to 'filled' — locally open but no longer open on the exchange.

    Kept pure so the decision is unit-tested without an exchange; ``exchange_open_ids`` is
    produced by OKXClient / BinanceClient.get_open_orders() (implemented from CCXT; the
    real response shape is pending one demo sampling — see _reconcile_ccxt_broker).
    """
    resolved: list[str] = []
    for o in local_orders:
        if (
            o.get("execution_mode") in modes
            and o.get("status") in _OPEN_LOCAL_STATES
            and o.get("exchange_order_id")
            and o["exchange_order_id"] not in exchange_open_ids
        ):
            resolved.append(o["exchange_order_id"])
    return resolved


def _run_coro(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _response_rows(rows) -> list[dict]:
    if isinstance(rows, dict):
        rows = rows.get("fillList") or rows.get("list") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _rows_reference_order(order: dict, rows) -> bool:
    """Return whether raw private history names this venue/client identity."""
    exchange_order_id = str(order.get("exchange_order_id") or "")
    client_order_id = str(order.get("client_order_id") or "")
    for row in _response_rows(rows):
        if exchange_order_id and str(
            row.get("orderId") or row.get("order_id") or ""
        ) == exchange_order_id:
            return True
        if client_order_id and str(
            row.get("clientOid") or row.get("clientOrderId") or ""
        ) == client_order_id:
            return True
    return False


def _needs_symbol_scoped_history(order: dict, rows) -> bool:
    """Detect when the default fill page cannot prove this unresolved Order."""
    if order.get("source") != "native":
        return not _rows_reference_order(order, rows)
    try:
        _, evidence = evidence_for_order(order, rows)
    except MaterializationIntegrityError:
        # Conflicting evidence must be rejected by the materializer, not hidden
        # by replacing it with a second query result.
        return False
    return not evidence


def _is_close_order(order: dict) -> bool:
    if order.get("close_reason"):
        return True
    position_id = order.get("position_id")
    if not position_id:
        return False
    row = get_db().execute(
        "SELECT pending_close_order_id FROM positions WHERE id=? LIMIT 1",
        (position_id,),
    ).fetchone()
    return row is not None and row["pending_close_order_id"] == order["id"]


def _write_reconcile_event(order: dict, *, state: str, reason: str) -> None:
    write_audit({
        "actor": "system",
        "source": "reconciliation",
        "event_type": "order_reconciled" if state == "filled" else "order_reconciliation_inconclusive",
        "entity_type": "order",
        "entity_id": order["id"],
        "reason": reason,
        "before": {"status": order.get("status")},
        "after": {"status": state, "reason": reason},
        "message": (
            f"Order reconciliation reached {state} from direct private exchange "
            f"evidence ({reason})."
        ),
    })


def _write_filled_gap_event(
    order: dict,
    *,
    recovered: bool,
    reason: str,
) -> None:
    """Keep an old filled status honest when its chain cannot be recovered."""
    write_audit({
        "actor": "system",
        "source": "reconciliation",
        "event_type": (
            "order_reconciled"
            if recovered
            else "order_reconciliation_inconclusive"
        ),
        "entity_type": "order",
        "entity_id": order["id"],
        "reason": reason,
        "before": {"status": order.get("status")},
        "after": {
            "status": order.get("status"),
            "chain_recovered": recovered,
            "reason": reason,
        },
        "message": (
            "Recovered the historical filled Order's durable Fill/Position chain "
            "from exact complete private evidence."
            if recovered
            else "Historical filled Order chain recovery remained inconclusive; "
            "the existing Order status and local exposure were left unchanged."
        ),
    })


def _fetch_multibroker_evidence(order: dict):
    """Select the broker-owned private history reader for this exact account."""
    execution_mode = order.get("execution_mode")
    if execution_mode in {"okx_demo", "okx_live_limited"}:
        from aletheia.execution.okx_broker import fetch_private_fill_evidence

        return fetch_private_fill_evidence(order)
    if execution_mode in {"binance_testnet", "binance_live_limited"}:
        from aletheia.execution.binance_broker import fetch_private_fill_evidence

        return fetch_private_fill_evidence(order)
    raise ValueError("unsupported exchange reconciliation mode")


async def _fetch_multibroker_funding(order: dict, request: dict) -> dict:
    """Read the exact private funding ledger for a proven complete close."""
    execution_mode = str(order.get("execution_mode") or "")
    if execution_mode in {"okx_demo", "okx_live_limited"}:
        from aletheia.exchanges.okx.client import OKXClient

        client = OKXClient(
            mode="demo" if execution_mode == "okx_demo" else "live"
        )
    elif execution_mode in {"binance_testnet", "binance_live_limited"}:
        from aletheia.exchanges.binance.client import BinanceClient

        client = BinanceClient(
            mode="testnet" if execution_mode == "binance_testnet" else "live"
        )
    else:
        raise ValueError("unsupported exchange funding mode")
    try:
        return await client.get_funding_ledger(
            broker_id=execution_mode,
            symbol=str(request["position"]["symbol"]),
            start_time_ms=int(request["start_time_ms"]),
            end_time_ms=int(request["end_time_ms"]),
        )
    finally:
        try:
            await client.close()
        except Exception as exc:
            logger.warning(
                "funding reconciliation client close failed (%s)",
                type(exc).__name__,
            )

def _ccxt_client_for_mode(mode: str):
    """Build the live CCXT client for a reconcilable mode."""
    if mode == "okx_live_limited":
        from aletheia.exchanges.okx.client import OKXClient
        return OKXClient(mode="live")
    if mode == "binance_live_limited":
        from aletheia.exchanges.binance.client import BinanceClient
        return BinanceClient(mode="live")
    raise ValueError(f"no CCXT reconcile client for mode {mode}")


def _reconcile_ccxt_broker(mode: str, all_orders: list[dict]) -> dict:
    """CR-EX-01: reconcile one CCXT broker's live orders via get_open_orders(), mirroring
    the Bitget path. Fail-safe — on ANY fetch failure infer NOTHING and return the orders
    as ``unreconciled`` (for alerting) rather than fabricating a fill from a failed read.

    Returns {"checked", "filled", "errors", "unreconciled": [orders that couldn't be
    reconciled]}.
    """
    live = [
        o for o in all_orders
        if o.get("execution_mode") == mode
        and o.get("status") in _OPEN_LOCAL_STATES
        and o.get("exchange_order_id")
    ]
    if not live:
        return {"checked": 0, "filled": 0, "errors": [], "unreconciled": []}
    try:
        rows = _run_coro(_ccxt_client_for_mode(mode).get_open_orders())
    except Exception as e:
        logger.warning("reconcile: %s get_open_orders failed: %s", mode, e)
        return {"checked": len(live), "filled": 0, "errors": [f"{mode}: {e}"], "unreconciled": live}

    open_ids = {str(r.get("id")) for r in (rows or []) if r.get("id")}
    resolved = set(resolve_orders_missing_from_exchange(live, open_ids, modes=(mode,)))
    filled = 0
    for o in live:
        if o["exchange_order_id"] in resolved:
            update_order(o["id"], status="filled", filled_at=datetime.now(timezone.utc).isoformat())
            write_audit({
                "actor": "system", "source": "reconciliation",
                "event_type": "order_reconciled_filled", "entity_type": "order",
                "entity_id": o["id"],
                "before": {"status": o["status"]}, "after": {"status": "filled"},
                "message": f"Order {o['exchange_order_id']} ({mode}) no longer open on exchange; marked filled",
            })
            filled += 1
    return {"checked": len(live), "filled": filled, "errors": [], "unreconciled": []}

def reconcile() -> dict:
    """Reconcile unresolved exchange Orders and materialize proven fills."""
    all_orders = _list_unresolved_core_orders()
    filled_gap_orders = _list_bitget_demo_filled_entry_gaps()
    pending_close_cleared_ids = _clear_verified_closed_pending()

    bitget_orders = [
        o for o in all_orders
        if o.get("execution_mode") in _BITGET_ACCOUNT_MODES
        and o.get("status") in _OPEN_LOCAL_STATES
        and (o.get("exchange_order_id") or o.get("client_order_id"))
    ]
    multibroker_orders = [
        o for o in all_orders
        if o.get("execution_mode") in _MULTIBROKER_ACCOUNT_MODES
        and o.get("status") in _OPEN_LOCAL_STATES
        and (o.get("exchange_order_id") or o.get("client_order_id"))
    ]
    errors: list[str] = []
    filled = 0
    inconclusive = 0
    partially_filled = 0
    materialized_fills = 0
    filled_gap_recovered = 0
    filled_gap_inconclusive = 0

    # OKX and Binance expose actual per-fill economics through broker-owned
    # account-explicit private history readers. Native entry and close Orders
    # share the atomic materializer after normalization.
    for order in multibroker_orders:
        execution_mode = str(order.get("execution_mode") or "")
        market_type = str(order.get("market_type") or "")
        if order.get("source") != "native":
            inconclusive += 1
            _write_reconcile_event(
                order,
                state=str(order.get("status") or "unresolved"),
                reason="non_native_demo_order_read_only",
            )
            continue
        try:
            rows = _response_rows(
                _run_coro(_fetch_multibroker_evidence(order))
            )
        except Exception as exc:
            inconclusive += 1
            errors.append(
                f"{execution_mode}/{market_type}: private fill query unavailable"
            )
            logger.warning(
                "reconcile: private fill query failed for %s order %s (%s)",
                execution_mode,
                order["id"],
                type(exc).__name__,
            )
            _write_reconcile_event(
                order,
                state=str(order.get("status") or "unresolved"),
                reason="private_fill_history_unavailable",
            )
            continue
        funding_ledger = None
        is_close_order = _is_close_order(order)
        if is_close_order:
            funding_request = funding_request_for_complete_close(order["id"], rows)
            if funding_request is not None:
                try:
                    funding_ledger = _run_coro(
                        _fetch_multibroker_funding(order, funding_request)
                    )
                except Exception as exc:
                    errors.append(
                        f"{execution_mode}/{market_type}: private funding history unavailable"
                    )
                    logger.warning(
                        "reconcile: funding query failed for %s order %s (%s)",
                        execution_mode,
                        order["id"],
                        type(exc).__name__,
                    )
        try:
            result = (
                materialize_bitget_close(
                    order["id"], rows, funding_ledger=funding_ledger
                )
                if is_close_order
                else materialize_bitget_entry(order["id"], rows)
            )
        except (MaterializationIntegrityError, ValueError) as exc:
            inconclusive += 1
            errors.append(
                f"{execution_mode}/{market_type}: local materialization rejected"
            )
            logger.warning(
                "reconcile: materialization rejected for %s order %s (%s)",
                execution_mode,
                order["id"],
                type(exc).__name__,
            )
            _write_reconcile_event(
                order,
                state=str(order.get("status") or "unresolved"),
                reason="local_materialization_rejected",
            )
            continue
        except Exception as exc:
            inconclusive += 1
            errors.append(
                f"{execution_mode}/{market_type}: local materialization failed"
            )
            logger.warning(
                "reconcile: materialization failed for %s order %s (%s)",
                execution_mode,
                order["id"],
                type(exc).__name__,
            )
            try:
                update_order(
                    order["id"],
                    status="materialization_missing",
                    error_reason="exchange_fill_local_materialization_missing",
                    filled_at=None,
                )
            except Exception:
                pass
            _write_reconcile_event(
                order,
                state="materialization_missing",
                reason="local_materialization_failed",
            )
            continue

        materialized_fills += result.new_fill_count
        if result.state == "filled":
            filled += 1
            _write_reconcile_event(
                order, state="filled", reason="private_fill_materialized"
            )
        elif result.state == "partially_filled":
            partially_filled += 1
            _write_reconcile_event(
                order,
                state="partially_filled",
                reason="partial_private_fill_materialized",
            )
        elif result.state == "overfilled":
            inconclusive += 1
            _write_reconcile_event(
                order,
                state="overfilled",
                reason="exchange_quantity_exceeds_local_authority",
            )
        else:
            inconclusive += 1
            _write_reconcile_event(
                order,
                state=str(order.get("status") or "unresolved"),
                reason="complete_exchange_fill_not_proven",
            )
    from aletheia.exchanges.bitget.client import BitgetClient

    # Historical terminal Orders are never added to the ordinary open-order
    # scan.  They get a dedicated, exact orderId-only path after local authority
    # and exposure preflights have passed.
    filled_gap_client = None
    for order in filled_gap_orders:
        market_type = str(order.get("market_type") or "")
        try:
            repo.validate_native_order_authority(order)
        except ValueError:
            filled_gap_inconclusive += 1
            inconclusive += 1
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_authority_missing",
            )
            continue
        if _has_compatible_open_position(order):
            filled_gap_inconclusive += 1
            inconclusive += 1
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_compatible_open_position",
            )
            continue
        if market_type != "usdt_futures":
            filled_gap_inconclusive += 1
            inconclusive += 1
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_exact_history_unsupported",
            )
            continue
        try:
            if filled_gap_client is None:
                filled_gap_client = BitgetClient(mode="demo")
            rows = _response_rows(
                _run_coro(
                    filled_gap_client.get_order_fills(
                        order_id=str(order["exchange_order_id"]),
                        symbol=to_bitget(str(order["symbol"])),
                        market_type=market_type,
                    )
                )
            )
        except Exception as exc:
            filled_gap_inconclusive += 1
            inconclusive += 1
            errors.append("bitget_demo: historical exact fill history unavailable")
            logger.warning(
                "reconcile: historical exact fills failed for order %s (%s)",
                order["id"],
                type(exc).__name__,
            )
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_exact_history_unavailable",
            )
            continue
        try:
            evidence_complete = _complete_exact_gap_evidence(order, rows)
        except MaterializationIntegrityError:
            evidence_complete = False
        if not evidence_complete:
            filled_gap_inconclusive += 1
            inconclusive += 1
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_private_evidence_incomplete",
            )
            continue
        try:
            result = materialize_bitget_entry(
                order["id"],
                rows,
                historical_gap_recovery=True,
            )
        except (MaterializationIntegrityError, ValueError) as exc:
            filled_gap_inconclusive += 1
            inconclusive += 1
            errors.append("bitget_demo: historical local materialization rejected")
            logger.warning(
                "reconcile: historical materialization rejected for order %s (%s)",
                order["id"],
                type(exc).__name__,
            )
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_materialization_rejected",
            )
            continue
        except Exception as exc:
            filled_gap_inconclusive += 1
            inconclusive += 1
            errors.append("bitget_demo: historical local materialization failed")
            logger.warning(
                "reconcile: historical materialization failed for order %s (%s)",
                order["id"],
                type(exc).__name__,
            )
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_materialization_failed",
            )
            continue
        if result.state != "filled":
            filled_gap_inconclusive += 1
            inconclusive += 1
            _write_filled_gap_event(
                order,
                recovered=False,
                reason="filled_gap_complete_chain_not_proven",
            )
            continue
        filled_gap_recovered += 1
        filled += 1
        materialized_fills += result.new_fill_count
        _write_filled_gap_event(
            order,
            recovered=True,
            reason="filled_gap_private_evidence_materialized",
        )

    for execution_mode, account_mode in _BITGET_ACCOUNT_MODES.items():
        mode_orders = [
            order for order in bitget_orders
            if order.get("execution_mode") == execution_mode
        ]
        if not mode_orders:
            continue
        client = BitgetClient(mode=account_mode)
        markets = sorted({str(order.get("market_type") or "spot") for order in mode_orders})
        open_rows: dict[str, list[dict]] = {}
        fill_rows: dict[str, list[dict]] = {}
        failed_default_fill_markets: set[str] = set()
        symbol_fill_rows: dict[tuple[str, str], list[dict] | None] = {}

        for market_type in markets:
            try:
                open_rows[market_type] = _response_rows(
                    _run_coro(client.get_open_orders(market_type=market_type))
                )
            except Exception as exc:
                open_rows[market_type] = []
                errors.append(
                    f"{execution_mode}/{market_type}: open-order query unavailable"
                )
                logger.warning(
                    "reconcile: open-order query failed for %s/%s (%s)",
                    execution_mode,
                    market_type,
                    type(exc).__name__,
                )
            try:
                fill_rows[market_type] = _response_rows(
                    _run_coro(client.get_fills(market_type=market_type))
                )
            except Exception as exc:
                fill_rows[market_type] = []
                failed_default_fill_markets.add(market_type)
                logger.warning(
                    "reconcile: default private fills failed for %s/%s (%s)",
                    execution_mode,
                    market_type,
                    type(exc).__name__,
                )

        for order in mode_orders:
            market_type = str(order.get("market_type") or "spot")

            # A submission timeout can lose only the response, not the stable
            # clientOid. Recover the venue id from an open order before looking
            # at fills; private fill matching can recover it as well.
            if not order.get("exchange_order_id") and order.get("client_order_id"):
                remote_ids = {
                    str(row.get("orderId") or row.get("order_id"))
                    for row in open_rows.get(market_type, [])
                    if str(row.get("clientOid") or row.get("clientOrderId") or "")
                    == str(order["client_order_id"])
                    and (row.get("orderId") or row.get("order_id"))
                }
                if len(remote_ids) == 1:
                    recovered_id = next(iter(remote_ids))
                    update_order(order["id"], exchange_order_id=recovered_id)
                    order["exchange_order_id"] = recovered_id
                elif len(remote_ids) > 1:
                    inconclusive += 1
                    _write_reconcile_event(
                        order,
                        state=str(order.get("status") or "unresolved"),
                        reason="client_order_identity_ambiguous",
                    )
                    continue

            is_close_order = _is_close_order(order)
            use_exact_order_history = (
                order.get("source") == "native"
                and market_type == "usdt_futures"
                and bool(order.get("exchange_order_id"))
            )
            if use_exact_order_history:
                try:
                    rows = _response_rows(
                        _run_coro(
                            client.get_order_fills(
                                order_id=str(order["exchange_order_id"]),
                                symbol=to_bitget(str(order["symbol"])),
                                market_type=market_type,
                            )
                        )
                    )
                except Exception as exc:
                    inconclusive += 1
                    errors.append(
                        f"{execution_mode}/{market_type}: exact fill history unavailable"
                    )
                    logger.warning(
                        "reconcile: exact fills failed for %s order %s (%s)",
                        execution_mode,
                        order["id"],
                        type(exc).__name__,
                    )
                    _write_reconcile_event(
                        order,
                        state=str(order.get("status") or "unresolved"),
                        reason="exact_fill_history_unavailable",
                    )
                    continue
            else:
                rows = list(fill_rows.get(market_type, []))
            if not use_exact_order_history and _needs_symbol_scoped_history(order, rows):
                try:
                    exchange_symbol = to_bitget(str(order["symbol"]))
                except Exception:
                    exchange_symbol = ""
                cache_key = (market_type, exchange_symbol)
                if exchange_symbol and cache_key not in symbol_fill_rows:
                    try:
                        symbol_fill_rows[cache_key] = _response_rows(
                            _run_coro(
                                client.get_fills(
                                    symbol=exchange_symbol,
                                    market_type=market_type,
                                )
                            )
                        )
                    except Exception as exc:
                        symbol_fill_rows[cache_key] = None
                        logger.warning(
                            "reconcile: symbol private fills failed for %s/%s/%s (%s)",
                            execution_mode,
                            market_type,
                            exchange_symbol,
                            type(exc).__name__,
                        )
                scoped_rows = symbol_fill_rows.get(cache_key) if exchange_symbol else None
                if scoped_rows is None:
                    inconclusive += 1
                    errors.append(
                        f"{execution_mode}/{market_type}: private fill history unavailable"
                    )
                    _write_reconcile_event(
                        order,
                        state=str(order.get("status") or "unresolved"),
                        reason="private_fill_history_unavailable",
                    )
                    continue
                rows.extend(scoped_rows)
            elif (
                not use_exact_order_history
                and market_type in failed_default_fill_markets
            ):
                # Defensive: a failed default query cannot normally contain
                # evidence. Keep the explicit state if a future client returns a
                # partial payload alongside an exception.
                errors.append(
                    f"{execution_mode}/{market_type}: default private fill history unavailable"
                )
            if order.get("source") == "native":
                funding_ledger = None
                if is_close_order:
                    funding_request = funding_request_for_complete_close(
                        order["id"], rows
                    )
                    if funding_request is not None:
                        try:
                            funding_ledger = _run_coro(client.get_funding_ledger(
                                broker_id=execution_mode,
                                symbol=str(funding_request["position"]["symbol"]),
                                start_time_ms=int(funding_request["start_time_ms"]),
                                end_time_ms=int(funding_request["end_time_ms"]),
                            ))
                        except Exception as exc:
                            errors.append(
                                f"{execution_mode}/{market_type}: "
                                "private funding history unavailable"
                            )
                            logger.warning(
                                "reconcile: funding query failed for order %s (%s)",
                                order["id"],
                                type(exc).__name__,
                            )
                try:
                    result = (
                        materialize_bitget_close(
                            order["id"], rows, funding_ledger=funding_ledger
                        )
                        if is_close_order
                        else materialize_bitget_entry(order["id"], rows)
                    )
                except (MaterializationIntegrityError, ValueError) as exc:
                    inconclusive += 1
                    errors.append(
                        f"{execution_mode}/{market_type}: local materialization rejected"
                    )
                    logger.warning(
                        "reconcile: materialization rejected for order %s (%s)",
                        order["id"],
                        type(exc).__name__,
                    )
                    _write_reconcile_event(
                        order,
                        state=str(order.get("status") or "unresolved"),
                        reason="local_materialization_rejected",
                    )
                    continue
                except Exception as exc:
                    inconclusive += 1
                    errors.append(
                        f"{execution_mode}/{market_type}: local materialization failed"
                    )
                    logger.warning(
                        "reconcile: materialization failed for order %s (%s)",
                        order["id"],
                        type(exc).__name__,
                    )
                    try:
                        update_order(
                            order["id"],
                            status="materialization_missing",
                            error_reason="exchange_fill_local_materialization_missing",
                            filled_at=None,
                        )
                    except Exception:
                        pass
                    _write_reconcile_event(
                        order,
                        state="materialization_missing",
                        reason="local_materialization_failed",
                    )
                    continue

                materialized_fills += result.new_fill_count
                if result.state == "filled":
                    filled += 1
                    _write_reconcile_event(
                        order, state="filled", reason="private_fill_materialized"
                    )
                    continue
                if result.state == "partially_filled":
                    partially_filled += 1
                    _write_reconcile_event(
                        order,
                        state="partially_filled",
                        reason="partial_private_fill_materialized",
                    )
                    continue
                if result.state == "overfilled":
                    inconclusive += 1
                    _write_reconcile_event(
                        order,
                        state="overfilled",
                        reason="exchange_quantity_exceeds_local_authority",
                    )
                    continue

            # Historical/adopted Orders remain read-only: reconciliation may
            # converge their status only when both private quantity evidence and
            # a complete local Fill/Position chain already exist.
            quantities = _filled_quantity_by_order(rows)
            exchange_order_id = str(order.get("exchange_order_id") or "")
            reported_quantity = quantities.get(exchange_order_id, 0.0)
            requested_quantity = float(order.get("quantity") or 0.0)
            tolerance = max(1e-12, requested_quantity * 1e-9)
            complete = (
                requested_quantity > 0
                and abs(reported_quantity - requested_quantity) <= tolerance
            )
            if complete and _local_fill_chain_materialized(order):
                update_order(order["id"], status="filled")
                filled += 1
                _write_reconcile_event(
                    order, state="filled", reason="legacy_local_chain_verified"
                )
                continue

            remote_open = exchange_order_id and any(
                str(row.get("orderId") or row.get("order_id") or "")
                == exchange_order_id
                for row in open_rows.get(market_type, [])
            )
            if remote_open and reported_quantity <= 0:
                continue
            inconclusive += 1
            _write_reconcile_event(
                order,
                state=str(order.get("status") or "unresolved"),
                reason=(
                    "exchange_fill_seen_local_materialization_missing"
                    if complete
                    else "complete_exchange_fill_not_proven"
                ),
            )

    return {
        "checked": (
            len(bitget_orders)
            + len(multibroker_orders)
            + len(filled_gap_orders)
        ),
        "filled": filled,
        "partially_filled": partially_filled,
        "materialized_fills": materialized_fills,
        "inconclusive": inconclusive,
        "filled_gap_checked": len(filled_gap_orders),
        "filled_gap_recovered": filled_gap_recovered,
        "filled_gap_inconclusive": filled_gap_inconclusive,
        "pending_close_cleared": len(pending_close_cleared_ids),
        "pending_close_cleared_ids": pending_close_cleared_ids,
        "errors": errors,
        "unreconciled_live": 0,
    }

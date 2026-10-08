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
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import math
import time
from aletheia.async_utils import run_coro_from_sync
from aletheia.config.exchanges import execution_mode_environment, execution_mode_venue
from aletheia.data.symbols import to_bitget
from aletheia.portfolio.pnl import fee_in_quote
from aletheia.execution.broker_funding import make_complete_ledger
from aletheia.execution.errors import IncompleteReadError
from aletheia.execution.pending_close_recovery import (
    recover_absent_stale_close_orders,
)
from aletheia.execution.exchange_materialization import (
    MaterializationIntegrityError,
    durable_private_fill_id,
    evidence_for_order,
    funding_request_for_complete_close,
    materialize_bitget_close,
    materialize_bitget_entry,
)
from aletheia.infra.events import mark_resolved_from_durable, upsert_critical_unresolved
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.repositories import transition_order, update_order_metadata
from aletheia.storage.sqlite import get_db
from aletheia.files.jsonl import write_audit
from aletheia.uta.compat import instrument_from_local, scope_from_local
from aletheia.uta.identity import IdentityRelation, identity_relation
from aletheia.uta.manager import UTA_EXECUTION_MODES, build_uta_registry, query_uta
from aletheia.uta.models import (
    UTAAccountIdentity,
    FillQuery,
    FillReport,
    FundingLedgerQuery,
    OrderLifecycle,
    OrderQuery,
    ReadCompleteness,
)

logger = logging.getLogger("aletheia.reconciliation")

# Margin applied to an order's own creation time when building the window for a
# clientOid history read, so venue/process clock skew cannot push the window past
# the order.
_ORDER_LOOKUP_SKEW = timedelta(hours=1)


def _order_lookup_start(order: dict) -> datetime | None:
    """Window start for a clientOid order-history read.

    Derived from the order's own ``created_at``. ``None`` when the row carries no
    usable timestamp, in which case the adapter returns its structural PARTIAL
    rather than this code inventing a window.
    """
    created = order.get("created_at")
    if not created:
        return None
    try:
        parsed = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed - _ORDER_LOOKUP_SKEW


# Each execution mode selects its own account explicitly; using the current
# config mode could reconcile a live Order against a demo/testnet account after
# an operator configuration change. The identity registry supplies the venue
# partition; Bitget's native recovery path remains the only explicit exception.
_BITGET_ACCOUNT_MODES = {
    mode: execution_mode_environment(mode)
    for mode in UTA_EXECUTION_MODES
    if execution_mode_venue(mode) == "bitget"
    and execution_mode_environment(mode) in {"demo", "live"}
}
_MULTIBROKER_ACCOUNT_MODES = tuple(
    sorted(
        mode
        for mode in UTA_EXECUTION_MODES
        if execution_mode_venue(mode) not in {None, "bitget"}
    )
)
# ``partial`` is retained only for rows written by older builds.  Brokers now
# persist the exchange-normalized ``partially_filled`` spelling.
_OPEN_LOCAL_STATES = (
    "submitted", "pending", "partial", "partially_filled", "submission_unknown",
    "materialization_missing",
)


def _list_unresolved_core_orders() -> list[dict]:
    """Read every unresolved core Order; reconciliation must not truncate risk.

    Orders whose linked Position carries the terminally ambiguous
    ``manual_reconciliation_pending`` close state are excluded from the ordinary
    scan. That claim is human-owned, and polling it every pass would only keep
    writing ``order_reconciliation_inconclusive`` rows for a condition no
    machine action can resolve. The Order itself stays in an open local state,
    so a fill arriving after the bound is still materialized once an operator
    resolves the claim (PR4).
    """
    placeholders = ",".join("?" for _ in _OPEN_LOCAL_STATES)
    rows = get_db().execute(
        f"""
        SELECT orders.*
        FROM orders
        LEFT JOIN positions ON positions.id = orders.position_id
        WHERE orders.status IN ({placeholders})
          AND (
              positions.pending_close_status IS NULL
              OR positions.pending_close_status != 'manual_reconciliation_pending'
          )
        ORDER BY orders.created_at ASC, orders.rowid ASC
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
    """Require every exact-query report to carry complete matching economics."""
    reports = _response_rows(rows)
    exchange_order_id = str(order.get("exchange_order_id") or "").strip()
    if not exchange_order_id or not reports:
        return False
    if any(str(report.venue_order_id or "") != exchange_order_id for report in reports):
        return False
    matched_id, evidence = evidence_for_order(order, reports)
    if matched_id != exchange_order_id or len(evidence) != len(reports):
        return False
    requested = _finite_float(order.get("quantity"), positive=True)
    if requested is None:
        return False
    if any(
        not math.isfinite(item.price)
        or not math.isfinite(item.quantity)
        or item.fee is None
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
        quote_fee = fee_in_quote(
            fee=item_fee,
            fee_currency=item_fee_currency,
            fill_price=item_price,
            symbol=str(order.get("symbol") or ""),
        )
        if quote_fee is None:
            return False
        expected_remote_ids.append(remote_fill_id)
        expected_durable_ids.append(durable_fill_id)
        total_quantity += item_quantity
        total_notional += item_quantity * item_price
        total_fee_in_quote += quote_fee
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
            position = repo.get_position(position_id)
            if position is None:
                continue
            close_order_id = str(position.get("pending_close_order_id") or "")
            order = repo.get_order(close_order_id)
            fill_rows = [
                dict(fill)
                for fill in db.execute(
                    "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                    (close_order_id,),
                ).fetchall()
            ]
            if not _closed_pending_chain_complete(position, order, fill_rows):
                continue
            repo.release_position_close_claim(
                position_id, order_id=close_order_id
            )
            cleared = True
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
    """Return cumulative quantity from normalized private FillReports."""
    if isinstance(rows, FillReport):
        rows = [rows]
    if not isinstance(rows, (list, tuple)):
        return {}
    quantities: dict[str, float] = {}
    for report in rows:
        if not isinstance(report, FillReport) or not report.venue_order_id:
            continue
        try:
            quantity = float(report.quantity)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(quantity) or quantity <= 0:
            continue
        key = str(report.venue_order_id)
        quantities[key] = quantities.get(key, 0.0) + quantity
    return quantities
_CCXT_LIVE_MODES = tuple(
    sorted(
        mode
        for mode in _MULTIBROKER_ACCOUNT_MODES
        if execution_mode_environment(mode) == "live"
    )
)

def resolve_orders_missing_from_exchange(
    local_orders: list[dict],
    exchange_open_ids: set[str],
    modes: tuple[str, ...] = _CCXT_LIVE_MODES,
) -> list[str]:
    """Historical CR-EX-01 projection: identify orders absent from the open set.

    This result alone is not Fill evidence. Current reconciliation reads exact
    UTA Order/Fill reports and materializes proven private fills separately.
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


def _response_rows(rows) -> list[FillReport]:
    if isinstance(rows, FillReport):
        return [rows]
    if not isinstance(rows, (list, tuple)):
        return []
    if any(not isinstance(row, FillReport) for row in rows):
        return []
    return list(rows)


def _rows_reference_order(order: dict, rows) -> bool:
    """Return whether normalized private history names this order identity."""
    exchange_order_id = str(order.get("exchange_order_id") or "")
    client_order_id = str(order.get("client_order_id") or "")
    for report in _response_rows(rows):
        if exchange_order_id and str(report.venue_order_id or "") == exchange_order_id:
            return True
        if client_order_id and str(report.client_order_id or "") == client_order_id:
            return True
    return False


def _needs_symbol_scoped_history(order: dict, rows) -> bool:
    """Detect when the default typed Fill page cannot prove this Order."""
    return not _rows_reference_order(order, rows)


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


def _last_inconclusive_reason(order_id: str) -> str | None:
    row = get_db().execute(
        "SELECT after_state FROM audit_events WHERE entity_id=? "
        "AND event_type='order_reconciliation_inconclusive' ORDER BY rowid DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["after_state"] or "{}").get("reason")
    except (TypeError, ValueError):
        return None


def _write_reconcile_event(order: dict, *, state: str, reason: str) -> None:
    event_type = "order_reconciled" if state == "filled" else "order_reconciliation_inconclusive"
    # An unchanged inconclusive verdict is not a new fact: one row per change of
    # reason, not one per 2-minute pass (a stuck Order accrued ~2,500 identical rows).
    if event_type == "order_reconciliation_inconclusive" and _last_inconclusive_reason(order["id"]) == reason:
        return
    write_audit({
        "actor": "system",
        "source": "reconciliation",
        "event_type": event_type,
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
    """Persist one stable fact for each materially distinct filled-gap outcome."""
    event_type = (
        "order_reconciled"
        if recovered
        else "order_reconciliation_inconclusive"
    )
    identity = json.dumps(
        {
            "entity_id": order["id"],
            "event_type": event_type,
            "reason": reason,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    audit_id = f"filled-gap:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
    before = {"status": order.get("status")}
    after = {
        "status": order.get("status"),
        "chain_recovered": recovered,
        "reason": reason,
    }
    message = (
        "Recovered the historical filled Order's durable Fill/Position chain "
        "from exact complete private evidence."
        if recovered
        else "Historical filled Order chain recovery remained inconclusive; "
        "the existing Order status and local exposure were left unchanged."
    )
    event = AuditEvent(
        id=audit_id,
        actor="system",
        source="reconciliation",
        event_type=event_type,
        entity_type="order",
        entity_id=order["id"],
        before_state=json.dumps(before, sort_keys=True),
        after_state=json.dumps(after, sort_keys=True),
        message=message,
    )
    try:
        repo.insert_audit_event_once(event)
    except Exception:
        logger.warning(
            "filled-gap SQLite audit persistence failed for %s (%s)",
            order["id"],
            event_type,
            exc_info=True,
        )
    try:
        write_audit(
            {
                "id": event.id,
                "timestamp": event.timestamp,
                "actor": event.actor,
                "source": event.source,
                "event_type": event.event_type,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "reason": reason,
                "before": before,
                "after": after,
                "message": message,
            },
            mirror_db=False,
            dedup_id=audit_id,
        )
    except Exception:
        logger.warning(
            "filled-gap JSONL audit persistence failed for %s (%s)",
            order["id"],
            event_type,
            exc_info=True,
        )


# Per-(execution_mode, market_type) circuit-breaker state for private fill floods.
# ponytail: module-level dict; per-process singleton is correct for this scheduler
_fill_breaker: dict[tuple[str, str], dict] = {}
_FILL_BREAKER_THRESHOLD = 5
_FILL_BREAKER_COOLDOWN_S = 300.0


def _on_fill_unavailable(execution_mode: str, market_type: str) -> None:
    key = (execution_mode, market_type)
    state = _fill_breaker.setdefault(key, {"consecutive": 0})
    state["consecutive"] += 1
    if state["consecutive"] >= _FILL_BREAKER_THRESHOLD:
        state.setdefault("open_until", time.monotonic() + _FILL_BREAKER_COOLDOWN_S)
        upsert_critical_unresolved(
            "reconciliation",
            "exchange_reconciliation_stalled",
            f"{execution_mode}/{market_type}/get_fills",
            f"Private fill surface unavailable: {state['consecutive']} consecutive failures",
        )


def _on_fill_complete(execution_mode: str, market_type: str) -> None:
    key = (execution_mode, market_type)
    state = _fill_breaker.pop(key, None)
    if state and state.get("consecutive", 0) >= _FILL_BREAKER_THRESHOLD:
        mark_resolved_from_durable(
            "reconciliation",
            "exchange_reconciliation_stalled",
            f"{execution_mode}/{market_type}/get_fills",
        )


class _UTAReconciliationProjection:
    """Typed UTA projection for the existing atomic materializer."""

    def __init__(self, execution_mode: str, expected_account: UTAAccountIdentity) -> None:
        self.execution_mode = execution_mode
        self.expected_account = expected_account

    @staticmethod
    def _order_row(order) -> dict:
        return {
            "orderId": order.venue_order_id,
            "clientOid": order.client_order_id,
            "status": order.state.value,
            "symbol": order.instrument.canonical_symbol,
        }

    async def get_open_orders(self, *, market_type: str | None = None) -> list[dict]:
        report = await query_uta(
            self.execution_mode,
            "get_open_orders",
            OrderQuery(
                scope=scope_from_local(
                    "BTC/USDT", market_type or "usdt_futures"
                ).model_copy(update={"instrument": None})
            ),
            expected_account=self.expected_account,
        )
        if report.completeness is not ReadCompleteness.COMPLETE:
            raise IncompleteReadError(
                report.reason_code,
                "get_open_orders",
                self.execution_mode,
                market_type,
                completeness=report.completeness,
            )
        return [
            self._order_row(order)
            for order in report.orders
            if market_type is None or order.instrument.market_type == market_type
        ]

    async def get_fills(
        self,
        *,
        symbol: str | None = None,
        market_type: str = "usdt_futures",
        start_at=None,
    ) -> list[FillReport]:
        breaker = _fill_breaker.get((self.execution_mode, market_type), {})
        if breaker.get("consecutive", 0) >= _FILL_BREAKER_THRESHOLD:
            cooldown_until = breaker.get("open_until", 0.0)
            if time.monotonic() < cooldown_until:
                raise IncompleteReadError(
                    "exchange_reconciliation_stalled",
                    "get_fills",
                    self.execution_mode,
                    market_type,
                    symbol,
                    completeness=ReadCompleteness.UNAVAILABLE,
                )
            # Cooldown expired: allow one probe through and reset the timer.
            breaker.pop("open_until", None)

        scope = (
            scope_from_local(symbol, market_type, start_at=start_at)
            if symbol
            else scope_from_local(
                "BTC/USDT", market_type, start_at=start_at
            ).model_copy(update={"instrument": None})
        )
        report = await query_uta(
            self.execution_mode,
            "get_fills",
            FillQuery(scope=scope),
            expected_account=self.expected_account,
        )
        if report.completeness is not ReadCompleteness.COMPLETE:
            if report.completeness is ReadCompleteness.UNAVAILABLE:
                _on_fill_unavailable(self.execution_mode, market_type)
            raise IncompleteReadError(
                report.reason_code,
                "get_fills",
                self.execution_mode,
                market_type,
                symbol,
                completeness=report.completeness,
            )
        _on_fill_complete(self.execution_mode, market_type)
        return [
            fill for fill in report.fills
            if fill.instrument.market_type == market_type
        ]

    async def get_order_fills(
        self,
        *,
        order_id: str | None,
        symbol: str,
        market_type: str,
        client_order_id: str | None = None,
        start_at=None,
    ) -> list[FillReport]:
        report = await query_uta(
            self.execution_mode,
            "get_fills",
            FillQuery(
                scope=scope_from_local(symbol, market_type, start_at=start_at),
                venue_order_id=order_id,
                client_order_id=client_order_id,
            ),
            expected_account=self.expected_account,
        )
        if report.completeness is not ReadCompleteness.COMPLETE:
            raise IncompleteReadError(
                report.reason_code,
                "get_fills",
                self.execution_mode,
                market_type,
                symbol,
                completeness=report.completeness,
            )
        return list(report.fills)
    async def get_order_status(
        self,
        *,
        order_id: str | None,
        client_order_id: str | None,
        symbol: str,
        market_type: str,
        start_at=None,
    ):
        report = await query_uta(
            self.execution_mode,
            "get_order",
            OrderQuery(
                scope=scope_from_local(symbol, market_type, start_at=start_at),
                venue_order_id=order_id,
                client_order_id=client_order_id,
            ),
            expected_account=self.expected_account,
        )
        if report.completeness is not ReadCompleteness.COMPLETE:
            raise IncompleteReadError(
                report.reason_code,
                "get_order",
                self.execution_mode,
                market_type,
                symbol,
                completeness=report.completeness,
            )
        return report.found

    async def get_order_detail_by_client_oid(
        self, client_order_id: str, symbol: str, *, start_at=None
    ) -> dict | None:
        report = await query_uta(
            self.execution_mode,
            "get_order",
            OrderQuery(
                scope=scope_from_local(symbol, "usdt_futures", start_at=start_at),
                client_order_id=client_order_id,
            ),
            expected_account=self.expected_account,
        )
        if report.completeness is not ReadCompleteness.COMPLETE:
            raise IncompleteReadError(
                report.reason_code,
                "get_order",
                self.execution_mode,
                "usdt_futures",
                symbol,
                completeness=report.completeness,
            )
        return self._order_row(report.found) if report.found else None

    async def get_funding_ledger(
        self,
        *,
        broker_id: str,
        symbol: str,
        start_time_ms: int,
        end_time_ms: int,
        market_type: str = "usdt_futures",
    ) -> dict:
        start = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
        end = datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc)
        report = await query_uta(
            self.execution_mode,
            "get_funding_ledger",
            FundingLedgerQuery(
                scope=scope_from_local(
                    symbol, market_type, start_at=start, end_at=end
                )
            ),
            expected_account=self.expected_account,
        )
        if report.completeness is not ReadCompleteness.COMPLETE:
            raise IncompleteReadError(
                report.reason_code,
                "get_funding_ledger",
                self.execution_mode,
                market_type,
                symbol,
                completeness=report.completeness,
            )
        events = [
            {
                "event_id": entry.venue_ledger_id,
                "symbol": entry.instrument.canonical_symbol,
                "cash_change": float(entry.amount),
                "funding_cost": -float(entry.amount),
                "currency": entry.currency,
                "scheduled_at": entry.settlement_at.isoformat(),
                "occurred_at_ms": int(entry.settlement_at.timestamp() * 1000),
                "raw_type": entry.source_provenance,
                "direction": None,
            }
            for entry in report.entries
        ]
        return make_complete_ledger(
            broker_id=broker_id,
            account_environment=report.identity.environment,
            source_endpoint="uta.get_funding_ledger",
            history_start_ms=start_time_ms,
            history_end_ms=end_time_ms,
            events=events,
        )

    async def close(self) -> None:
        return None


def _fetch_multibroker_evidence(order: dict):
    """Read normalized UTA fills and adapt them to the existing materializer."""
    mode = str(order.get("execution_mode") or "")
    client = _UTAReconciliationProjection(mode, UTAAccountIdentity.model_validate_json(order["uta_account_identity_json"]))
    return client.get_order_fills(
        order_id=str(order.get("exchange_order_id") or "") or None,
        client_order_id=str(order.get("client_order_id") or "") or None,
        symbol=str(order["symbol"]),
        market_type=str(order.get("market_type") or "spot"),
        start_at=order.get("created_at"),
    )


async def _fetch_multibroker_funding(order: dict, request: dict) -> dict:
    """Read the exact normalized UTA funding ledger for a complete close."""
    return await _UTAReconciliationProjection(
        str(order.get("execution_mode") or ""),
        UTAAccountIdentity.model_validate_json(order["uta_account_identity_json"]),
    ).get_funding_ledger(
        broker_id=str(order.get("execution_mode") or ""),
        symbol=str(request["position"]["symbol"]),
        market_type=str(order.get("market_type") or "usdt_futures"),
        start_time_ms=int(request["start_time_ms"]),
        end_time_ms=int(request["end_time_ms"]),
    )


def _validate_reconcile_identities(
    candidate_orders, filled_gap_orders, configured_identities,
) -> tuple[list, list, list[str], int, int]:
    """Exclude orders whose persisted account cannot authorize private reads."""
    errors: list[str] = []
    inconclusive = 0
    filled_gap_inconclusive = 0
    for orders in (candidate_orders, filled_gap_orders):
        verified = []
        for order in orders:
            try:
                identity = UTAAccountIdentity.model_validate_json(order.get("uta_account_identity_json") or "")
                current = configured_identities.get(order["execution_mode"])
                if current is None or identity_relation(identity, current) is not IdentityRelation.SAME:
                    raise ValueError("uta_account_identity_mismatch")
            except ValueError:
                inconclusive += 1
                if orders is filled_gap_orders:
                    filled_gap_inconclusive += 1
                errors.append(f"{order['id']}:uta_account_identity_unavailable")
                _write_reconcile_event(order, state=str(order["status"]), reason="uta_account_identity_unavailable")
                continue
            verified.append(order)
        orders[:] = verified
    return candidate_orders, filled_gap_orders, errors, inconclusive, filled_gap_inconclusive


def _recover_filled_gap_orders(filled_gap_orders, configured) -> dict:
    """Recover historical Bitget demo gaps from exact private fill evidence."""
    errors: list[str] = []
    inconclusive = 0
    filled_gap_inconclusive = 0
    filled_gap_recovered = 0
    filled = 0
    materialized_fills = 0
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
                filled_gap_client = _UTAReconciliationProjection("bitget_demo", configured["bitget_demo"])
            rows = _response_rows(
                run_coro_from_sync(
                    filled_gap_client.get_order_fills(
                        order_id=str(order["exchange_order_id"]),
                        symbol=to_bitget(str(order["symbol"])),
                        market_type=market_type,
                        start_at=order.get("created_at"),
                    )
                )
            )
        except Exception as exc:
            filled_gap_inconclusive += 1
            inconclusive += 1
            errors.append("bitget_demo: historical exact fill history unavailable")
            logger.warning(
                "reconcile: historical exact fills failed for order %s (%s: %s)",
                order["id"],
                type(exc).__name__,
                getattr(exc, "reason_code", "no_reason_code"),
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
    return {
        "errors": errors,
        "inconclusive": inconclusive,
        "filled_gap_inconclusive": filled_gap_inconclusive,
        "filled_gap_recovered": filled_gap_recovered,
        "filled": filled,
        "materialized_fills": materialized_fills,
    }


def _reconcile_multibroker_orders(multibroker_orders) -> dict:
    """Materialize account-matched OKX and Binance entry and close evidence."""
    errors: list[str] = []
    inconclusive = 0
    filled = 0
    partially_filled = 0
    materialized_fills = 0
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
                run_coro_from_sync(_fetch_multibroker_evidence(order))
            )
        except Exception as exc:
            inconclusive += 1
            errors.append(
                f"{execution_mode}/{market_type}: private fill query unavailable"
            )
            logger.warning(
                "reconcile: private fill query failed for %s order %s (%s: %s)",
                execution_mode,
                order["id"],
                type(exc).__name__,
                getattr(exc, "reason_code", "no_reason_code"),
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
                    funding_ledger = run_coro_from_sync(
                        _fetch_multibroker_funding(order, funding_request)
                    )
                except Exception as exc:
                    errors.append(
                        f"{execution_mode}/{market_type}: private funding history unavailable"
                    )
                    logger.warning(
                        "reconcile: funding query failed for %s order %s (%s: %s)",
                        execution_mode,
                        order["id"],
                        type(exc).__name__,
                        getattr(exc, "reason_code", "no_reason_code"),
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
                transition_order(
                    order["id"],
                    status="materialization_missing",
                    error_reason="exchange_fill_local_materialization_missing",
                    filled_at=None,
                )
            except Exception as transition_exc:
                # `transition_order` failed — the database order status did
                # NOT reach `materialization_missing`. Do not emit an audit
                # event saying it did (that would split audit truth from
                # database truth). Surface the failure via `errors` and a
                # warning; the next order still gets processed.
                errors.append(
                    f"{order['id']}:materialization_missing_transition_failed"
                )
                logger.warning(
                    "reconcile: materialization_missing transition failed "
                    "for order %s (%s)",
                    order["id"],
                    type(transition_exc).__name__,
                )
            else:
                # Transition succeeded — the database is now at
                # `materialization_missing`. The audit event may be written.
                try:
                    _write_reconcile_event(
                        order,
                        state="materialization_missing",
                        reason="local_materialization_failed",
                    )
                except Exception as audit_exc:
                    # Committed order state is authoritative
                    # (execution_contract.md §7); an audit-sink failure
                    # after that must not roll it back or bubble up.
                    logger.warning(
                        "reconcile: materialization failure audit write failed "
                        "for order %s (%s)",
                        order["id"],
                        type(audit_exc).__name__,
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
    return {
        "errors": errors,
        "inconclusive": inconclusive,
        "filled": filled,
        "partially_filled": partially_filled,
        "materialized_fills": materialized_fills,
    }


def _prepare_bitget_private_views(execution_mode, bitget_orders, configured) -> dict | None:
    """Gather one mode's private views after persisted account validation."""
    errors: list[str] = []
    mode_orders = [
        order for order in bitget_orders
        if order.get("execution_mode") == execution_mode
    ]
    if not mode_orders:
        return None
    client = _UTAReconciliationProjection(execution_mode, configured[execution_mode])
    markets = sorted({str(order.get("market_type") or "spot") for order in mode_orders})
    open_rows: dict[str, list[dict]] = {}
    fill_rows: dict[str, list[FillReport]] = {}
    failed_default_fill_markets: set[str] = set()

    for market_type in markets:
        history_start = min(
            [
                str(order["created_at"])
                for order in mode_orders
                if str(order.get("market_type") or "spot") == market_type
                and order.get("created_at")
            ],
            default=None,
        )
        try:
            open_rows[market_type] = run_coro_from_sync(
                client.get_open_orders(market_type=market_type)
            )
        except Exception as exc:
            open_rows[market_type] = []
            errors.append(
                f"{execution_mode}/{market_type}: open-order query unavailable"
            )
            logger.warning(
                "reconcile: open-order query failed for %s/%s (%s: %s)",
                execution_mode,
                market_type,
                type(exc).__name__,
                getattr(exc, "reason_code", "no_reason_code"),
            )
        try:
            fill_rows[market_type] = _response_rows(
                run_coro_from_sync(client.get_fills(
                    market_type=market_type, start_at=history_start
                ))
            )
        except Exception as exc:
            fill_rows[market_type] = []
            if getattr(exc, "completeness", None) is ReadCompleteness.UNSUPPORTED:
                # Structural, not a failure: this venue cannot answer an account-wide
                # fill read at all (Bitget requires a symbol on every request variant,
                # #3564), so every pass would fail identically. Empty rows reference no
                # order, so each order is proven through its own symbol-scoped read
                # below — and a failure there still leaves it unresolved.
                continue
            failed_default_fill_markets.add(market_type)
            logger.warning(
                "reconcile: default private fills failed for %s/%s (%s: %s)",
                execution_mode,
                market_type,
                type(exc).__name__,
                getattr(exc, "reason_code", "no_reason_code"),
            )
    return {
        "mode_orders": mode_orders,
        "client": client,
        "open_rows": open_rows,
        "fill_rows": fill_rows,
        "failed_default_fill_markets": failed_default_fill_markets,
        "errors": errors,
    }


def _recover_exchange_order_id(
    order: dict, client: _UTAReconciliationProjection, open_rows: dict[str, list[dict]],
) -> tuple[str | None, bool, bool, bool]:
    """Recover and persist a missing venue ID, then derive history flags."""
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
            update_order_metadata(order["id"], exchange_order_id=recovered_id)
            order["exchange_order_id"] = recovered_id
        elif len(remote_ids) > 1:
            return None, True, False, False
        elif market_type == "usdt_futures":
            # Order may have already filled and left the open-order list.
            # Fall back to a direct detail lookup by clientOid (#1779).
            #
            # `start_at` is REQUIRED here: the venue profile declines an
            # account-wide history read with no window and returns a structural
            # PARTIAL (`private_order_history_start_required`, ccxt adapter:486),
            # which made this lookup return IncompleteReadError forever and left
            # the order machine-owned with no convergence path (#3564).
            try:
                from aletheia.data.symbols import to_bitget as _to_bitget
                detail = run_coro_from_sync(
                    client.get_order_detail_by_client_oid(
                        str(order["client_order_id"]),
                        _to_bitget(str(order["symbol"])),
                        start_at=_order_lookup_start(order),
                    )
                )
            except Exception as exc:
                logger.warning(
                    "reconcile: clientOid detail lookup (get_order) failed for order %s (%s: %s)",
                    order["id"],
                    type(exc).__name__,
                    getattr(exc, "reason_code", "no_reason_code"),
                )
                detail = None
            if detail and str(detail.get("clientOid") or "") == str(order["client_order_id"]):
                recovered_id = str(detail.get("orderId") or "").strip()
                if recovered_id:
                    update_order_metadata(order["id"], exchange_order_id=recovered_id)
                    order["exchange_order_id"] = recovered_id

    is_close_order = _is_close_order(order)
    use_exact_order_history = (
        order.get("source") == "native"
        and market_type == "usdt_futures"
        and bool(order.get("exchange_order_id"))
    )
    return order.get("exchange_order_id"), False, is_close_order, use_exact_order_history


def _fetch_order_history_and_ioc_state(
    order: dict,
    client: _UTAReconciliationProjection,
    use_exact_order_history: bool,
    fill_rows: dict[str, list[FillReport]],
    failed_default_fill_markets: set[str],
    symbol_fill_rows: dict[tuple[str, str], list[FillReport] | None],
    errors: list[str],
) -> tuple[list[FillReport], bool, float | None] | None:
    """Read private fill/IOC evidence; None means history was unavailable."""
    execution_mode = order["execution_mode"]
    market_type = str(order.get("market_type") or "spot")
    if use_exact_order_history:
        try:
            rows = _response_rows(
                run_coro_from_sync(
                        client.get_order_fills(
                            order_id=str(order["exchange_order_id"]),
                            symbol=to_bitget(str(order["symbol"])),
                            market_type=market_type,
                            start_at=order.get("created_at"),
                        )
                )
            )
        except Exception as exc:
            errors.append(
                f"{execution_mode}/{market_type}: exact fill history unavailable"
            )
            logger.warning(
                "reconcile: exact fills failed for %s order %s (%s: %s)",
                execution_mode,
                order["id"],
                type(exc).__name__,
                getattr(exc, "reason_code", "no_reason_code"),
            )
            _write_reconcile_event(
                order,
                state=str(order.get("status") or "unresolved"),
                reason="exact_fill_history_unavailable",
            )
            return None
    else:
        rows = list(fill_rows.get(market_type, []))
    if not use_exact_order_history and _needs_symbol_scoped_history(order, rows):
        # `to_bitget()` is pure string canonicalisation over `str`
        # (see `data/symbols.py`) — it cannot raise on a `str` input,
        # so the historical `except Exception: exchange_symbol = None`
        # was masking data-integrity issues rather than protecting a
        # real failure path. Other call sites (`:1211`, `:1416`) call
        # it directly with no guard, which is the correct pattern.
        exchange_symbol = to_bitget(str(order["symbol"]))
        cache_key = (market_type, exchange_symbol)
        if exchange_symbol and cache_key not in symbol_fill_rows:
            try:
                symbol_fill_rows[cache_key] = _response_rows(
                    run_coro_from_sync(
                        client.get_fills(
                            symbol=exchange_symbol,
                            market_type=market_type,
                            start_at=order.get("created_at"),
                        )
                    )
                )
            except Exception as exc:
                symbol_fill_rows[cache_key] = None
                logger.warning(
                    "reconcile: symbol private fills failed for %s/%s/%s (%s: %s)",
                    execution_mode,
                    market_type,
                    exchange_symbol,
                    type(exc).__name__,
                    getattr(exc, "reason_code", "no_reason_code"),
                )
        scoped_rows = symbol_fill_rows.get(cache_key) if exchange_symbol else None
        if scoped_rows is None:
            errors.append(
                f"{execution_mode}/{market_type}: private fill history unavailable"
            )
            _write_reconcile_event(
                order,
                state=str(order.get("status") or "unresolved"),
                reason="private_fill_history_unavailable",
            )
            return None
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
    ioc_terminal = False
    ioc_terminal_quantity: float | None = None
    if (
        order.get("source") == "native"
        and order.get("order_tactic") == "OPPOSING_BBO_IOC"
        and use_exact_order_history
    ):
        try:
            exact_order = run_coro_from_sync(client.get_order_status(
                order_id=str(order["exchange_order_id"]),
                client_order_id=(
                    str(order["client_order_id"])
                    if order.get("client_order_id")
                    else None
                ),
                symbol=str(order["symbol"]),
                market_type=market_type,
                start_at=order.get("created_at"),
            ))
            ioc_terminal = bool(
                exact_order
                and exact_order.state in {
                    OrderLifecycle.CANCELED,
                    OrderLifecycle.EXPIRED,
                    OrderLifecycle.REJECTED,
                }
            )
            if ioc_terminal and exact_order is not None:
                ioc_terminal_quantity = float(exact_order.filled_quantity)
        except Exception as exc:
            logger.warning(
                "reconcile: exact IOC status failed for order %s (%s: %s)",
                order["id"],
                type(exc).__name__,
                getattr(exc, "reason_code", "no_reason_code"),
            )
    return rows, ioc_terminal, ioc_terminal_quantity


def _converge_legacy_order_status(
    order: dict, rows: list[FillReport], open_rows: dict[str, list[dict]],
) -> tuple[int, int]:
    """Converge existing local chains; return filled and inconclusive counts."""
    market_type = str(order.get("market_type") or "spot")
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
        transition_order(order["id"], status="filled")
        _write_reconcile_event(
            order, state="filled", reason="legacy_local_chain_verified"
        )
        return 1, 0

    remote_open = exchange_order_id and any(
        str(row.get("orderId") or row.get("order_id") or "")
        == exchange_order_id
        for row in open_rows.get(market_type, [])
    )
    if remote_open and reported_quantity <= 0:
        return 0, 0
    _write_reconcile_event(
        order,
        state=str(order.get("status") or "unresolved"),
        reason=(
            "exchange_fill_seen_local_materialization_missing"
            if complete
            else "complete_exchange_fill_not_proven"
        ),
    )
    return 0, 1


def _materialize_native_order(
    order: dict,
    client: _UTAReconciliationProjection,
    rows: list[FillReport],
    is_close_order: bool,
    ioc_terminal: bool,
    ioc_terminal_quantity: float | None,
    errors: list[str],
) -> tuple[str | None, int]:
    """Return the summary category and new-fill count; None needs chain checks."""
    execution_mode = order["execution_mode"]
    market_type = str(order.get("market_type") or "spot")
    funding_ledger = None
    if is_close_order:
        funding_request = funding_request_for_complete_close(
            order["id"], rows
        )
        if funding_request is not None:
            try:
                funding_ledger = run_coro_from_sync(client.get_funding_ledger(
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
                    "reconcile: funding query failed for order %s (%s: %s)",
                    order["id"],
                    type(exc).__name__,
                    getattr(exc, "reason_code", "no_reason_code"),
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
        return "inconclusive", 0
    except Exception as exc:
        errors.append(
            f"{execution_mode}/{market_type}: local materialization failed"
        )
        logger.warning(
            "reconcile: materialization failed for order %s (%s)",
            order["id"],
            type(exc).__name__,
        )
        try:
            transition_order(
                order["id"],
                status="materialization_missing",
                error_reason="exchange_fill_local_materialization_missing",
                filled_at=None,
            )
        except Exception as transition_exc:
            # See sibling block above (~line 1130): database
            # state did not reach `materialization_missing`;
            # do not emit an audit event that would claim it
            # did. Surface via `errors` + warning.
            errors.append(
                f"{order['id']}:materialization_missing_transition_failed"
            )
            logger.warning(
                "reconcile: materialization_missing transition failed "
                "for order %s (%s)",
                order["id"],
                type(transition_exc).__name__,
            )
        else:
            try:
                _write_reconcile_event(
                    order,
                    state="materialization_missing",
                    reason="local_materialization_failed",
                )
            except Exception as audit_exc:
                logger.warning(
                    "reconcile: materialization failure audit write failed "
                    "for order %s (%s)",
                    order["id"],
                    type(audit_exc).__name__,
                )
        return "inconclusive", 0

    materialized_fills = result.new_fill_count
    if ioc_terminal_quantity is not None:
        local_fill_row = get_db().execute(
            "SELECT COALESCE(SUM(fill_quantity), 0) AS quantity "
            "FROM fills WHERE order_id=?",
            (order["id"],),
        ).fetchone()
        local_quantity = float(local_fill_row["quantity"] or 0.0)
        tolerance = max(1e-12, float(order["quantity"]) * 1e-9)
        ioc_terminal = abs(local_quantity - ioc_terminal_quantity) <= tolerance
    if result.state == "filled":
        _write_reconcile_event(
            order, state="filled", reason="private_fill_materialized"
        )
        return "filled", materialized_fills
    if result.state == "partially_filled":
        if ioc_terminal:
            transition_order(
                order["id"],
                status="cancelled",
                error_reason="ioc_remainder_terminal",
            )
            _write_reconcile_event(
                order,
                state="cancelled",
                reason="ioc_partial_fill_remainder_terminal",
            )
            return "cancelled", materialized_fills
        _write_reconcile_event(
            order,
            state="partially_filled",
            reason="partial_private_fill_materialized",
        )
        return "partially_filled", materialized_fills
    if result.state == "evidence_missing" and ioc_terminal:
        transition_order(
            order["id"],
            status="cancelled",
            error_reason="ioc_zero_fill_terminal",
        )
        _write_reconcile_event(
            order,
            state="cancelled",
            reason="ioc_zero_fill_terminal",
        )
        return "cancelled", materialized_fills
    if result.state == "overfilled":
        _write_reconcile_event(
            order,
            state="overfilled",
            reason="exchange_quantity_exceeds_local_authority",
        )
        return "inconclusive", materialized_fills
    return None, materialized_fills


def reconcile() -> dict:
    """Reconcile unresolved exchange Orders and materialize proven fills.

    Also the single production entry to the conservative absent-close recovery
    (``pending_close_recovery.recover_absent_stale_close_orders``): an open
    Position whose close Order is proven absent, unfilled and terminal on the
    venue gets its stale claim released so the protective exit may retry.
    """
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
    checked = len(bitget_orders) + len(multibroker_orders) + len(filled_gap_orders)
    filled_gap_checked = len(filled_gap_orders)
    configured = {identity.execution_mode: identity for identity in build_uta_registry().configured_identities()}
    # An Order stored under an older credential generation can only be read once its
    # identity is re-bound, and re-binding needs a venue read: do that first, so the
    # check below sees the bound identity instead of skipping the Order every pass.
    from aletheia.execution.coordinator import reanchor_drifted_orders

    unresolved = bitget_orders + multibroker_orders
    for execution_mode in sorted({str(o.get("execution_mode") or "") for o in unresolved} & set(configured)):
        reanchor_drifted_orders(
            execution_mode, [o for o in unresolved if o.get("execution_mode") == execution_mode],
        )
    (
        candidate_orders, filled_gap_orders, identity_errors,
        identity_inconclusive, identity_gap_inconclusive,
    ) = _validate_reconcile_identities(
        bitget_orders + multibroker_orders, filled_gap_orders, configured,
    )
    errors.extend(identity_errors)
    inconclusive += identity_inconclusive
    filled_gap_inconclusive += identity_gap_inconclusive
    bitget_orders = [
        order for order in candidate_orders
        if order.get("execution_mode") in _BITGET_ACCOUNT_MODES
    ]
    multibroker_orders = [
        order for order in candidate_orders
        if order.get("execution_mode") in _MULTIBROKER_ACCOUNT_MODES
    ]
    multibroker_result = _reconcile_multibroker_orders(multibroker_orders)
    errors.extend(multibroker_result["errors"])
    inconclusive += multibroker_result["inconclusive"]
    filled += multibroker_result["filled"]
    partially_filled += multibroker_result["partially_filled"]
    materialized_fills += multibroker_result["materialized_fills"]
    gap_result = _recover_filled_gap_orders(filled_gap_orders, configured)
    errors.extend(gap_result["errors"])
    inconclusive += gap_result["inconclusive"]
    filled_gap_inconclusive += gap_result["filled_gap_inconclusive"]
    filled_gap_recovered += gap_result["filled_gap_recovered"]
    filled += gap_result["filled"]
    materialized_fills += gap_result["materialized_fills"]

    for execution_mode in _BITGET_ACCOUNT_MODES:
        views = _prepare_bitget_private_views(execution_mode, bitget_orders, configured)
        if views is None:
            continue
        mode_orders = views["mode_orders"]
        client = views["client"]
        open_rows = views["open_rows"]
        fill_rows = views["fill_rows"]
        failed_default_fill_markets = views["failed_default_fill_markets"]
        errors.extend(views["errors"])
        symbol_fill_rows: dict[tuple[str, str], list[FillReport] | None] = {}

        for order in mode_orders:
            _, ambiguous, is_close_order, use_exact_order_history = (
                _recover_exchange_order_id(order, client, open_rows)
            )
            if ambiguous:
                inconclusive += 1
                _write_reconcile_event(
                    order,
                    state=str(order.get("status") or "unresolved"),
                    reason="client_order_identity_ambiguous",
                )
                continue
            history = _fetch_order_history_and_ioc_state(
                order, client, use_exact_order_history, fill_rows,
                failed_default_fill_markets, symbol_fill_rows, errors,
            )
            if history is None:
                inconclusive += 1
                continue
            rows, ioc_terminal, ioc_terminal_quantity = history
            if order.get("source") == "native":
                native_state, new_fill_count = _materialize_native_order(
                    order, client, rows, is_close_order,
                    ioc_terminal, ioc_terminal_quantity, errors,
                )
                materialized_fills += new_fill_count
                filled += native_state == "filled"
                partially_filled += native_state == "partially_filled"
                inconclusive += native_state == "inconclusive"
                if native_state is not None:
                    continue

            legacy_filled, legacy_inconclusive = _converge_legacy_order_status(
                order, rows, open_rows,
            )
            filled += legacy_filled
            inconclusive += legacy_inconclusive

    # The single production entry to the conservative absent-close recovery. It
    # re-proves the whole evidence set itself (complete open-order view, exact
    # empty fill history, typed order history, unchanged local claim) and runs
    # last so a fill materialized above is already local and its zero-local-fill
    # guard skips the claim. This call site grants the existing path a cadence;
    # it does not relax the decision.
    close_recovery = recover_absent_stale_close_orders()
    errors.extend(
        f"pending_close_recovery:{reason}" for reason in close_recovery["errors"]
    )

    return {
        "checked": checked,
        "filled": filled,
        "partially_filled": partially_filled,
        "materialized_fills": materialized_fills,
        "inconclusive": inconclusive,
        "filled_gap_checked": filled_gap_checked,
        "filled_gap_recovered": filled_gap_recovered,
        "filled_gap_inconclusive": filled_gap_inconclusive,
        "pending_close_cleared": len(pending_close_cleared_ids),
        "pending_close_cleared_ids": pending_close_cleared_ids,
        "pending_close_released": close_recovery["released"],
        "pending_close_released_ids": close_recovery["released_position_ids"],
        "pending_close_parked": close_recovery["parked"],
        "errors": errors,
        "unreconciled_live": 0,
    }

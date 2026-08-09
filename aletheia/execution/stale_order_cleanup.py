"""Stale orphan-order cleanup (bookkeeping GC for abandoned exchange orders).

``reconciliation.py`` only *converges* an unresolved Order when it has direct
private exchange evidence; it never cancels one.  That is the correct trust
posture, but it means an Order that can never be proven — a demo limit order left
by a previous environment, an account whose credentials are no longer configured,
a venue that dropped the order silently — sits in an open-local state forever and
makes ``reconciliation`` report the same "private fill query unavailable" error
every cycle (the ``job_consecutive_failure:reconciliation`` alert).

This module is the missing lifecycle end: after an Order has been unresolved long
enough, verify against the exchange whether it is still open, and if it is
provably gone — or cannot be verified at all after a hard age cutoff — mark it
``cancelled`` with a full audit trail.  It is deliberately conservative:

  * It never touches an Order that carries real economics (any local Fill, a
    linked open Position, or an in-flight close for a still-open Position) — those
    belong exclusively to ``reconciliation``'s evidence path.
  * Following ``position_reconciler``'s rule, "cannot see the exchange" is NEVER
    treated as "the exchange has no such order".  A failed/absent-credential venue
    query only lets the conservative aged-out fallback act, never the evidence
    path.

It lives outside ``reconciliation.py`` (a change-controlled trust-boundary file)
and is wired into the daily ``_run_cleanup`` scheduler job.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging

from aletheia.data.symbols import to_bitget
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.sqlite import get_db
from aletheia.files.jsonl import write_audit

logger = logging.getLogger("aletheia.stale_order_cleanup")

# Kept in lockstep with reconciliation._OPEN_LOCAL_STATES: the states a genuinely
# unresolved Order can sit in.  A local copy avoids importing a private name from
# the trust-boundary module.
_OPEN_LOCAL_STATES = (
    "submitted", "pending", "partial", "partially_filled", "submission_unknown",
    "materialization_missing",
)

# An Order younger than the soft window is still normal in-flight work.  Past it,
# we may verify against the exchange and cancel a *provably* absent Order.  Only
# past the hard window will we cancel an Order we cannot verify at all (no
# credentials / venue unreachable).  Hardcoded like the other _run_cleanup
# thresholds (prune_*(7/30/90)); no config field, so no Config↔UI obligation.
# 24h soft window: enough to confirm order lifecycle on exchange (normal orders
# fill or cancel within minutes). 72h hard window: accommodates weekends/holidays.
_STALE_ORDER_SOFT_HOURS = 24
_STALE_ORDER_HARD_HOURS = 72  # 3 days

# Venues with a clean open-order reader we can use for evidence-based cancels.
_BITGET_ACCOUNT_MODES = {"bitget_live_limited": "live", "bitget_demo": "demo"}


def _run_coro(coro):
    """Run an async broker call from this sync job without blocking the event loop."""
    from aletheia.agent_runtime.async_utils import run_coro_from_sync
    return run_coro_from_sync(coro)


def _age_hours(created_at) -> float | None:
    text = str(created_at or "").strip()
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
    delta = datetime.now(timezone.utc) - parsed
    return delta.total_seconds() / 3600.0


def _has_real_economics(order: dict) -> bool:
    """True when the Order carries value reconciliation alone may resolve.

    Any local Fill, a linked open Position (entry backlink or position_id), or an
    in-flight close for a still-open Position means real economics could exist —
    cancelling would fabricate/lose state, so this cleanup must not touch it.
    """
    db = get_db()
    oid = order["id"]
    filled = db.execute(
        "SELECT COALESCE(SUM(fill_quantity), 0) AS q FROM fills WHERE order_id=?",
        (oid,),
    ).fetchone()
    if float(filled["q"] or 0.0) > 0:
        return True

    entry_pos = db.execute(
        "SELECT status FROM positions WHERE order_id=? LIMIT 1", (oid,)
    ).fetchone()
    if entry_pos is not None and entry_pos["status"] == "open":
        return True

    position_id = order.get("position_id")
    if position_id:
        linked = db.execute(
            "SELECT status FROM positions WHERE id=? LIMIT 1", (position_id,)
        ).fetchone()
        if linked is not None and linked["status"] == "open":
            return True

    # An in-flight close for a still-open Position belongs to reconciliation /
    # tpsl_fill_check, never here.  A close marker on an ALREADY-closed Position is
    # the stale-pending case handled explicitly below, so it is not "economics".
    pending_close = db.execute(
        "SELECT status FROM positions WHERE pending_close_order_id=? LIMIT 1",
        (oid,),
    ).fetchone()
    if pending_close is not None and pending_close["status"] == "open":
        return True
    return False


def _stale_pending_close_position_id(order: dict) -> str | None:
    """Return the id of an already-CLOSED Position still naming this close Order.

    reconciliation._clear_verified_closed_pending only clears bitget_demo; an
    okx_demo (or any non-bitget) close Order that finished after its Position was
    already closed leaves pending_close_* set forever.
    """
    row = get_db().execute(
        "SELECT id FROM positions "
        "WHERE pending_close_order_id=? AND status='closed' LIMIT 1",
        (order["id"],),
    ).fetchone()
    return str(row["id"]) if row is not None else None


def _exchange_open_order_ids(order: dict) -> set[str] | None:
    """Best-effort set of the venue's currently-open order identities.

    Returns None when we cannot obtain an authoritative open-order view (no clean
    reader for this venue, missing credentials, or a failed query).  Per
    position_reconciler's rule, None must NOT be read as "the order is gone".
    """
    execution_mode = str(order.get("execution_mode") or "")
    account_mode = _BITGET_ACCOUNT_MODES.get(execution_mode)
    if account_mode is None:
        return None  # no clean open-order reader (e.g. okx) → aged-out only
    market_type = str(order.get("market_type") or "spot")
    try:
        from aletheia.exchanges.bitget.client import BitgetClient

        client = BitgetClient(mode=account_mode)
        rows = _run_coro(client.get_open_orders(market_type=market_type))
    except Exception as exc:
        logger.warning(
            "stale_order_cleanup: open-order query unavailable for %s/%s (%s)",
            execution_mode, market_type, type(exc).__name__,
        )
        return None
    if isinstance(rows, dict):
        rows = rows.get("orderList") or rows.get("list") or []
    if not isinstance(rows, list):
        return None
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("orderId", "order_id", "clientOid", "clientOrderId"):
            value = str(row.get(key) or "").strip()
            if value:
                ids.add(value)
    return ids


def _order_present(order: dict, open_ids: set[str]) -> bool:
    return any(
        str(order.get(key) or "").strip() in open_ids
        for key in ("exchange_order_id", "client_order_id")
        if str(order.get(key) or "").strip()
    )


def _decide(order: dict) -> tuple[str, str] | None:
    """Return (reason_code, kind) for an actionable Order, else None to skip.

    kind is "cancel" for a plain orphan entry or "clear_pending_close" for a
    stale close Order on an already-closed Position.
    """
    age = _age_hours(order.get("created_at"))
    if age is None or age < _STALE_ORDER_SOFT_HOURS:
        return None
    if _has_real_economics(order):
        return None

    # Stale close Order on an already-closed Position: safe to converge once old,
    # regardless of venue reachability — the Position is already terminal.
    if _stale_pending_close_position_id(order) is not None:
        return ("stale_close_order_position_already_closed", "clear_pending_close")

    open_ids = _exchange_open_order_ids(order)
    if open_ids is not None:
        if _order_present(order, open_ids):
            return None  # exchange still holds it open — never cancel
        return ("stale_order_absent_on_exchange", "cancel")

    # Cannot verify against the venue (no reader / credentials / unreachable).
    # Only the conservative hard-age fallback may act.
    if age >= _STALE_ORDER_HARD_HOURS:
        return ("stale_order_unverifiable_aged_out", "cancel")
    return None


def _apply(order: dict, reason_code: str, kind: str) -> str | None:
    """Compare-and-write the cancel under the SQLite writer slot.

    Re-reads and re-verifies every guard inside BEGIN IMMEDIATE so a fill/close
    reconciliation materialized between _decide() and here can never be clobbered.
    Returns the audit payload dict via a closure list, or None if nothing changed.
    """
    oid = order["id"]
    cleared_position_id: str | None = None
    changed = False
    with repo.transaction(immediate=True):
        db = get_db()
        row = db.execute(
            "SELECT * FROM orders WHERE id=? LIMIT 1", (oid,)
        ).fetchone()
        if row is None:
            return None
        fresh = dict(row)
        if fresh.get("status") not in _OPEN_LOCAL_STATES:
            return None  # already resolved by another path
        if _has_real_economics(fresh):
            return None  # a fill/position appeared under the lock

        if kind == "clear_pending_close":
            position_id = _stale_pending_close_position_id(fresh)
            if position_id is None:
                return None  # no longer a stale close carrier
            cursor = db.execute(
                "UPDATE positions "
                "SET pending_close_order_id=NULL, pending_close_status=NULL, "
                "    pending_close_requested_at=NULL "
                "WHERE id=? AND status='closed' AND pending_close_order_id=?",
                (position_id, oid),
            )
            if cursor.rowcount == 1:
                cleared_position_id = position_id

        # The network verification for a plain cancel already happened in _decide
        # (outside any lock); we deliberately do not re-query the venue while
        # holding the writer slot. The local guards re-checked above are enough.
        db.execute(
            "UPDATE orders SET status='cancelled', error_reason=? "
            "WHERE id=? AND status=?",
            (reason_code, oid, fresh["status"]),
        )
        before = {"status": fresh["status"]}
        if cleared_position_id:
            before["pending_close_position_id"] = cleared_position_id
        after = {"status": "cancelled", "error_reason": reason_code}
        repo.insert_audit_event(AuditEvent(
            actor="system",
            source="stale_order_cleanup",
            event_type="order_stale_cancelled",
            entity_type="order",
            entity_id=oid,
            before_state=json.dumps(before, sort_keys=True),
            after_state=json.dumps(after, sort_keys=True),
            message=(
                f"Cancelled unresolved {fresh.get('execution_mode')} order after "
                f"{reason_code}; no local Fill or open Position depended on it."
                + (
                    f" Cleared stale pending-close marker on closed position "
                    f"{cleared_position_id}."
                    if cleared_position_id else ""
                )
            ),
        ))
        changed = True

    if not changed:
        return None
    # DB state + DB audit committed atomically above. A secondary JSONL sink
    # outage must not make the job re-cancel a completed action (mirrors
    # reconciliation._clear_verified_closed_pending). mirror_db=False: the DB
    # audit_events row already exists from insert_audit_event inside the tx, so
    # this only appends the hash-chained JSONL record (no duplicate DB row).
    try:
        write_audit({
            "actor": "system",
            "source": "stale_order_cleanup",
            "event_type": "order_stale_cancelled",
            "entity_type": "order",
            "entity_id": oid,
            "reason": reason_code,
            "before": {"status": order.get("status")},
            "after": {"status": "cancelled", "error_reason": reason_code},
            "cleared_pending_close_position_id": cleared_position_id,
            "message": (
                "Stale unresolved order cancelled by bookkeeping cleanup; no "
                "local Fill or open Position depended on it."
            ),
        }, mirror_db=False)
    except Exception as exc:
        logger.error(
            "stale_order_cleanup JSONL audit failed for %s (%s)",
            oid, type(exc).__name__,
        )
    return reason_code


def cleanup_stale_orphan_orders() -> dict:
    """Cancel abandoned unresolved Orders that reconciliation can never resolve.

    Safe to run repeatedly (idempotent: only acts on still-open-local Orders past
    the age thresholds, and re-verifies every guard under the writer slot).
    """
    placeholders = ",".join("?" for _ in _OPEN_LOCAL_STATES)
    rows = get_db().execute(
        f"SELECT * FROM orders WHERE status IN ({placeholders}) "
        f"ORDER BY created_at ASC, rowid ASC",
        _OPEN_LOCAL_STATES,
    ).fetchall()

    cancelled: list[str] = []
    cleared_pending: list[str] = []
    skipped = 0
    errors: list[str] = []

    for row in rows:
        order = dict(row)
        try:
            decision = _decide(order)
        except Exception as exc:
            errors.append(f"decide_failed:{order.get('id')}")
            logger.warning(
                "stale_order_cleanup: decide failed for order %s (%s)",
                order.get("id"), type(exc).__name__,
            )
            continue
        if decision is None:
            skipped += 1
            continue
        reason_code, kind = decision
        try:
            applied = _apply(order, reason_code, kind)
        except Exception as exc:
            errors.append(f"apply_failed:{order.get('id')}")
            logger.warning(
                "stale_order_cleanup: apply failed for order %s (%s)",
                order.get("id"), type(exc).__name__,
            )
            continue
        if applied is None:
            skipped += 1
            continue
        cancelled.append(order["id"])
        if kind == "clear_pending_close":
            cleared_pending.append(order["id"])

    if cancelled:
        logger.info(
            "stale_order_cleanup: cancelled=%d (cleared_pending=%d) skipped=%d errors=%d",
            len(cancelled), len(cleared_pending), skipped, len(errors),
        )
    return {
        "cancelled": len(cancelled),
        "cancelled_ids": cancelled,
        "cleared_pending": len(cleared_pending),
        "skipped": skipped,
        "errors": errors,
    }

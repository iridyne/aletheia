"""Stale orphan-order cleanup (bookkeeping GC for abandoned exchange orders).

``reconciliation.py`` only *converges* an unresolved Order when it has direct
private exchange evidence; it never cancels one.  That is the correct trust
posture, but it means an Order that can never be proven — a demo limit order left
by a previous environment, an account whose credentials are no longer configured,
a venue that dropped the order silently — sits in an open-local state forever and
makes ``reconciliation`` report the same "private fill query unavailable" error
every cycle (the ``job_consecutive_failure:reconciliation`` alert).

This module is the missing lifecycle end: after an Order has been unresolved long
enough, verify exact order, Fill and Position evidence and mark it ``cancelled``
only when complete evidence proves absence. It is deliberately conservative:

  * It never touches an Order that carries real economics (any local Fill, a
    linked open Position, or an in-flight close for a still-open Position) — those
    belong exclusively to ``reconciliation``'s evidence path.
  * Following ``position_reconciler``'s rule, "cannot see the exchange" is NEVER
    treated as "the exchange has no such order".

It lives outside ``reconciliation.py`` (a change-controlled trust-boundary file)
and is wired into the daily ``_run_cleanup`` scheduler job.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import logging

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
# incomplete or unavailable evidence remains unresolved regardless of age.
_STALE_ORDER_SOFT_HOURS = 48

_UTA_ACCOUNT_MODES = frozenset({
    "bitget_live_limited", "bitget_demo", "okx_live_limited", "okx_demo",
    "binance_live_limited", "binance_testnet",
})


def _run_coro(coro):
    """Run an async broker call from this sync job (mirrors reconciliation)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


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


def _exchange_order_absent_unfilled(order: dict) -> bool | None:
    """Return exact complete UTA absence, never open-list inference."""
    execution_mode = str(order.get("execution_mode") or "")
    if execution_mode not in _UTA_ACCOUNT_MODES:
        return None
    try:
        from aletheia.uta.compat import scope_from_local
        from aletheia.uta.manager import query_uta
        from aletheia.uta.models import ReadCompleteness, ReconciliationQuery

        client_id = str(order.get("client_order_id") or "").strip()
        venue_id = str(order.get("exchange_order_id") or "").strip()
        report = _run_coro(query_uta(
            execution_mode,
            "get_mass_status",
            ReconciliationQuery(
                scope=scope_from_local(
                    str(order["symbol"]),
                    str(order.get("market_type") or "spot"),
                    start_at=order.get("created_at"),
                ),
                client_order_ids=(client_id,) if client_id else (),
                venue_order_ids=(venue_id,) if venue_id else (),
            ),
        ))
    except Exception as exc:
        logger.warning(
            "stale_order_cleanup: exact UTA evidence unavailable for %s (%s)",
            execution_mode, type(exc).__name__,
        )
        return None
    if report.completeness is not ReadCompleteness.COMPLETE:
        return None
    if report.fills:
        return False
    return not any(item.state.value in {"accepted", "open", "partially_filled", "filled"} for item in report.orders)


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

    absent_unfilled = _exchange_order_absent_unfilled(order)
    if absent_unfilled is True:
        return ("stale_order_absent_on_exchange", "cancel")
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

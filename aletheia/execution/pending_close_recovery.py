"""Recover stale pending-close claims that reconciliation cannot converge.

``reconciliation`` materializes close Orders only with exact private fill
evidence. When a close acknowledgement is lost (order gone from the exchange
open-book but no fill row indexed yet), the Position keeps
``pending_close_status`` in an open-close state and ``position_manager`` will
not resubmit — a deadlock.

``release_stale_submission_rejected_claims`` clears aged
``submission_rejected`` markers so protective exits can retry after an explicit
exchange rejection.

Both paths are conservative:
  * never act when local Fill economics exist;
  * for Bitget modes, require an authoritative open-order view showing the
    order absent AND empty exact fill history before releasing a submitted claim;
  * that confirmed-absent path uses ``ABSENT_UNFILLED_RELEASE_HOURS`` (ack-lag
    floor), not the 4h ``STALE_PENDING_CLOSE_RELEASE_HOURS`` used for
    ``submission_rejected`` without a recoverable cancel and for
    ``position_manager`` escalation. Waiting 4h after the exchange already
    shows no order and no fill leaves protective exits deadlocked.
  * UTA ``trade_too_many_times_cancel`` is not a lost ack. Park the claim as
    ``venue_trade_rate_limited`` (never auto-released). Releasing it would
    resubmit into Bitget 40725. Do not overwrite ``pending_close_requested_at``
    with a cooldown end time — that field is request time only; cooldown belongs
    in operator recovery notes or a future ``pending_close_retry_after`` column.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging

from aletheia.utils.dt import parse_utc
from aletheia.config.exchanges import EXTERNAL_EXECUTION_MODES
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent
from aletheia.storage.sqlite import get_db
from aletheia.files.jsonl import write_audit
from aletheia.uta.models import UTAAccountIdentity
logger = logging.getLogger("aletheia.pending_close_recovery")

# Lockstep with position_manager escalation threshold.
# Cleanup-cancelled closes that never filled must retry with close-{id}-rN;
# otherwise submission_rejected holds the claim until the 4h age-out.
RECOVERABLE_CLOSE_CANCEL_REASONS = frozenset({
    "stale_close_order_absent_unfilled",
    "stale_order_absent_on_exchange",
    "operator_authorized_cleanup_trade_rate_limited",
})

# Exchange-native UTA cancel. Not recoverable: retrying place-order close
# immediately re-hits Bitget 40725 (trade_too_many_times_cancel).
TRADE_RATE_LIMIT_CANCEL_REASON = "trade_too_many_times_cancel"

# Owner-visible park for Bitget 40725. Distinct from ordinary submission_rejected
# (which may age out after STALE_PENDING_CLOSE_RELEASE_HOURS).
VENUE_TRADE_RATE_LIMITED_STATUS = "venue_trade_rate_limited"

_HISTORY_FAILED = object()


def is_recoverable_cancelled_close(order: dict) -> bool:
    return (
        str(order.get("status") or "") == "cancelled"
        and str(order.get("error_reason") or "") in RECOVERABLE_CLOSE_CANCEL_REASONS
    )


def is_durable_close_client_oid(position_id: str, client_order_id: str | None) -> bool:
    """True for close-{id} or close-{id}-rN retry identities after recoverable cancel."""
    base = f"close-{position_id}"
    oid = str(client_order_id or "")
    if oid == base:
        return True
    prefix = f"{base}-r"
    if not oid.startswith(prefix):
        return False
    suffix = oid[len(prefix):]
    return suffix.isdigit() and int(suffix) >= 1


def next_close_client_oid(position_id: str) -> str:
    """Allocate a Bitget clientOid for a close retry after recoverable cancellation."""
    base = f"close-{position_id}"
    row = get_db().execute(
        "SELECT client_order_id FROM orders WHERE position_id=? "
        "AND close_reason IS NOT NULL ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    if row is None:
        return base
    latest_oid = str(row["client_order_id"] or base)
    if latest_oid == base:
        return f"{base}-r1"
    if latest_oid.startswith(f"{base}-r"):
        try:
            generation = int(latest_oid.rsplit("-r", 1)[-1])
        except ValueError:
            generation = 1
        return f"{base}-r{generation + 1}"
    return f"{base}-r1"


# Lockstep with position_manager escalation (log-only; does not release a
# still-submitted claim). Also the floor for submission_rejected without a
# recoverable cancel reason.
STALE_PENDING_CLOSE_RELEASE_HOURS = 4.0

# Confirmed absent + unfilled close claims. Long enough that a just-submitted
# market close can appear on Bitget's open-order view; short enough that a
# lost acknowledgement cannot block take-profit for hours.
ABSENT_UNFILLED_RELEASE_HOURS = 0.25

_RECOVERABLE_ORDER_STATES = (
    "submitted",
    "pending",
    "submission_unknown",
    "materialization_missing",
)

# Every external execution identity is backed by the UTA account boundary.
_UTA_ACCOUNT_MODES = EXTERNAL_EXECUTION_MODES


def _run_coro(coro):
    from aletheia.async_utils import run_coro_from_sync

    return run_coro_from_sync(coro)




def _age_hours(*timestamps: str | None) -> float | None:
    candidates = [parse_utc(value) for value in timestamps if value]
    candidates = [value for value in candidates if value is not None]
    if not candidates:
        return None
    anchor = min(candidates)
    delta = datetime.now(timezone.utc) - anchor
    return delta.total_seconds() / 3600.0


def _response_rows(rows) -> list[dict]:
    if isinstance(rows, dict):
        rows = rows.get("fillList") or rows.get("list") or []
    return [row for row in rows] if isinstance(rows, list) else []


def _is_close_order(order: dict) -> bool:
    if order.get("close_reason"):
        return True
    client_order_id = str(order.get("client_order_id") or "")
    return client_order_id.startswith("close-")


def _local_fill_quantity(order_id: str) -> float:
    row = get_db().execute(
        "SELECT COALESCE(SUM(fill_quantity), 0) AS quantity FROM fills WHERE order_id=?",
        (order_id,),
    ).fetchone()
    return float(row["quantity"] or 0.0)


def _exchange_open_order_ids(order: dict) -> set[str] | None:
    execution_mode = str(order.get("execution_mode") or "")
    if execution_mode not in _UTA_ACCOUNT_MODES:
        return None
    try:
        from aletheia.uta.compat import scope_from_local
        from aletheia.uta.manager import query_uta
        from aletheia.uta.models import OrderQuery, ReadCompleteness

        report = _run_coro(query_uta(
            execution_mode,
            "get_open_orders",
            OrderQuery(scope=scope_from_local(
                str(order["symbol"]), str(order.get("market_type") or "spot")
            )),
            expected_account=UTAAccountIdentity.model_validate_json(order.get("uta_account_identity_json") or ""),
        ))
    except Exception as exc:
        logger.warning(
            "pending_close_recovery: open-order query unavailable for %s/%s (%s)",
            execution_mode,
            order.get("market_type"),
            type(exc).__name__,
        )
        return None
    if report.completeness is not ReadCompleteness.COMPLETE:
        return None
    ids: set[str] = set()
    for row in report.orders:
        for value in (row.venue_order_id, row.client_order_id):
            value = str(value or "").strip()
            if value:
                ids.add(value)
    return ids


def _order_present(order: dict, open_ids: set[str]) -> bool:
    return any(
        str(order.get(key) or "").strip() in open_ids
        for key in ("exchange_order_id", "client_order_id")
        if str(order.get(key) or "").strip()
    )


def _exchange_fill_rows(order: dict) -> list[dict] | None:
    execution_mode = str(order.get("execution_mode") or "")
    exchange_order_id = str(order.get("exchange_order_id") or "").strip()
    client_order_id = str(order.get("client_order_id") or "").strip()
    if execution_mode not in _UTA_ACCOUNT_MODES or not (
        exchange_order_id or client_order_id
    ):
        return None
    try:
        from aletheia.uta.compat import scope_from_local
        from aletheia.uta.manager import query_uta
        from aletheia.uta.models import FillQuery, ReadCompleteness

        report = _run_coro(query_uta(
            execution_mode,
            "get_fills",
            FillQuery(
                scope=scope_from_local(
                    str(order["symbol"]),
                    str(order.get("market_type") or "spot"),
                    start_at=order.get("created_at"),
                ),
                venue_order_id=exchange_order_id or None,
                client_order_id=client_order_id or None,
            ),
            expected_account=UTAAccountIdentity.model_validate_json(order.get("uta_account_identity_json") or ""),
        ))
        if report.completeness is not ReadCompleteness.COMPLETE:
            return None
        return [row.model_dump(mode="json") for row in report.fills]
    except Exception as exc:
        logger.warning(
            "pending_close_recovery: exact fill query unavailable for %s (%s)",
            order.get("id"),
            type(exc).__name__,
        )
        return None


def _uta_history_lookup(order: dict):
    """Exact UTA order report; partial/unavailable evidence fails closed."""
    execution_mode = str(order.get("execution_mode") or "")
    exchange_order_id = str(order.get("exchange_order_id") or "").strip()
    client_order_id = str(order.get("client_order_id") or "").strip()
    if execution_mode not in _UTA_ACCOUNT_MODES or not (exchange_order_id or client_order_id):
        return _HISTORY_FAILED
    try:
        from aletheia.uta.compat import scope_from_local
        from aletheia.uta.manager import query_uta
        from aletheia.uta.models import OrderQuery, ReadCompleteness

        report = _run_coro(query_uta(
            execution_mode,
            "get_order",
            OrderQuery(
                scope=scope_from_local(
                    str(order["symbol"]),
                    str(order.get("market_type") or "spot"),
                    start_at=order.get("created_at"),
                ),
                venue_order_id=exchange_order_id or None,
                client_order_id=client_order_id or None,
            ),
            expected_account=UTAAccountIdentity.model_validate_json(order.get("uta_account_identity_json") or ""),
        ))
        if report.completeness is not ReadCompleteness.COMPLETE:
            return _HISTORY_FAILED
        if report.found is None:
            return None
        return {
            "orderStatus": report.found.state.value,
            "status": report.found.state.value,
            "cancelReason": report.reason_code or "",
        }
    except Exception as exc:
        logger.warning(
            "pending_close_recovery: UTA history unavailable for %s (%s)",
            order.get("id"),
            type(exc).__name__,
        )
        return _HISTORY_FAILED


def _eligible_absent_close_recovery(order: dict, position: dict) -> str | None:
    if not _is_close_order(order):
        return None
    if str(order.get("status") or "") not in _RECOVERABLE_ORDER_STATES:
        return None
    if str(position.get("status") or "") != "open":
        return None
    if str(position.get("pending_close_order_id") or "") != str(order.get("id") or ""):
        return None
    if _local_fill_quantity(str(order["id"])) > 0:
        return None

    open_ids = _exchange_open_order_ids(order)
    if open_ids is None:
        return None
    if _order_present(order, open_ids):
        return None

    fill_rows = _exchange_fill_rows(order)
    if fill_rows is None:
        return None
    if fill_rows:
        return None

    history = _uta_history_lookup(order)
    if history is _HISTORY_FAILED:
        return None
    if isinstance(history, dict):
        status = str(history.get("orderStatus") or history.get("status") or "").lower()
        cancel_reason = str(history.get("cancelReason") or "")
        if status not in {"canceled", "cancelled", "expired", "rejected"}:
            return None
        if cancel_reason == TRADE_RATE_LIMIT_CANCEL_REASON:
            return TRADE_RATE_LIMIT_CANCEL_REASON

    age_hours = _age_hours(
        position.get("pending_close_requested_at"),
        order.get("created_at"),
    )
    if age_hours is None or age_hours < ABSENT_UNFILLED_RELEASE_HOURS:
        return None
    return "stale_close_order_absent_unfilled"


def _recovery_claim_is_unchanged(
    expected_order: dict,
    expected_position: dict,
    fresh_order: dict,
    fresh_position: dict,
) -> bool:
    """CAS the local claim after remote evidence was collected.

    The remote evidence is intentionally not re-queried here: a SQLite writer
    lock cannot make separate exchange requests atomic. Compare every local
    field used to identify the evidence target and require the claim/fill state
    to remain unchanged before applying the already-decided reason.
    """
    order_fields = (
        "id",
        "position_id",
        "status",
        "client_order_id",
        "exchange_order_id",
        "execution_mode",
        "market_type",
        "symbol",
        "close_reason",
        "error_reason",
        "created_at",
    )
    position_fields = (
        "id",
        "status",
        "pending_close_order_id",
        "pending_close_status",
        "pending_close_requested_at",
    )
    if any(
        str(expected_order.get(field) or "") != str(fresh_order.get(field) or "")
        for field in order_fields
    ):
        return False
    if any(
        str(expected_position.get(field) or "")
        != str(fresh_position.get(field) or "")
        for field in position_fields
    ):
        return False
    return _local_fill_quantity(str(fresh_order["id"])) == 0


def _apply_absent_close_recovery(order: dict, position: dict, reason_code: str) -> bool:
    order_id = str(order["id"])
    position_id = str(position["id"])
    with repo.transaction(immediate=True):
        db = get_db()
        fresh_order = db.execute(
            "SELECT * FROM orders WHERE id=? LIMIT 1", (order_id,)
        ).fetchone()
        fresh_position = db.execute(
            "SELECT * FROM positions WHERE id=? LIMIT 1", (position_id,)
        ).fetchone()
        if fresh_order is None or fresh_position is None:
            return False
        order_row = dict(fresh_order)
        position_row = dict(fresh_position)
        if not _recovery_claim_is_unchanged(
            order, position, order_row, position_row
        ):
            return False
        if reason_code == TRADE_RATE_LIMIT_CANCEL_REASON:
            raise RuntimeError("pending_close_recovery: rate-limited close must be parked, not released")

        repo.transition_order(order_id, status="cancelled", error_reason=reason_code)
        repo.release_position_close_claim(position_id, order_id=order_id)

        repo.insert_audit_event(
            AuditEvent(
                actor="system",
                source="pending_close_recovery",
                event_type="stale_pending_close_released",
                entity_type="position",
                entity_id=position_id,
                before_state=json.dumps(
                    {
                        "pending_close_order_id": order_id,
                        "pending_close_status": position_row.get("pending_close_status"),
                        "order_status": order_row.get("status"),
                    },
                    sort_keys=True,
                ),
                after_state=json.dumps(
                    {
                        "pending_close_order_id": None,
                        "pending_close_status": None,
                        "order_status": "cancelled",
                        "reason": reason_code,
                    },
                    sort_keys=True,
                ),
                message=(
                    f"Released stale pending-close claim on open position after "
                    f"{reason_code}; close order absent on exchange with no fill evidence."
                ),
            )
        )
    try:
        write_audit(
            {
                "actor": "system",
                "source": "pending_close_recovery",
                "event_type": "stale_pending_close_released",
                "entity_type": "position",
                "entity_id": position_id,
                "reason": reason_code,
                "order_id": order_id,
            },
            mirror_db=False,
        )
    except Exception as exc:
        logger.warning(
            "pending_close_recovery JSONL audit failed for %s (%s)",
            position_id,
            type(exc).__name__,
        )
    return True


def _apply_park_rate_limited_close(order: dict, position: dict) -> bool:
    """Keep the pending-close claim; mark the order cancelled for 40725."""
    order_id = str(order["id"])
    position_id = str(position["id"])
    with repo.transaction(immediate=True):
        db = get_db()
        fresh_order = db.execute(
            "SELECT * FROM orders WHERE id=? LIMIT 1", (order_id,)
        ).fetchone()
        fresh_position = db.execute(
            "SELECT * FROM positions WHERE id=? LIMIT 1", (position_id,)
        ).fetchone()
        if fresh_order is None or fresh_position is None:
            return False
        order_row = dict(fresh_order)
        position_row = dict(fresh_position)
        if not _recovery_claim_is_unchanged(
            order, position, order_row, position_row
        ):
            return False

        repo.transition_order(
            order_id,
            status="cancelled",
            error_reason=TRADE_RATE_LIMIT_CANCEL_REASON,
        )
        repo.update_position_close_claim(
            position_id,
            order_id=order_id,
            status=VENUE_TRADE_RATE_LIMITED_STATUS,
        )

        repo.insert_audit_event(
            AuditEvent(
                actor="system",
                source="pending_close_recovery",
                event_type="pending_close_parked_trade_rate_limited",
                entity_type="position",
                entity_id=position_id,
                before_state=json.dumps(
                    {
                        "pending_close_order_id": order_id,
                        "pending_close_status": position_row.get("pending_close_status"),
                        "order_status": order_row.get("status"),
                    },
                    sort_keys=True,
                ),
                after_state=json.dumps(
                    {
                        "pending_close_order_id": order_id,
                        "pending_close_status": VENUE_TRADE_RATE_LIMITED_STATUS,
                        "order_status": "cancelled",
                        "reason": TRADE_RATE_LIMIT_CANCEL_REASON,
                    },
                    sort_keys=True,
                ),
                message=(
                    "Parked pending-close claim after Bitget trade_too_many_times_cancel; "
                    f"status={VENUE_TRADE_RATE_LIMITED_STATUS} until owner-authorized "
                    "recovery — never auto-retry into 40725."
                ),
            )
        )
    try:
        write_audit(
            {
                "actor": "system",
                "source": "pending_close_recovery",
                "event_type": "pending_close_parked_trade_rate_limited",
                "entity_type": "position",
                "entity_id": position_id,
                "reason": TRADE_RATE_LIMIT_CANCEL_REASON,
                "order_id": order_id,
            },
            mirror_db=False,
        )
    except Exception as exc:
        logger.warning(
            "pending_close_recovery JSONL audit failed for %s (%s)",
            position_id,
            type(exc).__name__,
        )
    return True


def release_rate_limited_close_claim(
    position_id: str,
    *,
    actor: str,
    reason: str,
) -> dict:
    """Owner-authorized release of a venue_trade_rate_limited parked claim.

    The park exists because an automatic retry would resubmit into Bitget 40725
    (trade_too_many_times_cancel). This is the owner-authorized unlock the
    park's audit message promises: it validates that the claim is exactly the
    parked state, converts the cancelled close Order's reason to the reserved
    ``operator_authorized_cleanup_trade_rate_limited``, and clears the
    pending-close claim so protective exits retry with a fresh
    ``close-{position_id}-rN`` client order id. Never touches fills or
    economics; refusing states leave every row unchanged.
    """
    if not str(reason or "").strip():
        return {"released": False, "code": "reason_required"}
    order_id = None
    with repo.transaction(immediate=True):
        db = get_db()
        fresh_position = db.execute(
            "SELECT * FROM positions WHERE id=? LIMIT 1", (position_id,)
        ).fetchone()
        if fresh_position is None or str(fresh_position["status"] or "") != "open":
            return {"released": False, "code": "position_not_found_or_not_open"}
        claim_status = str(fresh_position["pending_close_status"] or "")
        order_id = str(fresh_position["pending_close_order_id"] or "")
        if not order_id:
            return {"released": False, "code": "claim_not_parked"}
        fresh_order = db.execute(
            "SELECT * FROM orders WHERE id=? LIMIT 1", (order_id,)
        ).fetchone()
        if fresh_order is None or not _is_close_order(dict(fresh_order)):
            return {"released": False, "code": "parked_order_mismatch"}
        if str(fresh_order["position_id"] or "") != position_id:
            return {"released": False, "code": "parked_order_mismatch"}
        if claim_status == VENUE_TRADE_RATE_LIMITED_STATUS:
            if (
                str(fresh_order["status"] or "") != "cancelled"
                or str(fresh_order["error_reason"] or "")
                != TRADE_RATE_LIMIT_CANCEL_REASON
            ):
                return {"released": False, "code": "parked_order_mismatch"}
        elif claim_status == "submission_rejected":
            # A close the venue rejected at submission and that provably never
            # reached the exchange (no exchange order id, terminal error) has
            # no in-flight ambiguity — the same safety property as the 40725
            # park. The default 4h age-out also covers it; this is the
            # owner-authorized early unlock for a fixed root cause.
            if (
                str(fresh_order["status"] or "") != "error"
                or str(fresh_order["exchange_order_id"] or "").strip() != ""
            ):
                return {"released": False, "code": "parked_order_mismatch"}
        else:
            return {"released": False, "code": "claim_not_parked"}

        next_oid = next_close_client_oid(position_id)
        db.execute(
            "UPDATE orders SET error_reason=? "
            "WHERE id=? AND status='cancelled' AND error_reason=?",
            ("operator_authorized_cleanup_trade_rate_limited",
             order_id, TRADE_RATE_LIMIT_CANCEL_REASON),
        )
        repo.release_position_close_claim(position_id, order_id=order_id)
        repo.insert_audit_event(
            AuditEvent(
                actor=str(actor or "human"),
                source="pending_close_recovery",
                event_type="pending_close_owner_authorized_release",
                entity_type="position",
                entity_id=position_id,
                before_state=json.dumps(
                    {
                        "pending_close_order_id": order_id,
                        "pending_close_status": VENUE_TRADE_RATE_LIMITED_STATUS,
                        "order_status": "cancelled",
                        "order_error_reason": TRADE_RATE_LIMIT_CANCEL_REASON,
                    },
                    sort_keys=True,
                ),
                after_state=json.dumps(
                    {
                        "pending_close_order_id": None,
                        "pending_close_status": None,
                        "order_status": "cancelled",
                        "order_error_reason": "operator_authorized_cleanup_trade_rate_limited",
                        "retry_client_order_id": next_oid,
                        "reason": str(reason),
                    },
                    sort_keys=True,
                ),
                message=(
                    f"Owner-authorized release of Bitget 40725-parked close claim "
                    f"({order_id}); protective exits may retry as {next_oid}. "
                    f"Reason: {reason}"
                ),
            )
        )
    try:
        write_audit(
            {
                "actor": str(actor or "human"),
                "source": "pending_close_recovery",
                "event_type": "pending_close_owner_authorized_release",
                "entity_type": "position",
                "entity_id": position_id,
                "order_id": order_id,
                "reason": str(reason),
            },
            mirror_db=False,
        )
    except Exception as exc:
        logger.warning(
            "pending_close_recovery JSONL audit failed for %s (%s)",
            position_id,
            type(exc).__name__,
        )
    return {
        "released": True,
        "position_id": position_id,
        "order_id": order_id,
        "retry_client_order_id": next_oid,
    }


def recover_absent_stale_close_orders() -> dict:
    """Release submitted close claims confirmed absent and unfilled on the exchange."""
    rows = get_db().execute(
        """
        SELECT o.*, p.id AS linked_position_id,
               p.pending_close_requested_at AS position_pending_close_requested_at,
               p.pending_close_status AS position_pending_close_status
        FROM orders o
        JOIN positions p ON p.pending_close_order_id = o.id
        WHERE p.status = 'open'
          AND o.status IN (?, ?, ?, ?)
        ORDER BY o.created_at ASC, o.rowid ASC
        """,
        _RECOVERABLE_ORDER_STATES,
    ).fetchall()

    released: list[str] = []
    parked: list[str] = []
    skipped = 0
    errors: list[str] = []

    for row in rows:
        order = dict(row)
        position = {
            "id": order.pop("linked_position_id"),
            "status": "open",
            "pending_close_order_id": order["id"],
            "pending_close_requested_at": order.pop(
                "position_pending_close_requested_at", None
            ),
            "pending_close_status": order.pop("position_pending_close_status", None),
        }
        try:
            reason = _eligible_absent_close_recovery(order, position)
        except Exception as exc:
            errors.append(f"decide_failed:{order.get('id')}")
            logger.warning(
                "pending_close_recovery: decide failed for order %s (%s)",
                order.get("id"),
                type(exc).__name__,
            )
            continue
        if reason is None:
            skipped += 1
            continue
        try:
            if reason == TRADE_RATE_LIMIT_CANCEL_REASON:
                applied = _apply_park_rate_limited_close(order, position)
            else:
                applied = _apply_absent_close_recovery(order, position, reason)
        except Exception as exc:
            errors.append(f"apply_failed:{order.get('id')}")
            logger.warning(
                "pending_close_recovery: apply failed for order %s (%s)",
                order.get("id"),
                type(exc).__name__,
            )
            continue
        if applied and reason == TRADE_RATE_LIMIT_CANCEL_REASON:
            parked.append(str(position["id"]))
        elif applied:
            released.append(str(position["id"]))
        else:
            skipped += 1

    if released or parked:
        logger.info(
            "pending_close_recovery: released=%d parked=%d skipped=%d errors=%d",
            len(released),
            len(parked),
            skipped,
            len(errors),
        )
    return {
        "released": len(released),
        "released_position_ids": released,
        "parked": len(parked),
        "parked_position_ids": parked,
        "skipped": skipped,
        "errors": errors,
    }


def _eligible_submission_rejected_release(position: dict) -> bool:
    if str(position.get("status") or "") != "open":
        return False
    status = str(position.get("pending_close_status") or "")
    # venue_trade_rate_limited is never auto-released; legacy 40725 rows may
    # still sit on submission_rejected until position_manager normalizes them.
    if status == VENUE_TRADE_RATE_LIMITED_STATUS:
        return False
    if status != "submission_rejected":
        return False
    order_id = position.get("pending_close_order_id")
    if order_id:
        order = repo.get_order(str(order_id))
        if order and str(order.get("error_reason") or "") == TRADE_RATE_LIMIT_CANCEL_REASON:
            # Bitget 40725 is a symbol/account restriction, not a lost ack.
            # 4h and 90m retries both re-hit trade_too_many_times_cancel.
            return False
        if order and is_recoverable_cancelled_close(order):
            return True
    age_hours = _age_hours(position.get("pending_close_requested_at"))
    if age_hours is None or age_hours < 0 or age_hours < STALE_PENDING_CLOSE_RELEASE_HOURS:
        return False
    return True


def _apply_submission_rejected_release(position: dict) -> bool:
    position_id = str(position["id"])
    with repo.transaction(immediate=True):
        db = get_db()
        fresh = db.execute(
            "SELECT * FROM positions WHERE id=? LIMIT 1", (position_id,)
        ).fetchone()
        if fresh is None:
            return False
        position_row = dict(fresh)
        if not _eligible_submission_rejected_release(position_row):
            return False
        before = {
            "pending_close_order_id": position_row.get("pending_close_order_id"),
            "pending_close_status": position_row.get("pending_close_status"),
        }
        order_id = str(position_row.get("pending_close_order_id") or "")
        if not order_id:
            return False
        repo.release_position_close_claim(position_id, order_id=order_id)
        repo.insert_audit_event(
            AuditEvent(
                actor="system",
                source="pending_close_recovery",
                event_type="submission_rejected_claim_released",
                entity_type="position",
                entity_id=position_id,
                before_state=json.dumps(before, sort_keys=True),
                after_state=json.dumps(
                    {
                        "pending_close_order_id": None,
                        "pending_close_status": None,
                        "reason": "stale_submission_rejected_released",
                    },
                    sort_keys=True,
                ),
                message=(
                    "Released aged submission_rejected pending-close claim so "
                    "protective exits may retry."
                ),
            )
        )
    return True


def release_stale_submission_rejected_claims() -> dict:
    """Clear aged submission_rejected markers on still-open positions."""
    rows = get_db().execute(
        """
        SELECT *
        FROM positions
        WHERE status='open'
          AND pending_close_status='submission_rejected'
        ORDER BY pending_close_requested_at ASC, rowid ASC
        """
    ).fetchall()

    released: list[str] = []
    skipped = 0
    errors: list[str] = []

    for row in rows:
        position = dict(row)
        try:
            if not _eligible_submission_rejected_release(position):
                skipped += 1
                continue
            if _apply_submission_rejected_release(position):
                released.append(str(position["id"]))
            else:
                skipped += 1
        except Exception as exc:
            errors.append(f"release_failed:{position.get('id')}")
            logger.warning(
                "pending_close_recovery: submission_rejected release failed for %s (%s)",
                position.get("id"),
                type(exc).__name__,
            )

    if released:
        logger.info(
            "pending_close_recovery: submission_rejected released=%d",
            len(released),
        )
    return {
        "released": len(released),
        "released_position_ids": released,
        "skipped": skipped,
        "errors": errors,
    }

"""Offline repair for the bounded Paper close Order/Fill timestamp race."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any


COMMAND_VERSION = "paper-close-timestamp-recovery-v1"
_MAX_SKEW = timedelta(milliseconds=1)


def _parse_utc(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _unavailable(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {**base, "action": "unavailable", "reason": reason}


def _plan_state(db, state: dict[str, Any]) -> dict[str, Any]:
    from aletheia.storage import repositories as repo

    base = {
        "position_id": state["position_id"],
        "trigger_id": state["trigger_id"],
        "close_order_id": state["close_order_id"],
    }
    trigger_row = db.execute(
        "SELECT * FROM position_protection_triggers "
        "WHERE position_id=? AND trigger_id=?",
        (state["position_id"], state["trigger_id"]),
    ).fetchone()
    position_row = db.execute(
        "SELECT * FROM positions WHERE id=?",
        (state["position_id"],),
    ).fetchone()
    order_row = db.execute(
        "SELECT * FROM orders WHERE id=?",
        (state["close_order_id"],),
    ).fetchone()
    if trigger_row is None or position_row is None or order_row is None:
        return _unavailable(base, "missing_protection_close_lineage")
    trigger, position, order = map(dict, (trigger_row, position_row, order_row))
    fills = [dict(row) for row in db.execute(
        "SELECT * FROM fills WHERE order_id=? ORDER BY rowid ASC",
        (order["id"],),
    ).fetchall()]
    if (
        position.get("status") != "closed"
        or position.get("execution_mode") != "paper_internal"
        or position.get("source") != "native"
        or order.get("execution_mode") != "paper_internal"
        or order.get("source") != "native"
        or order.get("status") != "filled"
        or len(fills) != 1
    ):
        return _unavailable(base, "non_exact_native_paper_close")
    try:
        repo._validate_protection_close_order(
            state=state,
            trigger=trigger,
            position=position,
            order=order,
        )
    except repo.PositionProtectionConflict as exc:
        return _unavailable(base, f"close_lineage_invalid:{exc}")

    fill = fills[0]
    created_at = _parse_utc(order.get("created_at"))
    order_filled_at = _parse_utc(order.get("filled_at"))
    fill_filled_at = _parse_utc(fill.get("filled_at"))
    detected_at = _parse_utc(trigger.get("detected_at"))
    if None in (created_at, order_filled_at, fill_filled_at, detected_at):
        return _unavailable(base, "invalid_close_timestamp")
    if order_filled_at != fill_filled_at:
        return _unavailable(base, "order_fill_timestamp_mismatch")
    if fill_filled_at < detected_at:
        return _unavailable(base, "fill_predates_protection_trigger")
    skew = created_at - fill_filled_at
    if skew <= timedelta(0):
        return {**base, "action": "already_consistent", "reason": None}
    if skew > _MAX_SKEW:
        return _unavailable(base, "order_fill_skew_exceeds_1ms")
    return {
        **base,
        "fill_id": fill["id"],
        "action": "repair",
        "reason": "legacy_paper_close_constructor_race",
        "skew_microseconds": round(skew.total_seconds() * 1_000_000),
        "before": {"created_at": order["created_at"]},
        "after": {"created_at": fill["filled_at"]},
    }


def inspect_paper_close_timestamp_recovery() -> dict[str, Any]:
    """Return a read-only, fingerprinted repair plan."""
    from aletheia.storage.sqlite import get_db

    db = get_db()
    states = [dict(row) for row in db.execute(
        "SELECT * FROM position_protection_state "
        "WHERE status='triggered' AND close_order_id IS NOT NULL "
        "ORDER BY position_id ASC"
    ).fetchall()]
    rows = [_plan_state(db, state) for state in states]
    report = {
        "command_version": COMMAND_VERSION,
        "scope": "triggered exact native paper close chains only",
        "summary": {
            "already_consistent": sum(row["action"] == "already_consistent" for row in rows),
            "repairs_planned": sum(row["action"] == "repair" for row in rows),
            "unavailable": sum(row["action"] == "unavailable" for row in rows),
        },
        "rows": rows,
    }
    report["report_fingerprint"] = _fingerprint(report)
    return report


def _audit_id(order_id: str, before: str, after: str) -> str:
    material = f"{COMMAND_VERSION}:{order_id}:{before}:{after}"
    return "pctr-" + hashlib.sha256(material.encode()).hexdigest()[:24]


def apply_paper_close_timestamp_recovery(*, expected_fingerprint: str) -> dict[str, Any]:
    """Apply the exact dry-run plan while the Gateway is stopped."""
    from aletheia.files.jsonl import write_audits_deduplicated
    from aletheia.gateway.pidfile import is_running
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import AuditEvent
    from aletheia.storage.sqlite import get_db

    if is_running():
        raise RuntimeError(
            "Gateway is running; stop it with `uv run aletheia gateway stop` first."
        )
    with repo.transaction(immediate=True):
        if is_running():
            raise RuntimeError("Gateway started while acquiring the recovery lock")
        report = inspect_paper_close_timestamp_recovery()
        if report["report_fingerprint"] != expected_fingerprint:
            raise ValueError("paper close timestamp recovery fingerprint is stale")
        db = get_db()
        now = datetime.now(timezone.utc).isoformat()
        for row in report["rows"]:
            if row["action"] != "repair":
                continue
            before = row["before"]["created_at"]
            after = row["after"]["created_at"]
            cursor = db.execute(
                "UPDATE orders SET created_at=? "
                "WHERE id=? AND position_id=? AND created_at=? AND filled_at=?",
                (
                    after,
                    row["close_order_id"],
                    row["position_id"],
                    before,
                    after,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"close Order {row['close_order_id']} changed before repair"
                )
            event_id = _audit_id(row["close_order_id"], before, after)
            before_state = {"created_at": before, "fill_id": row["fill_id"]}
            after_state = {"created_at": after, "fill_id": row["fill_id"]}
            inserted = repo.insert_audit_event_once(AuditEvent(
                id=event_id,
                timestamp=now,
                actor="human",
                source="paper_close_timestamp_recovery",
                event_type="paper_close_order_timestamp_repaired",
                entity_type="order",
                entity_id=row["close_order_id"],
                before_state=json.dumps(before_state, sort_keys=True),
                after_state=json.dumps(after_state, sort_keys=True),
                message=(
                    "Aligned a legacy native Paper close Order created_at with its "
                    "exact Fill after the bounded constructor race."
                ),
            ))
            if not inserted:
                raise ValueError(f"repair audit already exists for {row['close_order_id']}")
        applied = report["summary"]["repairs_planned"]

    audit_rows = [dict(row) for row in get_db().execute(
        "SELECT * FROM audit_events "
        "WHERE source='paper_close_timestamp_recovery' "
        "AND event_type='paper_close_order_timestamp_repaired' "
        "ORDER BY timestamp ASC, id ASC"
    ).fetchall()]
    audit_payloads = [{
        "id": row["id"],
        "timestamp": row["timestamp"],
        "actor": row["actor"],
        "source": row["source"],
        "event_type": row["event_type"],
        "entity_type": row["entity_type"],
        "entity_id": row["entity_id"],
        "before": json.loads(row["before_state"]),
        "after": json.loads(row["after_state"]),
        "message": row["message"],
    } for row in audit_rows]
    completed_ids = write_audits_deduplicated(audit_payloads)
    return {
        "applied": applied,
        "audit_jsonl_complete": completed_ids == {row["id"] for row in audit_rows},
        "report_fingerprint": expected_fingerprint,
    }

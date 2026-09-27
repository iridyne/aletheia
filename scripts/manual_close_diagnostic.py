#!/usr/bin/env python3
"""Read-only diagnostic for human-owned close claims (execution_contract §10.2.4).

A close claim that stays ``submission_unknown`` past
``SUBMISSION_UNKNOWN_TERMINAL_HOURS`` (24 h) becomes the human-owned
``manual_reconciliation_pending`` state (PR2). This tool takes a read-only
snapshot of every such claim — the Position row, its close Order, any local Fill
rows, and the manual-close audit trail — and prints one deterministic SHA-256
fingerprint over that exact JSON evidence.

The fingerprint is the ``--acknowledge`` token ``manual_close_resolver.py``
requires before it will mutate anything: it is deterministic for a fixed DB state
(SQL orders every list; JSON dumps sort keys), so the operator can prove the
resolver acts on exactly the evidence they reviewed. Two consecutive invocations
against an unchanged database produce the same fingerprint.

This script never writes: it opens the database with ``mode=ro``.

Usage:
  uv run python scripts/manual_close_diagnostic.py [--db PATH] [--position-id ID] [--json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANUAL_CLOSE_STATUS = "manual_reconciliation_pending"
PENDING_CLOSE_AUDIT_EVENT = "pending_close_reconciliation_required"
MANUAL_CLOSE_AUDIT_LIKE = "manual_close%"

# Deterministic projection of the audit trail. jsonl_* mirror bookkeeping columns
# are excluded: they are transport state, not close-claim evidence.
_AUDIT_COLUMNS = (
    "id, timestamp, actor, source, event_type, entity_type, entity_id, "
    "before_state, after_state, message"
)
_FILL_COLUMNS = (
    "id, order_id, fill_price, fill_quantity, fee, fee_currency, fee_source, filled_at"
)


def default_db_path() -> Path:
    """``ALETHEIA_DATA_DIR`` (default ``~/.aletheia``) + ``/app.db``."""
    base = os.environ.get("ALETHEIA_DATA_DIR", "").strip() or "~/.aletheia"
    return (Path(base).expanduser() / "app.db")


def _as_dicts(conn: sqlite3.Connection, sql: str, params: tuple) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def build_snapshot(conn: sqlite3.Connection, position_id: str) -> dict[str, Any] | None:
    """Return the exact evidence snapshot a resolver must acknowledge.

    Deterministic by construction: every list is SQL-ordered and returned as plain
    dicts. ``None`` means no Position with that id exists.
    """
    position_row = conn.execute(
        "SELECT * FROM positions WHERE id = ?", (position_id,)
    ).fetchone()
    if position_row is None:
        return None
    position = dict(position_row)

    order_id = position.get("pending_close_order_id")
    close_order: dict[str, Any] | None = None
    fills: list[dict[str, Any]] = []
    if order_id:
        order_row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        close_order = dict(order_row) if order_row is not None else None
        fills = _as_dicts(
            conn,
            f"SELECT {_FILL_COLUMNS} FROM fills WHERE order_id = ? ORDER BY id",
            (order_id,),
        )

    audit = _as_dicts(
        conn,
        f"SELECT {_AUDIT_COLUMNS} FROM audit_events "
        "WHERE entity_id = ? AND (event_type LIKE ? OR event_type = ?) "
        "ORDER BY timestamp, id",
        (position_id, MANUAL_CLOSE_AUDIT_LIKE, PENDING_CLOSE_AUDIT_EVENT),
    )

    return {
        "position": position,
        "close_order": close_order,
        "fills": fills,
        "audit": audit,
    }


def snapshot_fingerprint(snapshot: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON snapshot; the resolver's ``--acknowledge``."""
    canonical = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _manual_position_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM positions WHERE pending_close_status = ? ORDER BY id",
        (MANUAL_CLOSE_STATUS,),
    ).fetchall()
    return [row[0] for row in rows]


def _age_hours(requested_at: object) -> float | None:
    if not isinstance(requested_at, str) or not requested_at.strip():
        return None
    try:
        parsed = datetime.fromisoformat(requested_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    return delta.total_seconds() / 3600.0


def _open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _record(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_id": snapshot["position"]["id"],
        "fingerprint": snapshot_fingerprint(snapshot),
        "age_hours": _age_hours(snapshot["position"].get("pending_close_requested_at")),
        "snapshot": snapshot,
    }


def _print_human(record: dict[str, Any]) -> None:
    snapshot = record["snapshot"]
    position = snapshot["position"]
    order = snapshot["close_order"]
    fills = snapshot["fills"]
    audit = snapshot["audit"]

    print(f"Position: {position.get('id')}")
    print(
        f"  symbol={position.get('symbol')} market_type={position.get('market_type')} "
        f"side={position.get('direction')} qty={position.get('quantity')} "
        f"entry={position.get('entry_price')} "
        f"mark={position.get('current_price', 'not available')} "
        f"unrealized_pnl={position.get('unrealized_pnl', 'not available')}"
    )
    print(f"  pending_close_status={position.get('pending_close_status')}")
    print("Close claim:")
    age = record["age_hours"]
    age_text = f"{age:.2f}h" if isinstance(age, float) else "unavailable"
    print(f"  pending_close_order_id={position.get('pending_close_order_id')}")
    print(
        f"  pending_close_requested_at={position.get('pending_close_requested_at')} "
        f"age_hours={age_text}"
    )
    print("Close order snapshot:")
    if order is None:
        print("  (no close order row found for the claimed id)")
    else:
        print(
            f"  id={order.get('id')} status={order.get('status')} "
            f"error_reason={order.get('error_reason')} "
            f"exchange_order_id={order.get('exchange_order_id')} "
            f"created_at={order.get('created_at')} filled_at={order.get('filled_at')}"
        )
    if fills:
        print(f"Fills for close order ({len(fills)}):")
        for fill in fills:
            print(
                f"  {fill.get('id')} qty={fill.get('fill_quantity')} "
                f"price={fill.get('fill_price')} at={fill.get('filled_at')}"
            )
    else:
        print("Fills for close order: 0 fills recorded — anti-fabrication invariant preserved")
    print(f"Audit trail ({len(audit)}):")
    for event in audit:
        print(
            f"  {event.get('timestamp')} {event.get('event_type')} "
            f"actor={event.get('actor')} source={event.get('source')}"
        )
        if event.get("message"):
            print(f"    message: {event.get('message')}")
    print(f"Snapshot fingerprint: {record['fingerprint']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only snapshot / fingerprint of manual_reconciliation_pending claims."
    )
    parser.add_argument("--db", type=Path, default=None, help="Path to app.db")
    parser.add_argument("--position-id", default=None, help="Limit to one Position id")
    parser.add_argument("--json", action="store_true", help="Emit a JSON array")
    args = parser.parse_args(argv)

    db = args.db if args.db is not None else default_db_path()
    if not db.is_file():
        print(f"database not found: {db}", file=sys.stderr)
        return 1

    try:
        conn = _open_readonly(db)
    except sqlite3.Error as exc:
        print(f"cannot open database read-only: {db}: {exc}", file=sys.stderr)
        return 1

    try:
        if args.position_id is not None:
            position_ids = [args.position_id]
        else:
            position_ids = _manual_position_ids(conn)

        records: list[dict[str, Any]] = []
        for position_id in position_ids:
            snapshot = build_snapshot(conn, position_id)
            if snapshot is None:
                if args.position_id is not None:
                    print(f"position not found: {position_id}", file=sys.stderr)
                    return 1
                continue
            records.append(_record(snapshot))
    finally:
        conn.close()

    if args.json:
        print(json.dumps(records, indent=2, sort_keys=True))
    else:
        for record in records:
            _print_human(record)
    return 0


if __name__ == "__main__":
    sys.exit(main())

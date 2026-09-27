#!/usr/bin/env python3
"""Interactive single-claim resolver for ``manual_reconciliation_pending`` closes.

A close claim that stayed ``submission_unknown`` past the 24 h terminal bound is
human-owned (PR2). This tool lets the operator transition EXACTLY ONE claim
according to their own investigation of the venue, while enforcing every safety
invariant in execution_contract §10.2.4:

* The operator must first run ``manual_close_diagnostic.py`` and pass the exact
  snapshot fingerprint via ``--acknowledge``. The fingerprint is recomputed from
  the live database immediately before mutation; a mismatch exits ``2`` without
  writing anything.
* The claim is re-read under the SQLite writer lock (``BEGIN IMMEDIATE``) and the
  transition is refused (exit ``3``) if it is no longer
  ``manual_reconciliation_pending``.
* The operator must type the exact, case-sensitive string
  ``RESOLVE {position_id} AS {mode}`` on stdin. There is no ``--yes``/``--force``
  and no batch/loop mode.
* Every write — the transition, any venue-confirmed Fill, the claim release, and
  the ``manual_close_claim_resolved`` audit row with ``actor=human`` — happens
  inside ONE ``transaction(immediate=True)`` block, so a failure rolls back the
  whole resolution.

Permitted resolutions (no auto-close, no synthesized fills):

* ``venue_confirmed_fill`` — the venue shows the close filled. Requires
  ``--fill-id/--fill-quantity/--fill-price``; a Fill row is inserted and the
  ordinary reconciler materializes the Order/Position. The Order status is NOT
  changed here.
* ``venue_confirmed_absence`` — the venue proves the close never executed. The
  Order transitions to ``submission_rejected`` (irreversible via this tool) and
  the claim is released; the Position stays open and a later close is allocated a
  fresh ``client_order_id``.
* ``operator_settled_outside_gateway`` — audit only. The claim is released; the
  Order status and Fills are untouched. The Position remains open in Aletheia for
  the operator to settle separately.

Usage:
  uv run python scripts/manual_close_resolver.py \\
      --position-id ID --acknowledge <sha256> --mode <mode> --reason "<reason>" \\
      [--db PATH] [--fill-id ID --fill-quantity Q --fill-price P]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent, Fill

try:
    from scripts.manual_close_diagnostic import (
        default_db_path,
        build_snapshot,
        snapshot_fingerprint,
    )
except ImportError:  # pragma: no cover - direct ``python scripts/...`` execution
    from manual_close_diagnostic import (  # type: ignore[no-redef]
        default_db_path,
        build_snapshot,
        snapshot_fingerprint,
    )

MANUAL_CLOSE_STATUS = "manual_reconciliation_pending"
INCIDENT_SOURCE = "position_manager"
INCIDENT_CODE = "manual_close_reconciliation_required"
AUDIT_EVENT_TYPE = "manual_close_claim_resolved"

MODE_VENUE_FILL = "venue_confirmed_fill"
MODE_VENUE_ABSENCE = "venue_confirmed_absence"
MODE_OPERATOR_SETTLED = "operator_settled_outside_gateway"
MODES = (MODE_VENUE_FILL, MODE_VENUE_ABSENCE, MODE_OPERATOR_SETTLED)

EXIT_OK = 0
EXIT_DB_MISSING = 1
EXIT_STALE = 2
EXIT_NOT_MANUAL = 3
EXIT_CONFIRM = 4
EXIT_FAILED = 5

STALE_MESSAGE = (
    "Snapshot fingerprint changed since diagnostic; re-run diagnostic and "
    "re-acknowledge. Refusing to mutate stale evidence."
)
NOT_MANUAL_MESSAGE = (
    "Position is no longer in manual_reconciliation_pending; nothing to resolve."
)
CONFIRM_MESSAGE = "Confirmation string did not match; aborted."


class _ResolutionAbort(Exception):
    """A deterministic, operator-facing refusal; the caller maps it to an exit code."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def point_repository_at(db_path: Path) -> None:
    """Route the repository's thread-local connection at ``db_path``.

    The operator tool is the only writer in this process and holds the standard
    writer lock; pointing the injected path makes every repository helper
    (``transaction``, ``insert_fill``, ``release_position_close_claim``,
    ``transition_order``, ``insert_audit_event_once``) operate on exactly the
    database named by ``--db``.
    """
    from aletheia.storage import sqlite as sq

    sq._injected_db_path = db_path.resolve()
    sq._injected_db_active = True


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolution_audit_event(args: argparse.Namespace, snapshot: dict, order_id: str) -> AuditEvent:
    position = snapshot["position"]
    order = snapshot["close_order"] or {}
    before = {
        "pending_close_status": position.get("pending_close_status"),
        "pending_close_order_id": order_id,
        "order_status": order.get("status"),
    }
    after = {
        "pending_close_status": None,
        "resolution_mode": args.mode,
        "venue_fill_id": args.fill_id if args.mode == MODE_VENUE_FILL else None,
        "order_status": (
            "submission_rejected" if args.mode == MODE_VENUE_ABSENCE else order.get("status")
        ),
        "reason": args.reason,
    }
    message = (
        f"Human resolution ({args.mode}) of manual_reconciliation_pending claim "
        f"order={order_id}: {args.reason}"
    )
    if args.mode == MODE_VENUE_FILL:
        message += (
            f" venue_fill_id={args.fill_id} quantity={args.fill_quantity} "
            f"price={args.fill_price}"
        )
    return AuditEvent(
        id=f"manual-close-resolved-{position['id']}-{uuid.uuid4().hex[:12]}",
        actor="human",
        source="manual_close_resolver",
        event_type=AUDIT_EVENT_TYPE,
        entity_type="position",
        entity_id=position["id"],
        before_state=json.dumps(before, sort_keys=True),
        after_state=json.dumps(after, sort_keys=True),
        message=message,
    )


def _apply_resolution(args: argparse.Namespace, snapshot: dict) -> None:
    """Apply one mode's writes; MUST be called inside the caller's transaction."""
    position = snapshot["position"]
    position_id = position["id"]
    order_id = position.get("pending_close_order_id")
    if not order_id:
        raise _ResolutionAbort(
            EXIT_NOT_MANUAL,
            "Position carries no pending close order; nothing to resolve.",
        )

    if args.mode == MODE_VENUE_FILL:
        repo.insert_fill(
            Fill(
                id=args.fill_id,
                order_id=order_id,
                fill_price=float(args.fill_price),
                fill_quantity=float(args.fill_quantity),
                fee_source="manual_venue_confirmed",
                filled_at=_iso_now(),
            )
        )
    elif args.mode == MODE_VENUE_ABSENCE:
        repo.transition_order(
            order_id,
            status="submission_rejected",
            error_reason=f"operator_venue_confirmed_absence: {args.reason}",
        )
    elif args.mode != MODE_OPERATOR_SETTLED:
        raise _ResolutionAbort(EXIT_NOT_MANUAL, f"unsupported resolution mode: {args.mode}")

    # Release the human-owned claim so the ordinary reconciler re-observes the
    # Order (venue_confirmed_fill) and a later close can allocate a fresh
    # client_order_id (venue_confirmed_absence). This sets
    # pending_close_order_id/status/requested_at to NULL.
    repo.release_position_close_claim(position_id, order_id=order_id)

    event = _resolution_audit_event(args, snapshot, order_id)
    if not repo.insert_audit_event_once(event):
        raise RuntimeError(f"manual_close_claim_resolved audit identity conflict: {event.id}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve ONE manual_reconciliation_pending close claim per "
            "execution_contract §10.2.4."
        )
    )
    parser.add_argument("--db", type=Path, default=None, help="Path to app.db")
    parser.add_argument("--position-id", required=True, help="Position id to resolve")
    parser.add_argument(
        "--acknowledge",
        required=True,
        help="Exact snapshot fingerprint printed by manual_close_diagnostic.py",
    )
    parser.add_argument("--mode", required=True, choices=MODES, help="Resolution mode")
    parser.add_argument("--fill-id", default=None, help="Venue fill id (venue_confirmed_fill)")
    parser.add_argument(
        "--fill-quantity", type=float, default=None, help="Filled quantity (venue_confirmed_fill)"
    )
    parser.add_argument("--fill-price", type=float, default=None, help="Fill price (venue_confirmed_fill)")
    parser.add_argument("--reason", required=True, help="Human-readable reason for the resolution")
    args = parser.parse_args(argv)

    if not (args.reason or "").strip():
        parser.error("--reason must be a non-empty human-readable reason")

    if args.mode == MODE_VENUE_FILL:
        missing = [
            name
            for name, value in (
                ("--fill-id", args.fill_id),
                ("--fill-quantity", args.fill_quantity),
                ("--fill-price", args.fill_price),
            )
            if value is None
        ]
        if missing:
            parser.error("venue_confirmed_fill requires " + ", ".join(missing))
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    db = args.db if args.db is not None else default_db_path()
    if not db.is_file():
        print(f"database not found: {db}", file=sys.stderr)
        return EXIT_DB_MISSING

    point_repository_at(db)

    # Phase 1 — read-only validation, no writer lock held.
    pre = build_snapshot(repo.get_db(), args.position_id)
    if pre is None:
        print(f"position not found: {args.position_id}", file=sys.stderr)
        return EXIT_DB_MISSING
    if snapshot_fingerprint(pre) != args.acknowledge:
        print(STALE_MESSAGE, file=sys.stderr)
        return EXIT_STALE
    if pre["position"].get("pending_close_status") != MANUAL_CLOSE_STATUS:
        print(NOT_MANUAL_MESSAGE, file=sys.stderr)
        return EXIT_NOT_MANUAL

    # Phase 2 — explicit case-sensitive confirmation; never inside the
    # transaction, so the writer lock is not held across stdin I/O.
    expected = f"RESOLVE {args.position_id} AS {args.mode}"
    print(f"Type exactly to confirm: {expected}")
    typed = sys.stdin.readline()
    if typed.rstrip("\r\n") != expected:
        print(CONFIRM_MESSAGE, file=sys.stderr)
        return EXIT_CONFIRM

    # Phase 3 — one atomic writer-lock block. The evidence is revalidated under
    # the lock immediately before mutation; any failure rolls the whole block back.
    try:
        with repo.transaction(immediate=True):
            current = build_snapshot(repo.get_db(), args.position_id)
            if current is None or snapshot_fingerprint(current) != args.acknowledge:
                raise _ResolutionAbort(EXIT_STALE, STALE_MESSAGE)
            if current["position"].get("pending_close_status") != MANUAL_CLOSE_STATUS:
                raise _ResolutionAbort(EXIT_NOT_MANUAL, NOT_MANUAL_MESSAGE)
            _apply_resolution(args, current)
    except _ResolutionAbort as abort:
        print(abort.message, file=sys.stderr)
        return abort.code
    except Exception as exc:
        print(f"resolution failed; transaction rolled back: {exc}", file=sys.stderr)
        return EXIT_FAILED

    # Retire the in-process critical registry entry only after the durable write
    # committed. A restart reconstructs it from durable facts either way.
    from aletheia.infra.events import mark_resolved_from_durable

    mark_resolved_from_durable(
        source=INCIDENT_SOURCE, code=INCIDENT_CODE, entity_id=args.position_id
    )

    resolved = build_snapshot(repo.get_db(), args.position_id)
    new_fingerprint = snapshot_fingerprint(resolved) if resolved is not None else "unavailable"
    print(f"Resolved {args.position_id} via {args.mode}.")
    print(f"New snapshot fingerprint: {new_fingerprint}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

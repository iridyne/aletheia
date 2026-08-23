"""Repair a broker-ledger Position that was wrongly administratively closed.

The disappearance reconciler can race ahead of the normal close path and close a
Position via ``close_position_administrative`` (realized_pnl=NULL) moments before
the real exchange fill lands. The canonical fix is NOT a raw UPDATE to
positions.realized_pnl — that breaks the audit chain and (for paper) the
paper_ledger sync invariant. Instead we re-open the Position and replay the
normal ``close_position`` path so every downstream table (position_close_events,
audit_events) stays consistent.

Usage (Fedora Paper runtime)::

    uv run python -m aletheia.maintenance.repair_administrative_close --position-id d13674533c77
    uv run python -m aletheia.maintenance.repair_administrative_close --position-id d13674533c77 --dry-run

Idempotent: exits cleanly if the Position is already correctly closed.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent

logger = logging.getLogger(__name__)


def _find_native_close_order(position_id: str) -> dict | None:
    """Return the most recent native close Order for this Position, or None."""
    row = repo.get_db().execute(
        "SELECT id, exchange_order_id, price, quantity, filled_at, close_reason "
        "FROM orders "
        "WHERE position_id = ? AND source = 'native' "
        "AND close_reason IS NOT NULL AND close_reason <> '' "
        "AND status = 'filled' "
        "ORDER BY filled_at DESC, rowid DESC LIMIT 1",
        (position_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def _query_exchange_fill(exchange_order_id: str, symbol: str) -> dict | None:
    """Best-effort: fetch the exact private fill from the exchange.

    Returns None if the exchange query is unavailable — the repair still
    proceeds using the fee-rate estimate, just without exit_fill_verified=True.
    """
    try:
        from aletheia.exchanges.bitget.client import BitgetClient
        from aletheia.exchanges.bitget.profile_config import configured_bitget_profile

        client = BitgetClient(mode="demo", profile=configured_bitget_profile())
        try:
            from aletheia.data.symbols import to_bitget
            from aletheia.async_utils import run_coro_from_sync

            rows = run_coro_from_sync(
                client.get_order_fills(
                    order_id=str(exchange_order_id),
                    symbol=to_bitget(symbol),
                    market_type="usdt_futures",
                ),
                timeout=30,
            )
            if not rows:
                return None
            # Use the first fill row; market closes are typically single-fill.
            r = rows[0]
            return {
                "fill_id": str(r.get("tradeId") or r.get("id") or exchange_order_id),
                "price": float(r.get("fillPrice") or r.get("price") or 0.0),
                "quantity": float(r.get("fillQuantity") or r.get("size") or 0.0),
                "fee": float(r.get("fee") or 0.0),
                "fee_currency": str(r.get("feeCoin") or r.get("fee_currency") or "USDT"),
                "filled_at_ms": int(r.get("cTime") or r.get("createdTime") or 0),
            }
        finally:
            from aletheia.async_utils import run_coro_from_sync

            try:
                run_coro_from_sync(client.close(), timeout=10)
            except Exception:
                pass
    except Exception as exc:
        logger.warning("exchange fill query failed (%s); proceeding without exact fee", exc)
        return None


def _reopen_position(position_id: str, *, exit_price: float, evidence: dict) -> None:
    """Re-open a wrongly-closed Position so close_position can replay it."""
    now = datetime.now(timezone.utc).isoformat()
    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        if current.get("status") != "closed":
            return  # already open or in an unexpected state
        # These columns are NOT NULL (no usable DEFAULT); supply the same
        # placeholder values an administratively-open Position carries so the
        # replay can overwrite them with real economics.
        repo.update_position(
            position_id,
            status="open",
            current_price=exit_price,
            unrealized_pnl=0.0,
            close_reason=None,
            closed_at=None,
            close_category="unknown",
            exit_price_source="unavailable",
            exit_fill_verified=False,
            realized_pnl=None,
            entry_price_source="unavailable",
            funding_source="unavailable",
            outcome_exclusion_reason="unclassified",
            outcome_eligible=False,
            exit_fee=None,
            reconciliation_evidence=json.dumps(evidence, sort_keys=True, separators=(",", ":"), default=str),
        )
        repo.insert_audit_event(AuditEvent(
            actor="system",
            source="repair_administrative_close",
            event_type="position_reopened_for_close_replay",
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps({"status": "closed", "close_category": "administrative", "realized_pnl": None}),
            after_state=json.dumps({"status": "open", "intent": "replay normal close path"}),
            message=(
                f"Reopened Position {position_id} to replay the normal close path "
                f"after an incorrect administrative close."
            ),
        ))


def repair_position(position_id: str, *, dry_run: bool = False) -> dict:
    """Replay the normal close path for an administratively-closed Position."""
    from aletheia.portfolio.pnl import close_position

    pos = repo._select_one("positions", position_id)
    if pos is None:
        raise ValueError(f"position not found: {position_id}")

    # Idempotency: already correctly closed via the strategy path.
    if pos.get("status") == "closed" and pos.get("close_category") == "strategy" and pos.get("realized_pnl") is not None:
        return {"position_id": position_id, "status": "already_correct", "realized_pnl": pos["realized_pnl"]}

    if pos.get("status") != "closed":
        raise ValueError(
            f"repair requires an administratively-closed Position; "
            f"{position_id} is status={pos.get('status')!r}"
        )
    if pos.get("close_category") != "administrative":
        raise ValueError(
            f"repair targets close_category=administrative; "
            f"{position_id} is {pos.get('close_category')!r}"
        )

    order = _find_native_close_order(position_id)
    if order is None:
        raise ValueError(
            f"no filled native close Order found for {position_id}; "
            f"cannot determine exit economics for replay"
        )

    exit_price = float(order["price"])
    # Best-effort exchange fee lookup. Not required: close_position recomputes
    # fees from the execution-mode fee rate when actual_exit_fee is None, which
    # is the canonical fallback for broker-ledger modes.
    fill = _query_exchange_fill(str(order["exchange_order_id"]), str(pos["symbol"]))

    if dry_run:
        return {
            "position_id": position_id,
            "status": "dry_run",
            "close_order_id": order["id"],
            "exchange_order_id": order["exchange_order_id"],
            "exit_price": exit_price,
            "close_reason": order["close_reason"],
            "filled_at": order["filled_at"],
            "actual_exit_fee": fill["fee"] if fill else None,
            "exchange_fill_queried": fill is not None,
        }

    # Step 1: re-open so close_position's status=open preflight passes.
    reopen_evidence = {
        "repair": "replay_normal_close_after_administrative_close",
        "close_order_id": order["id"],
        "exchange_order_id": order["exchange_order_id"],
        "exit_price": exit_price,
    }
    _reopen_position(position_id, exit_price=exit_price, evidence=reopen_evidence)

    # Step 2: replay the canonical close path.
    closed_at = order["filled_at"]
    result = close_position(
        position_id,
        exit_price=exit_price,
        reason=order["close_reason"] or "verified_exit",
        actual_exit_fee=(float(fill["fee"]) if fill else None),
        closed_at=closed_at,
        actor="repair_administrative_close",
        source="repair_administrative_close",
    )

    result["close_order_id"] = order["id"]
    result["exchange_order_id"] = order["exchange_order_id"]
    result["exchange_fill_queried"] = fill is not None
    stored = repo._select_one("positions", position_id)
    result["final_realized_pnl"] = stored.get("realized_pnl") if stored else None
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--position-id", required=True, help="Position to repair")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        outcome = repair_position(args.position_id, dry_run=args.dry_run)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(outcome, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

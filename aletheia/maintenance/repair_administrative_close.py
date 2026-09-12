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

from aletheia.config.exchanges import execution_mode_environment
from aletheia.storage import repositories as repo
from aletheia.storage.models import AuditEvent

logger = logging.getLogger(__name__)


def _find_native_close_order(position_id: str) -> dict | None:
    """Return the most recent native close Order for this Position, or None."""
    row = (
        repo.get_db()
        .execute(
            "SELECT id, exchange_order_id, price, quantity, filled_at, close_reason "
            "FROM orders "
            "WHERE position_id = ? AND source = 'native' "
            "AND close_reason IS NOT NULL AND close_reason <> '' "
            "AND status = 'filled' "
            "ORDER BY filled_at DESC, rowid DESC LIMIT 1",
            (position_id,),
        )
        .fetchone()
    )
    return dict(row) if row is not None else None


def _aggregate_exchange_fills(
    rows: list[dict],
    *,
    exchange_order_id: str,
    symbol: str,
    execution_mode: str,
) -> dict | None:
    """Normalize and aggregate exact private fills through the shared contract."""
    from aletheia.execution.exchange_materialization import (
        _private_fill_totals,
        durable_private_fill_id,
        parse_bitget_private_fills,
    )

    evidence = parse_bitget_private_fills(rows)
    if not evidence or any(item.exchange_order_id != exchange_order_id for item in evidence):
        return None
    quantity, notional, fee = _private_fill_totals(evidence, symbol=symbol)
    if quantity <= 0 or notional <= 0:
        return None
    return {
        "fill_ids": [item.fill_id for item in evidence],
        "durable_fill_ids": [durable_private_fill_id(execution_mode, item.fill_id) for item in evidence],
        "fill_quantity": quantity,
        "price": notional / quantity,
        "fee": fee,
        "fee_status": "complete" if fee is not None else "unavailable",
        "filled_at": max(item.filled_at for item in evidence),
        "fills": [
            {
                "fill_id": item.fill_id,
                "remote_fill_id": item.fill_id,
                "durable_fill_id": durable_private_fill_id(execution_mode, item.fill_id),
                "price": item.price,
                "quantity": item.quantity,
                "fee": item.fee,
                "fee_currency": item.fee_currency,
                "filled_at": item.filled_at,
            }
            for item in evidence
        ],
    }


def _query_exchange_fill(
    exchange_order_id: str,
    symbol: str,
    *,
    execution_mode: str,
) -> dict | None:
    """Fetch complete private-fill evidence; unavailable evidence fails closed.

    Reads through the shared UTA projection rather than the hand-rolled Bitget
    client. Three reasons this is not merely a style change:

    * the account is *verified*. `_UTAReconciliationProjection` passes
      ``expected_account`` on every read, so a credential or profile change cannot
      silently return another account's fills. The old path constructed
      ``BitgetClient(mode="demo", ...)`` with the mode hardcoded, so it would have
      queried the demo account even when repairing a ``bitget_live_limited``
      Position.
    * incompleteness is explicit. The projection raises on any
      ``ReadCompleteness`` other than ``COMPLETE``, which the ``except`` below
      turns into "evidence unavailable, repair unchanged" — the fail-closed
      behaviour this function documents. A partial page previously looked like a
      short-but-valid fill list.
    * it is profile-aware. Bitget's classic_v2 (``/api/v2/``) and uta_v3
      (``/api/v3/``) surfaces are selected by the UTA profile, not routed by hand.
    """
    try:
        from aletheia.async_utils import run_coro_from_sync
        from aletheia.execution.reconciliation import _UTAReconciliationProjection
        from aletheia.uta.manager import build_uta_registry

        identity = next(
            (
                candidate
                for candidate in build_uta_registry().configured_identities()
                if candidate.execution_mode == execution_mode
            ),
            None,
        )
        if identity is None:
            logger.warning(
                "no configured UTA account for %s; administrative repair remains unchanged",
                execution_mode,
            )
            return None
        client = _UTAReconciliationProjection(execution_mode, identity)
        rows = run_coro_from_sync(
            client.get_order_fills(
                order_id=str(exchange_order_id),
                symbol=symbol,
                market_type="usdt_futures",
            ),
            timeout=30,
        )
        return _aggregate_exchange_fills(
            rows,
            exchange_order_id=str(exchange_order_id),
            symbol=symbol,
            execution_mode=execution_mode,
        )
    except Exception as exc:
        logger.warning("exchange fill query failed (%s); administrative repair remains unchanged", exc)
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
        repo.reopen_position_for_verified_repair(
            position_id,
            {
                "status": "open",
                "current_price": exit_price,
                "unrealized_pnl": 0.0,
                "close_reason": None,
                "closed_at": None,
                "close_category": "unknown",
                "exit_price_source": "unavailable",
                "exit_fill_verified": False,
                "realized_pnl": None,
                "outcome_exclusion_reason": "unclassified",
                "outcome_eligible": False,
                "exit_fee": None,
                "reconciliation_evidence": json.dumps(
                    evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ),
            },
        )
        repo.insert_audit_event(
            AuditEvent(
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
            )
        )


def repair_position(position_id: str, *, dry_run: bool = False) -> dict:
    """Replay the normal close path for an administratively-closed Position."""
    from aletheia.portfolio.pnl import close_position

    pos = repo._select_one("positions", position_id)
    if pos is None:
        raise ValueError(f"position not found: {position_id}")

    # Idempotency: already correctly closed via the strategy path.
    if (
        pos.get("status") == "closed"
        and pos.get("close_category") == "strategy"
        and pos.get("realized_pnl") is not None
    ):
        return {"position_id": position_id, "status": "already_correct", "realized_pnl": pos["realized_pnl"]}

    if pos.get("status") != "closed":
        raise ValueError(
            f"repair requires an administratively-closed Position; {position_id} is status={pos.get('status')!r}"
        )
    if pos.get("close_category") != "administrative":
        raise ValueError(
            f"repair targets close_category=administrative; {position_id} is {pos.get('close_category')!r}"
        )

    order = _find_native_close_order(position_id)
    if order is None:
        raise ValueError(
            f"no filled native close Order found for {position_id}; cannot determine exit economics for replay"
        )

    execution_mode = str(pos.get("execution_mode") or "")
    account_environment = execution_mode_environment(execution_mode)
    if account_environment not in {"demo", "testnet", "live"}:
        raise ValueError(
            "administrative close repair requires a registered external execution mode"
        )
    fill = _query_exchange_fill(
        str(order["exchange_order_id"]),
        str(pos["symbol"]),
        execution_mode=execution_mode,
    )
    if fill is None:
        raise ValueError("complete private fill evidence is unavailable; administrative close preserved")
    try:
        expected_quantity = float(pos["quantity"])
        observed_quantity = float(fill["fill_quantity"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("private fill quantity is invalid; administrative close preserved") from None
    tolerance = max(1e-10, abs(expected_quantity) * 1e-8)
    if abs(observed_quantity - expected_quantity) > tolerance:
        raise ValueError("private fills do not cover the Position quantity; administrative close preserved")
    if fill.get("fee_status") != "complete" or fill.get("fee") is None:
        raise ValueError("private fill quote fee is unavailable; administrative close preserved")
    exit_price = float(fill["price"])
    verified_exit_evidence = {
        "source": "bitget_private_close_fills",
        "source_endpoint": "private_fills",
        "broker_id": execution_mode,
        "account_environment": account_environment,
        "execution_mode": execution_mode,
        "exchange_order_id": str(order["exchange_order_id"]),
        **fill,
    }

    if dry_run:
        return {
            "position_id": position_id,
            "status": "dry_run",
            "close_order_id": order["id"],
            "exchange_order_id": order["exchange_order_id"],
            "exit_price": exit_price,
            "close_reason": order["close_reason"],
            "filled_at": fill["filled_at"],
            "actual_exit_fee": fill["fee"],
            "exchange_fill_queried": True,
        }

    # Step 1: re-open so close_position's status=open preflight passes.
    reopen_evidence = {
        "repair": "replay_normal_close_after_administrative_close",
        "close_order_id": order["id"],
        "exchange_order_id": order["exchange_order_id"],
        "exit_price": exit_price,
        "fill_ids": fill["fill_ids"],
        "fill_quantity": fill["fill_quantity"],
    }
    # Reopen and replay under one transaction. The intermediate open state must
    # never commit if the canonical close path rejects the evidence or fails.
    with repo.transaction(immediate=True):
        _reopen_position(position_id, exit_price=exit_price, evidence=reopen_evidence)

        # Step 2: replay the canonical close path.
        result = close_position(
            position_id,
            exit_price=exit_price,
            reason=order["close_reason"] or "verified_exit",
            actual_exit_fee=float(fill["fee"]),
            verified_exit_evidence=verified_exit_evidence,
            closed_at=fill["filled_at"],
            actor="repair_administrative_close",
            source="repair_administrative_close",
        )

    result["close_order_id"] = order["id"]
    result["exchange_order_id"] = order["exchange_order_id"]
    result["exchange_fill_queried"] = True
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

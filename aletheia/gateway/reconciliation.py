"""Gateway reconciliation — compares local state with exchange truth.

Implements the reconciliation rules from Plan §14: order, fill, and position
diffing with deterministic actions (backfill, import orphan, or SAFE_FREEZE).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from aletheia.storage.models import PSFill, PSPosition, PSReconciliationDiff, PSReconciliationReport, new_id
from aletheia.gateway.exchange_adapter import RemoteFill, RemoteOrder, RemotePosition
from aletheia.gateway.state_store import GatewayStateStore


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


ALETHEIA_PREFIX = "aletheia_"


def _is_aletheia_order(client_order_id: str | None) -> bool:
    return bool(client_order_id and client_order_id.startswith(ALETHEIA_PREFIX))


def reconcile_fills(
    remote_fills: list[RemoteFill],
    state_store: GatewayStateStore,
    mode: str,
) -> tuple[list[PSReconciliationDiff], list[PSFill]]:
    """Reconcile remote fills against local state. Returns (diffs, backfilled_fills)."""
    diffs: list[PSReconciliationDiff] = []
    backfilled: list[PSFill] = []

    for rf in remote_fills:
        if state_store.fill_exists(rf.venue_trade_id, rf.venue_trade_id):
            continue  # Already known

        # Try to match to a local order
        local_order = None
        if rf.client_order_id:
            local_order = state_store.get_order_by_client_id(rf.client_order_id)

        if local_order:
            fill = PSFill(
                order_id=local_order["order_id"],
                mode=mode,
                venue=local_order["venue"],
                venue_trade_id=rf.venue_trade_id,
                client_order_id=rf.client_order_id,
                symbol=rf.symbol,
                side=rf.side,
                quantity=rf.quantity,
                price=rf.price,
                fee=rf.fee,
                fee_currency=rf.fee_currency,
                filled_at=rf.filled_at or _utcnow(),
            )
            state_store.record_fill(fill)
            backfilled.append(fill)
            diffs.append(PSReconciliationDiff(
                diff_type="REMOTE_FILL_BACKFILLED",
                severity="info",
                remote_ref={"venue_trade_id": rf.venue_trade_id, "symbol": rf.symbol},
                local_ref={"order_id": local_order["order_id"]},
                message=f"Backfilled fill for {rf.symbol} {rf.side} {rf.quantity}@{rf.price}",
                recommended_action="auto_resolved",
            ))
        elif _is_aletheia_order(rf.client_order_id):
            # Orphan fill — order not found locally
            diffs.append(PSReconciliationDiff(
                diff_type="ORPHAN_FILL_IMPORTED",
                severity="critical",
                remote_ref={"venue_trade_id": rf.venue_trade_id, "client_order_id": rf.client_order_id},
                local_ref=None,
                message=f"Fill for Aletheia order but no local order found: {rf.client_order_id}",
                recommended_action="SAFE_FREEZE",
            ))

    return diffs, backfilled


def reconcile_orders(
    local_open_orders: list[dict],
    remote_open_orders: list[RemoteOrder],
    state_store: GatewayStateStore,
) -> list[PSReconciliationDiff]:
    """Reconcile local open orders against remote state."""
    diffs: list[PSReconciliationDiff] = []

    # Index remote orders by venue_order_id and client_order_id
    remote_by_venue_id: dict[str, RemoteOrder] = {}
    remote_by_client_id: dict[str, RemoteOrder] = {}
    for ro in remote_open_orders:
        remote_by_venue_id[ro.venue_order_id] = ro
        if ro.client_order_id:
            remote_by_client_id[ro.client_order_id] = ro

    for local in local_open_orders:
        venue_oid = local.get("venue_order_id")
        client_oid = local.get("client_order_id", "")

        # Try to find remote match
        remote = None
        if venue_oid and venue_oid in remote_by_venue_id:
            remote = remote_by_venue_id[venue_oid]
        elif client_oid and client_oid in remote_by_client_id:
            remote = remote_by_client_id[client_oid]

        if remote:
            if remote.status == "filled":
                diffs.append(PSReconciliationDiff(
                    diff_type="LOCAL_OPEN_REMOTE_FILLED",
                    severity="info",
                    local_ref={"order_id": local["order_id"], "client_order_id": client_oid},
                    remote_ref={"venue_order_id": remote.venue_order_id, "status": "filled"},
                    message=f"Local open order was filled on exchange",
                    recommended_action="backfill_fill",
                ))
            elif remote.status == "cancelled":
                state_store.mark_order_cancelled(local["order_id"])
                diffs.append(PSReconciliationDiff(
                    diff_type="LOCAL_OPEN_REMOTE_CANCELLED",
                    severity="info",
                    local_ref={"order_id": local["order_id"]},
                    remote_ref={"venue_order_id": remote.venue_order_id},
                    message=f"Local open order was cancelled on exchange",
                    recommended_action="auto_resolved",
                ))
            # else: both open — consistent, no diff
        else:
            # Local open but remote doesn't know about it
            diffs.append(PSReconciliationDiff(
                diff_type="LOCAL_ORDER_REMOTE_MISSING",
                severity="critical",
                local_ref={"order_id": local["order_id"], "client_order_id": client_oid},
                remote_ref=None,
                message="Local order is open but exchange does not return it and no matching recent fill was found.",
                recommended_action="SAFE_FREEZE",
            ))
            state_store.mark_order_unknown(local["order_id"])

    # Check for remote orders that are Aletheia's but not in our local state
    for ro in remote_open_orders:
        if _is_aletheia_order(ro.client_order_id):
            local = state_store.get_order_by_client_id(ro.client_order_id)
            if local is None:
                diffs.append(PSReconciliationDiff(
                    diff_type="REMOTE_ORDER_LOCAL_MISSING",
                    severity="warning",
                    local_ref=None,
                    remote_ref={"venue_order_id": ro.venue_order_id, "client_order_id": ro.client_order_id},
                    message=f"Remote Aletheia order not in local DB: {ro.client_order_id}",
                    recommended_action="import_recovered_order",
                ))

    return diffs


def reconcile_positions(
    local_open_positions: list[dict],
    remote_positions: list[RemotePosition],
    state_store: GatewayStateStore,
    mode: str,
) -> list[PSReconciliationDiff]:
    """Reconcile local open positions against remote exchange state."""
    diffs: list[PSReconciliationDiff] = []

    # Index remote by symbol
    remote_by_symbol: dict[str, RemotePosition] = {rp.symbol: rp for rp in remote_positions}

    for local in local_open_positions:
        symbol = local.get("symbol", "")
        remote = remote_by_symbol.pop(symbol, None)

        if remote is None:
            # Local position but remote has nothing
            diffs.append(PSReconciliationDiff(
                diff_type="LOCAL_POSITION_REMOTE_MISSING",
                severity="critical",
                local_ref={"position_id": local["position_id"], "symbol": symbol, "qty": local.get("quantity")},
                remote_ref=None,
                message=f"Local open position {symbol} not found on exchange",
                recommended_action="SAFE_FREEZE",
            ))
        else:
            # Both exist — check for quantity mismatch
            local_qty = local.get("quantity", "0")
            if str(local_qty) != str(remote.quantity):
                diffs.append(PSReconciliationDiff(
                    diff_type="POSITION_QUANTITY_MISMATCH",
                    severity="warning",
                    local_ref={"position_id": local["position_id"], "qty": local_qty},
                    remote_ref={"symbol": symbol, "qty": remote.quantity},
                    message=f"Position {symbol} qty mismatch: local={local_qty} remote={remote.quantity}",
                    recommended_action="attempt_fill_repair",
                ))
            # Check side mismatch
            local_side = local.get("side", "")
            if local_side and local_side != remote.side:
                diffs.append(PSReconciliationDiff(
                    diff_type="POSITION_SIDE_MISMATCH",
                    severity="critical",
                    local_ref={"position_id": local["position_id"], "side": local_side},
                    remote_ref={"symbol": symbol, "side": remote.side},
                    message=f"Position {symbol} side mismatch: local={local_side} remote={remote.side}",
                    recommended_action="SAFE_FREEZE",
                ))

    # Remote positions we don't have locally
    for symbol, rp in remote_by_symbol.items():
        diffs.append(PSReconciliationDiff(
            diff_type="REMOTE_POSITION_LOCAL_MISSING",
            severity="critical",
            local_ref=None,
            remote_ref={"symbol": symbol, "side": rp.side, "qty": rp.quantity},
            message=f"Exchange has position {symbol} {rp.side} {rp.quantity} not tracked locally",
            recommended_action="import_orphan_and_SAFE_FREEZE",
        ))
        # Import as orphan position
        orphan = PSPosition(
            mode=mode,
            strategy_id="orphan_imported",
            venue="exchange",
            symbol=symbol,
            side=rp.side,
            quantity=rp.quantity,
            avg_entry_price=rp.avg_entry_price,
            unrealized_pnl=rp.unrealized_pnl,
            status="orphan",
        )
        state_store.open_position(orphan)

    return diffs


def build_reconciliation_report(
    gateway_session_id: str,
    diffs: list[PSReconciliationDiff],
) -> PSReconciliationReport:
    """Build a reconciliation report from collected diffs."""
    has_critical = any(d.severity == "critical" for d in diffs)
    if not diffs:
        status = "ok"
    elif has_critical:
        status = "frozen"
    else:
        status = "diff_found"

    actions = []
    if has_critical:
        actions.append({"type": "ENTER_SAFE_FREEZE", "reason": "reconciliation_diff"})

    return PSReconciliationReport(
        gateway_session_id=gateway_session_id,
        status=status,
        diffs_json=json.dumps([d.model_dump() for d in diffs], ensure_ascii=False),
        actions_json=json.dumps(actions, ensure_ascii=False),
    )

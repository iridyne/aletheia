"""Route ready disappearance candidates through verified exit evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from aletheia.execution.verified_exit import (
    VerifiedExitFill,
    VerifiedExitRequest,
    classify_verified_exit,
    inconclusive_verified_exit,
)
from aletheia.portfolio.disappearance_confirmation import (
    DIRECT_CONFIRMED_ABSENT,
    disappearance_ready,
)
from aletheia.portfolio.outcome_closure import close_position_from_verified_exit
from aletheia.storage import repositories as repo


def _to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


async def _query_uta_verified_exit(
    execution_mode: str, request: VerifiedExitRequest
):
    from aletheia.uta.compat import instrument_from_local
    from aletheia.uta.manager import query_uta, select_account_port
    from aletheia.uta.models import VerifiedExitQuery, VerifiedExitState

    identity = select_account_port(execution_mode).identity
    if (
        request.execution_mode != identity.execution_mode
        or request.broker_id != identity.execution_mode
        or request.account_environment != identity.environment
        or request.account_scope != identity.account_fingerprint
    ):
        raise ValueError("verified exit UTA account identity mismatch")
    report = await query_uta(
        execution_mode,
        "verify_position_exit",
        VerifiedExitQuery(
            identity=identity,
            local_position_id=request.position_id,
            instrument=instrument_from_local(request.symbol, request.market_type),
            direction=request.direction,
            expected_quantity=Decimal(str(request.quantity)),
            history_start_at=datetime.fromtimestamp(
                request.history_start_ms / 1000, tz=timezone.utc
            ),
            history_end_at=datetime.fromtimestamp(
                request.history_end_ms / 1000, tz=timezone.utc
            ),
            local_order_ids=request.known_entry_order_ids,
        ),
    )
    if report.state is VerifiedExitState.POSITION_PRESENT:
        return inconclusive_verified_exit(request, "position_present")
    if report.state is VerifiedExitState.INCONCLUSIVE:
        return inconclusive_verified_exit(
            request,
            report.reason_code or "uta_verified_exit_inconclusive",
            position_absent=report.current_position is None,
        )
    fills = tuple(
        VerifiedExitFill(
            fill_id=fill.venue_fill_id,
            order_id=fill.venue_order_id or fill.client_order_id,
            price=float(fill.price),
            quantity=float(fill.quantity),
            fee=float(fill.fee) if fill.fee is not None else None,
            fee_currency=fill.fee_currency,
            realized_pnl=None,
            filled_at_ms=int(fill.executed_at.timestamp() * 1000),
        )
        for fill in report.exit_fills
    )
    return classify_verified_exit(
        request,
        position_absent=True,
        history_complete=True,
        close_fills=fills,
        ambiguous_activity=False,
        pages=1,
        source_endpoint="uta_v1",
    )


class _UTAVerifiedExitProjection:
    """Phase-3 compatibility seam over normalized UTA verified-exit reports."""

    def __init__(self, execution_mode: str) -> None:
        self.execution_mode = execution_mode

    async def get_verified_position_exit(self, request: VerifiedExitRequest):
        return await _query_uta_verified_exit(self.execution_mode, request)

    async def get_positions(self):
        from aletheia.uta.manager import query_uta
        from aletheia.uta.models import PositionQuery

        return await query_uta(
            self.execution_mode, "get_positions", PositionQuery()
        )

    async def close(self) -> None:
        return None


def _client_for_mode(execution_mode: str):
    return _UTAVerifiedExitProjection(execution_mode)


def _run_query(execution_mode: str, request: VerifiedExitRequest):
    from aletheia.async_utils import run_coro_from_sync

    async def run():
        client = _client_for_mode(execution_mode)
        try:
            return await client.get_verified_position_exit(request)
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()

    return run_coro_from_sync(run(), timeout=60)


def _direct_positions_present(execution_mode: str, position: dict) -> bool | None:
    """Fresh venue presence check through the configured profile client.

    Independent of the verified-exit protocol's internal absence determination
    and of the cached sync view (2026-08-19: a post-crash empty view plus an
    endpoint-family-blind fills check administratively closed a live exchange
    position). Returns True when the venue still reports this position, False
    when a successful query proves absence, and None when the query itself is
    unavailable — callers must fail closed on None.
    """
    from aletheia.async_utils import run_coro_from_sync
    from aletheia.uta.models import ReadCompleteness

    try:
        async def read():
            client = _client_for_mode(execution_mode)
            try:
                return await client.get_positions()
            finally:
                close = getattr(client, "close", None)
                if close is not None:
                    await close()

        report = run_coro_from_sync(read(), timeout=60)
    except Exception:
        return None
    if isinstance(report, list):
        rows = report
    else:
        if report.completeness is not ReadCompleteness.COMPLETE:
            return None
        rows = report.positions
    direction = str(position.get("direction") or "").lower()
    symbol = str(position.get("symbol") or "")
    market_type = str(position.get("market_type") or "")
    for row in rows:
        try:
            row_side = str(row.side or "").lower()
            quantity = float(row.quantity)
        except (TypeError, ValueError):
            continue
        row_instrument = getattr(row, "instrument", None)
        if row_instrument is not None and (
            row_instrument.canonical_symbol != symbol
            or row_instrument.market_type != market_type
        ):
            continue
        if quantity <= 0:
            continue
        if not row_side or row_side in {direction, "net", "both"}:
            return True
    return False


def _known_entry_order_ids(position: dict) -> tuple[str, ...]:
    """Return only exchange entry Orders already represented by this Position."""
    rows = repo.get_db().execute(
        """
        SELECT DISTINCT o.exchange_order_id
        FROM orders o
        WHERE o.execution_mode = ?
          AND TRIM(COALESCE(o.exchange_order_id, '')) <> ''
          AND TRIM(COALESCE(o.close_reason, '')) = ''
          AND (o.id = ? OR o.position_id = ?)
        ORDER BY o.exchange_order_id ASC
        """,
        (
            position.get("execution_mode"),
            position.get("order_id"),
            position.get("id"),
        ),
    ).fetchall()
    return tuple(str(row["exchange_order_id"]) for row in rows)


def settle_ready_disappearance_candidate(candidate_id: str) -> dict:
    candidate = repo._select_one("position_disappearance_candidates", candidate_id)
    if candidate is None:
        raise ValueError(f"disappearance candidate not found: {candidate_id}")
    if candidate.get("status") != "pending":
        return {"candidate_id": candidate_id, "closed": False, "reason": "already_resolved"}
    if not disappearance_ready(candidate):
        return {"candidate_id": candidate_id, "closed": False, "reason": "not_ready"}
    position = repo._select_one("positions", str(candidate["position_id"]))
    if position is None:
        raise ValueError(f"candidate position not found: {candidate['position_id']}")
    if position.get("status") != "open":
        return {"candidate_id": candidate_id, "closed": False, "reason": "position_not_open"}
    execution_mode = str(position["execution_mode"])

    history_start_ms = _to_ms(position.get("opened_at"))
    if history_start_ms is None:
        return {
            "candidate_id": candidate_id, "closed": False,
            "reason": "missing_complete_history_start",
        }
    history_end_ms = max(
        int(datetime.now(timezone.utc).timestamp() * 1000), history_start_ms + 1,
    )
    request = VerifiedExitRequest(
        broker_id=str(candidate["broker_id"]),
        account_environment=str(candidate["account_environment"]),
        account_scope=str(candidate["account_fingerprint"]),
        execution_mode=execution_mode,
        position_id=str(position["id"]),
        symbol=str(position["symbol"]),
        market_type=str(position["market_type"]),
        direction=str(position["direction"]).lower(),
        quantity=float(position["quantity"]),
        history_start_ms=history_start_ms,
        history_end_ms=history_end_ms,
        known_entry_order_ids=_known_entry_order_ids(position),
    )
    try:
        evidence = _run_query(execution_mode, request)
    except Exception as exc:
        return {
            "candidate_id": candidate_id, "closed": False,
            "reason": "verified_exit_query_failed", "error_type": type(exc).__name__,
        }
    # Administrative close is irreversible for realized-PnL attribution, so
    # `confirmed_absent_without_fill` alone is not sufficient authority: a
    # fresh direct positions query must independently confirm absence. Presence
    # defers to the normal managed path (the next classify cycle resolves the
    # candidate as reappeared); an unavailable query fails closed and defers.
    direct_absence_confirmed = None
    if evidence.state == "confirmed_absent_without_fill":
        direct_absence_confirmed = _direct_positions_present(
            execution_mode, position,
        )
        if direct_absence_confirmed is None:
            return {
                "candidate_id": candidate_id, "closed": False,
                "reason": "direct_query_unavailable",
            }
        if direct_absence_confirmed:
            return {
                "candidate_id": candidate_id, "closed": False,
                "reason": "direct_query_position_present",
            }
    # The observed Order/Fill chain, Position closure, and candidate resolution
    # are one local state transition.  Keeping the outer writer transaction open
    # makes the nested close transaction join it, so a final candidate-write
    # failure cannot leave a closed Position with a permanently pending event.
    with repo.transaction(immediate=True):
        if direct_absence_confirmed is False:
            repo.update_position_disappearance_candidate(
                candidate_id, direct_query_state=DIRECT_CONFIRMED_ABSENT,
            )
        result = close_position_from_verified_exit(
            str(position["id"]), evidence,
            reason="reconciled_gone",
            candidate_id=candidate_id,
        )
        if result.get("closed") or result.get("already_closed"):
            stamp = datetime.now(timezone.utc).isoformat()
            status = (
                "settled_verified"
                if result.get("closure_type") in {"verified_strategy", "strategy"}
                else "settled_administrative"
            )
            repo.update_position_disappearance_candidate(
                candidate_id, status=status, resolved_at=stamp, updated_at=stamp,
            )
    return {"candidate_id": candidate_id, **result}


def settle_ready_disappearance_candidates(candidate_ids: list[str]) -> list[dict]:
    results = []
    for candidate_id in dict.fromkeys(candidate_ids):
        results.append(settle_ready_disappearance_candidate(candidate_id))
    return results

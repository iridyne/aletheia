"""Authoritative daily realized account economics for the Risk Engine.

This read model is deliberately separate from strategy-performance analytics. A
closed lifecycle row is not account truth unless its exit economics are verified;
local paper results must additionally reconcile to the append-only cash ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import assess_position_closure, raw_account_events


@dataclass(frozen=True)
class AccountLossSnapshot:
    available: bool
    realized_pnl_usdt: float | None
    loss_usdt: float | None
    source: str
    reason: str
    event_count: int


def authoritative_account_realized_loss(execution_mode: str) -> AccountLossSnapshot:
    """Return today's exact signed result, or explicit unavailability.

    An unknown close never becomes zero and never becomes a fabricated loss. Risk
    decides how an unavailable snapshot affects proposal approval.
    """

    today_start = f"{datetime.now(timezone.utc).date().isoformat()}T00:00:00+00:00"
    try:
        rows = raw_account_events(
            execution_mode=execution_mode,
            closed_after=today_start,
        )
        from aletheia.storage.sqlite import get_db

        exact_event_count = int(get_db().execute(
            "SELECT COUNT(*) FROM positions "
            "WHERE status='closed' AND execution_mode=? "
            "AND datetime(closed_at) >= datetime(?)",
            (execution_mode, today_start),
        ).fetchone()[0])
    except Exception:
        return _unavailable("account_event_read_failed")

    # ``raw_account_events`` is intentionally bounded for general analytics.
    # Risk must never mistake that display/read-model window for the complete
    # UTC-day account ledger: an older loss hidden behind 10,000 newer closes
    # would otherwise loosen the daily-loss brake.  A concurrent close between
    # these reads also yields a mismatch and therefore fails closed.
    if exact_event_count != len(rows):
        return _unavailable(
            "account_event_window_truncated",
            event_count=exact_event_count,
        )

    for row in rows:
        assessment = assess_position_closure(row)
        if not assessment.eligible:
            # Block on: truly unclassifiable closes (legacy_unverified_close),
            # administrative/maintenance closes (not real trade economics),
            # and any ineligible close without a recorded realized_pnl.
            # Structured-but-ineligible closes with computable realized_pnl
            # (e.g. unverified_exit_fill from demo mode) pass through to the sum.
            if (
                assessment.reason == "legacy_unverified_close"
                or assessment.category in {"administrative", "maintenance"}
                or row.get("realized_pnl") is None
            ):
                return _unavailable(assessment.reason, event_count=len(rows))

    if execution_mode == "paper_internal":
        return _paper_ledger_snapshot(rows, today_start)

    realized = round(sum(float(row["realized_pnl"]) for row in rows), 8)
    # Add realized PnL from PARTIAL reductions today (exit-intent R1 Stage 2). Full
    # closes are already summed above from the verified closed-position read; partial
    # reductions leave the position OPEN so they never appear there — they live only in
    # the per-close ledger. Zero until reduce-in-place (Stage 3) writes any, so this is
    # equivalence-preserving for the current full-close-only world.
    realized = round(realized + _partial_close_realized_today(execution_mode, today_start), 8)
    return AccountLossSnapshot(
        available=True,
        realized_pnl_usdt=realized,
        loss_usdt=round(max(0.0, -realized), 8),
        source="verified_position_economics",
        reason="available",
        event_count=len(rows),
    )


def _paper_ledger_snapshot(rows: list[dict], today_start: str) -> AccountLossSnapshot:
    from aletheia.storage.sqlite import get_db

    try:
        ledger_rows = get_db().execute(
            "SELECT position_id, amount_usdt FROM paper_account_events "
            "WHERE action = 'position_closed' "
            "AND datetime(created_at) >= datetime(?) "
            "ORDER BY datetime(created_at) ASC, rowid ASC",
            (today_start,),
        ).fetchall()
    except Exception:
        return _unavailable("paper_ledger_read_failed", event_count=len(rows))

    expected = {str(row["id"]): float(row["realized_pnl"]) for row in rows}
    actual: dict[str, list[float]] = {}
    for ledger_row in ledger_rows:
        position_id = str(ledger_row["position_id"] or "")
        actual.setdefault(position_id, []).append(float(ledger_row["amount_usdt"]))

    if set(actual) != set(expected):
        return _unavailable("paper_ledger_mismatch", event_count=len(rows))
    for position_id, amount in expected.items():
        ledger_amounts = actual.get(position_id, [])
        if len(ledger_amounts) != 1 or abs(ledger_amounts[0] - amount) > 1e-8:
            return _unavailable("paper_ledger_mismatch", event_count=len(rows))

    realized = round(sum(expected.values()), 8)
    # Partial reductions today (see non-paper branch). Zero until Stage 3.
    realized = round(realized + _partial_close_realized_today("paper_internal", today_start), 8)
    return AccountLossSnapshot(
        available=True,
        realized_pnl_usdt=realized,
        loss_usdt=round(max(0.0, -realized), 8),
        source="paper_account_ledger",
        reason="available",
        event_count=len(rows),
    )


def _partial_close_realized_today(execution_mode: str, today_start: str) -> float:
    """Realized PnL from PARTIAL close events today (exit-intent R1 Stage 2).

    Full closes are summed from the verified closed-position read; partial reductions
    (is_partial=1) live only in the per-close ledger because the position stays open, so
    they are added here — closing the gap the research flagged (a losing partial reduce
    would otherwise be invisible to the daily-loss breaker). Returns 0.0 when there are
    no partial events (the current world), keeping the breaker's output unchanged.
    """
    try:
        from aletheia.storage.repositories import list_position_close_events
        events = list_position_close_events(since_iso=today_start, execution_mode=execution_mode)
    except Exception:
        return 0.0
    return round(
        sum(float(e.get("realized_pnl") or 0.0) for e in events if e.get("is_partial")), 8
    )


def _unavailable(reason: str, *, event_count: int = 0) -> AccountLossSnapshot:
    return AccountLossSnapshot(
        available=False,
        realized_pnl_usdt=None,
        loss_usdt=None,
        source="unavailable",
        reason=reason,
        event_count=event_count,
    )

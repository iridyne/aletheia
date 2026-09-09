"""Daily realized account economics; archived ledger queries never repair history."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import assess_position_closure, raw_account_events

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountLossSnapshot:
    available: bool
    realized_pnl_usdt: float | None
    loss_usdt: float | None
    source: str
    reason: str
    event_count: int



def authoritative_account_realized_loss(execution_mode: str) -> AccountLossSnapshot:
    """Return today's exact signed result without modifying accounting history."""

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

    rows = [
        row
        for row in rows
        if str(row.get("outcome_exclusion_reason") or "")
        != "invalid_exit_price_evidence"
    ]

    settled_rows: list[dict] = []

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
            ):
                return _unavailable(assessment.reason, event_count=len(rows))
            if row.get("realized_pnl") is None:
                if (
                    str(row.get("outcome_exclusion_reason") or "")
                    == "funding_permanently_unrecoverable"
                ):
                    return _unavailable(
                        "funding_permanently_unrecoverable", event_count=len(rows)
                    )
                if execution_mode == "paper_internal":
                    return _unavailable("pending_funding_economics", event_count=len(rows))
                return _unavailable(assessment.reason, event_count=len(rows))
        if row.get("realized_pnl") is None and execution_mode == "paper_internal":
            # Eligible + realized_pnl=NULL: funding not yet computed by the settler.
            # Unknown futures funding has no finite conservative lower bound; treating
            # it as zero understates loss and delays the mode brake.  Fail closed.
            if (
                str(row.get("outcome_exclusion_reason") or "")
                == "funding_permanently_unrecoverable"
            ):
                return _unavailable(
                    "funding_permanently_unrecoverable", event_count=len(rows)
                )
            return _unavailable("pending_funding_economics", event_count=len(rows))
        else:
            settled_rows.append(row)

    if execution_mode == "paper_internal":
        return _paper_ledger_snapshot(settled_rows, today_start)

    realized = round(sum(float(row["realized_pnl"]) for row in settled_rows), 8)
    # Add realized PnL from PARTIAL reductions today (exit-intent R1 Stage 2). Full
    # closes are already summed above from the verified closed-position read; partial
    # reductions leave the position OPEN so they never appear there — they live only in
    # the per-close ledger. Zero until reduce-in-place (Stage 3) writes any, so this is
    # equivalence-preserving for the current full-close-only world.
    partial_pnl = _partial_close_realized_today(execution_mode, today_start)
    if partial_pnl is None:
        return _unavailable("partial_close_ledger_read_failed", event_count=len(rows))
    realized = round(realized + partial_pnl, 8)
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

    db = get_db()
    _ledger_query = (
        "SELECT pae.position_id, pae.amount_usdt FROM paper_account_events pae "
        "JOIN positions p ON p.id = pae.position_id "
        "WHERE pae.action = 'position_closed' "
        "AND p.status = 'closed' AND p.execution_mode = 'paper_internal' "
        "AND COALESCE(p.outcome_exclusion_reason, '') != ? "
        "AND datetime(p.closed_at) >= datetime(?) "
        "ORDER BY datetime(p.closed_at) ASC, pae.rowid ASC"
    )
    _new_invalid_event_query = (
        "SELECT 1 FROM paper_account_events pae "
        "LEFT JOIN positions p ON p.id = pae.position_id "
        "WHERE pae.action = 'position_closed' "
        "AND datetime(pae.created_at) >= datetime(?) "
        "AND COALESCE(p.outcome_exclusion_reason, '') != ? "
        "AND (p.id IS NULL "
        "OR COALESCE(p.status, '') != 'closed' "
        "OR COALESCE(p.execution_mode, '') != 'paper_internal' "
        "OR p.closed_at IS NULL OR datetime(p.closed_at) IS NULL "
        "OR p.realized_pnl IS NULL "
        "OR ABS(pae.amount_usdt - p.realized_pnl) > 1e-8"
        ") LIMIT 1"
    )
    _unreconciled_invalidation_query = (
        "SELECT pae.position_id FROM paper_account_events pae "
        "JOIN positions p ON p.id=pae.position_id "
        "WHERE p.status='closed' AND p.execution_mode='paper_internal' "
        "AND p.outcome_exclusion_reason=? "
        "AND pae.action IN ('position_closed','manual_credit','manual_debit',"
        "'position_economics_invalidated') "
        "GROUP BY pae.position_id "
        "HAVING SUM(CASE WHEN pae.action='position_closed' THEN 1 ELSE 0 END) > 0 "
        "AND (ABS(SUM(pae.amount_usdt)) > 1e-8 OR "
        "SUM(CASE WHEN pae.action='position_economics_invalidated' THEN 1 ELSE 0 END) != 1) "
        "LIMIT 1"
    )

    def _read_ledger():
        return db.execute(
            _ledger_query, ("invalid_exit_price_evidence", today_start)
        ).fetchall()

    def _has_new_invalid_event() -> bool:
        return db.execute(
            _new_invalid_event_query,
            (today_start, "invalid_exit_price_evidence"),
        ).fetchone() is not None

    def _build_actual(lrows):
        result: dict[str, list[float]] = {}
        for lr in lrows:
            pid = str(lr["position_id"] or "")
            result.setdefault(pid, []).append(float(lr["amount_usdt"]))
        return result

    try:
        ledger_rows = _read_ledger()
        has_new_invalid_event = _has_new_invalid_event()
        has_unreconciled_invalidation = db.execute(
            _unreconciled_invalidation_query,
            ("invalid_exit_price_evidence",),
        ).fetchone() is not None
    except Exception:
        return _unavailable("paper_ledger_read_failed", event_count=len(rows))

    expected = {str(row["id"]): float(row["realized_pnl"]) for row in rows}
    actual = _build_actual(ledger_rows)

    if has_new_invalid_event or has_unreconciled_invalidation:
        return _unavailable("paper_ledger_mismatch", event_count=len(rows))

    if set(actual) != set(expected):
        return _unavailable("paper_ledger_mismatch", event_count=len(rows))

    for position_id, amount in expected.items():
        ledger_amounts = actual.get(position_id, [])
        if len(ledger_amounts) != 1 or abs(ledger_amounts[0] - amount) > 1e-8:
            return _unavailable("paper_ledger_mismatch", event_count=len(rows))

    realized = round(sum(expected.values()), 8)
    # Partial reductions today (see non-paper branch). Zero until Stage 3.
    partial_pnl = _partial_close_realized_today("paper_internal", today_start)
    if partial_pnl is None:
        return _unavailable("partial_close_ledger_read_failed", event_count=len(rows))
    realized = round(realized + partial_pnl, 8)
    return AccountLossSnapshot(
        available=True,
        realized_pnl_usdt=realized,
        loss_usdt=round(max(0.0, -realized), 8),
        source="paper_account_ledger",
        reason="available",
        event_count=len(rows),
    )


def _partial_close_realized_today(execution_mode: str, today_start: str) -> float | None:
    """Realized PnL from PARTIAL close events today (exit-intent R1 Stage 2).

    Full closes are summed from the verified closed-position read; partial reductions
    (is_partial=1) live only in the per-close ledger because the position stays open, so
    they are added here — closing the gap the research flagged (a losing partial reduce
    would otherwise be invisible to the daily-loss breaker). Returns 0.0 when there are
    no partial events (the current world). Returns None on ledger read failure so callers
    can propagate unavailability instead of silently treating the error as zero loss.
    """
    try:
        from aletheia.storage.repositories import list_position_close_events
        events = list_position_close_events(since_iso=today_start, execution_mode=execution_mode)
    except Exception:
        return None
    partial_events = [event for event in events if event.get("is_partial")]
    if any(event.get("realized_pnl") is None for event in partial_events):
        return None
    try:
        values = [float(event["realized_pnl"]) for event in partial_events]
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return round(sum(values), 8)


def _unavailable(reason: str, *, event_count: int = 0) -> AccountLossSnapshot:
    return AccountLossSnapshot(
        available=False,
        realized_pnl_usdt=None,
        loss_usdt=None,
        source="unavailable",
        reason=reason,
        event_count=event_count,
    )

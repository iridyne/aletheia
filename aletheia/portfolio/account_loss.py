"""Authoritative daily realized account economics for the Risk Engine.

This read model is deliberately separate from strategy-performance analytics. A
closed lifecycle row is not account truth unless its exit economics are verified;
local paper results must additionally reconcile to the append-only cash ledger.

## Paper-ledger sync invariant
Two tables must stay in sync for every paper_internal close:

  positions.realized_pnl          — set by the normal close path (pnl.py) or
                                     settle_unsettled_paper_closes() after backfill
  paper_account_events.action=position_closed — written by settle_position_closed()

If the two fall out of sync (e.g. realized_pnl written by a direct DB repair without
calling settle_position_closed), the next daily-loss check sees paper_ledger_mismatch
and blocks all paper trades for the rest of the UTC day.

### How to diagnose
  # Find positions with realized_pnl set but no ledger entry:
  SELECT id, symbol, realized_pnl FROM positions
  WHERE status='closed' AND execution_mode='paper_internal'
    AND realized_pnl IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM paper_account_events pae
                    WHERE pae.action='position_closed' AND pae.position_id=positions.id);

  # Find ledger entries with no matching position:
  SELECT pae.position_id, pae.amount_usdt FROM paper_account_events pae
  WHERE pae.action='position_closed'
    AND NOT EXISTS (SELECT 1 FROM positions p WHERE p.id=pae.position_id);

### How to fix
Call settle_unsettled_paper_closes() — its second pass auto-reconciles orphaned
positions (realized_pnl set, no ledger entry). It runs as part of the paper_funding_settler
scheduled job; to trigger immediately, call it directly from a Python shell.

For same-day positions with realized_pnl=NULL (funding not yet computed), this module
uses a conservative gross-minus-fees estimate (funding=0, gains capped at 0) rather than
blocking trades. The PAPER_PNL_CONSERVATIVE_ESTIMATE warning signals this state; it
self-resolves once paper_funding_settler backfills and settles the position.

### How to prevent
  - Always write realized_pnl via the normal close path (pnl.settle_position_closed).
  - Never write directly to positions.realized_pnl without a corresponding
    settle_position_closed() call.
  - DB repair scripts must call settle_position_closed(position_id, realized_pnl)
    AFTER updating positions.realized_pnl.
"""

from __future__ import annotations

import logging
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


def _conservative_pnl_estimate(row: dict) -> float:
    """Gross PnL minus fees with funding treated as zero.

    Used when a paper_internal position's realized_pnl is still NULL (funding
    computation incomplete).  Gains are capped at 0 because unknown funding can
    flip a small gross gain into a net loss; losses are kept as-is because actual
    funding (paid) makes them no better.  Returns 0.0 on any error — the estimate
    must never raise.
    """
    try:
        entry = float(row["entry_price"])
        exit_p = float(row["current_price"])
        qty = float(row["quantity"])
        direction = str(row.get("direction") or "").lower()
        entry_fee = float(row.get("entry_fee") or 0)
        exit_fee = float(row.get("exit_fee") or 0)
        if direction in {"long", "buy"}:
            gross = (exit_p - entry) * qty
        elif direction in {"short", "sell"}:
            gross = (entry - exit_p) * qty
        else:
            return 0.0
        return round(min(0.0, gross - entry_fee - exit_fee), 8)
    except Exception:
        return 0.0


def _emit_unverifiable_pnl_alert(pending_rows: list[dict]) -> None:
    """Emit a prominent warning when paper positions have unverifiable realized_pnl.

    Logs at WARNING level (visible in the system log) and writes to both the infra
    event bus and errors.log so downstream AI agents and on-call humans can detect
    and investigate the anomaly.
    """
    ids = ", ".join(str(r.get("id", "?")) for r in pending_rows[:5])
    if len(pending_rows) > 5:
        ids += f" … and {len(pending_rows) - 5} more"
    symbols = ", ".join(dict.fromkeys(str(r.get("symbol", "?")) for r in pending_rows))
    msg = (
        f"PAPER_PNL_CONSERVATIVE_ESTIMATE: {len(pending_rows)} paper_internal "
        f"position(s) closed today with realized_pnl=NULL (funding computation "
        f"incomplete). Using gross−fees, funding=0 as conservative daily-loss "
        f"estimate. Symbols: {symbols}. Position IDs: {ids}. "
        f"Auto-recovery: paper_funding_settler retries when funding_rate_snapshots "
        f"are available. Manual fix: run settle_unsettled_paper_closes() or call "
        f"settle_position_closed(position_id, realized_pnl) directly."
    )
    logger.warning(msg)
    try:
        from aletheia.infra.events import emit_infra_event
        emit_infra_event(
            severity="warning",
            source="account_loss",
            code="paper_pnl_conservative_estimate",
            message=msg,
            action=(
                "paper_funding_settler retries automatically. To force recovery: "
                "check funding_rate_snapshots for collection gaps, then run "
                "settle_unsettled_paper_closes(). See account_loss.py module docstring "
                "for the full diagnosis and repair procedure."
            ),
            dedupe_sec=300,
        )
    except Exception:
        pass
    try:
        from aletheia.files.jsonl import write_error
        write_error({"source": "account_loss", "message": msg})
    except Exception:
        pass


def authoritative_account_realized_loss(execution_mode: str) -> AccountLossSnapshot:
    """Return today's exact signed result, or explicit unavailability.

    For paper_internal positions, an unknown close is never silently treated as
    zero loss. When funding economics are still pending (realized_pnl=NULL), a
    conservative gross-minus-fees estimate (funding=0, gains capped at 0) is used
    rather than blocking all trades. A prominent warning is emitted so AI agents
    and humans can detect and investigate the anomaly.

    For all other modes an unknown close blocks the check — Risk decides how that
    affects proposal approval.
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

    settled_rows: list[dict] = []
    pending_rows: list[dict] = []  # paper_internal only: realized_pnl IS NULL

    for row in rows:
        assessment = assess_position_closure(row)
        if not assessment.eligible:
            # Block on: truly unclassifiable closes (legacy_unverified_close),
            # administrative/maintenance closes (not real trade economics),
            # and any ineligible close without a recorded realized_pnl (non-paper).
            # Structured-but-ineligible closes with computable realized_pnl
            # (e.g. unverified_exit_fill from demo mode) pass through to the sum.
            if (
                assessment.reason == "legacy_unverified_close"
                or assessment.category in {"administrative", "maintenance"}
            ):
                return _unavailable(assessment.reason, event_count=len(rows))
            if row.get("realized_pnl") is None:
                if execution_mode == "paper_internal":
                    # Funding not yet computed: use conservative estimate, don't block.
                    pending_rows.append(row)
                    continue
                return _unavailable(assessment.reason, event_count=len(rows))
        if row.get("realized_pnl") is None and execution_mode == "paper_internal":
            # Eligible position still awaiting funding settlement — conservative path.
            pending_rows.append(row)
        else:
            settled_rows.append(row)

    if pending_rows:
        _emit_unverifiable_pnl_alert(pending_rows)

    conservative_sum = round(sum(_conservative_pnl_estimate(r) for r in pending_rows), 8)

    if execution_mode == "paper_internal":
        # _paper_ledger_snapshot only sees settled rows; pending rows have no ledger
        # entry yet (settle_position_closed is only called after realized_pnl is set),
        # so their absence from both expected and actual never triggers a mismatch.
        snapshot = _paper_ledger_snapshot(settled_rows, today_start)
        if not snapshot.available:
            return snapshot
        if not pending_rows:
            return snapshot  # clean path — preserve original source/reason labels
        total = round((snapshot.realized_pnl_usdt or 0.0) + conservative_sum, 8)
        return AccountLossSnapshot(
            available=True,
            realized_pnl_usdt=total,
            loss_usdt=round(max(0.0, -total), 8),
            source="paper_account_ledger_partial_estimate",
            reason="available_with_conservative_estimates",
            event_count=len(rows),
        )

    realized = round(sum(float(row["realized_pnl"]) for row in settled_rows), 8) + conservative_sum
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


def _inline_reconcile_orphans(orphaned_ids: set, row_by_id: dict) -> None:
    """Write missing position_closed entries for positions whose realized_pnl is already set.

    Called when _paper_ledger_snapshot finds positions with realized_pnl in the
    positions table but no corresponding entry in paper_account_events.  This
    auto-repairs the gap inline — during the daily-loss check itself — instead of
    waiting for the next paper_funding_settler run.

    Root cause: realized_pnl was written to positions (e.g. a DB repair) without
    calling settle_position_closed().  Prevention: always go through the normal
    close path or explicitly call settle_position_closed() after any direct write.
    """
    from aletheia.portfolio.paper_account import settle_position_closed
    from aletheia.files.jsonl import write_audit, write_error

    for position_id in sorted(orphaned_ids):
        row = row_by_id.get(position_id)
        if row is None:
            continue
        realized_pnl = row.get("realized_pnl")
        if realized_pnl is None:
            continue
        sym = str(row.get("symbol") or "?")
        try:
            settle_position_closed(position_id, float(realized_pnl))
            msg = (
                f"PAPER_LEDGER_INLINE_RECONCILE: wrote missing position_closed entry "
                f"for {sym} position {position_id} (realized_pnl={realized_pnl}). "
                f"Cause: positions.realized_pnl was set without calling "
                f"settle_position_closed(). Always use the normal close path or call "
                f"settle_position_closed() after any direct DB write."
            )
            logger.warning(msg)
            write_audit({
                "actor": "system",
                "source": "account_loss",
                "event_type": "paper_ledger_inline_reconcile",
                "entity_type": "position",
                "entity_id": position_id,
                "message": msg,
            })
            write_error({"source": "account_loss", "message": msg})
        except Exception as exc:
            logger.warning(
                "Inline reconcile failed for %s position %s: %s", sym, position_id, exc,
            )


def _paper_ledger_snapshot(rows: list[dict], today_start: str) -> AccountLossSnapshot:
    from aletheia.storage.sqlite import get_db

    db = get_db()
    _ledger_query = (
        "SELECT position_id, amount_usdt FROM paper_account_events "
        "WHERE action = 'position_closed' "
        "AND datetime(created_at) >= datetime(?) "
        "ORDER BY datetime(created_at) ASC, rowid ASC"
    )

    def _read_ledger():
        return db.execute(_ledger_query, (today_start,)).fetchall()

    def _build_actual(lrows):
        result: dict[str, list[float]] = {}
        for lr in lrows:
            pid = str(lr["position_id"] or "")
            result.setdefault(pid, []).append(float(lr["amount_usdt"]))
        return result

    try:
        ledger_rows = _read_ledger()
    except Exception:
        return _unavailable("paper_ledger_read_failed", event_count=len(rows))

    expected = {str(row["id"]): float(row["realized_pnl"]) for row in rows}
    actual = _build_actual(ledger_rows)

    if set(actual) != set(expected):
        orphaned = set(expected) - set(actual)  # realized_pnl set, ledger entry missing
        phantom = set(actual) - set(expected)   # ledger entry exists, no matching position

        if phantom:
            # Ledger has entries we cannot explain — genuinely anomalous, block.
            return _unavailable("paper_ledger_mismatch", event_count=len(rows))

        if orphaned:
            # Auto-repair: write the missing ledger entries right now, then recheck.
            # This eliminates the window between the orphan being created and the
            # next paper_funding_settler run.
            row_by_id = {str(r["id"]): r for r in rows}
            _inline_reconcile_orphans(orphaned, row_by_id)
            try:
                ledger_rows = _read_ledger()
            except Exception:
                return _unavailable("paper_ledger_read_failed", event_count=len(rows))
            actual = _build_actual(ledger_rows)
            if set(actual) != set(expected):
                # Reconciliation didn't fully close the gap — block as before.
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

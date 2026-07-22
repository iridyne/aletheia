"""Cash ledger and affordability view for the local ``paper_internal`` account."""

from datetime import datetime, timezone
import logging
import math

from aletheia.storage import repositories as repo
from aletheia.storage.models import PaperAccountEvent

logger = logging.getLogger(__name__)

# Funding-computation failure reasons that can be resolved by fetching historical
# exchange data — as opposed to structural problems (invalid direction, missing fills).
_BACKFILLABLE_REASONS = frozenset({
    "missing_event_observation",
    "missing_schedule_transition",
    "missing_close_anchor",
    "missing_open_anchor",
    "stale_open_anchor",
})

# After this age past `closed_at`, an unsettled paper_internal futures position
# whose funding economics still cannot be reconstructed transitions to a
# terminal "gave up" state. Rationale: paper_funding_settler runs every 15
# minutes, so 24h is ~96 retry attempts — enough to survive any transient
# collector/proxy outage. Longer waits only stall the UI without new evidence.
# The number stays NULL (funding_cost is NEVER fabricated as 0 — see the
# outcome_integrity contract); only the reason we surface changes.
_FUNDING_GIVE_UP_HOURS = 24.0

# Terminal reason surfaced by paper_funding_settler when a position has been
# unsettled past _FUNDING_GIVE_UP_HOURS. Distinct from "incomplete_funding_
# economics" (which reads as "still trying"). Not a whitelist for
# validated_funding_economics — that layer still returns (None, "unavailable").
FUNDING_PERMANENTLY_UNRECOVERABLE = "funding_permanently_unrecoverable"

INITIAL_PAPER_CASH_USDT = 200.0


def _open_local_positions() -> list[dict]:
    return [
        position for position in repo.list_all_open_positions()
        if position.get("execution_mode") == "paper_internal"
    ]


def _unsettled_local_closes() -> list[dict]:
    """Closed local positions whose net account result is still in flight.

    A row that the settler has terminally given up on
    (outcome_exclusion_reason=FUNDING_PERMANENTLY_UNRECOVERABLE) is NOT
    in flight — it is a durable, distinct outcome. Excluding it here lets
    `get_paper_account().economics_complete` return True once every
    remaining unsettled row is either settled or terminally excluded,
    which in turn unblocks `total_value_usdt` / `available_balance_usdt` /
    `equity_usdt` in `compute_portfolio` (Overview hero card previously
    stuck at "— USDT" whenever any historical close remained NULL after
    the settler retry budget ran out).
    """
    from aletheia.portfolio.pnl import _realized_economics_value
    from aletheia.storage.sqlite import get_db

    return [
        position
        for row in get_db().execute(
            "SELECT * FROM positions "
            "WHERE status='closed' AND execution_mode='paper_internal' "
            "ORDER BY rowid DESC"
        )
        for position in [dict(row)]
        if _realized_economics_value(position) is None
        and str(position.get("outcome_exclusion_reason") or "")
            != FUNDING_PERMANENTLY_UNRECOVERABLE
    ]


def _reserved_margin_usdt(positions: list[dict]) -> float:
    total = 0.0
    for position in positions:
        leverage = max(float(position.get("leverage") or 1), 1.0)
        total += float(position["quantity"]) * float(position["entry_price"]) / leverage
    return round(total, 8)


def _write_event(*, action: str, amount_usdt: float, actor: str, source: str,
                 position_id: str | None = None, note: str = "") -> PaperAccountEvent:
    if not math.isfinite(amount_usdt):
        raise ValueError("paper account event amount must be finite")
    # SQLite is the single serialization authority for the ledger.  This helper
    # may be called from an existing Risk/entry/close transaction; transaction()
    # joins that writer instead of taking a process lock in the opposite order.
    with repo.transaction(immediate=True):
        before = repo.get_paper_account_cash_balance()
        if not math.isfinite(before):
            raise RuntimeError("paper account cash balance is not finite")
        after = round(before + amount_usdt, 8)
        if not math.isfinite(after):
            raise ValueError("paper account event balance must remain finite")
        if after < -1e-8:
            raise ValueError("paper account cash balance cannot be negative")
        event = PaperAccountEvent(
            action=action, amount_usdt=round(amount_usdt, 8),
            cash_balance_before_usdt=round(before, 8),
            cash_balance_after_usdt=max(0.0, after), actor=actor, source=source,
            position_id=position_id, note=note,
        )
        repo.insert_paper_account_event(event)
    return event


def ensure_paper_account() -> None:
    """Create exactly one starting-funds event, without reviving deliberate zero cash."""
    if repo.has_paper_account_event():
        return
    with repo.transaction(immediate=True):
        # Recheck after owning SQLite's writer slot so concurrent cold readers
        # cannot both seed the account.
        if not repo.has_paper_account_event():
            _write_event(
                action="initial_funding", amount_usdt=INITIAL_PAPER_CASH_USDT,
                actor="system", source="paper_account_bootstrap",
                note="Initial local paper trading balance",
            )


def get_paper_account(event_limit: int = 20) -> dict:
    ensure_paper_account()
    positions = _open_local_positions()
    cash = repo.get_paper_account_cash_balance()
    reserved = _reserved_margin_usdt(positions)
    unrealized = sum(float(position.get("unrealized_pnl") or 0.0) for position in positions)
    unsettled = _unsettled_local_closes()
    economics_complete = not unsettled
    return {
        "cash_balance_usdt": round(cash, 8),
        "reserved_margin_usdt": reserved,
        # The current cash balance is authoritative even when a futures close has
        # unsettled funding economics.  Only the *future* funding-cost delta is
        # unknown; the ledger cash reflects what is actually in the account today.
        # economics_complete=False is a monitoring signal — it must not zero out
        # the spendable balance and trigger false account_equity_unavailable rejects
        # in the Risk engine (#316).
        "available_balance_usdt": round(max(0.0, cash - reserved), 8),
        "equity_usdt": round(cash + unrealized, 8),
        "economics_complete": economics_complete,
        "unsettled_closed_positions_count": len(unsettled),
        "open_positions_count": len(positions),
        "events": repo.list_paper_account_events(limit=event_limit),
    }


def adjust_cash(amount_usdt: float, *, note: str = "", actor: str = "human",
                source: str = "dashboard") -> dict:
    if not math.isfinite(amount_usdt):
        raise ValueError("paper account adjustment must be finite")
    if amount_usdt == 0:
        raise ValueError("paper account adjustment must be non-zero")
    ensure_paper_account()
    action = "manual_credit" if amount_usdt > 0 else "manual_debit"
    _write_event(
        action=action, amount_usdt=amount_usdt, actor=actor,
        source=source, note=note,
    )
    return get_paper_account()


def set_cash_balance(amount_usdt: float, *, note: str = "", actor: str = "human",
                     source: str = "dashboard") -> dict:
    if not math.isfinite(amount_usdt):
        raise ValueError("paper account cash balance must be finite")
    if amount_usdt < 0:
        raise ValueError("paper account cash balance must be non-negative")
    ensure_paper_account()
    with repo.transaction(immediate=True):
        delta = round(amount_usdt - repo.get_paper_account_cash_balance(), 8)
        _write_event(action="manual_set_balance", amount_usdt=delta, actor=actor,
                     source=source, note=note)
    return get_paper_account()


def record_position_opened(position_id: str) -> None:
    ensure_paper_account()
    with repo.transaction(immediate=True):
        if not repo.has_paper_account_position_event("position_opened", position_id):
            _write_event(action="position_opened", amount_usdt=0.0, actor="system",
                         source="paper_broker", position_id=position_id,
                         note="Local paper position opened")


def settle_position_closed(position_id: str, realized_pnl: float) -> None:
    """Record local realized PnL once after the normal close path succeeds."""
    ensure_paper_account()
    with repo.transaction(immediate=True):
        if not repo.has_paper_account_position_event("position_closed", position_id):
            _write_event(action="position_closed", amount_usdt=realized_pnl, actor="system",
                         source="portfolio_pnl", position_id=position_id,
                         note="Local paper position realized PnL")


def _settle_position_economics(
    position_id: str,
    realized_pnl: float,
    funding_cost: float,
    outcome_eligible: bool,
    outcome_exclusion_reason: str,
) -> None:
    from aletheia.storage.sqlite import get_db
    with repo.transaction(immediate=True):
        get_db().execute(
            """UPDATE positions SET
               realized_pnl=?, funding_cost=?, funding_source='local_simulation',
               outcome_eligible=?, outcome_exclusion_reason=?
               WHERE id=?""",
            (realized_pnl, funding_cost, int(outcome_eligible),
             outcome_exclusion_reason, position_id),
        )
        settle_position_closed(position_id, realized_pnl)


async def _backfill_historical_funding_for_position(row: dict) -> int:
    """Fetch and store historical funding snapshots for a position's symbol+window.

    Used by settle_unsettled_paper_closes() when compute_local_funding() fails
    due to a collection gap.  The inserted rows use the event+transition anchor
    schema (see _insert_historical_funding_snapshot_pair) so they are immediately
    usable by _load_scheduled_snapshots().

    Returns the number of new rows inserted.
    """
    from aletheia.data.collectors.derivatives import _insert_historical_funding_snapshot_pair
    from aletheia.data.ccxt_provider import get_market_data_provider
    from aletheia.data.symbols import to_ccxt_perp

    sym = str(row.get("symbol") or "")
    if not sym:
        return 0
    provider = None
    try:
        provider = get_market_data_provider()
        exchange = provider._ensure_primary()
        exchange_name = provider._primary_name
        perp_sym = to_ccxt_perp(sym)
        history = await exchange.fetch_funding_rate_history(perp_sym, limit=48)
    except Exception as exc:
        logger.debug("paper_funding_settler: funding history fetch failed for %s: %s", sym, exc)
        return 0
    finally:
        if provider is not None:
            try:
                await provider.close()
            except Exception:
                pass

    valid_items = sorted(
        [(item["timestamp"], float(item["fundingRate"]))
         for item in history
         if item.get("timestamp") is not None and item.get("fundingRate") is not None],
        key=lambda x: x[0],
    )
    n = 0
    for i, (ts_ms, rate) in enumerate(valid_items):
        next_ts_ms = valid_items[i + 1][0] if i + 1 < len(valid_items) else None
        try:
            n += _insert_historical_funding_snapshot_pair(
                sym, exchange_name, ts_ms, rate, next_ts_ms=next_ts_ms,
            )
        except Exception as exc:
            logger.debug("paper_funding_settler: snapshot insert failed: %s", exc)
    return n


async def settle_unsettled_paper_closes() -> dict:
    """Retry funding computation for paper_internal futures with realized_pnl=NULL.

    Scans for closed local-paper usdt_futures positions whose funding economics
    are still unknown (funding_source='unavailable', realized_pnl IS NULL).
    For each, re-runs compute_local_funding().  If that still fails due to a
    collection gap, fetches historical funding from the exchange and retries.
    On success, updates the position and settles the cash ledger.

    Designed to run as a periodic maintenance job after a collection outage.
    Returns {'settled': int, 'still_pending': int, 'errors': list[str]}.
    """
    from aletheia.storage.sqlite import get_db
    from aletheia.portfolio.local_funding import compute_local_funding
    from aletheia.analytics.outcome_integrity import outcome_write_eligibility
    from aletheia.files.jsonl import write_audit

    db = get_db()
    # Exclude already-terminal ("gave up") rows so the settler stops touching
    # them on every tick. They stay realized_pnl=NULL forever — only the
    # surfaced reason distinguishes "still trying" from "gave up".
    rows = [
        dict(r) for r in db.execute(
            """SELECT * FROM positions
               WHERE status='closed' AND execution_mode='paper_internal'
                 AND market_type='usdt_futures' AND realized_pnl IS NULL
                 AND (outcome_exclusion_reason IS NULL
                      OR outcome_exclusion_reason != ?)
               ORDER BY rowid ASC LIMIT 50""",
            (FUNDING_PERMANENTLY_UNRECOVERABLE,),
        ).fetchall()
    ]

    settled = 0
    still_pending = 0
    gave_up = 0
    errors: list[str] = []
    now_utc = datetime.now(timezone.utc)

    for row in rows:
        position_id = str(row.get("id") or "")
        sym = str(row.get("symbol") or "")
        try:
            result = compute_local_funding(row, closed_at=row["closed_at"])
            if not result.complete and result.evidence.get("reason") in _BACKFILLABLE_REASONS:
                inserted = await _backfill_historical_funding_for_position(row)
                if inserted:
                    result = compute_local_funding(row, closed_at=row["closed_at"])

            if not result.complete:
                # Age-based give-up: past _FUNDING_GIVE_UP_HOURS since close,
                # the funding gap is structurally unrecoverable (see
                # local_funding.py docstring on close_anchor). Surface a
                # distinct terminal reason so UI/consumers stop reading it
                # as "still retrying". realized_pnl stays NULL — we do NOT
                # fabricate a number.
                closed_at_str = str(row.get("closed_at") or "").strip()
                age_hours: float | None = None
                if closed_at_str:
                    try:
                        parsed = datetime.fromisoformat(
                            closed_at_str.replace("Z", "+00:00")
                        )
                        if parsed.tzinfo is not None:
                            age_hours = (now_utc - parsed).total_seconds() / 3600.0
                    except ValueError:
                        age_hours = None
                if age_hours is not None and age_hours >= _FUNDING_GIVE_UP_HOURS:
                    reason_before = str(row.get("outcome_exclusion_reason") or "")
                    with repo.transaction(immediate=True):
                        get_db().execute(
                            "UPDATE positions SET outcome_exclusion_reason=?, "
                            "outcome_eligible=0 WHERE id=?",
                            (FUNDING_PERMANENTLY_UNRECOVERABLE, position_id),
                        )
                    write_audit({
                        "actor": "system",
                        "source": "paper_funding_settler",
                        "event_type": "paper_position_funding_gave_up",
                        "entity_type": "position",
                        "entity_id": position_id,
                        "before": {"outcome_exclusion_reason": reason_before},
                        "after": {
                            "outcome_exclusion_reason": (
                                FUNDING_PERMANENTLY_UNRECOVERABLE
                            ),
                        },
                        "message": (
                            f"Funding economics unrecoverable for {sym} "
                            f"position {position_id} after {age_hours:.1f}h; "
                            "realized_pnl stays NULL, outcome ineligible"
                        ),
                    })
                    logger.warning(
                        "paper_funding_settler: gave up on %s position %s "
                        "after %.1fh (reason=%s)",
                        sym, position_id, age_hours,
                        result.evidence.get("reason"),
                    )
                    gave_up += 1
                    continue
                still_pending += 1
                continue

            entry = float(row["entry_price"])
            exit_p = float(row["current_price"])
            qty = float(row["quantity"])
            efee = float(row["entry_fee"])
            xfee = float(row["exit_fee"])
            funding_cost = result.funding_cost

            direction = str(row.get("direction") or "").lower()
            if direction in {"long", "buy"}:
                gross = (exit_p - entry) * qty
            elif direction in {"short", "sell"}:
                gross = (entry - exit_p) * qty
            else:
                errors.append(f"{position_id}: invalid direction {direction!r}")
                still_pending += 1
                continue

            realized_pnl = round(gross - efee - xfee - funding_cost, 8)
            if not math.isfinite(realized_pnl):
                errors.append(f"{position_id}: non-finite realized_pnl")
                still_pending += 1
                continue

            check_row = {**row, "realized_pnl": realized_pnl, "funding_cost": funding_cost,
                         "funding_source": "local_simulation", "exit_fill_verified": True}
            outcome_eligible, outcome_reason = outcome_write_eligibility(
                check_row, exit_fill_verified=True,
            )
            _settle_position_economics(position_id, realized_pnl, funding_cost,
                                       outcome_eligible, outcome_reason)
            write_audit({
                "actor": "system",
                "source": "paper_funding_settler",
                "event_type": "paper_position_funding_settled",
                "entity_type": "position",
                "entity_id": position_id,
                "before": {"realized_pnl": None, "funding_source": "unavailable"},
                "after": {"realized_pnl": realized_pnl, "funding_source": "local_simulation"},
                "message": (
                    f"Auto-settled {sym} paper_internal position {position_id}: "
                    f"realized_pnl={realized_pnl}"
                ),
            })
            logger.info(
                "paper_funding_settler: settled %s position %s realized_pnl=%s",
                sym, position_id, realized_pnl,
            )
            settled += 1

        except Exception as exc:
            logger.warning(
                "paper_funding_settler: error settling %s position %s: %s",
                sym, position_id, exc,
            )
            errors.append(f"{position_id}: {exc}")
            still_pending += 1

    # Second pass: positions where realized_pnl IS already set but the cash ledger
    # has no matching position_closed entry.  This happens when funding_cost is written
    # to the positions table out-of-band (e.g. a DB repair) without going through the
    # normal close path that calls settle_position_closed().  A missing ledger entry
    # causes paper_ledger_mismatch in authoritative_account_realized_loss, which blocks
    # every subsequent paper trade for the rest of the UTC day.
    orphaned = [
        dict(r) for r in db.execute(
            """SELECT * FROM positions
               WHERE status='closed' AND execution_mode='paper_internal'
                 AND realized_pnl IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM paper_account_events pae
                     WHERE pae.action = 'position_closed' AND pae.position_id = positions.id
                 )
               ORDER BY rowid ASC LIMIT 50"""
        ).fetchall()
    ]
    for row in orphaned:
        position_id = str(row.get("id") or "")
        sym = str(row.get("symbol") or "")
        realized_pnl = float(row["realized_pnl"])
        try:
            settle_position_closed(position_id, realized_pnl)
            write_audit({
                "actor": "system",
                "source": "paper_funding_settler",
                "event_type": "paper_ledger_orphan_reconciled",
                "entity_type": "position",
                "entity_id": position_id,
                "before": {"paper_account_events_position_closed": False},
                "after": {"paper_account_events_position_closed": True},
                "message": (
                    f"Reconciled orphaned ledger entry for {sym} paper_internal "
                    f"position {position_id}: realized_pnl={realized_pnl}"
                ),
            })
            logger.info(
                "paper_funding_settler: reconciled orphaned ledger for %s position %s",
                sym, position_id,
            )
            settled += 1
        except Exception as exc:
            logger.warning(
                "paper_funding_settler: failed to reconcile orphaned ledger for %s "
                "position %s: %s",
                sym, position_id, exc,
            )
            errors.append(f"{position_id}:ledger_orphan: {exc}")

    return {
        "settled": settled,
        "still_pending": still_pending,
        "gave_up": gave_up,
        "errors": errors,
    }

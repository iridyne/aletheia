"""Cash ledger and affordability view for the local ``paper_internal`` account."""

from datetime import datetime, timezone
import hashlib
import json
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

_STRUCTURALLY_UNRECOVERABLE_REASONS = frozenset({
    "invalid_direction",
    "invalid_window",
    "unsupported_position",
    "missing_entry_identity",
    "missing_entry_orders",
    "missing_primary_entry_order",
    "entry_identity_mismatch",
    "entry_quantity_mismatch",
    "entry_price_mismatch",
    "entry_fill_before_open",
    "primary_entry_time_mismatch",
    "entry_fill_on_settlement_boundary",
})

# After this age past `closed_at`, only an explicit structural lineage failure
# may transition to terminal "gave up". The 30-minute settler therefore gets up
# to ~48 observations before surfacing a stable structural reason. Missing
# source history or elapsed time alone never proves permanent loss. The number
# stays NULL (funding_cost is NEVER fabricated as 0); only the reason changes.
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


def _economics_fingerprint(funding_evidence: dict) -> str:
    payload = json.dumps(
        funding_evidence, sort_keys=True, separators=(",", ":"), default=str,
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def materialize_position_economics(
    position_id: str,
    *,
    realized_pnl: float,
    funding_cost: float,
    funding_evidence: dict,
    outcome_eligible: bool,
    outcome_exclusion_reason: str,
    actor: str = "system",
    source: str = "paper_funding_settler",
    allow_correction: bool = False,
) -> dict:
    """Atomically converge every durable projection of one paper close."""
    from aletheia.files.jsonl import write_audit
    from aletheia.portfolio.outcome_hooks import (
        SERENITY_POST_MORTEM_HOOK,
        SHADOW_TRADES_HOOK,
        enqueue_outcome_hook_jobs,
    )
    from aletheia.storage.models import AuditEvent
    from aletheia.storage.sqlite import get_db

    if not isinstance(funding_evidence, dict) or funding_evidence.get("status") != "complete":
        raise ValueError("complete funding evidence is required")
    realized = round(float(realized_pnl), 8)
    funding = float(funding_cost)
    if not math.isfinite(realized) or not math.isfinite(funding):
        raise ValueError("position economics must be finite")

    ensure_paper_account()
    fingerprint = _economics_fingerprint(funding_evidence)
    audit_payload: dict | None = None
    status = "settled"
    compensation = 0.0

    with repo.transaction(immediate=True):
        db = get_db()
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        if (
            current.get("status") != "closed"
            or current.get("execution_mode") != "paper_internal"
            or current.get("market_type") != "usdt_futures"
        ):
            raise ValueError("economics materialization requires a closed paper future")

        evidence_root: dict = {}
        raw_evidence = current.get("reconciliation_evidence")
        if raw_evidence:
            try:
                parsed = json.loads(str(raw_evidence))
                if isinstance(parsed, dict):
                    evidence_root = parsed
            except (TypeError, ValueError, json.JSONDecodeError):
                evidence_root = {}
        previous_fingerprint = evidence_root.get("funding_economics_fingerprint")
        previous_realized = current.get("realized_pnl")
        close_events = [
            dict(row) for row in db.execute(
                "SELECT * FROM position_close_events WHERE position_id=? "
                "AND is_partial=0 ORDER BY rowid",
                (position_id,),
            ).fetchall()
        ]
        if len(close_events) != 1:
            raise ValueError("exactly one full close event is required")

        cash_events = [dict(row) for row in db.execute(
            "SELECT * FROM paper_account_events WHERE position_id=? AND action IN "
            "('position_closed','manual_credit','manual_debit') ORDER BY rowid",
            (position_id,),
        ).fetchall()]
        cash_total = round(
            sum(float(event["amount_usdt"]) for event in cash_events), 8,
        )
        expected_hooks: set[str] = set()
        if outcome_eligible:
            expected_hooks.add(SHADOW_TRADES_HOOK)
            if current.get("trade_school") == "serenity" and current.get("opportunity_id"):
                expected_hooks.add(SERENITY_POST_MORTEM_HOOK)
        existing_hooks = {
            str(item["hook_type"])
            for item in repo.list_outcome_hook_jobs(position_id=position_id, limit=20)
        }
        same_realized = previous_realized is not None and math.isclose(
            float(previous_realized), realized, rel_tol=0.0, abs_tol=1e-8,
        )
        same_funding = current.get("funding_cost") is not None and math.isclose(
            float(current["funding_cost"]), funding, rel_tol=0.0, abs_tol=1e-12,
        )
        same_fingerprint = previous_fingerprint == fingerprint
        if same_fingerprint and previous_realized is not None and not (
            same_realized and same_funding
        ):
            raise ValueError("same funding fingerprint produced different economics")
        close_projection_current = (
            same_fingerprint
            and same_realized
            and same_funding
            and math.isclose(
                float(close_events[0]["realized_pnl"]), realized,
                rel_tol=0.0, abs_tol=1e-8,
            ) if close_events[0].get("realized_pnl") is not None else False
        )
        projections_current = (
            close_projection_current
            and any(event["action"] == "position_closed" for event in cash_events)
            and math.isclose(cash_total, realized, rel_tol=0.0, abs_tol=1e-8)
            and bool(current.get("outcome_eligible")) == bool(outcome_eligible)
            and current.get("outcome_exclusion_reason") == outcome_exclusion_reason
            and expected_hooks.issubset(existing_hooks)
        )
        if projections_current:
            return {
                "position_id": position_id,
                "status": "already_current",
                "realized_pnl": realized,
                "funding_cost": funding,
                "fingerprint": fingerprint,
                "cash_compensation": 0.0,
            }
        if same_fingerprint and same_realized and same_funding:
            status = "repaired"
        if previous_realized is not None and not same_realized and not allow_correction:
            raise ValueError("numeric economics correction requires explicit approval")

        evidence_root.update({
            "funding": funding_evidence,
            "funding_economics_fingerprint": fingerprint,
            "funding_accounting_version": funding_evidence.get(
                "accounting_version", "local-paper-funding-v2",
            ),
        })
        repo.update_position(
            position_id,
            realized_pnl=realized,
            funding_cost=funding,
            funding_source="local_simulation",
            outcome_eligible=outcome_eligible,
            outcome_exclusion_reason=outcome_exclusion_reason,
            reconciliation_evidence=json.dumps(
                evidence_root, sort_keys=True, separators=(",", ":"), default=str,
            ),
        )
        db.execute(
            "UPDATE position_close_events SET realized_pnl=? WHERE id=?",
            (realized, close_events[0]["id"]),
        )

        cash_event = next(
            (event for event in cash_events if event["action"] == "position_closed"),
            None,
        )
        if cash_event is None:
            if cash_events:
                raise ValueError("paper cash adjustments exist without initial settlement")
            _write_event(
                action="position_closed",
                amount_usdt=realized,
                actor=actor,
                source=source,
                position_id=position_id,
                note="Local paper position realized PnL after funding finalization",
            )
        else:
            compensation = round(realized - cash_total, 8)
            if abs(compensation) > 1e-8:
                action = "manual_credit" if compensation > 0 else "manual_debit"
                existing_adjustment = next(
                    (
                        event for event in cash_events
                        if event["action"] in {"manual_credit", "manual_debit"}
                    ),
                    None,
                )
                if existing_adjustment is not None:
                    raise ValueError("position economics compensation already exists")
                _write_event(
                    action=action,
                    amount_usdt=compensation,
                    actor=actor,
                    source=source,
                    position_id=position_id,
                    note=(
                        "Owner-approved position economics compensation "
                        f"{fingerprint}"
                    ),
                )
                status = "corrected"

        if outcome_eligible:
            enqueue_outcome_hook_jobs({**current, **{
                "realized_pnl": realized,
                "funding_cost": funding,
                "funding_source": "local_simulation",
                "outcome_eligible": True,
                "outcome_exclusion_reason": outcome_exclusion_reason,
            }})

        before = {
            "realized_pnl": previous_realized,
            "funding_cost": current.get("funding_cost"),
            "outcome_eligible": bool(current.get("outcome_eligible")),
            "funding_economics_fingerprint": previous_fingerprint,
        }
        after = {
            "realized_pnl": realized,
            "funding_cost": funding,
            "outcome_eligible": bool(outcome_eligible),
            "outcome_exclusion_reason": outcome_exclusion_reason,
            "funding_economics_fingerprint": fingerprint,
            "cash_compensation": compensation,
        }
        event_type = {
            "corrected": "paper_position_economics_corrected",
            "repaired": "paper_position_economics_repaired",
        }.get(status, "paper_position_funding_settled")
        repo.insert_audit_event(AuditEvent(
            actor=actor,
            source=source,
            event_type=event_type,
            entity_type="position",
            entity_id=position_id,
            before_state=json.dumps(before, sort_keys=True),
            after_state=json.dumps(after, sort_keys=True),
            message=(
                f"Materialized paper funding economics for {position_id}; "
                f"realized_pnl={realized}; compensation={compensation}"
            ),
        ))
        audit_payload = {
            "actor": actor,
            "source": source,
            "event_type": event_type,
            "entity_type": "position",
            "entity_id": position_id,
            "before": before,
            "after": after,
            "message": f"Materialized paper funding economics for {position_id}",
        }

    if audit_payload is not None:
        try:
            write_audit(audit_payload, mirror_db=False)
        except Exception as exc:
            logger.error(
                "paper economics JSONL audit sink failed (%s)", type(exc).__name__,
            )
    return {
        "position_id": position_id,
        "status": status,
        "realized_pnl": realized,
        "funding_cost": funding,
        "fingerprint": fingerprint,
        "cash_compensation": compensation,
    }


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

    # Exclude already-terminal ("gave up") rows so the settler stops touching
    # them on every tick. They stay realized_pnl=NULL forever — only the
    # surfaced reason distinguishes "still trying" from "gave up".
    rows = [
        dict(r) for r in get_db().execute(
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
                await _backfill_historical_funding_for_position(row)
                result = compute_local_funding(
                    row,
                    closed_at=row["closed_at"],
                    allow_historical_ledger=True,
                )

            if not result.complete:
                # A known structural lineage failure can be terminalized after
                # the retry window. Age or unavailable source history alone is
                # never sufficient. realized_pnl stays NULL.
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
                structural_reason = str(result.evidence.get("reason") or "")
                if (
                    structural_reason in _STRUCTURALLY_UNRECOVERABLE_REASONS
                    and age_hours is not None
                    and age_hours >= _FUNDING_GIVE_UP_HOURS
                ):
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
            materialize_position_economics(
                position_id,
                realized_pnl=realized_pnl,
                funding_cost=funding_cost,
                funding_evidence=result.evidence,
                outcome_eligible=outcome_eligible,
                outcome_exclusion_reason=outcome_reason,
            )
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
        dict(r) for r in get_db().execute(
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

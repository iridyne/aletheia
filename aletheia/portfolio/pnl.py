"""Portfolio PnL computation + snapshots (guide §7 portfolio/)."""

import logging
import json
import math
from collections.abc import Callable
from datetime import datetime, timezone

from aletheia.analytics.outcome_integrity import validated_funding_economics
from aletheia.storage.models import PortfolioSnapshot

logger = logging.getLogger("aletheia.pnl")
from aletheia.storage.repositories import (
    list_all_open_positions,
    insert_portfolio_snapshot,
    update_position,
    list_market_snapshots,
)


_CLOSE_ECONOMIC_SNAPSHOT_FIELDS = (
    "symbol",
    "market_type",
    "direction",
    "quantity",
    "entry_price",
    "execution_mode",
    "entry_fee",
    "funding_cost",
    "funding_source",
    "slippage_cost",
    "entry_price_source",
    "entry_fill_verified",
)


def _same_snapshot_value(left: object, right: object) -> bool:
    if left == right:
        return True
    return isinstance(left, float) and isinstance(right, float) and math.isnan(left) and math.isnan(right)


def _parse_utc_instant(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate_complete_funding_evidence(
    *,
    row: dict,
    position_id: str,
    closed_at: str,
    funding_cost: object,
    funding_source: object,
    evidence: dict,
) -> None:
    """Reject a claimed-complete funding handoff that cannot be replayed."""
    opened = _parse_utc_instant(row.get("opened_at"))
    closed = _parse_utc_instant(closed_at)
    evidence_opened = _parse_utc_instant(evidence.get("opened_at"))
    evidence_closed = _parse_utc_instant(evidence.get("closed_at"))
    if (
        evidence.get("status") != "complete"
        or evidence.get("source") != funding_source
        or evidence.get("position_id") != position_id
        or evidence.get("symbol") != row.get("symbol")
        or opened is None
        or closed is None
        or evidence_opened != opened
        or evidence_closed != closed
    ):
        raise ValueError("funding evidence identity or holding window does not match")

    events = evidence.get("events")
    if not isinstance(events, list):
        raise ValueError("funding evidence events must be a list")
    event_total = 0.0
    scheduled_instants: list[datetime] = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("funding evidence event must be a mapping")
        scheduled = _parse_utc_instant(event.get("scheduled_at"))
        try:
            event_cost = float(event.get("funding_cost"))
        except (TypeError, ValueError):
            raise ValueError("funding evidence event cost is invalid") from None
        if (
            scheduled is None
            or not (opened < scheduled <= closed)
            or not math.isfinite(event_cost)
            or scheduled in scheduled_instants
        ):
            raise ValueError("funding evidence event lies outside the holding window")
        scheduled_instants.append(scheduled)
        event_total += event_cost
    if scheduled_instants != sorted(scheduled_instants):
        raise ValueError("funding evidence events are not chronological")
    try:
        supplied_total = float(funding_cost)
    except (TypeError, ValueError):
        raise ValueError("funding evidence has no finite total") from None
    tolerance = max(1e-12, abs(supplied_total) * 1e-9)
    if (
        not math.isfinite(supplied_total)
        or not math.isfinite(event_total)
        or abs(event_total - supplied_total) > tolerance
    ):
        raise ValueError("funding evidence event sum does not match funding cost")


def _latest_price(symbol: str) -> float | None:
    from aletheia.data.price_feed import get_price_for_pnl

    return get_price_for_pnl(symbol)


def _btc_price_at_close() -> float | None:
    """Fetch BTC/USDT price for benchmark recording on position close."""
    try:
        from aletheia.trading.market_data import latest_snapshot

        snap = latest_snapshot("BTC/USDT")
        return float(snap["price"]) if snap else None
    except Exception:
        return None


def _close_provenance() -> dict:
    """Return only available producer identity for the close event."""
    try:
        from aletheia.agent_runtime.runtime_session import current_close_provenance

        return {key: value for key, value in current_close_provenance().items() if value is not None}
    except Exception:
        return {}


def _account_environment(execution_mode: str) -> str:
    if execution_mode == "paper_internal":
        return "local"
    if execution_mode == "binance_testnet":
        return "testnet"
    if execution_mode.endswith("_demo"):
        return "demo"
    if execution_mode.endswith("_live_limited"):
        return "live"
    return "unknown"


def _build_exchange_upnl_index() -> dict[tuple[str, str, str], float]:
    """Build exact (canonical_symbol, broker_id, direction) exchange uPnL matches.

    Legacy exchange caches lack broker identity, so they are deliberately not
    used for per-position PnL. Ambiguous duplicate records also fall back to the
    local estimate rather than selecting an arbitrary exposure.
    """
    try:
        from aletheia.data.symbols import to_canonical
        from aletheia.portfolio import exchange_sync

        candidates: dict[tuple[str, str, str], list[float]] = {}
        cached = exchange_sync.get_unified_cached_state()
        for position in (cached or {}).get("positions") or []:
            symbol = to_canonical(position.get("symbol", ""))
            broker_id = str(position.get("broker_id") or "")
            side = str(position.get("side") or "").lower()
            direction = "long" if side in ("long", "buy") else "short" if side in ("short", "sell") else None
            upnl = position.get("unrealized_pnl")
            if not symbol or not broker_id or direction is None or upnl is None:
                continue
            candidates.setdefault((symbol, broker_id, direction), []).append(float(upnl))
        return {
            (symbol, broker_id, direction): values[0]
            for (symbol, broker_id, direction), values in candidates.items()
            if len(values) == 1
        }
    except Exception:
        return {}


def mark_positions_to_market(persist: bool = False) -> list[dict]:
    """Compute unrealized PnL on open positions.

    Prefers exchange uPnL (includes funding fees and mark-price basis) when the
    exchange cache is available. Falls back to the local (price - entry) * qty
    estimate only when the cache is absent or the position has no exchange match.

    When persist=False (default, used by GET /api/portfolio), only computes in
    memory — no DB writes, no WAL lock contention with scheduler threads.
    When persist=True (used by scheduler's portfolio_snapshot job), also writes
    the updated price/PnL back to the positions table.
    """
    positions = list_all_open_positions()
    exc_upnl_index = _build_exchange_upnl_index()
    from aletheia.data.symbols import to_canonical

    # A venue Position is an aggregate exposure. Without a direct broker
    # Position identity, one aggregate uPnL cannot be allocated independently to
    # multiple local Position rows that share the same current match key.
    local_match_counts: dict[tuple[str, str, str], int] = {}
    for position in positions:
        direction = position["direction"]
        norm_dir = "long" if direction in ("long", "buy") else "short"
        match_key = (
            to_canonical(position["symbol"]),
            position.get("execution_mode") or "",
            norm_dir,
        )
        local_match_counts[match_key] = local_match_counts.get(match_key, 0) + 1

    updated = []
    for p in positions:
        price = _latest_price(p["symbol"])
        if price is None:
            price = p.get("current_price") or p["entry_price"]
        qty = p["quantity"]
        entry = p["entry_price"]
        direction = p["direction"]
        norm_dir = "long" if direction in ("long", "buy") else "short"
        # Exchange uPnL is authoritative only for an exact broker-scoped match.
        # A single aggregate remote value is also ambiguous when more than one
        # local Position shares the key; never copy the full value to every row.
        match_key = (
            to_canonical(p["symbol"]), p.get("execution_mode") or "", norm_dir,
        )
        exc_val = (
            exc_upnl_index.get(match_key)
            if local_match_counts.get(match_key) == 1
            else None
        )
        if exc_val is not None:
            upnl = exc_val
        elif direction in ("long", "buy"):
            upnl = (price - entry) * qty
        else:
            upnl = (entry - price) * qty
        if persist:
            update_position(p["id"], current_price=price, unrealized_pnl=upnl)
        rec = dict(p)
        rec["current_price"] = price
        rec["unrealized_pnl"] = upnl
        updated.append(rec)
    return updated


def _realized_pnl(direction: str, entry: float, exit_price: float, qty: float) -> float:
    if direction in ("long", "buy"):
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty  # short/sell


def build_exit_reasoning(
    position: dict,
    *,
    exit_price: float | None,
    reason: str,
    actor: str = "system",
    source: str = "position_lifecycle",
    reasoning: str | None = None,
    is_ai_judgment: bool = False,
    trigger: dict | None = None,
) -> dict:
    """Build an honest close explanation from facts known at close time.

    Mechanical close reasons are deliberately described as rule triggers, not
    new AI opinions.  Callers with an actual System Operator review may provide
    its original text and mark it as an AI judgment.  This function never
    synthesizes an AI justification when none was recorded.
    """
    known_trigger: dict = {"exit_price": exit_price}
    if reason == "stop_loss":
        known_trigger["configured_stop_price"] = position.get("stop_price")
        default_reason = "Protective stop was reached; this is the rule committed at entry, not a new AI judgment."
    elif reason == "take_profit":
        known_trigger["configured_take_profit_price"] = position.get("take_profit_price")
        default_reason = "Take-profit target was reached; this is the rule committed at entry, not a new AI judgment."
    elif reason == "max_hold":
        known_trigger["opened_at"] = position.get("opened_at")
        default_reason = (
            "The configured maximum holding period elapsed; this is an automatic lifecycle rule, not a new AI judgment."
        )
    elif reason == "market_signal_exit":
        known_trigger["market_signal_reason"] = position.get("last_market_signal_reason")
        default_reason = "The market-aware exit rule fired; this is an automatic lifecycle rule, not a new AI judgment."
    elif reason == "human_manual_close":
        default_reason = (
            "The owner manually closed this position from the dashboard; this is a human decision, not an AI judgment."
        )
    elif reason == "human_limit_exit":
        known_trigger["exit_limit_price"] = position.get("exit_limit_price")
        default_reason = (
            "A limit exit level set manually by the owner was reached; this is a human decision, not an AI judgment."
        )
    elif reason == "exchange_bracket_triggered":
        default_reason = "The exchange reported that the pre-existing protective bracket closed the position; no new AI judgment was recorded."
    elif reason in {"verified_exit", "reconciled_gone"}:
        default_reason = (
            "The position was closed from verified reconciliation evidence; no AI trade judgment was recorded."
        )
    else:
        default_reason = (
            "The position was closed through the recorded lifecycle path; no AI trade judgment was recorded."
        )
    if trigger:
        known_trigger.update(trigger)
    text = reasoning.strip()[:1200] if isinstance(reasoning, str) and reasoning.strip() else default_reason
    return {
        "version": "trade_decision_rationale.v1",
        "actor": actor,
        "source": source,
        "reason_code": reason,
        "reason": text,
        "is_ai_judgment": bool(is_ai_judgment),
        "trigger": known_trigger,
    }


def persist_exit_reasoning(
    position_id: str,
    *,
    actor: str,
    source: str,
    reason: str,
    reasoning: str | None,
    is_ai_judgment: bool,
    trigger: dict | None = None,
) -> bool:
    """Replace a generic close explanation only after the position is closed.

    Exchange brokers close through their own adapters, which call
    :func:`close_position` after a confirmed fill.  A later AI-specific write is
    therefore allowed only on an already-closed position; a failed close cannot
    acquire an explanation that would make the dashboard lie.
    """
    from aletheia.storage import repositories as repo

    row = repo._select_one("positions", position_id)
    if row is None or row.get("status") != "closed":
        return False
    payload = build_exit_reasoning(
        row,
        exit_price=row.get("current_price"),
        reason=reason,
        actor=actor,
        source=source,
        reasoning=reasoning,
        is_ai_judgment=is_ai_judgment,
        trigger=trigger,
    )
    update_position(position_id, exit_reasoning=json.dumps(payload, sort_keys=True))
    return True


# Exit fee rate per execution mode. Must match the open-leg constants in each broker
# file so realized PnL is internally consistent regardless of which path opened it.
_EXIT_FEE_BY_MODE: dict[str, float] = {
    "paper_internal": 0.0006,  # matches paper_broker default (Bitget taker)
    "bitget_demo": 0.0006,  # bitget_broker._DEMO_FEE_RATE
    "bitget_live_limited": 0.0006,  # bitget_broker._LIVE_FEE_RATE
    "okx_demo": 0.0005,  # okx_broker._OKX_FEE_RATE
    "okx_live_limited": 0.0005,
    "binance_testnet": 0.0004,  # binance_broker._TESTNET_FEE_RATE
    "binance_live_limited": 0.0004,
}


def _exit_fee_rate(execution_mode: str) -> float:
    return _EXIT_FEE_BY_MODE.get(execution_mode or "", 0.0006)


def close_position(
    position_id: str,
    exit_price: float,
    reason: str = "manual",
    *,
    actual_exit_fee: float | None = None,
    closed_at: str | None = None,
    verified_exit_evidence: dict | None = None,
    materialize_close: Callable[[], None] | None = None,
    actor: str = "system",
    source: str = "scheduler",
    funding_cost: float | None = None,
    funding_source: str | None = None,
    funding_evidence: dict | None = None,
) -> dict:
    """Close an open position at exit_price, realizing PnL. Returns the result.

    Writes a position_closed audit event (trust §1.4). `reason` is recorded as
    close_reason (e.g. stop_loss / take_profit / max_hold / manual). Exchange
    reconciliation may supply the venue-confirmed exit fee, fill timestamp, and
    private evidence so every downstream audit/notification/feedback consumer
    observes the same actual close economics instead of an estimate later being
    written over it. Local paper fills are authoritative within the simulator;
    exchange closes without private evidence remain explicitly ineligible. A
    local broker may pass a database-only ``materialize_close`` callback; it runs
    inside the same transaction as Position and cash settlement, after the
    second open-state check and before any post-commit side effect.  A local
    futures broker may also pass its completed simulated funding amount, source,
    and event evidence.  Those facts are validated and committed with the same
    Position/Fill transaction; callers that omit them retain the legacy behavior
    of reading already-persisted Position funding fields.
    """
    from aletheia.analytics.close_reasons import is_synthetic_close

    if is_synthetic_close(reason):
        raise ValueError("administrative close reasons must use close_position_administrative")
    from aletheia.storage import repositories as repo
    from aletheia.files.jsonl import write_audit

    row = repo._select_one("positions", position_id)
    if row is None:
        raise ValueError(f"position not found: {position_id}")
    if row.get("status") != "open":
        raise ValueError(f"position is not open: {position_id}")

    # Normalize the close boundary before validating any economics that claim
    # to cover this exact holding window.
    supplied_closed_at = closed_at is not None
    if closed_at is None:
        closed_at = datetime.now(timezone.utc).isoformat()
    else:
        parsed_closed_at = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
        if parsed_closed_at.tzinfo is None:
            raise ValueError("closed_at must include an explicit timezone")
        closed_at = parsed_closed_at.astimezone(timezone.utc).isoformat()

    gross_pnl = _realized_pnl(row["direction"], row["entry_price"], exit_price, row["quantity"])
    # Deduct round-trip fees; prefer stored entry_fee (precise) over re-derivation.
    fee_rate = _exit_fee_rate(row.get("execution_mode", ""))
    # Sprint B (QR-2026-07-01-02): entry_fee=0.0 is a legitimate stored value (zero-cost
    # paper fills). Only fall back to re-deriving from the fee schedule when the field is
    # genuinely missing (NULL from pre-Sprint-B positions). Truthiness "or 0" had been
    # recomputing fees on legible zeros and inflating realized PnL.
    entry_fee = row.get("entry_fee")
    if entry_fee is None:
        entry_fee = row["entry_price"] * row["quantity"] * fee_rate
    if actual_exit_fee is None:
        exit_fee = exit_price * row["quantity"] * fee_rate
    else:
        exit_fee = float(actual_exit_fee)
        if not math.isfinite(exit_fee) or exit_fee < 0:
            raise ValueError("actual_exit_fee must be a finite non-negative value")
    # Spot has no funding leg. Futures funding is part of realized economics,
    # but only when it came from a durable broker/local ledger.  An unknown or
    # malformed funding value must not be guessed from current configuration:
    # the lifecycle may still close, while Outcome/Feedback stays fail-closed.
    market_type = str(row.get("market_type") or "")
    explicit_funding = funding_cost is not None or funding_source is not None
    raw_funding_cost = funding_cost if explicit_funding else row.get("funding_cost")
    raw_funding_source = funding_source if explicit_funding else row.get("funding_source")
    if funding_evidence is not None and not isinstance(funding_evidence, dict):
        raise ValueError("funding_evidence must be a mapping")
    evidence_status = funding_evidence.get("status") if isinstance(funding_evidence, dict) else None
    # An explicit incomplete computation always supersedes any stale Position
    # value. A claimed-complete handoff is accepted only when its identity,
    # window, and per-event sum reproduce the supplied total.
    if funding_evidence is not None and evidence_status != "complete":
        raw_funding_cost = None
        raw_funding_source = "unavailable"
    elif evidence_status == "complete":
        _validate_complete_funding_evidence(
            row=row,
            position_id=position_id,
            closed_at=closed_at,
            funding_cost=raw_funding_cost,
            funding_source=raw_funding_source,
            evidence=funding_evidence,
        )
    elif explicit_funding and raw_funding_source == "local_simulation":
        raw_funding_cost = None
        raw_funding_source = "unavailable"
    funding_cost, funding_source = validated_funding_economics(
        market_type=market_type,
        execution_mode=row.get("execution_mode"),
        funding_cost=(0.0 if market_type == "spot" else raw_funding_cost),
        funding_source=("not_applicable" if market_type == "spot" else raw_funding_source),
    )

    # A futures close is operationally complete once its Fill is durable, but
    # its *economic* result is not complete until funding is known.  Persisting
    # gross-minus-fees here used to silently treat unknown funding as zero,
    # publish a reassuring PnL number, and move local-paper cash by that invented
    # amount.  Keep the lifecycle closed while leaving the net result unavailable.
    realized: float | None = None
    if funding_cost is not None:
        realized = gross_pnl - entry_fee - exit_fee - funding_cost

    # BTC benchmark at exit for alpha/beta separation
    btc_exit = _btc_price_at_close()

    execution_mode = str(row.get("execution_mode") or "")
    local_paper_fill = execution_mode == "paper_internal"
    private_exchange_fill = verified_exit_evidence is not None
    if private_exchange_fill:
        if not isinstance(verified_exit_evidence, dict):
            raise ValueError("verified_exit_evidence must be a mapping")
        fill_ids = verified_exit_evidence.get("fill_ids")
        try:
            evidence_quantity = float(verified_exit_evidence.get("fill_quantity"))
        except (TypeError, ValueError):
            evidence_quantity = 0.0
        tolerance = max(1e-12, float(row["quantity"]) * 1e-9)
        if (
            actual_exit_fee is None
            or not supplied_closed_at
            or not isinstance(fill_ids, list)
            or not fill_ids
            or len(fill_ids) != len(set(fill_ids))
            or abs(evidence_quantity - float(row["quantity"])) > tolerance
        ):
            raise ValueError(
                "verified exchange close requires complete private fill identity, quantity, fee, and timestamp"
            )

    exit_fill_verified = local_paper_fill or private_exchange_fill
    from aletheia.analytics.outcome_integrity import (
        OUTCOME_SEMANTICS_VERSION,
        outcome_write_eligibility,
    )
    from aletheia.portfolio.disappearance_confirmation import (
        logical_account_fingerprint,
    )

    account_environment = _account_environment(execution_mode)
    account_fingerprint = logical_account_fingerprint(
        broker_id=execution_mode,
        account_environment=account_environment,
        execution_mode=execution_mode,
    )
    reconciliation_identity = {
        "broker_id": execution_mode,
        "account_environment": account_environment,
        "account_fingerprint": account_fingerprint,
        "execution_mode": execution_mode,
        "position_id": position_id,
        "symbol": row.get("symbol"),
        "market_type": row.get("market_type"),
        "direction": row.get("direction"),
    }
    if local_paper_fill:
        outcome_evidence = {
            "source": "local_paper_fill",
            "fill_id": f"paper-close:{position_id}:{closed_at}",
            "fill_price": float(exit_price),
            "fill_quantity": float(row["quantity"]),
            "fee": float(exit_fee),
            "fee_currency": "USDT",
            "filled_at": closed_at,
        }
    elif private_exchange_fill:
        outcome_evidence = dict(verified_exit_evidence or {})
    else:
        outcome_evidence = {
            "source": "broker_response_unverified",
            "fill_price": float(exit_price),
            "fill_quantity": float(row["quantity"]),
            "fee_estimated": actual_exit_fee is None,
            "closed_at": closed_at,
        }
    if funding_evidence is not None:
        outcome_evidence["funding"] = dict(funding_evidence)
    exit_reasoning = build_exit_reasoning(
        row,
        exit_price=exit_price,
        reason=reason,
    )
    update_fields = {
        "status": "closed",
        "current_price": exit_price,
        "realized_pnl": realized,
        "unrealized_pnl": 0.0,
        "close_reason": reason,
        "closed_at": closed_at,
        "btc_exit_price": btc_exit,
        "entry_fee": round(entry_fee, 8),
        "exit_fee": round(exit_fee, 8),
        "funding_cost": funding_cost,
        "funding_source": funding_source,
        "slippage_cost": (row.get("slippage_cost") if local_paper_fill else None),
        "exit_reasoning": json.dumps(exit_reasoning, sort_keys=True),
        "close_category": "strategy",
        "exit_price_source": (
            "local_fill" if local_paper_fill else "broker_fill" if private_exchange_fill else "unavailable"
        ),
        "exit_fill_verified": exit_fill_verified,
        "reconciliation_identity": json.dumps(
            reconciliation_identity,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "reconciliation_evidence": json.dumps(
            outcome_evidence,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        "outcome_semantics_version": OUTCOME_SEMANTICS_VERSION,
    }
    if exit_fill_verified and funding_cost is None:
        outcome_eligible = False
        outcome_exclusion_reason = "incomplete_funding_economics"
    else:
        outcome_eligible, outcome_exclusion_reason = outcome_write_eligibility(
            {**row, **update_fields},
            exit_fill_verified=exit_fill_verified,
        )
    update_fields.update(
        {
            "outcome_eligible": outcome_eligible,
            "outcome_exclusion_reason": outcome_exclusion_reason,
        }
    )
    update_fields.update(_close_provenance())
    audit_before = {
        "status": row.get("status"),
        "unrealized_pnl": row.get("unrealized_pnl"),
    }
    audit_after = {
        "status": "closed",
        "realized_pnl": realized,
        "close_reason": reason,
        "outcome_eligible": outcome_eligible,
        "outcome_exclusion_reason": update_fields["outcome_exclusion_reason"],
    }
    audit_message = (
        f"Position {row.get('symbol')} closed @ {exit_price} ({reason}); "
        f"realized={realized}; outcome={update_fields['outcome_exclusion_reason']}"
    )
    # The Position outcome and the local-paper cash event are one accounting
    # fact.  Commit them together so a ledger failure cannot leave a closed
    # Position with unchanged cash (or release an eligible Outcome downstream).
    # Re-read under the writer claim to make concurrent close attempts fail
    # closed instead of both publishing close side effects.
    with repo.transaction(immediate=True):
        current = repo._select_one("positions", position_id)
        if current is None:
            raise ValueError(f"position not found: {position_id}")
        if current.get("status") != "open":
            raise ValueError(f"position is not open: {position_id}")
        changed_fields = [
            field
            for field in _CLOSE_ECONOMIC_SNAPSHOT_FIELDS
            if not _same_snapshot_value(row.get(field), current.get(field))
        ]
        if changed_fields:
            raise RuntimeError(
                f"position economics changed before close; retry with a fresh snapshot ({', '.join(changed_fields)})"
            )
        if materialize_close is not None:
            materialize_close()
        repo.update_position(position_id, **update_fields)
        if outcome_eligible or outcome_exclusion_reason == "attribution_read_failed":
            from aletheia.portfolio.outcome_hooks import enqueue_outcome_hook_jobs

            enqueue_outcome_hook_jobs({**row, **update_fields})
        if row.get("execution_mode") == "paper_internal" and realized is not None:
            from aletheia.portfolio.paper_account import settle_position_closed

            settle_position_closed(position_id, realized)
        from aletheia.storage.models import AuditEvent, new_id

        # Per-close ledger (exit-intent R1): committed atomically with the Position
        # close so a crash between COMMIT and the ledger write cannot leave a closed
        # Position with no close event. is_partial=0 for a full close.
        repo.insert_position_close_event(
            {
                "id": new_id(),
                "position_id": position_id,
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "execution_mode": row.get("execution_mode"),
                "close_reason": reason,
                "exit_price": exit_price,
                "closed_qty": row["quantity"],
                "realized_pnl": realized,
                "entry_fee": round(entry_fee, 8),
                "exit_fee": round(exit_fee, 8),
                "is_partial": 0,
                "closed_at": closed_at,
            }
        )
        repo.insert_audit_event(
            AuditEvent(
                actor=actor,
                source=source,
                event_type="position_closed",
                entity_type="position",
                entity_id=position_id,
                before_state=json.dumps(audit_before, sort_keys=True),
                after_state=json.dumps(audit_after, sort_keys=True),
                message=audit_message,
            )
        )
    row["closed_at"] = closed_at  # so the Serenity hook computes real hold_h

    audit_payload = {
        "actor": actor,
        "source": source,
        "event_type": "position_closed",
        "entity_type": "position",
        "entity_id": position_id,
        "before": audit_before,
        "after": audit_after,
        "message": audit_message,
    }
    try:
        # The authoritative audit row committed with the Position above. Keep
        # the append-only JSONL chain in sync without mirroring a duplicate row
        # back into SQLite.
        write_audit(audit_payload, mirror_db=False)
    except Exception as exc:
        # The database event above is the durable audit authority. A secondary
        # JSONL sink outage after commit must not make the caller retry a close
        # that has already settled, but it remains visible in process logs.
        logger.error("position close JSONL audit sink failed (%s)", type(exc).__name__)
    # Notify operator of trade close (Telegram + toast)
    try:
        from aletheia.notifications import notify_operator

        pnl_emoji = "+" if realized is not None and realized > 0 else ""
        if realized is None:
            pnl_message = (
                "Net PnL unavailable: funding economics are incomplete"
                "\nOutcome excluded from feedback: "
                f"{outcome_exclusion_reason}"
            )
        elif outcome_eligible:
            pnl_message = f"PnL: {pnl_emoji}{realized:.4f} USDT"
        else:
            pnl_message = (
                f"Recorded PnL: {pnl_emoji}{realized:.4f} USDT"
                "\nOutcome excluded from feedback: "
                f"{outcome_exclusion_reason}"
            )
        notify_operator(
            title=f"Position Closed: {row.get('symbol')}",
            message=(f"{row.get('direction')} → {reason} @ {exit_price:.4f}\n" + pnl_message),
            level=("warning" if realized is None or realized < 0 else "info"),
            event_type="trade_notification",
            entity_type="position",
            entity_id=position_id,
            error_log=False,
        )
    except Exception:
        pass
    if outcome_eligible or outcome_exclusion_reason == "attribution_read_failed":
        try:
            from aletheia.portfolio.outcome_hooks import process_outcome_hook_jobs

            process_outcome_hook_jobs(position_id=position_id)
        except Exception as exc:
            # The durable jobs were committed with the Position. A later worker
            # will recover them even if the immediate delivery attempt itself fails.
            logger.warning(
                "post-commit Outcome hook dispatch failed for %s: %s",
                position_id,
                exc,
            )
    return {
        "position_id": position_id,
        "realized_pnl": realized,
        "exit_price": exit_price,
        "reason": reason,
        "outcome_eligible": outcome_eligible,
        "outcome_exclusion_reason": update_fields["outcome_exclusion_reason"],
    }


def _all_closed_positions(execution_mode: str | None = None) -> list[dict]:
    """Read the complete closed-position history used for account authority.

    Display endpoints may cap rows, but an authorization or completeness check
    must not forget an old unresolved close merely because newer history crossed
    an arbitrary list limit.
    """
    from aletheia.storage.sqlite import get_db

    sql = "SELECT * FROM positions WHERE status='closed'"
    params: tuple = ()
    if execution_mode:
        sql += " AND execution_mode=?"
        params = (execution_mode,)
    sql += " ORDER BY rowid DESC"
    return [dict(row) for row in get_db().execute(sql, params)]


def _closed_today(execution_mode: str | None = None) -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    matched: list[dict] = []
    for r in _all_closed_positions(execution_mode):
        closed_at = r.get("closed_at") or ""
        try:
            closed_date = datetime.fromisoformat(closed_at).date().isoformat()
        except (ValueError, TypeError):
            continue
        if closed_date != today:
            continue
        matched.append(r)
    return matched


def reduce_position(
    position_id: str,
    reduce_qty: float,
    exit_price: float,
    reason: str = "operator_reduce",
) -> dict:
    """Partially close a position (reduce-in-place, exit-intent R1 Stage 3).

    Realizes PnL on ``reduce_qty`` at ``exit_price``, writes ONE partial close event
    (is_partial=1) to the per-close ledger, shrinks the position's quantity, prorates its
    stored entry_fee onto the reduced portion, and keeps the position OPEN with its
    average entry frozen. The realized PnL reaches the daily-loss breaker via the Stage-2
    partial-close term; the open position's own realized_pnl is left untouched (the ledger
    is the source of truth for partials, so a later full close never double-counts).

    Use close_position for a full exit — reduce_qty must be a strict fraction of the open
    quantity. Returns a result dict; {"error": ...} on invalid input.

    NOTE (needs follow-on, see PARTIAL_REDUCE_RESEARCH.md): paper_internal cash-ledger
    settlement of a partial (paper_account_events UNIQUE(action, position_id)) and the
    real-exchange reduce-only order are not wired here. The risk-critical realized-PnL
    accounting IS complete via the per-close ledger + the Stage-2 breaker term.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import new_id
    from aletheia.files.jsonl import write_audit

    row = repo._select_one("positions", position_id)
    if row is None:
        return {"error": "position_not_found", "position_id": position_id}
    if row.get("status") != "open":
        return {"error": "position_not_open", "position_id": position_id}
    # Product boundary (2026-07): the active product has no authorized partial-reduce
    # entry in any execution mode. This accounting primitive remains for the isolated
    # OKX reference helper and focused tests, but the production router fails closed.
    # paper_internal additionally cannot represent partial settlement in its cash ledger.
    if row.get("execution_mode") == "paper_internal":
        return {
            "error": "partial_reduce_unsupported_on_paper_internal",
            "position_id": position_id,
            "hint": ("active product supports full closes only; partial reduce is not production-authorized"),
        }
    qty = float(row["quantity"])
    reduce_qty = float(reduce_qty)
    if reduce_qty <= 0:
        return {"error": "reduce_qty_must_be_positive", "position_id": position_id}
    if reduce_qty >= qty:
        return {
            "error": "reduce_qty_not_less_than_position",
            "position_id": position_id,
            "hint": "use close_position for a full exit",
        }

    fee_rate = _exit_fee_rate(row.get("execution_mode", ""))
    gross = _realized_pnl(row["direction"], row["entry_price"], exit_price, reduce_qty)
    entry_fee_total = row.get("entry_fee")
    if entry_fee_total is None:
        entry_fee_total = row["entry_price"] * qty * fee_rate
    entry_fee_portion = float(entry_fee_total) * reduce_qty / qty
    exit_fee = exit_price * reduce_qty * fee_rate
    realized = round(gross - entry_fee_portion - exit_fee, 8)

    new_qty = round(qty - reduce_qty, 10)
    remaining_entry_fee = round(float(entry_fee_total) - entry_fee_portion, 8)
    closed_at = datetime.now(timezone.utc).isoformat()

    # Shrink quantity + prorate the entry fee to the remainder; entry_price (avg) frozen;
    # position stays OPEN. Both writes in one transaction so a crash cannot leave the
    # position shrunk with no close event (or a close event with a stale quantity).
    with repo.transaction():
        update_position(position_id, quantity=new_qty, entry_fee=remaining_entry_fee)
        repo.insert_position_close_event(
            {
                "id": new_id(),
                "position_id": position_id,
                "symbol": row.get("symbol"),
                "direction": row.get("direction"),
                "execution_mode": row.get("execution_mode"),
                "close_reason": reason,
                "exit_price": exit_price,
                "closed_qty": reduce_qty,
                "realized_pnl": realized,
                "entry_fee": round(entry_fee_portion, 8),
                "exit_fee": round(exit_fee, 8),
                "is_partial": 1,
                "closed_at": closed_at,
            }
        )
    write_audit(
        {
            "actor": "system",
            "source": "position_lifecycle",
            "event_type": "position_reduced",
            "entity_type": "position",
            "entity_id": position_id,
            "before": {"quantity": qty},
            "after": {"quantity": new_qty, "reduced_qty": reduce_qty, "realized_pnl": realized, "reason": reason},
            "message": f"Position {row.get('symbol')} reduced {reduce_qty}@{exit_price} ({reason}); realized={realized}",
        }
    )
    return {
        "reduced": True,
        "position_id": position_id,
        "reduced_qty": reduce_qty,
        "remaining_qty": new_qty,
        "realized_pnl": realized,
        "exit_price": exit_price,
    }


def _finite_number(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _realized_economics_value(row: dict) -> float | None:
    """Return reproducible net PnL, or ``None`` for incomplete economics.

    This deliberately re-checks stored inputs instead of trusting that a
    numeric ``realized_pnl`` is complete. Older releases wrote futures PnL
    after treating missing funding as zero, so NULL-only detection would keep
    publishing those historical false numbers.

    IMPORTANT — this predicate does NOT distinguish "still retrying" from
    "terminally given up". A row with
    ``outcome_exclusion_reason == funding_permanently_unrecoverable`` also
    returns None here, because the audit chain remains genuinely
    unreconstructible; that is the honest answer for count-style tiles like
    the Realized "N closes unverified" caveat. For "is this close still
    in flight?" callers (Overview hero card, Risk affordability, paper
    ledger completeness), see the canonical recipe
    ``aletheia.portfolio.paper_account._unsettled_local_closes`` which
    additionally excludes the terminal state. Do NOT use
    ``_realized_economics_value(row) is None`` as an "in-flight" signal —
    that pattern was the root cause of the 2026-07-20 "— USDT" hero-card
    outage (fixed in d20973d6). Prefer the canonical helper.
    """
    pnl = _finite_number(row.get("realized_pnl"))
    entry = _finite_number(row.get("entry_price"))
    exit_price = _finite_number(
        row.get("current_price") if row.get("current_price") is not None else row.get("exit_price")
    )
    quantity = _finite_number(row.get("quantity"))
    entry_fee = _finite_number(row.get("entry_fee"))
    exit_fee = _finite_number(row.get("exit_fee"))
    if (
        pnl is None
        or entry is None
        or exit_price is None
        or quantity is None
        or entry_fee is None
        or exit_fee is None
        or entry <= 0
        or exit_price <= 0
        or quantity <= 0
        or entry_fee < 0
        or exit_fee < 0
    ):
        return None

    market_type = str(row.get("market_type") or "").lower()
    funding_cost, _funding_source = validated_funding_economics(
        market_type=market_type,
        execution_mode=row.get("execution_mode"),
        funding_cost=row.get("funding_cost"),
        funding_source=row.get("funding_source"),
    )
    if funding_cost is None:
        return None

    direction = str(row.get("direction") or "").lower()
    if direction in {"long", "buy"}:
        gross = (exit_price - entry) * quantity
    elif direction in {"short", "sell"}:
        gross = (entry - exit_price) * quantity
    else:
        return None
    expected = gross - entry_fee - exit_fee - funding_cost
    tolerance = max(1e-8, abs(expected) * 1e-8, entry * quantity * 1e-10)
    return pnl if abs(pnl - expected) <= tolerance else None


def _complete_realized_total(rows: list[dict]) -> tuple[float, int]:
    """Sum net economics of all reproducible closes; count the unverifiable ones.

    Always returns a partial sum over verified closes so callers can display
    a value even when some positions lack funding data.  The unavailable count
    tells the caller (and the UI) how many closes were excluded.
    """
    total = 0.0
    unavailable = 0
    for row in rows:
        value = _realized_economics_value(row)
        if value is None:
            unavailable += 1
            continue
        total += value
    return (round(total, 8), unavailable)


def position_realized_pnl_view(row: dict) -> dict:
    """Return one display/export row without trusting legacy numeric PnL.

    The immutable database row remains available for audit and remediation.  The
    operator-facing read model exposes a number only when the same close inputs
    can reproduce it, so a legacy futures row cannot look valid on the blotter
    while portfolio totals correctly say that funding is unknown.
    """
    view = dict(row)
    if str(row.get("status") or "") != "closed":
        return view

    # A historical local-paper reconstruction is an audit layer, never a
    # substitute for current verified realized PnL.  Keep the fields distinct
    # so a legacy Risk decision cannot silently appear as a verified outcome.
    from aletheia.maintenance.historical_accounting import historical_accounting_for_position

    historical = historical_accounting_for_position(str(row.get("id") or ""))
    if historical is not None:
        view["historical_accounting_status"] = historical["status"]
        view["historical_accounting_pnl"] = historical["reconstructed_realized_pnl"]
        view["historical_accounting_reason"] = historical["reason"]
        view["historical_accounting_method"] = historical["method"]

    realized = _realized_economics_value(row)
    complete = realized is not None
    view["realized_pnl"] = realized
    view["realized_pnl_complete"] = complete
    if complete:
        view["realized_pnl_unavailable_reason"] = None
        return view

    market_type = str(row.get("market_type") or "").lower()
    funding_cost, _funding_source = validated_funding_economics(
        market_type=market_type,
        execution_mode=row.get("execution_mode"),
        funding_cost=row.get("funding_cost"),
        funding_source=row.get("funding_source"),
    )
    # A terminal "gave up" reason must win over the generic
    # "incomplete_funding_economics" — the settler has stopped retrying and
    # the UI needs to distinguish that from an in-flight retry (FUND-B).
    stored_reason = str(row.get("outcome_exclusion_reason") or "")
    if stored_reason == "funding_permanently_unrecoverable":
        reason = stored_reason
    elif market_type in {"spot", "usdt_futures"} and funding_cost is None:
        reason = "incomplete_funding_economics"
    else:
        reason = stored_reason or "incomplete_realized_economics"
    view["realized_pnl_unavailable_reason"] = reason
    return view


def today_raw_closed_realized_pnl(
    execution_mode: str | None = None,
) -> float | None:
    """Raw stored realized total, or unavailable if any close is incomplete."""
    total, _ = _complete_realized_total(_closed_today(execution_mode))
    return total


def today_eligible_strategy_pnl(execution_mode: str | None = None) -> float:
    """Today's PnL only from centrally verified, economically complete outcomes."""
    from aletheia.analytics.outcome_integrity import eligible_strategy_outcomes

    today = datetime.now(timezone.utc).date().isoformat()
    rows = eligible_strategy_outcomes(
        execution_mode=execution_mode,
        closed_after=f"{today}T00:00:00+00:00",
    )
    return round(sum(float(row.get("realized_pnl") or 0.0) for row in rows), 8)


def today_realized_pnl(execution_mode: str | None = None) -> float | None:
    """Compatibility raw metric; Risk migration to authoritative loss is RG-T09."""
    return today_raw_closed_realized_pnl(execution_mode=execution_mode)


def today_pnl_by_symbol(
    execution_mode: str | None = None,
) -> dict[str, float | None]:
    """Per-symbol realized PnL for positions closed today (UTC)."""
    grouped: dict[str, list[dict]] = {}
    for row in _closed_today(execution_mode):
        grouped.setdefault(str(row.get("symbol") or "?"), []).append(row)
    result: dict[str, float | None] = {}
    for symbol, rows in grouped.items():
        total, unavailable = _complete_realized_total(rows)
        # Return None when ALL closes for this symbol are unavailable so the
        # tooltip can show '—' rather than a misleading 0.00.  The portfolio
        # aggregate uses the partial sum instead (with a completeness flag).
        result[symbol] = None if unavailable == len(rows) else round(total, 4)
    return result


def compute_portfolio(execution_mode: str | None = None, persist_marks: bool = False) -> dict:
    """Aggregate open positions into a portfolio summary (marks to market first)."""
    positions = mark_positions_to_market(persist=persist_marks)
    if execution_mode:
        positions = [p for p in positions if p["execution_mode"] == execution_mode]

    total_unrealized = sum(p["unrealized_pnl"] for p in positions)
    total_notional = sum(p["quantity"] * (p.get("current_price") or p["entry_price"]) for p in positions)
    today_closed = _closed_today(execution_mode)
    total_today_realized, today_realized_unavailable_count = _complete_realized_total(today_closed)

    # Realized PnL: per-mode for the requested execution_mode (paper dashboard),
    # plus all-mode total for the exchange_account row.
    all_closed = _all_closed_positions()
    if execution_mode:
        mode_closed = [p for p in all_closed if p.get("execution_mode") == execution_mode]
    else:
        mode_closed = all_closed
    total_realized, realized_unavailable_count = _complete_realized_total(mode_closed)
    all_realized, all_realized_unavailable_count = _complete_realized_total(all_closed)
    from aletheia.maintenance.historical_accounting import historical_accounting_summary

    historical_accounting = historical_accounting_summary(execution_mode=execution_mode)
    all_historical_accounting = historical_accounting_summary(execution_mode=None)

    # Exposure by symbol
    exposure: dict[str, float] = {}
    for p in positions:
        notional = p["quantity"] * (p.get("current_price") or p["entry_price"])
        exposure[p["symbol"]] = exposure.get(p["symbol"], 0.0) + notional

    # Exchange-account view (real Bitget balance/positions, cached). Never blocks
    # on a network call here — the scheduler refreshes the cache out-of-band, so
    # the portfolio API stays fast and degrades honestly when the cache is empty
    # or stale (closes the portfolio-sync-gap; see exchange_sync.py).
    exchange_equity_usdt = None
    exchange_state_age_sec = None
    exchange_external_positions = None
    exchange_asset_breakdown: list = []
    # These get overridden by the real exchange view when available.
    final_unrealized = total_unrealized
    final_positions_count = len(positions)
    final_exposure = exposure
    final_today_pnl = total_unrealized + total_today_realized
    # The headline must be an account value, never the locally-managed notional.
    # In local paper mode the append-only cash ledger plus freshly marked open
    # PnL is the only trustworthy source.  If old unsettled closes make that
    # ledger incomplete, return None rather than presenting position notional as
    # account equity.
    final_total_value: float | None = None
    account_equity_usdt: float | None = None
    exchange_external_summary: list = []
    from aletheia.config.loader import load_config

    uses_external_exchange = load_config().exchange.paper_execution_mode != "paper_internal"
    sync_status = "not_applicable" if not uses_external_exchange else "unconfigured"
    if not uses_external_exchange:
        from aletheia.portfolio.paper_account import get_paper_account

        local_account = get_paper_account(event_limit=0)
        # equity_usdt is always set now (issue #316: no longer gated on
        # economics_complete); use it directly.
        _eq = local_account.get("equity_usdt")
        if _eq is not None:
            account_equity_usdt = round(float(_eq), 4)
            final_total_value = account_equity_usdt
    else:
        try:
            from aletheia.portfolio import exchange_sync

            cached = exchange_sync.get_cached_state()
            sync_status = exchange_sync.status_for(cached)
            if cached is not None and sync_status in ("ok", "stale"):
                # get_cached_state() default path may now return the unified (multi-broker)
                # cache, which uses total_equity_usdt instead of the Bitget-specific
                # equity_usdt. Fall back so exchange_equity_usdt isn't null on non-Bitget
                # accounts (mirrors trading_context.py:64-65).
                exchange_equity_usdt = cached.get("equity_usdt") or cached.get("total_equity_usdt")
                exchange_state_age_sec = cached.get("age_seconds")
                # Real account equity is the authoritative total value when present.
                if exchange_equity_usdt is not None:
                    final_total_value = float(exchange_equity_usdt)
                    account_equity_usdt = final_total_value
                # Accurate external-position classification via a pair-based (symbol,
                # direction) join — the crude `exchange_count - local_count` count is
                # wrong under hedge mode (a symbol can be long AND short at once). The
                # back-compat int is derived from the precise list below.
                try:
                    from aletheia.portfolio.position_reconciler import classify_positions

                    _recon = classify_positions()
                    exchange_external_summary = [i.to_dict() for i in _recon.external]
                    exchange_external_positions = len(_recon.external)
                except Exception:
                    local_open = len(positions)
                    ext_total = int(cached.get("open_positions_count") or 0)
                    exchange_external_positions = max(0, ext_total - local_open)
                exchange_asset_breakdown = cached.get("asset_breakdown") or []

                # Override PnL/exposure metrics with real exchange data when available.
                # Unified cache uses total_unrealized_pnl; Bitget cache uses futures_unrealized_pnl.
                exc_upnl = cached.get("futures_unrealized_pnl")
                if exc_upnl is None:
                    exc_upnl = cached.get("total_unrealized_pnl")
                if exc_upnl is not None:
                    final_unrealized = float(exc_upnl)
                    final_today_pnl = float(exc_upnl) + total_today_realized

                exc_positions = cached.get("positions") or []
                if exc_positions:
                    final_positions_count = len(exc_positions)
                    exc_exposure: dict[str, float] = {}
                    for p in exc_positions:
                        sym = p.get("symbol", "")
                        notional = float(p.get("quantity") or 0) * float(p.get("entry_price") or 0)
                        exc_exposure[sym] = exc_exposure.get(sym, 0.0) + notional
                    if exc_exposure:
                        final_exposure = exc_exposure
        except Exception:
            sync_status = "error"

    # UTA unified state (multi-broker aggregate, cached)
    uta_state = None
    if uses_external_exchange:
        try:
            from aletheia.portfolio import exchange_sync

            unified = exchange_sync.get_unified_cached_state()
            if unified is not None:
                uta_age = unified.get("age_seconds")
                if uta_age is not None and uta_age < 600:
                    uta_state = unified
                    # When unified state is available and has data, use as hero number
                    uta_equity = unified.get("total_equity_usdt")
                    if uta_equity and uta_equity > 0:
                        final_total_value = float(uta_equity)
                        account_equity_usdt = final_total_value
                    # UTA uPnL is the authoritative cross-broker unrealized PnL. The
                    # legacy single-broker Bitget cache above sets final_unrealized to
                    # 0 when the active account is on another broker (e.g. OKX), which
                    # contradicted the equity-curve's local estimate. Prefer UTA here
                    # so the UNREALIZED card matches the real exchange mark-price uPnL.
                    uta_count = len(unified.get("positions") or [])
                    uta_upnl = unified.get("total_unrealized_pnl")
                    if uta_upnl is not None:
                        final_unrealized = float(uta_upnl)
                        final_today_pnl = float(uta_upnl) + total_today_realized
                    if uta_count:
                        final_positions_count = uta_count
        except Exception:
            pass

    return {
        "total_value_usdt": (round(final_total_value, 4) if final_total_value is not None else None),
        "account_equity_usdt": account_equity_usdt,
        "position_notional_usdt": round(total_notional, 4),
        "unrealized_pnl": round(final_unrealized, 4),
        "realized_pnl": (round(total_realized, 4)),
        "realized_pnl_complete": realized_unavailable_count == 0,
        "realized_pnl_unavailable_count": realized_unavailable_count,
        "all_realized_pnl": round(all_realized, 4),
        "all_realized_pnl_complete": all_realized_unavailable_count == 0,
        "all_realized_pnl_unavailable_count": all_realized_unavailable_count,
        # Historical reconstruction is explicitly separate from strict verified
        # realized PnL.  It cannot affect Risk, Learning, equity snapshots, or
        # local paper cash until an independently authorized settlement process.
        **historical_accounting,
        "all_historical_reconciled_pnl": all_historical_accounting["historical_reconciled_pnl"],
        "all_historical_reconciled_count": all_historical_accounting["historical_reconciled_count"],
        "all_historical_reconciliation_unavailable_count": all_historical_accounting[
            "historical_reconciliation_unavailable_count"
        ],
        # #56 note (2026-07-08): `today_pnl` is a legacy misnomer — it's
        # `unrealized_pnl (lifetime for all currently-open positions) +
        # today_realized_pnl`. It is NOT PnL earned today alone. A card
        # labelled "Today" using this value overstates today's activity by
        # any long-held open position's total uPnL. Callers wanting pure
        # today-only PnL should read `today_realized_pnl` and add whatever
        # they can defensibly claim as today's mark-to-market delta
        # (requires a yesterday-close snapshot which the schema doesn't
        # currently persist — parked as a Serenity-adjacent improvement).
        # Kept as-is for API back-compat; UI callers are expected to
        # transition to today_realized_pnl for the honest "Today" number.
        "today_pnl": round(final_today_pnl, 4),
        "positions_count": final_positions_count,
        "exposure_by_symbol": {k: round(v, 4) for k, v in final_exposure.items()},
        "unrealized_by_symbol": {
            p["symbol"]: round(p["unrealized_pnl"], 4) for p in positions if p.get("unrealized_pnl")
        },
        "today_pnl_by_symbol": today_pnl_by_symbol(execution_mode),
        "positions": positions,
        "today_realized_pnl": round(total_today_realized, 4),
        "today_realized_pnl_complete": today_realized_unavailable_count == 0,
        "today_realized_pnl_unavailable_count": (today_realized_unavailable_count),
        # Exchange-account view (closes WEBUI_BACKEND_HANDOFF #3 / portfolio-sync-gap).
        "exchange_equity_usdt": exchange_equity_usdt,
        "exchange_state_age_sec": exchange_state_age_sec,
        "exchange_external_positions": exchange_external_positions,
        "exchange_external_summary": exchange_external_summary,
        "exchange_sync_status": sync_status,
        "exchange_asset_breakdown": exchange_asset_breakdown,
        # UTA unified view (multi-broker aggregate).
        "uta_state": uta_state,
    }


def compute_and_store_snapshot(execution_mode: str = "paper_internal") -> PortfolioSnapshot:
    """Compute the portfolio and persist portfolio_snapshot rows.

    Writes two rows when exchange data is available:
    1. Paper-local row (execution_mode from arg) — pure mark-to-market, no exchange data
    2. Exchange row (execution_mode="exchange_account") — real account equity from cache

    Called from the scheduler thread — uses persist=True to also write
    mark-to-market prices back to the positions table.
    """
    summary = compute_portfolio(execution_mode=execution_mode, persist_marks=True)

    # Row 1: mode-specific reading.  Local-paper snapshots are only persisted as
    # account-equity readings when the cash ledger is complete; they must never
    # silently fall back to position notional.
    mode_positions = [p for p in summary.get("positions", []) if p.get("execution_mode") == execution_mode]
    local_unrealized = sum(p.get("unrealized_pnl", 0) for p in mode_positions)
    local_notional = sum(p["quantity"] * (p.get("current_price") or p["entry_price"]) for p in mode_positions)
    local_account_equity = summary.get("account_equity_usdt") if execution_mode == "paper_internal" else None
    snap = PortfolioSnapshot(
        total_value_usdt=round(
            local_account_equity if local_account_equity is not None else local_notional,
            4,
        ),
        unrealized_pnl=round(local_unrealized, 4),
        realized_pnl=summary["realized_pnl"],
        positions_count=len(mode_positions),
        execution_mode=execution_mode,
        account_equity_usdt=local_account_equity,
    )
    if execution_mode != "paper_internal" or local_account_equity is not None:
        insert_portfolio_snapshot(snap)
    else:
        logger.warning("Skipping local-paper account-equity snapshot: local ledger economics are incomplete.")

    # Row 2: exchange account — prefer UTA unified equity (multi-broker sum),
    # fallback to legacy single-broker Bitget cache.
    uta_state = summary.get("uta_state")
    uta_equity = None
    if uta_state:
        uta_equity = uta_state.get("total_equity_usdt")
    exchange_equity = uta_equity or summary.get("exchange_equity_usdt")
    # View-completeness gate (P0, 2026-07-02): the UNIFIED total (uta_equity) is a
    # multi-broker SUM. When the ACTIVE broker is missing from the unified view
    # (e.g. OKX API 50001 → the sum is only the idle bitget $167, not the real
    # ~$89k), recording it as "account equity" poisons the drawdown history with a
    # fake ~99% drawdown that then zeroes the size cap (drawdown-notional-trap
    # family). Skip the row rather than record a lie. The legacy single-broker
    # fallback (exchange_equity_usdt) is a direct per-broker read and is NOT subject
    # to this — only the unified-sum path can misattribute another broker's balance.
    if uta_equity is not None:
        from aletheia.portfolio.exchange_sync import active_broker_view_complete

        _view_complete, _view_reason = active_broker_view_complete()
        if not _view_complete:
            logger.warning(
                "Skipping exchange_account equity snapshot: unified total is missing the "
                "active broker (%s) — recording it would poison the drawdown history.",
                _view_reason,
            )
            exchange_equity = None
    if exchange_equity is not None:
        exc_snap = PortfolioSnapshot(
            total_value_usdt=float(exchange_equity),
            unrealized_pnl=summary["unrealized_pnl"],
            realized_pnl=summary["all_realized_pnl"],
            positions_count=summary["positions_count"],
            execution_mode="exchange_account",
            exchange_equity_usdt=float(exchange_equity),
            account_equity_usdt=float(exchange_equity),
        )
        insert_portfolio_snapshot(exc_snap)

    return snap


# ---------------------------------------------------------------------------
# Serenity feedback loop (Link 9→10 closure)
# ---------------------------------------------------------------------------


def _close_serenity_feedback(position: dict, realized_pnl: float, close_reason: str) -> None:
    """Write SerenityPostMortem and update opportunity status when a serenity position closes.

    This closes the attribution loop: position close → PostMortem → opportunity status.
    The dreaming_agent reads PostMortems for pattern synthesis; trade_attribution joins
    opportunity_id back to serenity_opportunities. Without this hook both are severed.
    """
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import SerenityPostMortem

    opp_id = position.get("opportunity_id")
    if not opp_id:
        return

    outcome = "win" if realized_pnl > 0 else "loss"
    entry = position.get("entry_price") or 0
    exit_price = position.get("current_price") or entry
    notional = entry * (position.get("quantity") or 0)
    pnl_pct = (realized_pnl / notional * 100) if notional else 0.0

    failure_bucket = None
    if outcome == "loss":
        if close_reason == "stop_loss":
            failure_bucket = "stop_hit"
        elif close_reason == "thesis_invalidated":
            failure_bucket = "thesis_invalidated"
        elif close_reason == "max_hold":
            failure_bucket = "time_decay"
        else:
            failure_bucket = close_reason or "unknown"

    pm = SerenityPostMortem(
        position_id=str(position.get("id") or "") or None,
        opportunity_id=opp_id,
        outcome=outcome,
        failure_bucket=failure_bucket,
        score_delta=None,
        attribution=(
            f"trade_school=serenity close_reason={close_reason} "
            f"pnl={realized_pnl:.2f} pnl_pct={pnl_pct:.1f}% "
            f"hold_h={_compute_holding_hours(position.get('opened_at'), position.get('closed_at')) or 0:.0f}"
        ),
        summary=(
            f"{position.get('symbol')} {position.get('direction')} "
            f"{'profit' if realized_pnl > 0 else 'loss'} {pnl_pct:+.1f}% "
            f"closed by {close_reason}"
        ),
        created_by="system_auto_close",
    )
    repo.insert_serenity_post_mortem(pm)

    repo.update_serenity_opportunity(
        opp_id,
        status="traded_closed",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def _compute_holding_hours(opened_at: str | None, closed_at: str | None) -> float | None:
    """Compute holding duration in hours between two ISO timestamps."""
    if not opened_at or not closed_at:
        return None
    try:
        from datetime import datetime as _dt

        fmt_candidates = [
            "%Y-%m-%dT%H:%M:%S.%f+00:00",
            "%Y-%m-%dT%H:%M:%S+00:00",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]
        t_open = t_close = None
        for fmt in fmt_candidates:
            try:
                t_open = _dt.strptime(opened_at.replace("Z", "+00:00").split("+")[0], fmt.split("+")[0])
                break
            except ValueError:
                continue
        for fmt in fmt_candidates:
            try:
                t_close = _dt.strptime(closed_at.replace("Z", "+00:00").split("+")[0], fmt.split("+")[0])
                break
            except ValueError:
                continue
        if t_open and t_close:
            return (t_close - t_open).total_seconds() / 3600.0
    except Exception:
        pass
    return None

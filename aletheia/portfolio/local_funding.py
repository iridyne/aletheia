"""Deterministic funding economics for ``paper_internal`` futures.

This module consumes only durable, public funding schedule snapshots.  It does
not claim that the resulting number came from an exchange account ledger: the
source is always ``local_simulation`` when the observation window is complete.
Any gap leaves the funding amount unavailable so Outcome/Learning remains
fail-closed while the protective close can still complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any, Mapping


_CROSS_VENUE_EXCHANGES = frozenset({
    "hyperliquid",
    "hyperliquid_predicted",
    "coinalyze",
})

# The scheduler observes derivatives every two minutes.  Funding is only
# considered complete when the persisted schedule is close enough to entry,
# every settlement, and exit to prove the collector did not disappear between
# events.  A generous 15-minute allowance tolerates transient ticks without
# silently bridging a prolonged outage.
_MAX_OBSERVATION_GAP = timedelta(minutes=15)
# Older local-paper entries constructed Fill and Position in sequence, so their
# durable timestamps can differ by a few microseconds. One second preserves that
# harmless write-order skew while still rejecting economically meaningful gaps.
_ENTRY_TIME_TOLERANCE = timedelta(seconds=1)
_METHOD = "active_entry_fill_notional_x_scheduled_rate"


@dataclass(frozen=True)
class LocalFundingResult:
    """One complete or explicitly unavailable local funding computation."""

    complete: bool
    funding_cost: float | None
    funding_source: str
    evidence: dict[str, Any]


@dataclass(frozen=True)
class _ScheduledSnapshot:
    id: str
    symbol: str
    exchange: str
    funding_rate: float
    scheduled_at: datetime
    observed_at: datetime


@dataclass(frozen=True)
class _EntryFill:
    order_id: str
    proposal_id: str
    fill_id: str
    fill_price: float
    fill_quantity: float
    filled_at: datetime


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _as_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _load_scheduled_snapshots(symbol: str, *, through: datetime) -> list[_ScheduledSnapshot]:
    from aletheia.storage.sqlite import get_db

    rows = get_db().execute(
        """
        SELECT id, symbol, exchange, funding_rate, next_funding_time, observed_at
        FROM funding_rate_snapshots
        WHERE symbol=? AND next_funding_time IS NOT NULL
        ORDER BY observed_at ASC, rowid ASC
        """,
        (symbol,),
    ).fetchall()
    result: list[_ScheduledSnapshot] = []
    for raw in rows:
        row = dict(raw)
        exchange = str(row.get("exchange") or "").strip().lower()
        if not exchange or exchange in _CROSS_VENUE_EXCHANGES:
            continue
        observed_at = _parse_utc(row.get("observed_at"))
        scheduled_at = _parse_utc(row.get("next_funding_time"))
        try:
            rate = float(row.get("funding_rate"))
        except (TypeError, ValueError):
            continue
        if (
            observed_at is None
            or scheduled_at is None
            or observed_at > through
            or not math.isfinite(rate)
        ):
            continue
        result.append(_ScheduledSnapshot(
            id=str(row.get("id") or ""),
            symbol=str(row.get("symbol") or symbol),
            exchange=exchange,
            funding_rate=rate,
            scheduled_at=scheduled_at,
            observed_at=observed_at,
        ))
    return result


def _snapshot_evidence(row: _ScheduledSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": row.id,
        "exchange": row.exchange,
        "funding_rate": row.funding_rate,
        "scheduled_at": _as_iso(row.scheduled_at),
        "observed_at": _as_iso(row.observed_at),
    }


def _load_exact_entry_fills(
    position: Mapping[str, Any],
    *,
    closed_at: datetime,
) -> tuple[list[_EntryFill] | None, str | None]:
    """Return the exact entry Fill ledger represented by ``position``.

    Local-paper averaging updates the Position's weighted entry price and final
    quantity, but every leg keeps a direct Order -> Position backlink. Funding
    must therefore be reconstructed from those immutable fills, not projected
    backwards from the final Position snapshot.
    """
    from aletheia.storage.sqlite import get_db

    position_id = str(position.get("id") or "")
    primary_order_id = str(position.get("order_id") or "")
    primary_proposal_id = str(position.get("proposal_id") or "")
    if not position_id or not primary_order_id or not primary_proposal_id:
        return None, "missing_entry_identity"

    rows = get_db().execute(
        """
        SELECT
            o.id AS order_id,
            o.proposal_id AS order_proposal_id,
            o.risk_decision_id AS order_risk_decision_id,
            o.position_id AS order_position_id,
            o.symbol AS order_symbol,
            o.market_type AS order_market_type,
            o.direction AS order_direction,
            o.order_type AS order_type,
            o.quantity AS order_quantity,
            o.execution_mode AS order_execution_mode,
            o.status AS order_status,
            o.source AS order_source,
            o.close_reason AS order_close_reason,
            p.id AS proposal_id,
            p.symbol AS proposal_symbol,
            p.market_type AS proposal_market_type,
            p.direction AS proposal_direction,
            p.order_type AS proposal_order_type,
            p.quantity AS proposal_quantity,
            p.execution_mode AS proposal_execution_mode,
            p.status AS proposal_status,
            p.source AS proposal_source,
            p.commit_hash AS proposal_commit_hash,
            rd.id AS risk_decision_id,
            rd.proposal_id AS risk_proposal_id,
            rd.approved AS risk_approved,
            rd.violations AS risk_violations,
            rd.allowed_execution_mode AS risk_allowed_execution_mode,
            rd.proposal_execution_mode AS risk_proposal_execution_mode,
            rd.decision_role AS risk_decision_role,
            rd.evaluated_quantity AS risk_evaluated_quantity,
            rd.proposal_commit_hash AS risk_proposal_commit_hash,
            f.id AS fill_id,
            f.fill_price AS fill_price,
            f.fill_quantity AS fill_quantity,
            f.filled_at AS fill_filled_at
        FROM orders o
        LEFT JOIN trade_proposals p ON p.id = o.proposal_id
        LEFT JOIN risk_decisions rd ON rd.id = o.risk_decision_id
        LEFT JOIN fills f ON f.order_id = o.id
        WHERE o.position_id = ? AND o.close_reason IS NULL
        ORDER BY o.rowid ASC, f.rowid ASC
        """,
        (position_id,),
    ).fetchall()
    if not rows:
        return None, "missing_entry_orders"

    expected_identity = {
        "symbol": str(position.get("symbol") or ""),
        "market_type": str(position.get("market_type") or ""),
        "direction": str(position.get("direction") or ""),
        "execution_mode": str(position.get("execution_mode") or ""),
    }
    orders: dict[str, dict[str, Any]] = {}
    fills: list[_EntryFill] = []
    seen_fill_ids: set[str] = set()
    for raw in rows:
        row = dict(raw)
        order_id = str(row.get("order_id") or "")
        proposal_id = str(row.get("proposal_id") or "")
        risk_decision_id = str(row.get("risk_decision_id") or "")
        if not order_id or not proposal_id or not risk_decision_id:
            return None, "entry_identity_mismatch"
        for field, expected in expected_identity.items():
            if row.get(f"order_{field}") != expected:
                return None, "entry_identity_mismatch"
            if row.get(f"proposal_{field}") != expected:
                return None, "entry_identity_mismatch"
        if (
            row.get("order_position_id") != position_id
            or row.get("order_proposal_id") != proposal_id
            or row.get("order_risk_decision_id") != risk_decision_id
            or row.get("risk_proposal_id") != proposal_id
            or row.get("order_type") != row.get("proposal_order_type")
            or row.get("order_status") != "filled"
            or row.get("proposal_status") != "executed"
            or row.get("order_source") != "native"
            or row.get("proposal_source") != "native"
            or not bool(row.get("risk_approved"))
            or row.get("risk_decision_role") != "authoritative"
            or row.get("risk_allowed_execution_mode") != expected_identity["execution_mode"]
            or row.get("risk_proposal_execution_mode") != expected_identity["execution_mode"]
            or row.get("risk_proposal_commit_hash") != row.get("proposal_commit_hash")
        ):
            return None, "entry_identity_mismatch"
        try:
            violations = json.loads(row.get("risk_violations") or "[]")
            order_quantity = float(row.get("order_quantity"))
            proposal_quantity = float(row.get("proposal_quantity"))
            evaluated_quantity = float(row.get("risk_evaluated_quantity"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, "entry_identity_mismatch"
        quantity_tolerance = max(1e-12, abs(order_quantity) * 1e-9)
        if (
            violations != []
            or not math.isfinite(order_quantity)
            or order_quantity <= 0
            or abs(proposal_quantity - order_quantity) > quantity_tolerance
            or abs(evaluated_quantity - order_quantity) > quantity_tolerance
        ):
            return None, "entry_identity_mismatch"

        fill_id = str(row.get("fill_id") or "")
        filled_at = _parse_utc(row.get("fill_filled_at"))
        try:
            fill_price = float(row.get("fill_price"))
            fill_quantity = float(row.get("fill_quantity"))
        except (TypeError, ValueError):
            return None, "entry_quantity_mismatch"
        if (
            not fill_id
            or fill_id in seen_fill_ids
            or filled_at is None
            or filled_at > closed_at
            or not math.isfinite(fill_price)
            or fill_price <= 0
            or not math.isfinite(fill_quantity)
            or fill_quantity <= 0
        ):
            return None, "entry_quantity_mismatch"
        seen_fill_ids.add(fill_id)
        fills.append(_EntryFill(
            order_id=order_id,
            proposal_id=proposal_id,
            fill_id=fill_id,
            fill_price=fill_price,
            fill_quantity=fill_quantity,
            filled_at=filled_at,
        ))
        order_state = orders.setdefault(order_id, {
            "proposal_id": proposal_id,
            "quantity": order_quantity,
            "fill_quantity": 0.0,
        })
        if order_state["proposal_id"] != proposal_id:
            return None, "entry_identity_mismatch"
        order_state["fill_quantity"] += fill_quantity

    if primary_order_id not in orders:
        return None, "missing_primary_entry_order"
    if orders[primary_order_id]["proposal_id"] != primary_proposal_id:
        return None, "entry_identity_mismatch"
    for state in orders.values():
        tolerance = max(1e-12, abs(float(state["quantity"])) * 1e-9)
        if abs(float(state["fill_quantity"]) - float(state["quantity"])) > tolerance:
            return None, "entry_quantity_mismatch"

    try:
        position_quantity = float(position.get("quantity"))
        position_entry_price = float(position.get("entry_price"))
    except (TypeError, ValueError):
        return None, "entry_quantity_mismatch"
    total_quantity = sum(fill.fill_quantity for fill in fills)
    quantity_tolerance = max(1e-12, abs(position_quantity) * 1e-9)
    if (
        not math.isfinite(position_quantity)
        or position_quantity <= 0
        or abs(total_quantity - position_quantity) > quantity_tolerance
    ):
        return None, "entry_quantity_mismatch"
    total_notional = sum(fill.fill_price * fill.fill_quantity for fill in fills)
    represented_notional = position_entry_price * position_quantity
    notional_tolerance = max(1e-9, abs(represented_notional) * 1e-9)
    if (
        not math.isfinite(position_entry_price)
        or position_entry_price <= 0
        or abs(total_notional - represented_notional) > notional_tolerance
    ):
        return None, "entry_price_mismatch"

    fills.sort(key=lambda fill: (fill.filled_at, fill.order_id, fill.fill_id))
    opened_at = _parse_utc(position.get("opened_at"))
    if opened_at is None:
        return None, "missing_entry_identity"
    if any(
        fill.filled_at < opened_at - _ENTRY_TIME_TOLERANCE
        for fill in fills
    ):
        return None, "entry_fill_before_open"
    primary_fills = [fill for fill in fills if fill.order_id == primary_order_id]
    if (
        not primary_fills
        or abs(primary_fills[0].filled_at - opened_at) > _ENTRY_TIME_TOLERANCE
        or abs(fills[0].filled_at - opened_at) > _ENTRY_TIME_TOLERANCE
    ):
        return None, "primary_entry_time_mismatch"
    return fills, None


def _incomplete(
    *,
    position: Mapping[str, Any],
    opened_at: datetime | None,
    closed_at: datetime | None,
    reason: str,
    exchange: str | None = None,
    events: list[dict[str, Any]] | None = None,
) -> LocalFundingResult:
    evidence: dict[str, Any] = {
        "status": "incomplete",
        "reason": reason,
        "source": "local_simulation",
        "method": _METHOD,
        "position_id": position.get("id"),
        "symbol": position.get("symbol"),
        "exchange": exchange,
        "opened_at": _as_iso(opened_at) if opened_at is not None else None,
        "closed_at": _as_iso(closed_at) if closed_at is not None else None,
        "events": events or [],
    }
    return LocalFundingResult(
        complete=False,
        funding_cost=None,
        funding_source="unavailable",
        evidence=evidence,
    )


def has_fresh_local_funding_schedule(
    symbol: str,
    *,
    as_of: str | datetime | None = None,
) -> bool:
    """Return whether entry has a fresh, persisted next-settlement anchor."""

    if as_of is None:
        instant = datetime.now(timezone.utc)
    elif isinstance(as_of, datetime):
        instant = as_of.astimezone(timezone.utc) if as_of.tzinfo else None
    else:
        instant = _parse_utc(as_of)
    if instant is None:
        return False
    rows = _load_scheduled_snapshots(symbol, through=instant)
    candidates = [
        row
        for row in rows
        if row.observed_at <= instant < row.scheduled_at
        and instant - row.observed_at <= _MAX_OBSERVATION_GAP
    ]
    return bool(candidates)


def compute_local_funding(
    position: Mapping[str, Any],
    *,
    closed_at: str | datetime,
) -> LocalFundingResult:
    """Aggregate complete local-paper funding over one Position lifetime.

    Each event satisfies ``opened_at < scheduled_at <= closed_at``.  The final
    rate for that schedule is the latest durable observation no more than 15
    minutes before settlement.  The first observation after each settlement
    must point to the next schedule, and a fresh schedule strictly after close
    anchors the end of the window.  Missing any of these facts returns no number.
    """

    if (
        str(position.get("execution_mode") or "") != "paper_internal"
        or str(position.get("market_type") or "") != "usdt_futures"
    ):
        return _incomplete(
            position=position,
            opened_at=None,
            closed_at=None,
            reason="unsupported_position",
        )

    opened = _parse_utc(position.get("opened_at"))
    if isinstance(closed_at, datetime):
        closed = closed_at.astimezone(timezone.utc) if closed_at.tzinfo else None
    else:
        closed = _parse_utc(closed_at)
    if opened is None or closed is None or closed < opened:
        return _incomplete(
            position=position,
            opened_at=opened,
            closed_at=closed,
            reason="invalid_window",
        )

    entry_fills, lineage_error = _load_exact_entry_fills(
        position,
        closed_at=closed,
    )
    if entry_fills is None:
        return _incomplete(
            position=position,
            opened_at=opened,
            closed_at=closed,
            reason=lineage_error or "incomplete_entry_lineage",
        )

    direction = str(position.get("direction") or "").lower()
    if direction in {"long", "buy"}:
        direction_sign = 1.0
    elif direction in {"short", "sell"}:
        direction_sign = -1.0
    else:
        return _incomplete(
            position=position,
            opened_at=opened,
            closed_at=closed,
            reason="invalid_direction",
        )

    rows = _load_scheduled_snapshots(str(position.get("symbol") or ""), through=closed)
    opening_candidates = [
        row
        for row in rows
        if row.observed_at <= opened < row.scheduled_at
    ]
    if not opening_candidates:
        return _incomplete(
            position=position,
            opened_at=opened,
            closed_at=closed,
            reason="missing_open_anchor",
        )
    opening = max(opening_candidates, key=lambda row: row.observed_at)
    if opened - opening.observed_at > _MAX_OBSERVATION_GAP:
        return _incomplete(
            position=position,
            opened_at=opened,
            closed_at=closed,
            reason="stale_open_anchor",
            exchange=opening.exchange,
        )

    exchange = opening.exchange
    venue_rows = [row for row in rows if row.exchange == exchange]
    current_target = opening.scheduled_at
    previous_event = opened
    events: list[dict[str, Any]] = []
    total_cost = 0.0
    visited: set[datetime] = set()

    while current_target <= closed:
        if current_target in visited:
            return _incomplete(
                position=position,
                opened_at=opened,
                closed_at=closed,
                reason="invalid_schedule_cycle",
                exchange=exchange,
                events=events,
            )
        visited.add(current_target)

        event_candidates = [
            row
            for row in venue_rows
            if row.scheduled_at == current_target
            and previous_event <= row.observed_at <= current_target
            and current_target - row.observed_at <= _MAX_OBSERVATION_GAP
        ]
        if not event_candidates:
            return _incomplete(
                position=position,
                opened_at=opened,
                closed_at=closed,
                reason="missing_event_observation",
                exchange=exchange,
                events=events,
            )
        event_snapshot = max(event_candidates, key=lambda row: row.observed_at)
        if any(fill.filled_at == current_target for fill in entry_fills):
            return _incomplete(
                position=position,
                opened_at=opened,
                closed_at=closed,
                reason="entry_fill_on_settlement_boundary",
                exchange=exchange,
                events=events,
            )
        active_fills = [
            fill for fill in entry_fills if fill.filled_at < current_target
        ]
        active_notional = sum(
            fill.fill_price * fill.fill_quantity for fill in active_fills
        )
        if not math.isfinite(active_notional) or active_notional <= 0:
            return _incomplete(
                position=position,
                opened_at=opened,
                closed_at=closed,
                reason="missing_active_entry_notional",
                exchange=exchange,
                events=events,
            )
        event_cost = active_notional * event_snapshot.funding_rate * direction_sign
        event_evidence = _snapshot_evidence(event_snapshot)
        event_evidence["active_notional"] = active_notional
        event_evidence["active_quantity"] = sum(
            fill.fill_quantity for fill in active_fills
        )
        event_evidence["active_fill_ids"] = [fill.fill_id for fill in active_fills]
        event_evidence["direction_sign"] = int(direction_sign)
        event_evidence["funding_cost"] = event_cost
        events.append(event_evidence)
        total_cost += event_cost

        transition_candidates = [
            row
            for row in venue_rows
            if current_target <= row.observed_at <= closed
            and row.observed_at - current_target <= _MAX_OBSERVATION_GAP
            and row.scheduled_at > current_target
        ]
        if not transition_candidates:
            return _incomplete(
                position=position,
                opened_at=opened,
                closed_at=closed,
                reason="missing_schedule_transition",
                exchange=exchange,
                events=events,
            )
        transition = min(transition_candidates, key=lambda row: row.observed_at)
        previous_event = current_target
        current_target = transition.scheduled_at

    close_anchor_candidates = [
        row
        for row in venue_rows
        if row.observed_at <= closed < row.scheduled_at
        and closed - row.observed_at <= _MAX_OBSERVATION_GAP
    ]
    if not close_anchor_candidates:
        return _incomplete(
            position=position,
            opened_at=opened,
            closed_at=closed,
            reason="missing_close_anchor",
            exchange=exchange,
            events=events,
        )
    close_anchor = max(close_anchor_candidates, key=lambda row: row.observed_at)

    evidence = {
        "status": "complete",
        "source": "local_simulation",
        "method": _METHOD,
        "position_id": position.get("id"),
        "symbol": position.get("symbol"),
        "exchange": exchange,
        "opened_at": _as_iso(opened),
        "closed_at": _as_iso(closed),
        "entry_order_count": len({fill.order_id for fill in entry_fills}),
        "entry_fill_count": len(entry_fills),
        "entry_fill_ids": [fill.fill_id for fill in entry_fills],
        "direction": direction,
        "direction_sign": int(direction_sign),
        "events": events,
        "next_settlement_anchor": _snapshot_evidence(close_anchor),
    }
    return LocalFundingResult(
        complete=True,
        funding_cost=total_cost,
        funding_source="local_simulation",
        evidence=evidence,
    )

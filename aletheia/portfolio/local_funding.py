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
_ACCOUNTING_VERSION = "local-paper-funding-v2"
_SETTLEMENT_SKEW = timedelta(seconds=1)


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


def _canonical_settlement(value: datetime) -> datetime:
    """Remove sub-second exchange serialization noise from event identity."""
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _is_settled_event(row: _ScheduledSnapshot) -> bool:
    return (
        _canonical_settlement(row.observed_at)
        == _canonical_settlement(row.scheduled_at)
        and abs(row.observed_at - row.scheduled_at) < _SETTLEMENT_SKEW
    )


def _conflicting_rates(rows: list[_ScheduledSnapshot]) -> bool:
    if not rows:
        return False
    first = rows[0].funding_rate
    return any(
        not math.isclose(row.funding_rate, first, rel_tol=0.0, abs_tol=1e-15)
        for row in rows[1:]
    )


def _event_evidence(
    row: _ScheduledSnapshot,
    *,
    canonical_at: datetime,
) -> dict[str, Any]:
    evidence = _snapshot_evidence(row)
    evidence["raw_scheduled_at"] = evidence["scheduled_at"]
    evidence["raw_observed_at"] = evidence["observed_at"]
    evidence["scheduled_at"] = _as_iso(canonical_at)
    return evidence


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
    diagnostic: dict[str, Any] | None = None,
) -> LocalFundingResult:
    """Build an incomplete LocalFundingResult with an audit trail.

    The optional ``diagnostic`` dict is merged into the evidence so the
    operator can see WHERE the audit chain broke — e.g.
    ``{"last_observation_at": "2026-07-19T23:31:15Z",
       "gap_from_close_sec": 1037,
       "max_gap_allowed_sec": 900}`` for a close_anchor failure. Fields
    are additive; recognized keys should be prefixed unambiguously so a
    future diagnostic addition can't collide with the schedule/event
    fields ``events`` already occupies.
    """
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
    if diagnostic:
        for key, value in diagnostic.items():
            evidence.setdefault(key, value)
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


def _compute_local_funding_with_entry_fills(
    position: Mapping[str, Any],
    *,
    opened: datetime,
    closed: datetime,
    entry_fills: list[_EntryFill],
) -> LocalFundingResult:
    """Aggregate funding after an entry ledger has passed its own validation.

    This is deliberately private: callers must prove the entry ledger before it
    reaches this schedule calculator.  The current producer uses the strict
    authority validator; the historical-recovery reader has a separate,
    explicitly labelled legacy validator and never upgrades an old row into a
    current verified outcome.
    """

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
        if row.observed_at <= opened < _canonical_settlement(row.scheduled_at)
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
    current_target = _canonical_settlement(opening.scheduled_at)
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
            if _canonical_settlement(row.scheduled_at) == current_target
            and previous_event <= row.observed_at <= current_target
            and current_target - row.observed_at <= _MAX_OBSERVATION_GAP
        ]
        settled_candidates = [
            row for row in venue_rows
            if _canonical_settlement(row.scheduled_at) == current_target
            and _is_settled_event(row)
            and previous_event <= row.observed_at < current_target + _SETTLEMENT_SKEW
        ]
        if _conflicting_rates(settled_candidates):
            return _incomplete(
                position=position,
                opened_at=opened,
                closed_at=closed,
                reason="conflicting_settlement_rates",
                exchange=exchange,
                events=events,
                diagnostic={
                    "canonical_settlement_at": _as_iso(current_target),
                    "snapshot_ids": [row.id for row in settled_candidates],
                },
            )
        if settled_candidates:
            event_candidates = settled_candidates
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
        if any(
            _canonical_settlement(fill.filled_at) == current_target
            for fill in entry_fills
        ):
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
        event_evidence = _event_evidence(
            event_snapshot,
            canonical_at=current_target,
        )
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
            if current_target <= row.observed_at < current_target + _SETTLEMENT_SKEW
            and _canonical_settlement(row.scheduled_at) > current_target
        ] or [
            row
            for row in venue_rows
            if current_target <= row.observed_at <= closed
            and row.observed_at - current_target <= _MAX_OBSERVATION_GAP
            and _canonical_settlement(row.scheduled_at) > current_target
        ]
        if not transition_candidates:
            # Diagnose: what's the FIRST observation strictly after
            # current_target? If it exists but sits outside the 15-min
            # budget, we can quantify by how much the collector missed —
            # e.g. a first observation 29 min after the target is a
            # different failure signature than no observation at all.
            later_obs = [
                r for r in venue_rows
                if current_target < r.observed_at <= closed
            ]
            first_after = min(
                later_obs, key=lambda r: r.observed_at,
            ) if later_obs else None
            diagnostic = {
                "current_target": _as_iso(current_target),
                "next_observation_at": (
                    _as_iso(first_after.observed_at) if first_after else None
                ),
                "next_observation_scheduled_at": (
                    _as_iso(first_after.scheduled_at) if first_after else None
                ),
                "gap_from_current_target_sec": (
                    (first_after.observed_at - current_target).total_seconds()
                    if first_after else None
                ),
                "max_gap_allowed_sec": int(_MAX_OBSERVATION_GAP.total_seconds()),
            }
            return _incomplete(
                position=position,
                opened_at=opened,
                closed_at=closed,
                reason="missing_schedule_transition",
                exchange=exchange,
                events=events,
                diagnostic=diagnostic,
            )
        transition = min(transition_candidates, key=lambda row: row.observed_at)
        previous_event = current_target
        current_target = _canonical_settlement(transition.scheduled_at)

    close_anchor_candidates = [
        row
        for row in venue_rows
        if row.observed_at <= closed < _canonical_settlement(row.scheduled_at)
        and closed - row.observed_at <= _MAX_OBSERVATION_GAP
    ]
    if not close_anchor_candidates:
        # Diagnose: what's the LATEST observation before close, and how far
        # from the 15-min budget did it fall? This turns a bare
        # "missing_close_anchor" into an actionable audit trail — the
        # operator can see if the collector was 17 min stale or 4 hours
        # stale, and whether the close_anchor invariant was almost met.
        last_before_close = max(
            (r.observed_at for r in venue_rows if r.observed_at <= closed),
            default=None,
        )
        diagnostic = {
            "last_observation_at": (
                _as_iso(last_before_close) if last_before_close else None
            ),
            "gap_from_close_sec": (
                (closed - last_before_close).total_seconds()
                if last_before_close else None
            ),
            "max_gap_allowed_sec": int(_MAX_OBSERVATION_GAP.total_seconds()),
        }
        return _incomplete(
            position=position,
            opened_at=opened,
            closed_at=closed,
            reason="missing_close_anchor",
            exchange=exchange,
            events=events,
            diagnostic=diagnostic,
        )
    close_anchor = max(close_anchor_candidates, key=lambda row: row.observed_at)

    evidence = {
        "status": "complete",
        "source": "local_simulation",
        "method": _METHOD,
        "accounting_version": _ACCOUNTING_VERSION,
        "coverage_mode": "live_coverage",
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
        "next_settlement_anchor": _event_evidence(
            close_anchor,
            canonical_at=_canonical_settlement(close_anchor.scheduled_at),
        ),
    }
    return LocalFundingResult(
        complete=True,
        funding_cost=total_cost,
        funding_source="local_simulation",
        evidence=evidence,
    )


def _compute_historical_funding_with_entry_fills(
    position: Mapping[str, Any],
    *,
    opened: datetime,
    closed: datetime,
    through: datetime,
    entry_fills: list[_EntryFill],
) -> LocalFundingResult:
    direction = str(position.get("direction") or "").lower()
    if direction in {"long", "buy"}:
        direction_sign = 1.0
    elif direction in {"short", "sell"}:
        direction_sign = -1.0
    else:
        return _incomplete(
            position=position, opened_at=opened, closed_at=closed,
            reason="invalid_direction",
        )

    rows = _load_scheduled_snapshots(
        str(position.get("symbol") or ""), through=through,
    )
    venues = sorted({row.exchange for row in rows})
    complete_venues: list[tuple[str, list[tuple[datetime, _ScheduledSnapshot]]]] = []
    conflict: dict[str, list[str]] | None = None

    for exchange in venues:
        venue_rows = [row for row in rows if row.exchange == exchange]
        buckets: dict[datetime, list[_ScheduledSnapshot]] = {}
        for row in venue_rows:
            if _is_settled_event(row):
                buckets.setdefault(
                    _canonical_settlement(row.scheduled_at), [],
                ).append(row)
        selected: list[tuple[datetime, _ScheduledSnapshot]] = []
        venue_conflict = False
        for settled_at in sorted(buckets):
            candidates = buckets[settled_at]
            if _conflicting_rates(candidates):
                conflict = {
                    "exchange": exchange,
                    "snapshot_ids": [row.id for row in candidates],
                }
                venue_conflict = True
                break
            selected.append((settled_at, max(candidates, key=lambda row: row.observed_at)))
        if venue_conflict:
            continue

        before = [item for item in selected if item[0] <= opened]
        after = [item for item in selected if item[0] > closed]
        if not before or not after:
            continue
        start_index = selected.index(before[-1])
        end_index = selected.index(after[0])
        bracket = selected[start_index:end_index + 1]
        contiguous = True
        for (current_at, _), (next_at, _) in zip(bracket, bracket[1:]):
            if not any(
                _canonical_settlement(row.observed_at) == current_at
                and _canonical_settlement(row.scheduled_at) == next_at
                and row.observed_at < current_at + _SETTLEMENT_SKEW
                for row in venue_rows
            ):
                contiguous = False
                break
        if contiguous:
            complete_venues.append((exchange, bracket))

    if conflict is not None:
        return _incomplete(
            position=position, opened_at=opened, closed_at=closed,
            reason="conflicting_settlement_rates",
            exchange=str(conflict["exchange"]),
            diagnostic={"snapshot_ids": conflict["snapshot_ids"]},
        )
    if len(complete_venues) != 1:
        return _incomplete(
            position=position, opened_at=opened, closed_at=closed,
            reason=(
                "ambiguous_historical_funding_venues"
                if len(complete_venues) > 1
                else "incomplete_historical_funding_ledger"
            ),
            diagnostic={
                "complete_exchanges": [exchange for exchange, _ in complete_venues],
                "through": _as_iso(through),
            },
        )

    exchange, bracket = complete_venues[0]
    events: list[dict[str, Any]] = []
    total_cost = 0.0
    for settled_at, snapshot in bracket:
        if not (opened < settled_at <= closed):
            continue
        if any(
            _canonical_settlement(fill.filled_at) == settled_at
            for fill in entry_fills
        ):
            return _incomplete(
                position=position, opened_at=opened, closed_at=closed,
                reason="entry_fill_on_settlement_boundary", exchange=exchange,
                events=events,
            )
        active_fills = [fill for fill in entry_fills if fill.filled_at < settled_at]
        active_notional = sum(
            fill.fill_price * fill.fill_quantity for fill in active_fills
        )
        if not math.isfinite(active_notional) or active_notional <= 0:
            return _incomplete(
                position=position, opened_at=opened, closed_at=closed,
                reason="missing_active_entry_notional", exchange=exchange,
                events=events,
            )
        event_cost = active_notional * snapshot.funding_rate * direction_sign
        evidence = _event_evidence(snapshot, canonical_at=settled_at)
        evidence.update({
            "active_notional": active_notional,
            "active_quantity": sum(fill.fill_quantity for fill in active_fills),
            "active_fill_ids": [fill.fill_id for fill in active_fills],
            "direction_sign": int(direction_sign),
            "funding_cost": event_cost,
        })
        events.append(evidence)
        total_cost += event_cost

    next_settlement = next(item for item in bracket if item[0] > closed)
    return LocalFundingResult(
        complete=True,
        funding_cost=total_cost,
        funding_source="local_simulation",
        evidence={
            "status": "complete",
            "source": "local_simulation",
            "method": _METHOD,
            "accounting_version": _ACCOUNTING_VERSION,
            "coverage_mode": "historical_ledger",
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
            "next_settlement_anchor": _event_evidence(
                next_settlement[1], canonical_at=next_settlement[0],
            ),
        },
    )


def compute_local_funding_from_entry_fills(
    position: Mapping[str, Any],
    *,
    closed_at: str | datetime,
    entry_fills: list[_EntryFill],
) -> LocalFundingResult:
    """Calculate funding from an already validated immutable entry Fill ledger.

    It is intentionally not an authority validator.  The only non-current
    caller is the historical accounting repair, whose result is stored in a
    separate reconciliation layer and is never eligible for strategy outcome
    or paper-cash settlement.
    """
    if (
        str(position.get("execution_mode") or "") != "paper_internal"
        or str(position.get("market_type") or "") != "usdt_futures"
    ):
        return _incomplete(position=position, opened_at=None, closed_at=None,
                           reason="unsupported_position")
    opened = _parse_utc(position.get("opened_at"))
    closed = (
        closed_at.astimezone(timezone.utc) if isinstance(closed_at, datetime) and closed_at.tzinfo
        else _parse_utc(closed_at) if not isinstance(closed_at, datetime) else None
    )
    if opened is None or closed is None or closed < opened:
        return _incomplete(position=position, opened_at=opened, closed_at=closed,
                           reason="invalid_window")
    return _compute_local_funding_with_entry_fills(
        position, opened=opened, closed=closed, entry_fills=entry_fills,
    )


def compute_local_funding(
    position: Mapping[str, Any],
    *,
    closed_at: str | datetime,
    allow_historical_ledger: bool = False,
    as_of: str | datetime | None = None,
) -> LocalFundingResult:
    """Aggregate complete local-paper funding over one Position lifetime.

    Each event satisfies ``opened_at < scheduled_at <= closed_at``.  Current
    producer rows must first pass the full Order/Proposal/Risk authority check.
    """
    if (
        str(position.get("execution_mode") or "") != "paper_internal"
        or str(position.get("market_type") or "") != "usdt_futures"
    ):
        return _incomplete(position=position, opened_at=None, closed_at=None,
                           reason="unsupported_position")
    opened = _parse_utc(position.get("opened_at"))
    closed = (
        closed_at.astimezone(timezone.utc) if isinstance(closed_at, datetime) and closed_at.tzinfo
        else _parse_utc(closed_at) if not isinstance(closed_at, datetime) else None
    )
    if opened is None or closed is None or closed < opened:
        return _incomplete(position=position, opened_at=opened, closed_at=closed,
                           reason="invalid_window")
    entry_fills, lineage_error = _load_exact_entry_fills(position, closed_at=closed)
    if entry_fills is None:
        return _incomplete(position=position, opened_at=opened, closed_at=closed,
                           reason=lineage_error or "incomplete_entry_lineage")
    live_result = _compute_local_funding_with_entry_fills(
        position, opened=opened, closed=closed, entry_fills=entry_fills,
    )
    if live_result.complete or not allow_historical_ledger:
        return live_result
    if as_of is None:
        through = datetime.now(timezone.utc)
    elif isinstance(as_of, datetime):
        through = as_of.astimezone(timezone.utc) if as_of.tzinfo else None
    else:
        through = _parse_utc(as_of)
    if through is None or through < closed:
        return _incomplete(
            position=position, opened_at=opened, closed_at=closed,
            reason="invalid_historical_funding_horizon",
        )
    return _compute_historical_funding_with_entry_fills(
        position,
        opened=opened,
        closed=closed,
        through=through,
        entry_fills=entry_fills,
    )


# ---------------------------------------------------------------------------
# Live close-anchor seeding
# ---------------------------------------------------------------------------
#
# `_MAX_OBSERVATION_GAP = 15 min` is intentionally tight so a stale collector
# cannot masquerade as fresh coverage. Exchange history endpoints
# (`fetch_funding_rate_history`) only return timestamps AT the settlement
# instants (4h or 8h grid), so a mid-window paper close (e.g. 23:48 UTC with
# the next settlement at 24:00 UTC) can never be satisfied by history
# backfill alone — the closest historical anchor is hours away.
#
# The only architectural closure is to write a snapshot at the close boundary
# itself. `try_seed_close_anchor_snapshot` performs that best-effort write
# just before ``compute_local_funding`` runs. Silent on failure — the
# existing settler retry chain still runs, and callers must not depend on
# it succeeding (network/proxy/timeout failure is expected in the same
# environment that produced the collector gap in the first place).


def _close_anchor_already_covered(symbol: str, closed_at: str) -> bool:
    """DB-only: does `funding_rate_snapshots` already satisfy close_anchor?

    Cheap early-exit for the seed helper — in the happy path (collector fresh),
    the periodic collector has already written a snapshot inside the 15-min
    window and there's no reason to hit the exchange at close time.
    """
    parsed = _parse_utc(closed_at)
    if parsed is None:
        return False
    window_start = (parsed - _MAX_OBSERVATION_GAP).isoformat()
    upper = parsed.isoformat()
    from aletheia.storage.sqlite import get_db
    row = get_db().execute(
        "SELECT 1 FROM funding_rate_snapshots "
        "WHERE symbol=? AND observed_at>=? AND observed_at<=? LIMIT 1",
        (symbol, window_start, upper),
    ).fetchone()
    return row is not None


async def _fetch_funding_from(
    ensure_fn,
    exchange_name: str,
    perp: str,
    fetch_timeout_sec: float,
):
    """Single-exchange fetch with a bounded timeout. Returns raw dict or None.

    Extracted so try_seed_close_anchor_snapshot can cascade primary → fallback
    without duplicating the timeout/exception handling.
    """
    import asyncio
    try:
        raw = await asyncio.wait_for(
            ensure_fn().fetch_funding_rate(perp), timeout=fetch_timeout_sec
        )
        return raw
    except Exception:
        return None


async def _seed_close_anchor_snapshot_async(
    row: Mapping[str, Any],
    closed_at: str,
    fetch_timeout_sec: float,
) -> dict:
    """Attempt to seed a live close-anchor snapshot.

    Returns a diagnostic dict describing every attempt so the caller can
    surface a per-position audit trail (see the `attempts` list in
    funding_evidence). The `inserted` key is True iff a snapshot was
    written to `funding_rate_snapshots`.
    """
    from aletheia.data.ccxt_provider import get_market_data_provider
    from aletheia.data.symbols import to_ccxt_perp
    from aletheia.data.collectors.derivatives import _parse_funding
    from aletheia.storage.repositories import insert_funding_rate_snapshot
    import asyncio

    diagnostic: dict = {"inserted": False, "attempts": [], "skipped": None}

    symbol = str(row.get("symbol") or "").strip()
    if not symbol:
        diagnostic["skipped"] = "missing_symbol"
        return diagnostic
    if str(row.get("market_type") or "") != "usdt_futures":
        diagnostic["skipped"] = "non_futures_market"
        return diagnostic
    if str(row.get("execution_mode") or "") != "paper_internal":
        diagnostic["skipped"] = "non_paper_internal"
        return diagnostic
    if _close_anchor_already_covered(symbol, closed_at):
        diagnostic["skipped"] = "close_anchor_already_covered"
        return diagnostic

    perp = to_ccxt_perp(symbol)
    if not perp:
        diagnostic["skipped"] = "no_perp_symbol_mapping"
        return diagnostic

    provider = get_market_data_provider(native_ccxt=True)
    try:
        # Cascade primary → fallback. A proxy/venue-side outage on primary
        # (the exact scenario that caused the underlying collector gap) is
        # exactly when the fallback venue is most useful. Each venue gets
        # its own bounded timeout so a stuck primary can't consume the
        # fallback's budget.
        venues = [
            (provider._primary_name, provider._ensure_primary),
            (provider._fallback_name, provider._ensure_fallback),
        ]
        for venue_name, ensure_fn in venues:
            raw = await _fetch_funding_from(
                ensure_fn, venue_name, perp, fetch_timeout_sec
            )
            attempt: dict = {"venue": venue_name, "outcome": None}
            if raw is None:
                attempt["outcome"] = "fetch_failed_or_timeout"
                diagnostic["attempts"].append(attempt)
                continue
            snap = _parse_funding(symbol, venue_name, raw)
            if snap is None:
                attempt["outcome"] = "parse_returned_none"
                diagnostic["attempts"].append(attempt)
                continue
            # observed_at MUST match the actual close boundary — that's what
            # close_anchor tests against. Using the persisted close boundary
            # keeps the funding-schedule audit deterministic.
            snap = snap.model_copy(update={"observed_at": closed_at})
            insert_funding_rate_snapshot(snap.model_dump())
            attempt["outcome"] = "inserted"
            diagnostic["attempts"].append(attempt)
            diagnostic["inserted"] = True
            return diagnostic
    finally:
        # Provider owns aiohttp sessions; not closing leaks connectors and
        # trips aiohttp's "Unclosed client session" warning at loop teardown.
        # Shield so a Timeout/Cancellation from fetch above doesn't skip it.
        try:
            await asyncio.shield(provider.close())
        except Exception:
            pass
    return diagnostic


def try_seed_close_anchor_snapshot(
    row: Mapping[str, Any],
    closed_at: str,
    *,
    timeout_sec: float = 3.0,
) -> dict:
    """Best-effort live snapshot at the close boundary.

    Returns a diagnostic dict:
        {"inserted": bool,
         "attempts": [{"venue": ..., "outcome": ...}, ...],
         "skipped": Optional[str]}

    ``inserted`` is True iff a `funding_rate_snapshots` row was written with
    ``observed_at == closed_at``. Callers must not depend on inserted=True for
    correctness (network/proxy failure is possible), only for observability.
    ``attempts`` records every venue tried (primary + fallback) with a per-
    venue outcome so ``close_paper_position`` can persist the trail into
    funding evidence (audit chain diagnostic; see FUND-Q2 in
    docs/_ai/session).

    Sync wrapper safe to call from `close_paper_position`. If an outer event
    loop is already running (async scheduler context) the helper no-ops
    rather than nesting loops, and returns ``{"inserted": False, "attempts":
    [], "skipped": "running_loop_reentrancy"}``.
    """
    import asyncio

    try:
        asyncio.get_running_loop()
        return {
            "inserted": False,
            "attempts": [],
            "skipped": "running_loop_reentrancy",
        }
    except RuntimeError:
        pass

    # Manage the loop by hand so aiohttp's internal connector-close
    # background tasks get one extra tick to finish before we close the
    # loop. asyncio.run tears down eagerly and provokes aiohttp's
    # "Unclosed client session" / "Unclosed connector" warnings.
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            _seed_close_anchor_snapshot_async(
                row, closed_at, fetch_timeout_sec=timeout_sec,
            )
        )
        try:
            loop.run_until_complete(asyncio.sleep(0.05))
        except Exception:
            pass
        return result
    except Exception as exc:
        return {
            "inserted": False,
            "attempts": [],
            "skipped": f"loop_exception:{type(exc).__name__}",
        }
    finally:
        try:
            loop.close()
        except Exception:
            pass

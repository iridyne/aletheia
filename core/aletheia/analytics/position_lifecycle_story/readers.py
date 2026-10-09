"""Read exact persisted Story facts and mask evidence after the requested cutoff.

The caller owns the connection. No collection, writes or service/render imports.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import json
import sqlite3
from typing import Any

from aletheia.data.symbols import normalize_market_type

from .events import _utc

_BAR_MS = 60_000
_BUCKET_MS = 300_000


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


def _row(db: Any, table: str, identity: object) -> dict[str, Any] | None:
    if identity in (None, ""):
        return None
    try:
        found = db.execute(f"SELECT * FROM {table} WHERE id=?", (identity,)).fetchone()
    except sqlite3.DatabaseError:
        return None
    return dict(found) if found is not None else None


def _rows(
    db: Any, sql: str, params: Sequence[object] = ()
) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in db.execute(sql, tuple(params)).fetchall()]
    except sqlite3.DatabaseError:
        return []


def _visible(row: Mapping[str, Any] | None, cutoff: datetime, *fields: str) -> bool:
    if row is None:
        return False
    timestamps = [_utc(row.get(field)) for field in fields]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return bool(timestamps) and max(timestamps) <= cutoff


def _visible_created_row(
    row: Mapping[str, Any] | None,
    cutoff: datetime,
    *,
    created_field: str,
    mutable_fields: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Keep an intent created before cutoff while masking later mutable fields."""
    if row is None:
        return None
    created_at = _utc(row.get(created_field))
    if created_at is None or created_at > cutoff:
        return None
    result = dict(row)
    for field in mutable_fields:
        value = _utc(row.get(field))
        if value is not None and value > cutoff:
            result[field] = None
            if field == "filled_at" and str(result.get("status") or "").lower() in {
                "filled",
                "closed",
                "complete",
                "completed",
            }:
                result["status"] = None
                result["status_evidence"] = "unavailable"
    return result


def _bar_ts(row: object) -> int | None:
    if not isinstance(row, (list, tuple)) or not row:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError, OverflowError):
        return None


def _canonical_bar(row: Mapping[str, Any]) -> list[Any]:
    return [
        row.get("ts"),
        row.get("open"),
        row.get("high"),
        row.get("low"),
        row.get("close"),
        row.get("volume"),
        row.get("provider") or "unknown",
    ]


def _contiguous_prefix(
    by_ts: Mapping[int, list[Any]], *, first: int, last_exclusive: int
) -> list[list[Any]]:
    prefix: list[list[Any]] = []
    for timestamp in range(first, last_exclusive, _BAR_MS):
        row = by_ts.get(timestamp)
        if row is None:
            break
        prefix.append(row)
    return prefix


def _ceil_bar(timestamp_ms: int) -> int:
    return ((timestamp_ms + _BAR_MS - 1) // _BAR_MS) * _BAR_MS


def _floor_bar(timestamp_ms: int) -> int:
    return (timestamp_ms // _BAR_MS) * _BAR_MS


def _funding_settlement_at(db: Any, position_id: str, *, cutoff: datetime) -> datetime | None:
    """Return the visible durable funding-settlement timestamp, if recorded."""
    rows = _rows(
        db,
        "SELECT timestamp FROM audit_events "
        "WHERE entity_type='position' AND entity_id=? "
        "AND event_type IN ('exchange_position_funding_settled', 'paper_position_funding_settled') "
        "ORDER BY datetime(timestamp) DESC, rowid DESC",
        (position_id,),
    )
    for row in rows:
        timestamp = _utc(row.get("timestamp"))
        if timestamp is not None and timestamp <= cutoff:
            return timestamp
    return None


def _position_snapshot(
    position: Mapping[str, Any], *, cutoff: datetime
) -> tuple[dict[str, Any], bool, datetime | None]:
    """Return a position view that does not expose a future close at ``cutoff``."""
    snapshot = dict(position)
    status = str(position.get("status") or "").strip().lower()
    closed_at = _utc(position.get("closed_at"))
    closed_visible = status == "closed" and closed_at is not None and closed_at <= cutoff
    if not closed_visible and closed_at is not None:
        snapshot.update(
            {
                "status": "open",
                "current_price": None,
                "realized_pnl": None,
                "exit_fee": None,
                "close_reason": None,
                "closed_at": None,
                "exit_plan_effective_at": None,
                "exit_reasoning": None,
                "exit_price_source": "unavailable",
                "exit_fill_verified": False,
                "outcome_eligible": False,
                "outcome_exclusion_reason": "position_state_after_cutoff",
                "reconciliation_identity": None,
                "reconciliation_evidence": None,
                "outcome_semantics_version": None,
                "closed_runtime_session_id": None,
                "closed_producer_commit": None,
                "closed_behavior_fingerprint": None,
            }
        )
        closed_at = None
    return snapshot, closed_visible, closed_at


def _related_rows(
    db: Any, position: Mapping[str, Any], *, cutoff: datetime
) -> dict[str, Any]:
    proposal = _row(db, "trade_proposals", position.get("proposal_id"))
    if proposal is not None and not _visible(proposal, cutoff, "created_at"):
        proposal = None
    entry_order = _row(db, "orders", position.get("order_id"))
    if entry_order is not None and not _visible(entry_order, cutoff, "created_at"):
        entry_order = None
    opportunity = _row(db, "opportunities", position.get("opportunity_id"))
    if opportunity is None:
        opportunity = _row(db, "serenity_opportunities", position.get("opportunity_id"))
    if opportunity is not None and not _visible(opportunity, cutoff, "created_at"):
        opportunity = None
    # Universe branch: resolve universe_member when opportunity_id is NULL
    universe_member: dict[str, Any] | None = None
    if opportunity is None and not position.get("opportunity_id"):
        ep_id = (proposal or {}).get("entry_plan_id")
        if ep_id:
            from aletheia.analytics.candidate_lifecycle import for_entry_plan
            lineage = for_entry_plan(str(ep_id), cutoff, db)
            if lineage.source_kind == "universe":
                universe_member = lineage.universe_member
    pi_decisions = [
        row
        for row in _rows(
            db,
            "SELECT * FROM pi_decisions WHERE proposal_id=? "
            "ORDER BY created_at, rowid",
            (position.get("proposal_id"),),
        )
        if _visible(row, cutoff, "created_at")
    ]
    risk = _row(db, "risk_decisions", (entry_order or {}).get("risk_decision_id"))
    if risk is not None and not _visible(risk, cutoff, "created_at"):
        risk = None
    entry_fills = _rows(
        db,
        "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
        ((entry_order or {}).get("id"),),
    )
    entry_fills = [
        row for row in entry_fills if _visible(row, cutoff, "filled_at")
    ]
    close_orders = _rows(
        db,
        "SELECT * FROM orders WHERE position_id=? "
        "AND COALESCE(trim(close_reason),'')!='' "
        "ORDER BY created_at, rowid",
        (position.get("id"),),
    )
    close_orders = [
        visible
        for row in close_orders
        if (visible := _visible_created_row(
            row,
            cutoff,
            created_field="created_at",
            mutable_fields=("filled_at",),
        )) is not None
    ]
    close_fills: list[dict[str, Any]] = []
    for order in close_orders:
        close_fills.extend(
            row
            for row in _rows(
                db,
                "SELECT * FROM fills WHERE order_id=? ORDER BY filled_at, rowid",
                (order.get("id"),),
            )
            if _visible(row, cutoff, "filled_at")
        )
    close_fills.sort(
        key=lambda row: (
            _utc(row.get("filled_at")) or cutoff,
            str(row.get("id") or ""),
        )
    )
    return {
        "proposal": proposal,
        "entry_order": entry_order,
        "opportunity": opportunity,
        "universe_member": universe_member,
        "pi_decisions": pi_decisions,
        "risk": risk,
        "entry_fills": entry_fills,
        "close_orders": close_orders,
        "close_fills": close_fills,
    }


def _exact_reviews(db: Any, position_id: str, cutoff: datetime) -> list[dict[str, Any]]:
    """Read only decisions whose persisted manifest names this Position exactly.

    Uses json_extract() to push the position_id filter to SQLite, avoiding a
    full table scan of pi_decisions (319k+ rows) + Python-side JSON decoding.
    """
    result: list[dict[str, Any]] = []
    for row in _rows(
        db,
        "SELECT * FROM pi_decisions"
        " WHERE json_valid(evidence_manifest_json)"
        " AND json_extract(evidence_manifest_json, '$.position_review.target_position_id') = ?"
        " AND datetime(created_at) <= datetime(?)"
        " ORDER BY created_at, rowid",
        (position_id, cutoff.isoformat()),
    ):
        if not _visible(row, cutoff, "created_at"):
            continue
        manifest = _mapping(row.get("evidence_manifest_json"))
        review = manifest.get("position_review")
        if not isinstance(review, Mapping):
            continue
        item = dict(row)
        item["position_review"] = dict(review)
        result.append(item)
    return result


def _plan_rows(db: Any, position_id: str, cutoff: datetime) -> list[dict[str, Any]]:
    rows = _rows(
        db,
        "SELECT * FROM position_exit_plan_versions WHERE position_id=? "
        "ORDER BY version, datetime(effective_at), rowid",
        (position_id,),
    )
    return [
        row for row in rows if _visible(row, cutoff, "effective_at", "created_at")
    ]


def _protection_facts(
    db: Any, position_id: str, cutoff: datetime
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    states = _rows(
        db,
        "SELECT * FROM position_protection_state WHERE position_id=?",
        (position_id,),
    )
    state = states[0] if states else None
    if state is not None and not _visible(state, cutoff, "activated_at", "created_at"):
        state = None
    triggers = _rows(
        db,
        "SELECT * FROM position_protection_triggers WHERE position_id=? "
        "ORDER BY datetime(detected_at), rowid",
        (position_id,),
    )
    trigger = next(
        (row for row in triggers if _visible(row, cutoff, "detected_at")), None
    )
    return state, trigger


def _instrument_identity(
    fact: Mapping[str, Any],
    position: Mapping[str, Any],
    opportunity: Mapping[str, Any] | None,
    db: Any,
) -> dict[str, Any]:
    def symbol_key(value: object) -> str:
        return str(value or "").strip().upper().replace("-", "/")

    position_symbol = symbol_key(position.get("symbol"))
    position_market = normalize_market_type(position.get("market_type"))
    existing = fact.get("market_data_identity")
    if isinstance(existing, Mapping):
        symbol = existing.get("symbol") or position.get("symbol")
        market_type = normalize_market_type(
            existing.get("market_type") or position.get("market_type")
        )
        if symbol_key(symbol) != position_symbol or market_type != position_market:
            return {
                "exchange": None,
                "symbol": None,
                "market_type": None,
                "timeframe": existing.get("timeframe") or "1m",
                "availability": "unavailable",
                "unavailable_reason": "market_identity_mismatch",
            }
        return {
            "exchange": str(existing.get("exchange") or existing.get("venue") or "").lower() or None,
            "symbol": symbol,
            "market_type": market_type,
            "timeframe": existing.get("timeframe") or "1m",
            "availability": "available",
        }
    member_id = (opportunity or {}).get("universe_member_id")
    member = _row(db, "universe_members", member_id)
    asset = _row(db, "universe_registry", (member or {}).get("asset_id"))
    asset_symbol = symbol_key((asset or {}).get("symbol") or (member or {}).get("symbol"))
    asset_market = normalize_market_type(
        (asset or {}).get("market_type") or (member or {}).get("market_type")
    )
    if (asset_symbol and asset_symbol != position_symbol) or (asset_market and asset_market != position_market):
        return {
            "exchange": None,
            "symbol": None,
            "market_type": None,
            "timeframe": "1m",
            "availability": "unavailable",
            "unavailable_reason": "universe_instrument_identity_mismatch",
        }
    return {
        "exchange": str((asset or {}).get("venue") or "").lower() or None,
        "symbol": position.get("symbol"),
        "market_type": asset_market or position_market,
        "timeframe": "1m",
        "availability": "available" if asset else "unavailable",
    }


def _market_bars(
    db: Any,
    instrument: Mapping[str, Any],
    *,
    entry_at: datetime | None,
    cutoff: datetime,
    exit_at: datetime | None,
) -> tuple[list[list[Any]], str, str]:
    """Read a completed, exact-instrument prefix without provider fallback."""
    exchange = instrument.get("exchange")
    symbol = instrument.get("symbol")
    market_type = instrument.get("market_type")
    if not exchange or not symbol or not market_type or entry_at is None:
        return [], "unavailable", "market_identity_unavailable"
    entry_ms = int(entry_at.timestamp() * 1000)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    boundary_ms = cutoff_ms
    if exit_at is not None and exit_at <= cutoff:
        boundary_ms = int(exit_at.timestamp() * 1000)
    first = _ceil_bar(entry_ms)
    last_exclusive = _floor_bar(boundary_ms)
    expected = max(0, (last_exclusive - first) // _BAR_MS)
    if expected <= 0:
        return [], "unavailable", "no_completed_1m_bar_in_prefix"
    rows = _rows(
        db,
        "SELECT ts, open, high, low, close, volume, provider "
        "FROM market_ohlcv WHERE exchange=? AND symbol=? AND market_type=? "
        "AND timeframe='1m' AND ts>=? AND ts<? ORDER BY ts",
        (str(exchange).lower(), str(symbol), str(market_type), first, last_exclusive),
    )
    bars = [_canonical_bar(row) for row in rows]
    by_ts = {
        timestamp: row
        for row in bars
        if (timestamp := _bar_ts(row)) is not None
    }
    missing = [
        timestamp
        for timestamp in range(first, last_exclusive, _BAR_MS)
        if timestamp not in by_ts
    ]
    if missing:
        return (
            _contiguous_prefix(by_ts, first=first, last_exclusive=last_exclusive),
            "unavailable",
            "completed_1m_prefix_coverage_unavailable",
        )
    return bars, "available", "complete_completed_1m_prefix"


def _observer_audit(
    db: Any,
    position_id: str,
    *,
    entry_at: datetime | None,
    cutoff: datetime,
    closed_at: datetime | None,
) -> dict[str, Any]:
    if entry_at is None:
        return {
            "status": "unavailable",
            "reason": "entry_fill_time_unavailable",
            "expected_bucket_count": None,
            "available_bucket_count": 0,
            "unavailable_bucket_count": 0,
            "missing_bucket_count": None,
            "missing_bucket_open_ms": [],
        }
    boundary = closed_at if closed_at is not None and closed_at <= cutoff else cutoff
    first = ((int(entry_at.timestamp() * 1000) + _BUCKET_MS - 1) // _BUCKET_MS) * _BUCKET_MS
    last = (int(boundary.timestamp() * 1000) // _BUCKET_MS) * _BUCKET_MS
    expected = max(0, (last - first) // _BUCKET_MS)
    observations = _rows(
        db,
        "SELECT * FROM position_path_observations WHERE position_id=? "
        "AND bucket_open_ms>=? AND bucket_open_ms<? ORDER BY bucket_open_ms",
        (position_id, first, last),
    )
    visible_observations: list[dict[str, Any]] = []
    for row in observations:
        observed_at = _utc(row.get("observed_at"))
        if observed_at is None or observed_at <= cutoff:
            visible_observations.append(row)
    observations = visible_observations
    expected_buckets = set(range(first, last, _BUCKET_MS))
    by_bucket = {int(row["bucket_open_ms"]): row for row in observations if row.get("bucket_open_ms") is not None}
    missing = sorted(expected_buckets - set(by_bucket))
    unavailable = sorted(
        bucket for bucket, row in by_bucket.items() if row.get("evidence_status") != "available"
    )
    if expected == 0:
        status, reason = "unavailable", "no_completed_5m_bucket_in_prefix"
    elif missing:
        status, reason = "partial", "missing_5m_buckets"
    elif unavailable:
        status, reason = "partial", "unavailable_5m_buckets"
    else:
        status, reason = "complete", "continuous_post_entry_5m_buckets"
    return {
        "status": status,
        "reason": reason,
        "expected_bucket_count": expected,
        "available_bucket_count": sum(
            row.get("evidence_status") == "available" for row in by_bucket.values()
        ),
        "unavailable_bucket_count": len(unavailable),
        "missing_bucket_count": len(missing),
        "missing_bucket_open_ms": missing,
    }


def _visible_position_path_observations(
    db: Any,
    position_id: str,
    *,
    entry_at: datetime | None,
    cutoff: datetime,
    closed_at: datetime | None,
) -> list[dict[str, Any]]:
    if entry_at is None:
        return []
    boundary = closed_at if closed_at is not None and closed_at <= cutoff else cutoff
    first = ((int(entry_at.timestamp() * 1000) + _BUCKET_MS - 1) // _BUCKET_MS) * _BUCKET_MS
    last = (int(boundary.timestamp() * 1000) // _BUCKET_MS) * _BUCKET_MS
    if last <= first:
        return []
    rows = _rows(
        db,
        "SELECT * FROM position_path_observations "
        "WHERE position_id=? AND bucket_open_ms>=? AND bucket_open_ms<? "
        "ORDER BY bucket_open_ms ASC",
        (position_id, first, last),
    )
    return [
        row
        for row in rows
        if (_utc(row.get("observed_at")) or cutoff) <= cutoff
    ]

"""Deterministic, read-only explanation of one Position's lifecycle.

This module joins persisted Position facts, the existing immutable replay, the
existing exact trade-path diagnostics, and the append-only observer. It exposes a
human-readable renderer and a narrative-free ``structured_events`` projection;
the latter is the only machine-facing Story output. It does not collect new
evidence, write trading state, call an LLM, or select a causal winner.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import json
import math
import sqlite3
from typing import Any

from aletheia.analytics.position_replay import build_position_replay
from aletheia.storage import repositories as repo
from aletheia.storage.proposal_hypothesis import (
    HYPOTHESIS_CONTRACT_VERSION,
    evaluate_hypothesis_prefix,
)

STORY_CONTRACT_VERSION = "position_lifecycle_story/v1"
MEANINGFUL_EXCURSION_RULE_VERSION = "meaningful_favorable_excursion/v1"
MEANINGFUL_NET_EDGE_BUFFER_PCT = 0.10
_BAR_MS = 60_000
_BUCKET_MS = 300_000


# ---------------------------------------------------------------------------
# Small read/normalization helpers
# ---------------------------------------------------------------------------


def _utc(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: object) -> str | None:
    parsed = _utc(value)
    return parsed.isoformat() if parsed is not None else None


def _finite(value: object) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


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


def _nested(value: object, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


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
                result["status"] = "open"
    return result


def _event(
    event_type: str,
    *,
    occurred_at: object = None,
    evidence_status: str = "unavailable",
    source_entity_type: str | None = None,
    source_entity_id: str | None = None,
    facts: Mapping[str, Any] | None = None,
    unavailable_reason: str | None = None,
) -> dict[str, Any]:
    event_facts = dict(facts or {})
    if unavailable_reason:
        event_facts["unavailable_reason"] = unavailable_reason
    return {
        "event_type": event_type,
        "occurred_at": _iso(occurred_at),
        "evidence_status": evidence_status,
        "source_entity_type": source_entity_type,
        "source_entity_id": source_entity_id,
        "facts": event_facts,
    }


_EVENT_ORDER = {
    "opportunity_admitted": 10,
    "decision_committed": 20,
    "risk_approved": 30,
    "entry_filled": 40,
    "first_meaningful_favorable_excursion": 50,
    "maximum_favorable_excursion": 60,
    "maximum_adverse_excursion": 70,
    "hypothesis_weakening": 80,
    "hypothesis_invalidated": 90,
    "expected_horizon_reached": 100,
    "position_reviewed": 110,
    "exit_plan_revised": 120,
    "profit_protection_activated": 130,
    "stop_revised": 140,
    "profit_giveback": 150,
    "exit_triggered": 160,
    "exit_filled": 170,
    "verified_costs": 180,
}

# This is the machine-facing vocabulary.  The human renderer may use the
# legacy event names above, but a future evidence compiler must consume only
# this projection and never rendered or agent-authored prose.
POSITION_LIFECYCLE_EVENT_TYPES = (
    "opportunity_admitted",
    "decision_committed",
    "risk_approved",
    "entry_filled",
    "first_meaningful_favorable_excursion",
    "maximum_favorable_excursion",
    "maximum_adverse_excursion",
    "hypothesis_weakening",
    "hypothesis_invalidated",
    "expected_horizon_reached",
    "management_decision",
    "exit_plan_revision",
    "protection_activation",
    "stop_revision",
    "profit_giveback",
    "exit_trigger",
    "close_fill",
    "verified_costs",
)

_STRUCTURED_EVENT_ALIASES = {
    "position_reviewed": "management_decision",
    "exit_plan_revised": "exit_plan_revision",
    "profit_protection_activated": "protection_activation",
    "stop_revised": "stop_revision",
    "exit_triggered": "exit_trigger",
    "exit_filled": "close_fill",
}

_STRUCTURED_EVENT_FACT_KEYS = {
    "opportunity_admitted": (),
    "decision_committed": ("action", "proposal_id", "candidate_decision_ids"),
    "risk_approved": ("decision_role", "policy_id", "position_id"),
    "entry_filled": ("fill_price", "fill_quantity", "order_id", "fill_ids", "fill_count"),
    "first_meaningful_favorable_excursion": ("return_pct", "rule"),
    "maximum_favorable_excursion": ("mfe_pct",),
    "maximum_adverse_excursion": ("mae_pct",),
    "hypothesis_weakening": ("from", "to", "bar_ts_ms"),
    "hypothesis_invalidated": ("from", "to", "bar_ts_ms"),
    "expected_horizon_reached": ("expected_horizon_hours",),
    "management_decision": ("target_position_id", "action", "trigger", "wake_batches"),
    "exit_plan_revision": ("version", "stop_price", "take_profit_price"),
    "protection_activation": ("activation_evidence",),
    "stop_revision": ("stop_price", "previous_stop_price"),
    "profit_giveback": ("giveback_pct",),
    "exit_trigger": (
        "reason",
        "close_reason",
        "stop_hit",
        "target_hit",
        "bar_open_ms",
        "bar_hash",
        "plan_version",
        "plan_hash",
        "collision_policy",
    ),
    "close_fill": ("fill_price", "fill_quantity", "fill_ids", "fill_count"),
    "verified_costs": ("entry_fee", "exit_fee", "funding_cost", "total_costs"),
}


def _structured_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project timeline facts without narrative fields for future statistics."""
    projected: list[dict[str, Any]] = []
    for event in events:
        legacy_type = str(event.get("event_type") or "")
        event_type = _STRUCTURED_EVENT_ALIASES.get(legacy_type, legacy_type)
        if event_type not in POSITION_LIFECYCLE_EVENT_TYPES:
            continue
        raw_facts = event.get("facts")
        raw_facts = raw_facts if isinstance(raw_facts, Mapping) else {}
        facts = {
            key: raw_facts[key]
            for key in _STRUCTURED_EVENT_FACT_KEYS.get(event_type, ())
            if key in raw_facts
        }
        item = {
            "event_type": event_type,
            "occurred_at": _iso(event.get("occurred_at")),
            "evidence_status": event.get("evidence_status") or "unavailable",
            "source_entity_type": event.get("source_entity_type"),
            "source_entity_id": event.get("source_entity_id"),
            "facts": facts,
        }
        reason = event.get("unavailable_reason") or raw_facts.get("unavailable_reason")
        if reason:
            item["unavailable_reason"] = str(reason)
        projected.append(item)
    return projected


def _event_sort_key(event: Mapping[str, Any]) -> tuple[int, str, int, str]:
    timestamp = _utc(event.get("occurred_at"))
    return (
        1 if timestamp is None else 0,
        timestamp.isoformat() if timestamp is not None else "",
        _EVENT_ORDER.get(str(event.get("event_type")), 999),
        str(event.get("source_entity_id") or ""),
    )


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


def _directional_return_pct(direction: str, entry: float, price: float) -> float:
    if direction == "long":
        return (price - entry) / entry * 100.0
    return (entry - price) / entry * 100.0


def _physical_market_type(value: object) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return {
        "spot": "spot",
        "perp": "perp",
        "swap": "perp",
        "future": "perp",
        "futures": "perp",
        "usdt_futures": "perp",
    }.get(normalized, normalized)


def _ceil_bar(timestamp_ms: int) -> int:
    return ((timestamp_ms + _BAR_MS - 1) // _BAR_MS) * _BAR_MS


def _floor_bar(timestamp_ms: int) -> int:
    return (timestamp_ms // _BAR_MS) * _BAR_MS


# ---------------------------------------------------------------------------
# Exact identity and historical-prefix reads
# ---------------------------------------------------------------------------


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
        "pi_decisions": pi_decisions,
        "risk": risk,
        "entry_fills": entry_fills,
        "close_orders": close_orders,
        "close_fills": close_fills,
    }


def _exact_reviews(db: Any, position_id: str, cutoff: datetime) -> list[dict[str, Any]]:
    """Read only decisions whose persisted manifest names this Position exactly."""
    result: list[dict[str, Any]] = []
    for row in _rows(
        db,
        "SELECT * FROM pi_decisions ORDER BY created_at, rowid",
    ):
        if not _visible(row, cutoff, "created_at"):
            continue
        manifest = _mapping(row.get("evidence_manifest_json"))
        review = manifest.get("position_review")
        if not isinstance(review, Mapping):
            continue
        if str(review.get("target_position_id") or "") != position_id:
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
    position_market = _physical_market_type(position.get("market_type"))
    existing = fact.get("market_data_identity")
    if isinstance(existing, Mapping):
        symbol = existing.get("symbol") or position.get("symbol")
        market_type = _physical_market_type(
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
    asset_market = _physical_market_type(
        (asset or {}).get("market_type") or (member or {}).get("market_type")
    )
    if asset_symbol and asset_symbol != position_symbol or asset_market and asset_market != position_market:
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


def _replay_prefix_bars(
    fact: Mapping[str, Any],
    *,
    entry_at: datetime | None,
    cutoff: datetime,
    exit_at: datetime | None,
) -> tuple[list[list[Any]], str, str]:
    ohlcv = _nested(fact, "stages", "holding", "ohlcv")
    path = _nested(fact, "stages", "holding", "trade_path")
    if not isinstance(ohlcv, Mapping) or not isinstance(path, Mapping):
        return [], "unavailable", "replay_path_not_recorded"
    raw = ohlcv.get("rows")
    path_status = str(path.get("path_evidence_status") or "")
    if not isinstance(raw, list) or entry_at is None:
        if path_status == "ambiguous":
            return [], "ambiguous", str(
                path.get("path_evidence_reason") or "same_bar_ordering_ambiguous"
            )
        return [], "unavailable", str(
            ohlcv.get("unavailable_reason") or "replay_ohlcv_unavailable"
        )
    entry_ms = int(entry_at.timestamp() * 1000)
    first = _ceil_bar(entry_ms)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    boundary_ms = cutoff_ms
    if exit_at is not None and exit_at <= cutoff:
        boundary_ms = int(exit_at.timestamp() * 1000)
    last_exclusive = _floor_bar(boundary_ms)
    result = [
        list(row)
        for row in raw
        if isinstance(row, (list, tuple))
        and len(row) >= 7
        and (timestamp := _bar_ts(row)) is not None
        and first <= timestamp < last_exclusive
        and timestamp + _BAR_MS <= cutoff_ms
    ]
    result.sort(key=lambda row: (_bar_ts(row) or 0, json.dumps(row, sort_keys=True, default=str)))
    expected = max(0, (last_exclusive - first) // _BAR_MS)
    if expected <= 0:
        return result, "unavailable", "no_completed_1m_bar_in_prefix"
    by_ts = {
        timestamp: row
        for row in result
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
    if path_status == "ambiguous":
        return result, "ambiguous", str(
            path.get("path_evidence_reason") or "same_bar_ordering_ambiguous"
        )
    if path_status != "available":
        return result, "unavailable", str(
            path.get("path_evidence_reason") or "ordered_1m_path_unavailable"
        )
    return result, "available", str(
        path.get("path_evidence_reason") or "complete_exact_1m_path"
    )




# ---------------------------------------------------------------------------
# Story sections
# ---------------------------------------------------------------------------


def _hypothesis_from(
    fact: Mapping[str, Any], proposal: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    candidate = _nested(
        fact,
        "stages",
        "decision",
        "proposal",
        "attribution_snapshot",
        "position_hypothesis",
    )
    if not isinstance(candidate, Mapping):
        candidate = _nested(
            _mapping((proposal or {}).get("attribution_snapshot")),
            "position_hypothesis",
        )
    return dict(candidate) if isinstance(candidate, Mapping) else None


def _producer_identity(
    fact: Mapping[str, Any], position: Mapping[str, Any], proposal: Mapping[str, Any] | None
) -> dict[str, Any]:
    producer = fact.get("producer")
    if isinstance(producer, Mapping) and producer:
        return {
            "runtime_session_ids": sorted(str(item) for item in producer.get("runtime_session_ids", []) if item),
            "producer_commits": sorted(str(item) for item in producer.get("producer_commits", []) if item),
            "behavior_fingerprints": sorted(str(item) for item in producer.get("behavior_fingerprints", []) if item),
            "status": producer.get("status"),
        }
    return {
        "runtime_session_ids": sorted(
            str(item)
            for item in (position.get("runtime_session_id"), proposal.get("runtime_session_id") if proposal else None)
            if item
        ),
        "producer_commits": sorted(
            str(item)
            for item in (position.get("producer_commit"), proposal.get("producer_commit") if proposal else None)
            if item
        ),
        "behavior_fingerprints": sorted(
            str(item)
            for item in (position.get("behavior_fingerprint"), proposal.get("behavior_fingerprint") if proposal else None)
            if item
        ),
        "status": "available" if proposal else "unavailable",
    }


def _opening(
    position: Mapping[str, Any],
    proposal: Mapping[str, Any] | None,
    hypothesis: Mapping[str, Any] | None,
    fact: Mapping[str, Any],
    instrument: Mapping[str, Any],
    *,
    entry_fills: Sequence[Mapping[str, Any]],
    first_plan: Mapping[str, Any] | None,
) -> dict[str, Any]:
    context = _nested(hypothesis or {}, "opening_decision_context")
    context = context if isinstance(context, Mapping) else {}
    initial_risk = _nested(hypothesis or {}, "initial_risk")
    initial_risk = initial_risk if isinstance(initial_risk, Mapping) else {}
    thesis = hypothesis.get("thesis") if hypothesis else None
    signals = hypothesis.get("supporting_signals") if hypothesis else None
    entry_fill = entry_fills[0] if len(entry_fills) == 1 else None
    return {
        "position_identity": {
            "position_id": position.get("id"),
            "status": position.get("status"),
            "source": "positions",
        },
        "instrument_identity": dict(instrument),
        "direction": position.get("direction") or "not_recorded",
        "entry_price": _finite(position.get("entry_price")),
        "entry_fill_time": _iso(entry_fill.get("filled_at")) if entry_fill else None,
        "entry_fill_ids": [str(row.get("id")) for row in entry_fills if row.get("id")],
        "entry_fill_count": len(entry_fills),
        "entry_fill_aggregation": "single" if len(entry_fills) == 1 else "ambiguous",
        "original_thesis": thesis if thesis not in (None, "") else "not_recorded",
        "supporting_signals": list(signals) if isinstance(signals, (list, tuple)) else "not_recorded",
        "opening_reasoning": context.get("reasoning") or "not_recorded",
        "bear_case": context.get("bear_case") or "not_recorded",
        "key_assumptions": list(context.get("key_assumptions") or []),
        "expected_holding_horizon_hours": _finite(
            hypothesis.get("expected_horizon_hours")
            if hypothesis
            else (proposal or {}).get("expected_horizon_hours")
        ),
        "initial_stop": _finite(
            initial_risk.get("stop_price")
            if hypothesis
            else (proposal or {}).get("stop_price")
        ),
        "initial_take_profit": _finite(
            hypothesis.get("target_price") if hypothesis else (proposal or {}).get("take_profit")
        ),
        "initial_risk_quote": _finite(initial_risk.get("quote_amount")),
        "hypothesis_contract_version": hypothesis.get("contract_version") if hypothesis else None,
        "provenance": {
            "proposal_id": (proposal or {}).get("id"),
            "opportunity_id": position.get("opportunity_id"),
            "risk_decision_id": _nested(fact, "identity", "risk_decision_id"),
            "runtime": _producer_identity(fact, position, proposal),
        },
        "initial_plan_version": (first_plan or {}).get("version"),
        "availability": "available" if position else "unavailable",
    }


def _causal_chain_available(related: Mapping[str, Any], position_id: str) -> bool:
    proposal = related.get("proposal")
    entry_order = related.get("entry_order")
    risk = related.get("risk")
    if not all(isinstance(item, Mapping) for item in (proposal, entry_order, risk)):
        return False
    if str(entry_order.get("position_id") or "") != position_id:
        return False
    if str(entry_order.get("proposal_id") or "") != str(proposal.get("id") or ""):
        return False
    risk_id = str(entry_order.get("risk_decision_id") or "")
    if risk_id and risk_id != str(risk.get("id") or ""):
        return False
    risk_proposal_id = risk.get("proposal_id")
    return risk_proposal_id in (None, proposal.get("id"))


def _decision_events(
    related: Mapping[str, Any],
    position_id: str,
    *,
    causal_available: bool = True,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        _event(
            "opportunity_admitted",
            unavailable_reason="opportunity_admission_event_not_recorded",
        )
    ]
    decisions = [
        row for row in related.get("pi_decisions", [])
        if str(row.get("action") or "") == "trade"
    ]
    if len(decisions) == 1:
        decision = decisions[0]
        events.append(
            _event(
                "decision_committed",
                occurred_at=decision.get("created_at"),
                evidence_status="available",
                source_entity_type="pi_decision",
                source_entity_id=str(decision.get("id")),
                facts={"action": decision.get("action"), "proposal_id": decision.get("proposal_id")},
            )
        )
    elif len(decisions) > 1:
        events.append(
            _event(
                "decision_committed",
                evidence_status="ambiguous",
                facts={"candidate_decision_ids": [str(row.get("id")) for row in decisions]},
                unavailable_reason="multiple_trade_decisions_for_proposal",
            )
        )
    else:
        events.append(
            _event("decision_committed", unavailable_reason="exact_trade_decision_not_recorded")
        )
    risk = related.get("risk")
    if (
        isinstance(risk, Mapping)
        and _as_bool(risk.get("approved"))
        and str(risk.get("decision_role") or "") == "authoritative"
    ):
        events.append(
            _event(
                "risk_approved",
                occurred_at=risk.get("created_at"),
                evidence_status="available",
                source_entity_type="risk_decision",
                source_entity_id=str(risk.get("id")),
                facts={
                    "decision_role": risk.get("decision_role"),
                    "policy_id": risk.get("policy_id"),
                    "position_id": position_id,
                },
            )
        )
    else:
        events.append(_event("risk_approved", unavailable_reason="authoritative_risk_approval_not_recorded"))
    entry_fills = [row for row in related.get("entry_fills", []) if isinstance(row, Mapping)]
    entry_fill = entry_fills[0] if entry_fills else None
    if len(entry_fills) == 1 and entry_fill is not None:
        events.append(
            _event(
                "entry_filled",
                occurred_at=entry_fill.get("filled_at"),
                evidence_status="available",
                source_entity_type="fill",
                source_entity_id=(str(entry_fill.get("id")) if entry_fill.get("id") else None),
                facts={
                    "fill_price": entry_fill.get("fill_price"),
                    "fill_quantity": entry_fill.get("fill_quantity"),
                    "order_id": entry_fill.get("order_id"),
                },
            )
        )
    elif entry_fills:
        events.append(
            _event(
                "entry_filled",
                evidence_status="ambiguous",
                facts={
                    "fill_ids": [str(row.get("id")) for row in entry_fills if row.get("id")],
                    "fill_count": len(entry_fills),
                },
                unavailable_reason="multiple_entry_fills_not_aggregated",
            )
        )
    else:
        events.append(_event("entry_filled", unavailable_reason="entry_fill_not_recorded"))
    if not causal_available:
        for event in events:
            if event.get("evidence_status") == "available":
                event["evidence_status"] = "unavailable"
                facts = event.setdefault("facts", {})
                facts["unavailable_reason"] = "position_replay_causal_link_unavailable"
    return events


def _hypothesis_lifecycle(
    hypothesis: Mapping[str, Any] | None,
    bars: Sequence[Sequence[Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lifecycle: dict[str, Any] = {
        "contract_version": HYPOTHESIS_CONTRACT_VERSION,
        "status": "not_recorded" if hypothesis is None else "not_evaluable",
        "initial_state": "not_recorded" if hypothesis is None else "not_evaluable",
        "states_observed": [],
        "transitions": [],
        "completed_bar_count": 0,
        "evidence_prefix_end_at": None,
        "future_bars_ignored": True,
    }
    events: list[dict[str, Any]] = []
    if hypothesis is None:
        return lifecycle, events
    prefix: list[Mapping[str, Any]] = []
    previous: str | None = None
    try:
        for row in bars:
            timestamp = _bar_ts(row)
            if timestamp is None or len(row) < 7:
                continue
            prefix.append(
                {
                    "ts": timestamp,
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "volume": row[5],
                }
            )
            state = evaluate_hypothesis_prefix(hypothesis, completed_bars=prefix)
            occurred_at = datetime.fromtimestamp(
                (timestamp + _BAR_MS) / 1000.0, timezone.utc
            )
            lifecycle["states_observed"].append(
                {
                    "state": state,
                    "occurred_at": occurred_at.isoformat(),
                    "bar_ts_ms": timestamp,
                }
            )
            if previous is None:
                lifecycle["initial_state"] = state
            if state != previous:
                lifecycle["transitions"].append(
                    {
                        "from": previous or "not_recorded",
                        "to": state,
                        "occurred_at": occurred_at.isoformat(),
                        "evidence_status": "available",
                        "source_entity_type": "market_ohlcv_bar",
                        "source_entity_id": str(timestamp),
                    }
                )
                if state in {"weakening", "invalidated"}:
                    events.append(
                        _event(
                            f"hypothesis_{state}",
                            occurred_at=occurred_at,
                            evidence_status="available",
                            source_entity_type="market_ohlcv_bar",
                            source_entity_id=str(timestamp),
                            facts={
                                "from": previous or "not_recorded",
                                "to": state,
                                "bar_ts_ms": timestamp,
                            },
                        )
                    )
            previous = state
    except (TypeError, ValueError):
        lifecycle["status"] = "unavailable"
        lifecycle["unavailable_reason"] = "hypothesis_evaluation_failed"
        return lifecycle, []
    lifecycle["completed_bar_count"] = len(prefix)
    if prefix:
        lifecycle["evidence_prefix_end_at"] = datetime.fromtimestamp(
            (int(prefix[-1]["ts"]) + _BAR_MS) / 1000.0, timezone.utc
        ).isoformat()
    states = {
        str(item.get("state"))
        for item in lifecycle["states_observed"]
        if isinstance(item, Mapping)
    }
    if states & {"confirming", "weakening", "invalidated"}:
        lifecycle["status"] = "available"
    else:
        lifecycle["status"] = "not_evaluable"
        lifecycle["unavailable_reason"] = "signal_or_regime_prefix_unavailable"
    earliest: dict[str, dict[str, Any]] = {}
    for event in events:
        earliest.setdefault(str(event["event_type"]), event)
    return lifecycle, list(earliest.values())


def _path_metrics(
    path: Mapping[str, Any],
    bars: Sequence[Sequence[Any]],
    *,
    direction: str,
    entry_price: float | None,
    entry_at: datetime | None,
    entry_fill: Mapping[str, Any] | None,
    exit_fill: Mapping[str, Any] | None,
    path_status: str,
    threshold_pct: float | None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "availability": "available" if path_status == "available" else path_status,
        "evidence_reason": path.get("path_evidence_reason"),
        "maximum_favorable_excursion_pct": (
            _finite(path.get("mfe_pct")) if path_status == "available" else None
        ),
        "maximum_adverse_excursion_pct": (
            _finite(path.get("mae_pct")) if path_status == "available" else None
        ),
        "mfe_lower_bound_pct": _finite(path.get("mfe_lower_bound_pct")),
        "mfe_upper_bound_pct": _finite(path.get("mfe_upper_bound_pct")),
        "mae_lower_bound_pct": _finite(path.get("mae_lower_bound_pct")),
        "mae_upper_bound_pct": _finite(path.get("mae_upper_bound_pct")),
        "same_bar_ordering": "ambiguous" if path_status == "ambiguous" else "available" if path_status == "available" else "unavailable",
        "first_meaningful_status": "ambiguous" if path_status == "ambiguous" else "unavailable",
        "first_meaningful_at": None,
        "peak_at": None,
        "adverse_peak_at": None,
        "exit_return_pct": None,
        "giveback_from_peak_to_exit_pct": None,
    }
    if path_status != "available":
        if path_status == "ambiguous":
            metrics["first_meaningful_reason"] = "same_bar_intrabar_order_unavailable"
        return metrics
    if direction not in {"long", "short"}:
        metrics["availability"] = "unavailable"
        metrics["evidence_reason"] = "direction_unavailable"
        return metrics
    if entry_price is None or entry_price <= 0 or entry_at is None:
        metrics["availability"] = "unavailable"
        metrics["evidence_reason"] = "entry_identity_unavailable"
        return metrics
    points: list[tuple[datetime, float, str, object | None]] = [
        (
            entry_at,
            entry_price,
            "entry_fill",
            str(entry_fill.get("id")) if entry_fill and entry_fill.get("id") else None,
        )
    ]
    for row in bars:
        timestamp = _bar_ts(row)
        high = _finite(row[2]) if len(row) > 2 else None
        low = _finite(row[3]) if len(row) > 3 else None
        if timestamp is None or high is None or low is None:
            continue
        occurred_at = datetime.fromtimestamp(
            (timestamp + _BAR_MS) / 1000.0, timezone.utc
        )
        favorable = high if direction == "long" else low
        adverse = low if direction == "long" else high
        points.append((occurred_at, favorable, "market_ohlcv_bar", timestamp))
        points.append((occurred_at, adverse, "market_ohlcv_bar", timestamp))
    if exit_fill is not None:
        exit_at = _utc(exit_fill.get("filled_at"))
        exit_price = _finite(exit_fill.get("fill_price"))
        if exit_at is not None and exit_price is not None:
            points.append(
                (
                    exit_at,
                    exit_price,
                    "exit_fill",
                    str(exit_fill.get("id")) if exit_fill.get("id") else None,
                )
            )
            metrics["exit_return_pct"] = round(
                _directional_return_pct(direction, entry_price, exit_price), 8
            )
    favorable_points = [
        point
        for point in points
        if point[1] is not None
        and _directional_return_pct(direction, entry_price, point[1]) >= 0
    ]
    adverse_points = points
    if direction == "long":
        peak = max(favorable_points, key=lambda point: (point[1], -point[0].timestamp()))
        adverse_peak = min(adverse_points, key=lambda point: (point[1], point[0].timestamp()))
    else:
        peak = min(favorable_points, key=lambda point: (point[1], point[0].timestamp()))
        adverse_peak = max(adverse_points, key=lambda point: (point[1], -point[0].timestamp()))
    metrics["peak_at"] = peak[0].isoformat()
    metrics["peak_source_entity_type"] = peak[2]
    metrics["peak_source_entity_id"] = str(peak[3]) if peak[3] is not None else None
    metrics["adverse_peak_at"] = adverse_peak[0].isoformat()
    metrics["adverse_peak_source_entity_id"] = str(adverse_peak[3]) if adverse_peak[3] is not None else None
    mfe = _directional_return_pct(direction, entry_price, peak[1])
    metrics["maximum_favorable_excursion_pct"] = round(max(0.0, mfe), 8)
    metrics["maximum_adverse_excursion_pct"] = round(
        min(0.0, _directional_return_pct(direction, entry_price, adverse_peak[1])), 8
    )
    if threshold_pct is None:
        metrics["first_meaningful_reason"] = "cost_complete_evidence_unavailable"
    else:
        for point in sorted(points, key=lambda item: (item[0], item[2], str(item[3]))):
            return_pct = _directional_return_pct(direction, entry_price, point[1])
            if return_pct > threshold_pct:
                metrics["first_meaningful_status"] = "available"
                metrics["first_meaningful_at"] = point[0].isoformat()
                metrics["first_meaningful_source_entity_type"] = point[2]
                metrics["first_meaningful_source_entity_id"] = (
                    str(point[3]) if point[3] is not None else None
                )
                metrics["first_meaningful_return_pct"] = round(return_pct, 8)
                break
        else:
            metrics["first_meaningful_status"] = "not_reached"
            metrics["first_meaningful_reason"] = "threshold_not_reached"
    if metrics["exit_return_pct"] is not None:
        metrics["giveback_from_peak_to_exit_pct"] = round(
            max(0.0, metrics["maximum_favorable_excursion_pct"] - metrics["exit_return_pct"]),
            8,
        )
    return metrics



def _mark_to_market_curve(
    bars: Sequence[Sequence[Any]],
    *,
    path_status: str,
    path_reason: str,
    direction: str,
    entry_price: float | None,
    closed_visible: bool,
    exit_fill: Mapping[str, Any] | None,
    price_verified: bool,
) -> dict[str, Any]:
    verified_close = bool(closed_visible and price_verified and exit_fill)
    curve: dict[str, Any] = {
        "status": path_status if path_status in {"available", "ambiguous"} else "unavailable",
        "reason": path_reason,
        "kind": (
            "price_return_pct_to_verified_exit"
            if verified_close
            else "unrealized_price_return_pct"
        ),
        "point_interval_seconds": 60,
        "points": [],
    }
    if path_status not in {"available", "ambiguous"}:
        return curve
    if direction not in {"long", "short"}:
        curve.update(status="unavailable", reason="direction_unavailable")
        return curve
    if entry_price is None or entry_price <= 0:
        curve.update(status="unavailable", reason="entry_identity_unavailable")
        return curve
    if closed_visible and not verified_close:
        curve.update(status="unavailable", reason="verified_close_fill_unavailable")
        return curve

    points: list[dict[str, Any]] = []
    for row in bars:
        timestamp = _bar_ts(row)
        close = _finite(row[4]) if len(row) > 4 else None
        if timestamp is None or close is None:
            continue
        points.append(
            {
                "occurred_at": datetime.fromtimestamp(
                    (timestamp + _BAR_MS) / 1000.0, timezone.utc
                ).isoformat(),
                "return_pct": round(
                    _directional_return_pct(direction, entry_price, close), 8
                ),
                "source_entity_type": "market_ohlcv_bar",
                "source_entity_id": str(timestamp),
            }
        )
    if verified_close:
        exit_at = _utc(exit_fill.get("filled_at")) if exit_fill else None
        exit_price = _finite(exit_fill.get("fill_price")) if exit_fill else None
        if exit_at is None or exit_price is None:
            curve.update(status="unavailable", reason="verified_close_fill_unavailable")
            return curve
        points.append(
            {
                "occurred_at": exit_at.isoformat(),
                "return_pct": round(
                    _directional_return_pct(direction, entry_price, exit_price), 8
                ),
                "source_entity_type": "fill",
                "source_entity_id": str(exit_fill.get("id")),
            }
        )
    if not points:
        curve.update(status="unavailable", reason="no_completed_1m_bar_in_prefix")
        return curve
    curve["points"] = points
    return curve


def _economics(
    fact: Mapping[str, Any],
    position: Mapping[str, Any],
    entry_fill: Mapping[str, Any] | None,
    exit_fill: Mapping[str, Any] | None,
    *,
    fill_aggregation_ambiguous: bool = False,
    funding_settlement_visible: bool = True,
) -> dict[str, Any]:
    source = fact.get("economics")
    source = source if isinstance(source, Mapping) else {}
    entry = _finite(position.get("entry_price"))
    quantity = _finite(position.get("quantity"))
    entry_fill_quantity = _finite(entry_fill.get("fill_quantity")) if entry_fill else None
    exit_fill_quantity = _finite(exit_fill.get("fill_quantity")) if exit_fill else None
    exit_price = _finite(exit_fill.get("fill_price")) if exit_fill else None
    direction = str(position.get("direction") or "").strip().lower()
    valid_price_identity = bool(
        direction in {"long", "short"}
        and entry is not None
        and entry > 0
        and exit_price is not None
        and exit_price > 0
        and quantity is not None
        and quantity > 0
    )
    notional = entry * quantity if valid_price_identity else None
    quantity_tolerance = max(1e-9, abs(quantity or 0.0) * 1e-8)
    quantities_verified = bool(
        entry_fill_quantity is not None
        and exit_fill_quantity is not None
        and quantity is not None
        and abs(entry_fill_quantity - quantity) <= quantity_tolerance
        and abs(exit_fill_quantity - quantity) <= quantity_tolerance
    )
    price_verified = bool(
        not fill_aggregation_ambiguous
        and valid_price_identity
        and quantities_verified
        and _as_bool(position.get("entry_fill_verified"))
        and _as_bool(position.get("exit_fill_verified"))
        and str(position.get("entry_price_source") or "") in {"local_fill", "broker_fill"}
        and str(position.get("exit_price_source") or "") in {"local_fill", "broker_fill"}
        and entry_fill is not None
        and exit_fill is not None
    )
    entry_fee = _finite(position.get("entry_fee"))
    exit_fee = _finite(position.get("exit_fee"))
    funding = _finite(position.get("funding_cost")) if funding_settlement_visible else None
    verified_costs = (
        entry_fee + exit_fee + funding
        if entry_fee is not None and exit_fee is not None and funding is not None
        else None
    )
    costs_complete = bool(
        verified_costs is not None
        and entry_fee is not None
        and entry_fee >= 0
        and exit_fee is not None
        and exit_fee >= 0
    )
    gross = _finite(source.get("gross_pnl"))
    net = _finite(source.get("net_pnl"))
    if gross is None and price_verified and entry is not None and exit_price is not None and quantity is not None:
        gross = (exit_price - entry) * quantity if direction == "long" else (entry - exit_price) * quantity
    if net is None:
        net = _finite(position.get("realized_pnl"))
    expected_net = gross - verified_costs if gross is not None and verified_costs is not None else None
    tolerance = max(1e-8, abs(expected_net) * 1e-8, abs(notional or 0.0) * 1e-10) if expected_net is not None else None
    accounting_ok = expected_net is None or net is None or tolerance is None or abs(net - expected_net) <= tolerance
    eligible = bool(
        price_verified
        and costs_complete
        and net is not None
        and accounting_ok
        and source.get("availability") == "available"
    )
    unavailable_reason = None
    if entry_fill is None or exit_fill is None:
        unavailable_reason = "verified_close_fill_unavailable"
    elif fill_aggregation_ambiguous and position.get("status") == "closed":
        unavailable_reason = "multiple_fills_not_aggregated"
    elif not quantities_verified:
        unavailable_reason = "fill_quantity_mismatch"
    elif not funding_settlement_visible:
        unavailable_reason = "funding_settlement_after_cutoff"
    elif not price_verified:
        unavailable_reason = "verified_close_fill_unavailable"
    elif not costs_complete:
        unavailable_reason = "verified_cost_evidence_incomplete"
    elif not accounting_ok:
        unavailable_reason = "realized_pnl_does_not_match_verified_economics"
    elif not eligible:
        unavailable_reason = str(source.get("unavailable_reason") or position.get("outcome_exclusion_reason") or "outcome_not_verified")
    return {
        "availability": "available" if eligible else "unavailable",
        "unavailable_reason": unavailable_reason,
        "verified_gross_pnl": gross if price_verified else None,
        "verified_costs": verified_costs if costs_complete and price_verified else None,
        "verified_net_pnl": net if eligible else None,
        "cost_evidence_status": "complete" if costs_complete else "incomplete",
        "price_evidence_status": "verified" if price_verified else "unavailable",
        "quantity_evidence_status": "verified" if quantities_verified else "unavailable",
        "fill_aggregation": "ambiguous" if fill_aggregation_ambiguous else "single",
        "notional": notional,
        "realized_return_pct": round(net / notional * 100.0, 8) if eligible and net is not None and notional and notional > 0 else None,
        "gross_return_pct": round(gross / notional * 100.0, 8) if price_verified and gross is not None and notional and notional > 0 else None,
        "accounting_residual": round(net - expected_net, 12) if net is not None and expected_net is not None else None,
        "cost_sources": {
            "entry_fee": "positions.entry_fee",
            "exit_fee": "positions.exit_fee",
            "funding_cost": "positions.funding_cost",
        },
    }


def _management_opportunities(
    events: Sequence[Mapping[str, Any]],
    reviews: Sequence[Mapping[str, Any]],
    plans: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") not in {
            "hypothesis_weakening",
            "hypothesis_invalidated",
            "expected_horizon_reached",
            "profit_protection_activated",
        }:
            continue
        occurred = _utc(event.get("occurred_at"))
        trigger_names = {
            "hypothesis_weakening": {"hypothesis_weakening", "thesis_weakening"},
            "hypothesis_invalidated": {"hypothesis_invalidated", "thesis_invalidated"},
            "expected_horizon_reached": {"horizon_due", "horizon_reached", "expected_horizon_reached"},
            "profit_protection_activated": {"protection", "profit_protection", "stop_protection"},
        }.get(str(event.get("event_type")), set())
        linked = [
            row
            for row in reviews
            if (created := _utc(row.get("created_at"))) is not None
            and occurred is not None
            and created >= occurred
            and str(
                _mapping(row.get("position_review")).get("trigger")
                or ""
            ).strip().lower() in trigger_names
        ]
        linked_review_times = [
            created
            for row in linked
            if (created := _utc(row.get("created_at"))) is not None
        ]
        plan_changed = bool(linked_review_times) and any(
            int(plan.get("version") or 0) > 1
            and (plan_at := _utc(plan.get("effective_at"))) is not None
            and (occurred is None or plan_at >= occurred)
            and any(plan_at >= review_at for review_at in linked_review_times)
            for plan in plans
        )
        nodes.append(
            {
                "node_type": event.get("event_type"),
                "occurred_at": event.get("occurred_at"),
                "what_was_known": dict(event.get("facts") or {}),
                "durable_management_decision": "available" if linked else "not_recorded",
                "management_action": [str(row.get("action") or "not_recorded") for row in linked] or "not_recorded",
                "exit_plan_changed": plan_changed if linked else None,
                "linked_decision_ids": [str(row.get("id")) for row in linked if row.get("id")],
            }
        )
    for row in reviews:
        review = row.get("position_review")
        review = review if isinstance(review, Mapping) else {}
        nodes.append(
            {
                "node_type": "position_reviewed",
                "occurred_at": _iso(row.get("created_at")),
                "what_was_known": {
                    "trigger": review.get("trigger", "not_recorded"),
                    "wake_batches": review.get("wake_batches", "not_recorded"),
                    "reasoning": row.get("reasoning") or "not_recorded",
                },
                "durable_management_decision": "available",
                "management_action": row.get("action") or "not_recorded",
                "exit_plan_changed": None,
                "linked_decision_ids": [str(row.get("id"))] if row.get("id") else [],
            }
        )
    nodes.sort(key=lambda node: (_utc(node.get("occurred_at")) or datetime.max.replace(tzinfo=timezone.utc), str(node.get("node_type"))))
    return nodes


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


# ---------------------------------------------------------------------------
# Public builder and deterministic renderer
# ---------------------------------------------------------------------------


def build_position_lifecycle_story(
    position_id: str,
    *,
    as_of_at: str | datetime | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Build one immutable, read-only Position lifecycle story."""
    source = db or repo.get_db()
    if as_of_at is not None and _utc(as_of_at) is None:
        raise ValueError("invalid as_of_at")
    cutoff = _utc(as_of_at) or datetime.now(timezone.utc)
    position = _row(source, "positions", position_id)
    result: dict[str, Any] = {
        "contract_version": STORY_CONTRACT_VERSION,
        "story_unit": "position_lifecycle_story",
        "position_id": position_id,
        "as_of_at": cutoff.isoformat(),
        "read_only": True,
        "availability": "unavailable" if position is None else "available",
        "unavailable_reason": "position_not_found" if position is None else None,
        "opening": {},
        "timeline": [],
        "structured_events": [],
        "economic_path": {},
        "hypothesis_lifecycle": {},
        "management_opportunities": [],
        "observed_mechanisms": [],
        "primary_cause": None,
        "evidence_gaps": [],
        "audit": {
            "position_management_linkage": {
                "status": "unavailable",
                "exact_position_id_used": False,
                "symbol_time_heuristics_used": False,
                "reason": "position_not_found",
            },
            "observer": {},
            "replay": {"status": "unavailable", "reason": "position_not_found"},
            "settlement": {"status": "unavailable", "settled_at": None},
        },
    }
    if position is None or not _visible(position, cutoff, "opened_at"):
        if position is not None:
            result["unavailable_reason"] = "position_opened_after_cutoff"
        return result

    raw_status = str(position.get("status") or "").strip().lower()
    raw_closed_at = _utc(position.get("closed_at"))
    if (raw_status == "closed" and raw_closed_at is None) or (
        raw_status == "open" and position.get("closed_at") not in (None, "")
    ) or raw_status not in {"open", "closed"}:
        result["availability"] = "unavailable"
        result["unavailable_reason"] = "position_close_boundary_invalid"
        result["audit"]["replay"] = {
            "status": "unavailable",
            "reason": "position_close_boundary_invalid",
        }
        return result

    settlement_at = _funding_settlement_at(source, position_id, cutoff=cutoff)
    funding_settlement_visible = True
    snapshot, closed_visible, closed_at = _position_snapshot(position, cutoff=cutoff)
    if (
        _physical_market_type(position.get("market_type")) == "perp"
        and str(position.get("funding_source") or "") == "broker_ledger"
        and settlement_at is None
    ):
        funding_settlement_visible = False
        snapshot.update(
            {
                "funding_cost": None,
                "funding_source": "unavailable",
                "realized_pnl": None,
                "outcome_eligible": False,
                "outcome_exclusion_reason": "funding_settlement_after_cutoff",
            }
        )
    result["audit"]["settlement"] = {
        "status": "available" if funding_settlement_visible else "unavailable",
        "settled_at": settlement_at.isoformat() if settlement_at is not None else None,
        "reason": None if funding_settlement_visible else "funding_settlement_after_cutoff",
    }
    lifecycle_cutoff = (
        closed_at if closed_visible and closed_at is not None else cutoff
    )
    related = _related_rows(source, snapshot, cutoff=lifecycle_cutoff)
    proposal = related.get("proposal")
    entry_fills = [row for row in related.get("entry_fills", []) if isinstance(row, Mapping)]
    entry_fill = entry_fills[0] if entry_fills else None
    entry_at_candidates = [
        timestamp
        for row in entry_fills
        if (timestamp := _utc(row.get("filled_at"))) is not None
    ]
    entry_at = min(entry_at_candidates) if entry_at_candidates else None
    close_fills = [row for row in related.get("close_fills", []) if isinstance(row, Mapping)]
    exit_fill = close_fills[-1] if close_fills else None
    exit_at = _utc(exit_fill.get("filled_at")) if exit_fill else None
    fill_aggregation_ambiguous = len(entry_fills) != 1 or len(close_fills) != 1
    if exit_at is not None and exit_at > cutoff:
        exit_fill = None
        exit_at = None

    fact: dict[str, Any] = {}
    replay_error: str | None = None
    if closed_visible:
        try:
            candidate = build_position_replay(position_id, as_of_at=cutoff, db=source)
            if isinstance(candidate, Mapping):
                fact = dict(candidate)
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            replay_error = type(exc).__name__
    result["audit"]["replay"] = {
        "status": fact.get("availability", "unavailable"),
        "reason": fact.get("unavailable_reason") or replay_error,
    }
    causal_link = fact.get("causal_link")
    causal_status = (
        str(causal_link.get("status") or "available")
        if isinstance(causal_link, Mapping)
        else "available"
    )
    causal_chain_available = causal_status == "available" and _causal_chain_available(
        related, position_id
    )
    if causal_status != "available":
        result["evidence_gaps"].append("position_replay_causal_link_unavailable")
    if not causal_chain_available:
        result["evidence_gaps"].append("position_causal_chain_unavailable")
    instrument = _instrument_identity(fact, snapshot, related.get("opportunity"), source)
    if instrument.get("availability") != "available":
        result["evidence_gaps"].append(
            str(instrument.get("unavailable_reason") or "instrument_identity_unavailable")
        )
    hypothesis = _hypothesis_from(fact, proposal)
    plans = _plan_rows(source, position_id, lifecycle_cutoff)
    result["opening"] = _opening(
        snapshot,
        proposal,
        hypothesis,
        fact,
        instrument,
        entry_fills=entry_fills,
        first_plan=plans[0] if plans else None,
    )

    path = _nested(fact, "stages", "holding", "trade_path")
    path = path if isinstance(path, Mapping) else {}

    timeline = _decision_events(
        related,
        position_id,
        causal_available=causal_chain_available,
    )
    if closed_visible:
        if path:
            bars, path_status, path_reason = _replay_prefix_bars(
                fact, entry_at=entry_at, cutoff=cutoff, exit_at=closed_at
            )
        else:
            bars, path_status, path_reason = [], "unavailable", "replay_path_not_recorded"
    else:
        bars, path_status, path_reason = _market_bars(
            source,
            instrument,
            entry_at=entry_at,
            cutoff=cutoff,
            exit_at=closed_at,
        )
    if closed_visible and fill_aggregation_ambiguous:
        path_status = "ambiguous"
        path_reason = "multiple_fills_not_aggregated"
    if not closed_visible and bars and path_status == "available":
        # A live/as-of prefix is useful for hypothesis evidence, but is not a
        # realized MFE/MAE claim because there is no verified exit boundary.
        path_for_metrics: dict[str, Any] = {
            "path_evidence_status": "unavailable",
            "path_evidence_reason": "open_position_prefix_has_no_exit_boundary",
        }
    else:
        path_for_metrics = dict(path)
    economics = _economics(
        fact,
        snapshot,
        entry_fill,
        exit_fill,
        fill_aggregation_ambiguous=fill_aggregation_ambiguous,
        funding_settlement_visible=funding_settlement_visible,
    )
    mark_to_market_curve = _mark_to_market_curve(
        bars,
        path_status=path_status,
        path_reason=path_reason,
        direction=str(snapshot.get("direction") or "").lower(),
        entry_price=_finite(snapshot.get("entry_price")),
        closed_visible=closed_visible,
        exit_fill=exit_fill,
        price_verified=economics.get("price_evidence_status") == "verified",
    )
    cost_pct = None
    if economics.get("verified_costs") is not None:
        notional = _finite(economics.get("notional"))
        if notional is not None and notional > 0:
            cost_pct = float(economics["verified_costs"]) / notional * 100.0
    threshold = cost_pct + MEANINGFUL_NET_EDGE_BUFFER_PCT if cost_pct is not None else None
    metrics = _path_metrics(
        path_for_metrics,
        bars,
        direction=str(snapshot.get("direction") or "").lower(),
        entry_price=_finite(snapshot.get("entry_price")),
        entry_at=entry_at,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        path_status=(
            "unavailable"
            if not closed_visible
            else (path_status if path_for_metrics else "unavailable")
        ),
        threshold_pct=threshold,
    )

    if metrics.get("first_meaningful_status") == "available":
        timeline.append(
            _event(
                "first_meaningful_favorable_excursion",
                occurred_at=metrics.get("first_meaningful_at"),
                evidence_status="available",
                source_entity_type=metrics.get("first_meaningful_source_entity_type"),
                source_entity_id=metrics.get("first_meaningful_source_entity_id"),
                facts={
                    "return_pct": metrics.get("first_meaningful_return_pct"),
                    "rule": {
                        "version": MEANINGFUL_EXCURSION_RULE_VERSION,
                        "verified_cost_pct": cost_pct,
                        "minimum_net_edge_buffer_pct": MEANINGFUL_NET_EDGE_BUFFER_PCT,
                        "required_favorable_return_pct": threshold,
                    },
                },
            )
        )
    elif metrics.get("first_meaningful_status") == "ambiguous":
        timeline.append(
            _event(
                "first_meaningful_favorable_excursion",
                evidence_status="ambiguous",
                facts={"rule_version": MEANINGFUL_EXCURSION_RULE_VERSION},
                unavailable_reason="same_bar_intrabar_order_unavailable",
            )
        )
    else:
        timeline.append(
            _event(
                "first_meaningful_favorable_excursion",
                evidence_status="unavailable",
                facts={
                    "rule": {
                        "version": MEANINGFUL_EXCURSION_RULE_VERSION,
                        "verified_cost_pct": cost_pct,
                        "minimum_net_edge_buffer_pct": MEANINGFUL_NET_EDGE_BUFFER_PCT,
                        "required_favorable_return_pct": threshold,
                    }
                },
                unavailable_reason=(
                    "cost_complete_evidence_unavailable"
                    if threshold is None
                    else str(metrics.get("first_meaningful_reason") or "threshold_not_reached")
                ),
            )
        )

    if metrics.get("availability") == "available":
        timeline.append(
            _event(
                "maximum_favorable_excursion",
                occurred_at=metrics.get("peak_at"),
                evidence_status="available",
                source_entity_type=metrics.get("peak_source_entity_type"),
                source_entity_id=metrics.get("peak_source_entity_id"),
                facts={"mfe_pct": metrics.get("maximum_favorable_excursion_pct")},
            )
        )
        timeline.append(
            _event(
                "maximum_adverse_excursion",
                occurred_at=metrics.get("adverse_peak_at"),
                evidence_status="available",
                source_entity_type="market_ohlcv_bar" if metrics.get("adverse_peak_source_entity_id") else "fill",
                source_entity_id=metrics.get("adverse_peak_source_entity_id") or (
                    str(entry_fill.get("id"))
                    if entry_fill and entry_fill.get("id")
                    else None
                ),
                facts={"mae_pct": metrics.get("maximum_adverse_excursion_pct")},
            )
        )
    else:
        timeline.extend(
            [
                _event("maximum_favorable_excursion", unavailable_reason=path_reason),
                _event("maximum_adverse_excursion", unavailable_reason=path_reason),
            ]
        )

    hypothesis_lifecycle, hypothesis_events = _hypothesis_lifecycle(hypothesis, bars)
    timeline.extend(hypothesis_events)

    expected_hours = _finite(result["opening"].get("expected_holding_horizon_hours"))
    horizon_at = (
        entry_at + timedelta(hours=expected_hours)
        if entry_at is not None and expected_hours is not None and expected_hours > 0
        else None
    )
    if horizon_at is not None and horizon_at <= lifecycle_cutoff:
        timeline.append(
            _event(
                "expected_horizon_reached",
                occurred_at=horizon_at,
                evidence_status="available",
                source_entity_type="position_hypothesis",
                source_entity_id=str((proposal or {}).get("id") or position_id),
                facts={"expected_horizon_hours": expected_hours},
            )
        )
    else:
        timeline.append(
            _event(
                "expected_horizon_reached",
                unavailable_reason="expected_horizon_not_reached" if horizon_at else "expected_horizon_not_recorded",
            )
        )

    reviews = _exact_reviews(source, position_id, lifecycle_cutoff)
    for review in reviews:
        review_manifest = review.get("position_review")
        review_manifest = review_manifest if isinstance(review_manifest, Mapping) else {}
        timeline.append(
            _event(
                "position_reviewed",
                occurred_at=review.get("created_at"),
                evidence_status="available",
                source_entity_type="pi_decision",
                source_entity_id=str(review.get("id")),
                facts={
                    "target_position_id": position_id,
                    "action": review.get("action"),
                    "reasoning": review.get("reasoning") or "not_recorded",
                    "trigger": review_manifest.get("trigger", "not_recorded"),
                    "wake_batches": review_manifest.get("wake_batches", "not_recorded"),
                },
            )
        )

    for index, plan in enumerate(plans):
        version = int(plan.get("version") or 0)
        if version <= 1:
            continue
        plan_id = f"{position_id}:exit_plan:v{version}"
        timeline.append(
            _event(
                "exit_plan_revised",
                occurred_at=plan.get("effective_at"),
                evidence_status="available",
                source_entity_type="position_exit_plan_version",
                source_entity_id=plan_id,
                facts={
                    "version": version,
                    "stop_price": plan.get("stop_price"),
                    "take_profit_price": plan.get("take_profit_price"),
                    "provenance": plan.get("provenance"),
                },
            )
        )
        previous = plans[index - 1] if index > 0 else None
        old_stop = _finite((previous or {}).get("stop_price"))
        new_stop = _finite(plan.get("stop_price"))
        direction = str(snapshot.get("direction") or "").lower()
        tightened = (
            old_stop is not None
            and new_stop is not None
            and ((direction == "long" and new_stop > old_stop) or (direction == "short" and new_stop < old_stop))
        )
        if tightened:
            timeline.append(
                _event(
                    "stop_revised",
                    occurred_at=plan.get("effective_at"),
                    evidence_status="available",
                    source_entity_type="position_exit_plan_version",
                    source_entity_id=plan_id,
                    facts={"stop_price": new_stop, "previous_stop_price": old_stop},
                )
            )

    state, trigger = _protection_facts(source, position_id, lifecycle_cutoff)
    protection_event: dict[str, Any] | None = None
    if state is not None:
        protection_event = _event(
            "profit_protection_activated",
            occurred_at=state.get("activated_at"),
            evidence_status="available",
            source_entity_type="position_protection_state",
            source_entity_id=position_id,
            facts={"activation_evidence": "immutable_position_protection_state"},
        )
        timeline.append(protection_event)

    if metrics.get("availability") == "available" and exit_fill is not None:
        exit_return = _finite(metrics.get("exit_return_pct"))
        mfe = _finite(metrics.get("maximum_favorable_excursion_pct"))
        if mfe is not None and exit_return is not None and mfe > 0 and exit_return < mfe:
            timeline.append(
                _event(
                    "profit_giveback",
                    occurred_at=exit_fill.get("filled_at"),
                    evidence_status="available",
                    source_entity_type="fill",
                    source_entity_id=(str(exit_fill.get("id")) if exit_fill.get("id") else None),
                    facts={"giveback_pct": metrics.get("giveback_from_peak_to_exit_pct")},
                )
            )
    elif path_status == "ambiguous" and str(snapshot.get("close_reason") or "") == "stop_loss":
        timeline.append(
            _event(
                "profit_giveback",
                evidence_status="ambiguous",
                unavailable_reason="same_bar_intrabar_order_unavailable",
            )
        )

    close_orders = related.get("close_orders", [])
    close_order = close_orders[0] if close_orders else None
    if trigger is not None:
        timeline.append(
            _event(
                "exit_triggered",
                occurred_at=trigger.get("detected_at"),
                evidence_status="available",
                source_entity_type="position_protection_trigger",
                source_entity_id=str(trigger.get("trigger_id")),
                facts={
                    "reason": trigger.get("reason"),
                    "stop_hit": trigger.get("stop_hit"),
                    "target_hit": trigger.get("target_hit"),
                    "bar_open_ms": trigger.get("bar_open_ms"),
                    "bar_hash": trigger.get("bar_hash"),
                    "plan_version": trigger.get("plan_version"),
                    "plan_hash": trigger.get("plan_hash"),
                    "collision_policy": trigger.get("collision_policy"),
                },
            )
        )
    elif isinstance(close_order, Mapping):
        timeline.append(
            _event(
                "exit_triggered",
                occurred_at=close_order.get("created_at"),
                evidence_status="available",
                source_entity_type="order",
                source_entity_id=(str(close_order.get("id")) if close_order.get("id") else None),
                facts={"close_reason": close_order.get("close_reason")},
            )
        )
    elif str(snapshot.get("close_reason") or "").strip():
        timeline.append(
            _event(
                "exit_triggered",
                occurred_at=closed_at,
                evidence_status="available",
                source_entity_type="position",
                source_entity_id=position_id,
                facts={"close_reason": snapshot.get("close_reason")},
            )
        )
    else:
        timeline.append(_event("exit_triggered", unavailable_reason="close_trigger_not_recorded"))
    if len(close_fills) == 1 and exit_fill is not None:
        quantity_verified = economics.get("quantity_evidence_status") == "verified"
        timeline.append(
            _event(
                "exit_filled",
                occurred_at=exit_fill.get("filled_at") if quantity_verified else None,
                evidence_status="available" if quantity_verified else "unavailable",
                source_entity_type="fill",
                source_entity_id=(str(exit_fill.get("id")) if exit_fill.get("id") else None),
                facts={"fill_price": exit_fill.get("fill_price"), "fill_quantity": exit_fill.get("fill_quantity")},
                unavailable_reason=(
                    None
                    if quantity_verified
                    else str(economics.get("unavailable_reason") or "fill_quantity_mismatch")
                ),
            )
        )
    elif close_fills:
        timeline.append(
            _event(
                "exit_filled",
                evidence_status="ambiguous",
                facts={
                    "fill_ids": [str(row.get("id")) for row in close_fills if row.get("id")],
                    "fill_count": len(close_fills),
                },
                unavailable_reason="multiple_close_fills_not_aggregated",
            )
        )
    else:
        timeline.append(_event("exit_filled", unavailable_reason="verified_close_fill_not_recorded"))

    if economics.get("verified_costs") is not None:
        timeline.append(
            _event(
                "verified_costs",
                occurred_at=exit_at,
                evidence_status="available",
                source_entity_type="position",
                source_entity_id=position_id,
                facts={
                    "entry_fee": snapshot.get("entry_fee"),
                    "exit_fee": snapshot.get("exit_fee"),
                    "funding_cost": snapshot.get("funding_cost"),
                    "total_costs": economics.get("verified_costs"),
                },
            )
        )
    else:
        timeline.append(
            _event(
                "verified_costs",
                evidence_status="unavailable",
                source_entity_type="position",
                source_entity_id=position_id,
                facts={"cost_evidence_status": economics.get("cost_evidence_status")},
                unavailable_reason=(
                    economics.get("unavailable_reason")
                    or "verified_cost_evidence_unavailable"
                ),
            )
        )

    timeline.sort(key=_event_sort_key)
    result["timeline"] = timeline
    result["structured_events"] = _structured_events(timeline)
    peak_at = _utc(metrics.get("peak_at"))
    invalidation_at = _utc(
        next(
            (
                event.get("occurred_at")
                for event in hypothesis_events
                if event.get("event_type") == "hypothesis_invalidated"
            ),
            None,
        )
    )
    result["economic_path"] = {
        **economics,
        "path_evidence_status": (
            "unavailable" if not closed_visible else path_status
        ),
        "path_evidence_reason": (
            "open_position_prefix_has_no_exit_boundary"
            if not closed_visible
            else path_reason
        ),
        "mark_to_market_curve": mark_to_market_curve,
        "maximum_favorable_excursion_pct": metrics.get("maximum_favorable_excursion_pct"),
        "maximum_adverse_excursion_pct": metrics.get("maximum_adverse_excursion_pct"),
        "mfe_lower_bound_pct": metrics.get("mfe_lower_bound_pct"),
        "mfe_upper_bound_pct": metrics.get("mfe_upper_bound_pct"),
        "mae_lower_bound_pct": metrics.get("mae_lower_bound_pct"),
        "mae_upper_bound_pct": metrics.get("mae_upper_bound_pct"),
        "giveback_from_peak_to_exit_pct": metrics.get("giveback_from_peak_to_exit_pct"),
        "same_bar_ordering": metrics.get("same_bar_ordering"),
        "time_entry_to_peak_seconds": (
            (peak_at - entry_at).total_seconds()
            if peak_at is not None and entry_at is not None
            else None
        ),
        "time_peak_to_exit_seconds": (
            (exit_at - peak_at).total_seconds()
            if exit_at is not None and peak_at is not None
            else None
        ),
        "time_hypothesis_invalidation_to_exit_seconds": (
            (exit_at - invalidation_at).total_seconds()
            if exit_at is not None and invalidation_at is not None
            else None
        ),
        "actual_holding_duration_seconds": (
            (exit_at - entry_at).total_seconds() if exit_at is not None and entry_at is not None else None
        ),
        "expected_holding_duration_seconds": expected_hours * 3600.0 if expected_hours is not None else None,
        "horizon_overrun_seconds": (
            (exit_at - entry_at).total_seconds() - expected_hours * 3600.0
            if exit_at is not None and entry_at is not None and expected_hours is not None
            else None
        ),
        "meaningful_favorable_excursion_rule": {
            "version": MEANINGFUL_EXCURSION_RULE_VERSION,
            "verified_cost_pct": cost_pct,
            "minimum_net_edge_buffer_pct": MEANINGFUL_NET_EDGE_BUFFER_PCT,
            "required_favorable_return_pct": threshold,
            "status": "available" if threshold is not None else "unavailable",
        },
    }
    result["hypothesis_lifecycle"] = hypothesis_lifecycle
    result["management_opportunities"] = _management_opportunities(timeline, reviews, plans)

    invalidated_at = _utc(next((event.get("occurred_at") for event in hypothesis_events if event.get("event_type") == "hypothesis_invalidated"), None))
    mechanisms: list[str] = []
    net = _finite(economics.get("verified_net_pnl"))
    gross = _finite(economics.get("verified_gross_pnl"))
    if net is not None and net < 0:
        if metrics.get("first_meaningful_status") == "not_reached":
            mechanisms.append("never_economically_favorable")
        if any(event.get("event_type") == "profit_giveback" and event.get("evidence_status") == "available" for event in timeline):
            mechanisms.append("profit_giveback")
        if invalidated_at is not None and exit_at is not None and invalidated_at < exit_at:
            mechanisms.append("hypothesis_invalidated_before_exit")
        if horizon_at is not None and exit_at is not None and exit_at > horizon_at:
            mechanisms.append("expected_horizon_exceeded")
        if gross is not None and gross >= 0:
            mechanisms.append("cost_flip")
    if closed_visible and economics.get("availability") != "available":
        mechanisms.append("accounting_gap")
    if any(event.get("event_type") == "stop_revised" for event in timeline):
        mechanisms.append("protection_tightened")
    result["observed_mechanisms"] = list(dict.fromkeys(mechanisms))

    observer = _observer_audit(
        source,
        position_id,
        entry_at=entry_at,
        cutoff=cutoff,
        closed_at=closed_at,
    )
    result["audit"]["observer"] = observer
    if observer.get("missing_bucket_count"):
        result["evidence_gaps"].append("missing_position_path_observer_buckets")
    if observer.get("status") == "partial":
        result["evidence_gaps"].append("position_path_observer_coverage_partial")
    if reviews:
        result["audit"]["position_management_linkage"] = {
            "status": "available",
            "exact_position_id_used": True,
            "symbol_time_heuristics_used": False,
            "linked_decision_ids": [str(row.get("id")) for row in reviews if row.get("id")],
        }
    else:
        result["audit"]["position_management_linkage"] = {
            "status": "unavailable",
            "exact_position_id_used": False,
            "symbol_time_heuristics_used": False,
            "reason": "no_exact_position_management_linkage",
        }
        result["evidence_gaps"].append("position_management_linkage_not_recorded")
    if hypothesis is None:
        result["evidence_gaps"].append("opening_hypothesis_not_recorded")
    if path_status != "available":
        result["evidence_gaps"].append("ordered_1m_path_unavailable")
    if closed_visible and economics.get("availability") != "available":
        result["evidence_gaps"].append("verified_close_economics_unavailable")
    result["evidence_gaps"] = sorted(set(result["evidence_gaps"]))
    return result


def _render_value(value: object) -> str:
    if value is None:
        return "unavailable"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def render_position_lifecycle_story(story: Mapping[str, Any]) -> str:
    """Render the structured contract deterministically; no LLM is involved."""

    def _inline(value: object) -> str:
        if isinstance(value, (Mapping, list, tuple)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return _render_value(value)

    opening = story.get("opening")
    opening = opening if isinstance(opening, Mapping) else {}
    instrument = opening.get("instrument_identity")
    instrument = instrument if isinstance(instrument, Mapping) else {}
    symbol = instrument.get("symbol") or story.get("position_id") or "unknown"
    direction = opening.get("direction") or "unknown"
    lines = [f"Position: {symbol} {direction}"]
    if story.get("availability") != "available":
        lines.append(
            f"Availability: {story.get('availability') or 'unavailable'}; "
            f"reason={story.get('unavailable_reason') or 'not recorded'}."
        )
    lines.extend(["", "Opening"])
    if opening.get("entry_fill_time"):
        lines.append(
            f"{opening['entry_fill_time']} - Entry filled at "
            f"{_render_value(opening.get('entry_price'))}."
        )
    else:
        lines.append("Entry fill: unavailable.")
    lines.extend(
        [
            f"Opening thesis: {_render_value(opening.get('original_thesis'))}.",
            f"Supporting signals: {_inline(opening.get('supporting_signals'))}.",
            f"Opening reasoning: {_render_value(opening.get('opening_reasoning'))}.",
            f"Bear case: {_render_value(opening.get('bear_case'))}.",
            f"Key assumptions: {_inline(opening.get('key_assumptions'))}.",
            f"Expected horizon: {_render_value(opening.get('expected_holding_horizon_hours'))}h.",
            f"Initial Stop: {_render_value(opening.get('initial_stop'))}.",
            f"Initial Take Profit: {_render_value(opening.get('initial_take_profit'))}.",
            f"Initial quote risk: {_render_value(opening.get('initial_risk_quote'))}.",
            f"Runtime provenance: {_inline((opening.get('provenance') or {}).get('runtime') if isinstance(opening.get('provenance'), Mapping) else None)}.",
        ]
    )

    lines.extend(["", "Lifecycle"])
    timeline = story.get("timeline")
    timeline = timeline if isinstance(timeline, list) else []
    available_events = [
        event
        for event in timeline
        if isinstance(event, Mapping) and event.get("evidence_status") == "available"
    ]
    if not available_events:
        lines.append("No lifecycle event is available from exact persisted evidence.")
    descriptions = {
        "entry_filled": "Entry fill verified.",
        "first_meaningful_favorable_excursion": "First economically meaningful favorable excursion.",
        "hypothesis_weakening": "Opening hypothesis became weakening.",
        "hypothesis_invalidated": "Opening hypothesis became invalidated.",
        "expected_horizon_reached": "Expected holding horizon reached.",
        "position_reviewed": "Exactly linked Position review recorded.",
        "profit_protection_activated": "Profit protection activated.",
        "profit_giveback": "Prior favorable excursion was given back.",
        "exit_filled": "Close fill verified.",
        "verified_costs": "Verified costs recorded.",
    }
    for event in available_events:
        event_type = str(event.get("event_type") or "unknown_event")
        at = event.get("occurred_at") or "unknown time"
        facts = event.get("facts")
        facts = facts if isinstance(facts, Mapping) else {}
        if event_type == "maximum_favorable_excursion":
            description = (
                "Maximum favorable excursion reached: "
                f"{_render_value(facts.get('mfe_pct'))}%."
            )
        elif event_type == "maximum_adverse_excursion":
            description = (
                "Maximum adverse excursion reached: "
                f"{_render_value(facts.get('mae_pct'))}%."
            )
        elif event_type == "exit_plan_revised":
            description = (
                f"ExitPlan revised to version {_render_value(facts.get('version'))}."
            )
        elif event_type == "stop_revised":
            description = f"Stop revised to {_render_value(facts.get('stop_price'))}."
        elif event_type == "exit_triggered":
            description = (
                f"Exit triggered ({_render_value(facts.get('reason') or facts.get('close_reason'))}); "
                f"stop_hit={_render_value(facts.get('stop_hit'))}, "
                f"target_hit={_render_value(facts.get('target_hit'))}, "
                f"collision_policy={_render_value(facts.get('collision_policy'))}, "
                f"plan_version={_render_value(facts.get('plan_version'))}, "
                f"plan_hash={_render_value(facts.get('plan_hash'))}, "
                f"bar_hash={_render_value(facts.get('bar_hash'))}."
            )
        else:
            description = descriptions.get(event_type, f"{event_type}.")
        lines.append(f"{at} - {description}")

    limits = [
        event
        for event in timeline
        if isinstance(event, Mapping) and event.get("evidence_status") != "available"
    ]
    lines.extend(["", "Evidence limits"])
    if limits:
        for event in limits:
            facts = event.get("facts")
            facts = facts if isinstance(facts, Mapping) else {}
            lines.append(
                f"- {event.get('event_type')}: "
                f"{event.get('evidence_status') or 'unavailable'}; "
                f"{event.get('unavailable_reason') or facts.get('unavailable_reason') or 'reason not recorded'}"
            )
    else:
        lines.append("- none recorded")

    lines.extend(["", "Economic path"])
    economics = story.get("economic_path")
    economics = economics if isinstance(economics, Mapping) else {}
    lines.append(f"Gross PnL: {_render_value(economics.get('verified_gross_pnl'))}")
    lines.append(f"Costs: {_render_value(economics.get('verified_costs'))}")
    lines.append(f"Net PnL: {_render_value(economics.get('verified_net_pnl'))}")
    curve = economics.get("mark_to_market_curve")
    curve = curve if isinstance(curve, Mapping) else {}
    lines.append(
        f"Mark-to-market curve: status={_render_value(curve.get('status'))}, "
        f"kind={_render_value(curve.get('kind'))}, "
        f"points={len(curve.get('points') or [])}, "
        f"reason={_render_value(curve.get('reason'))}."
    )
    lines.append(
        f"Path evidence: status={_render_value(economics.get('path_evidence_status'))}, "
        f"reason={_render_value(economics.get('path_evidence_reason'))}, "
        f"same_bar_ordering={_render_value(economics.get('same_bar_ordering'))}."
    )
    lines.append(
        "Meaningful excursion rule: "
        f"{_inline(economics.get('meaningful_favorable_excursion_rule'))}."
    )

    lines.extend(["", "Management opportunities"])
    management = story.get("management_opportunities")
    management = management if isinstance(management, list) else []
    if management:
        for node in management:
            if not isinstance(node, Mapping):
                continue
            lines.append(
                f"- {node.get('node_type')} at {node.get('occurred_at') or 'unknown time'}: "
                f"what_was_known={_inline(node.get('what_was_known'))}; "
                f"durable_decision={_render_value(node.get('durable_management_decision'))}; "
                f"action={_inline(node.get('management_action'))}; "
                f"exit_plan_changed={_render_value(node.get('exit_plan_changed'))}; "
                f"linked_decision_ids={_inline(node.get('linked_decision_ids'))}"
            )
    else:
        lines.append("- not recorded")

    audit = story.get("audit")
    audit = audit if isinstance(audit, Mapping) else {}
    observer = audit.get("observer")
    observer = observer if isinstance(observer, Mapping) else {}
    lines.extend(["", "Observer coverage"])
    lines.append(
        f"status={_render_value(observer.get('status'))}, "
        f"expected={_render_value(observer.get('expected_bucket_count'))}, "
        f"available={_render_value(observer.get('available_bucket_count'))}, "
        f"unavailable={_render_value(observer.get('unavailable_bucket_count'))}, "
        f"missing={_render_value(observer.get('missing_bucket_count'))}, "
        f"missing_bucket_open_ms={_inline(observer.get('missing_bucket_open_ms'))}"
    )

    lines.extend(["", "Evidence gaps"])
    gaps = story.get("evidence_gaps")
    gaps = gaps if isinstance(gaps, list) else []
    lines.extend(f"- {gap}" for gap in gaps) if gaps else lines.append("- none recorded")
    lines.extend(["", "Observed mechanisms"])
    mechanisms = story.get("observed_mechanisms")
    if isinstance(mechanisms, list) and mechanisms:
        lines.extend(f"- {mechanism}" for mechanism in mechanisms)
    else:
        lines.append("- none proven")
    lines.extend(["", "Causal conclusion", "Insufficient evidence to identify one unique cause."])
    return "\n".join(lines)


__all__ = [
    "MEANINGFUL_EXCURSION_RULE_VERSION",
    "POSITION_LIFECYCLE_EVENT_TYPES",
    "STORY_CONTRACT_VERSION",
    "build_position_lifecycle_story",
    "render_position_lifecycle_story",
]

"""Pure Position Story event vocabulary, ordering and structured projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any


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
    "position_path_observed": 45,
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
    "position_path_observed",
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
    "position_path_observed": (
        "position_id",
        "bucket_open_ms",
        "observed_at",
        "producer_commit",
        "runtime_session_id",
        "capture_mode",
        "evidence_status",
        "evidence_reason",
        "venue",
        "symbol",
        "market_type",
        "direction",
        "entry_price",
        "quantity",
        "mark_price",
        "unrealized_pnl",
        "entry_fee",
        "funding_cost",
        "funding_source",
        "bar_open",
        "bar_high",
        "bar_low",
        "bar_close",
        "bar_volume",
        "bar_provider",
        "stop_price",
        "take_profit_price",
        "exit_plan_version",
    ),
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

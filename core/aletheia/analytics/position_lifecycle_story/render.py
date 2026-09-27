"""Pure human-readable rendering of an already-built Position Story."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


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
        elif event_type == "position_path_observed":
            description = (
                "5m Position-path observation recorded: "
                f"bucket={_render_value(facts.get('bucket_open_ms'))}, "
                f"status={_render_value(event.get('evidence_status'))}."
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

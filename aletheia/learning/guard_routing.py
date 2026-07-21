"""Route automatic block suggestions to non-blocking guard shadow candidates."""

from __future__ import annotations

import hashlib
import json

from aletheia.knowledge.manager import create_guard_candidate


_BLOCK_ACTIONS = {"block_scanner", "block_direction", "block_symbol"}


def route_automatic_block_recommendation(
    recommendation: dict | None, *, evidence: dict, source_id: str,
) -> dict | None:
    if not recommendation or recommendation.get("action") not in _BLOCK_ACTIONS:
        return recommendation
    action = str(recommendation["action"])
    field, value = _predicate(action, recommendation)
    if not field or not value:
        return {"action": "guard_shadow_unavailable", "reason": "invalid_block_scope"}
    applies_to = _evidence_scope(action, str(value), evidence)
    if applies_to is None:
        return {"action": "guard_shadow_unavailable", "reason": "invalid_block_scope"}
    identity = json.dumps(
        {
            "action": action,
            "field": field,
            "value": value,
            "source_id": source_id,
            "applies_to": applies_to,
        },
        sort_keys=True,
    )
    guard_id = f"auto_shadow_{hashlib.sha256(identity.encode()).hexdigest()[:16]}"
    metadata = {
        "type": "Trading Guard", "guard_id": guard_id, "status": "candidate",
        "scope": "paper_entry", "enforcement": "advisory", "severity": "warning",
        "score_penalty": 0, "block_candidate": False, "auto_generated": True,
        "proposed_score_penalty": 0, "proposed_block_candidate": True,
        "shadow_only": True,
        "source_type": "automatic_lesson_recommendation", "source_id": source_id,
        "predicates": [{"field": field, "op": "eq", "value": value}],
        "applies_to": applies_to, "evidence_summary": evidence,
        "tags": ["auto-generated", "shadow-only", action],
    }
    try:
        item = create_guard_candidate(
            title=f"Shadow guard proposal: {action} {value}",
            content=(
                "Automatic Learning recommendation routed to guard shadow evaluation. "
                "It has no Risk Engine or paper-entry blocking authority.\n\n"
                f"Evidence: {json.dumps(evidence, sort_keys=True, default=str)}"
            ),
            metadata=metadata,
        )
    except Exception:
        return {"action": "guard_shadow_unavailable", "reason": "candidate_persist_failed"}
    return {
        "action": "guard_shadow_candidate", "guard_id": guard_id,
        "guard_candidate_id": getattr(item, "id", None), "original_action": action,
    }


def _evidence_scope(action: str, value: str, evidence: dict) -> dict | None:
    """Build an exact source scope; never turn partial evidence into a global Guard."""
    factor_rows: list[tuple[str, str, str]] = []
    for raw in evidence.get("factor_keys") or []:
        parts = str(raw).split(":")
        if len(parts) < 3:
            continue
        scanner, symbol, direction = parts[:3]
        if scanner and "/" in symbol and direction in {"long", "short"}:
            factor_rows.append((scanner, symbol, direction))

    if action == "block_scanner":
        factor_rows = [row for row in factor_rows if row[0] == value]
    elif action == "block_symbol":
        factor_rows = [row for row in factor_rows if row[1] == value]
    elif action == "block_direction":
        factor_rows = [row for row in factor_rows if row[2] == value]

    symbols = {row[1] for row in factor_rows}
    families = {row[0] for row in factor_rows}
    directions = {row[2] for row in factor_rows}
    if not factor_rows:
        symbols.update(_evidence_values(evidence, "symbol", "symbols"))
        families.update(_evidence_values(evidence, "scanner_family", "scanner_families"))
        directions.update(_evidence_values(evidence, "direction", "directions"))
        if action == "block_scanner":
            families.add(value)
        elif action == "block_symbol":
            symbols.add(value)
        elif action == "block_direction":
            directions.add(value)

    if not symbols or not families:
        return None
    scope = {
        "asset_class": ["crypto"],
        "symbol": sorted(symbols),
        "scanner_family": sorted(families),
    }
    if directions:
        scope["direction"] = sorted(directions)
    return scope


def _evidence_values(evidence: dict, singular: str, plural: str) -> set[str]:
    raw = evidence.get(plural)
    if raw is None:
        raw = evidence.get(singular)
    if raw in (None, ""):
        return set()
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    return {str(item) for item in values if item not in (None, "")}


def _predicate(action: str, recommendation: dict) -> tuple[str | None, str | None]:
    if action == "block_scanner":
        return "scanner_family", recommendation.get("scanner")
    if action == "block_direction":
        return "direction", recommendation.get("direction")
    if action == "block_symbol":
        return "symbol", recommendation.get("symbol")
    return None, None

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
    scope = _scope(field, value, evidence)
    if scope is None:
        return {"action": "guard_shadow_unavailable", "reason": "invalid_block_scope"}
    identity = json.dumps(
        {"action": action, "applies_to": scope, "field": field, "value": value, "source_id": source_id},
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
        "applies_to": scope, "evidence_summary": evidence,
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


def _predicate(action: str, recommendation: dict) -> tuple[str | None, str | None]:
    if action == "block_scanner":
        return "scanner_family", recommendation.get("scanner")
    if action == "block_direction":
        return "direction", recommendation.get("direction")
    if action == "block_symbol":
        return "symbol", recommendation.get("symbol")
    return None, None


def _scope(field: str, value: str, evidence: dict) -> dict | None:
    """Derive applies_to scope from the primary predicate + evidence factor_keys.

    Returns None if minimum scope cannot be derived (requires at least symbol
    or scanner_family to avoid a globally-scoped guard shadow).
    """
    factor_keys = evidence.get("factor_keys") or []
    scanner_families = evidence.get("scanner_families") or []

    scope: dict = {"asset_class": ["crypto"], field: [value]}

    if field == "scanner_family":
        # block_scanner: derive unique symbols from factor_keys for this scanner
        symbols = list(dict.fromkeys(
            fk.split(":")[1]
            for fk in factor_keys
            if fk.split(":")[0] == value
            and len(fk.split(":")) >= 2
            and fk.split(":")[1] != "unknown"
        ))
        if symbols:
            scope["symbol"] = symbols

    elif field == "symbol":
        # block_symbol: derive scanner families from factor_keys for this symbol
        scanners = list(dict.fromkeys(
            fk.split(":")[0]
            for fk in factor_keys
            if len(fk.split(":")) >= 2
            and fk.split(":")[1] == value
            and fk.split(":")[0] != "unknown"
        ))
        if not scanners:
            scanners = [s for s in scanner_families if s and s != "unknown"]
        if scanners:
            scope["scanner_family"] = scanners

    elif field == "direction":
        # block_direction: derive both symbols and scanners from matching factor_keys
        matching = [fk for fk in factor_keys if len(fk.split(":")) >= 3 and fk.split(":")[2] == value]
        symbols = list(dict.fromkeys(fk.split(":")[1] for fk in matching if fk.split(":")[1] != "unknown"))
        scanners = list(dict.fromkeys(fk.split(":")[0] for fk in matching if fk.split(":")[0] != "unknown"))
        if symbols:
            scope["symbol"] = symbols
        if scanners:
            scope["scanner_family"] = scanners

    # Fail closed: at least one of symbol or scanner_family must be present to
    # avoid creating a guard that matches any asset in the crypto class.
    if "symbol" not in scope and "scanner_family" not in scope:
        return None

    return scope

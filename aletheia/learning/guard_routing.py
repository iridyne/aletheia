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
    # Derive source scope from evidence so the shadow is not a global crypto block.
    # If the scope cannot be established, fail closed rather than write a broad guard.
    scope = _derive_scope(action, recommendation, evidence)
    if scope is None:
        return {"action": "guard_shadow_unavailable", "reason": "invalid_block_scope"}
    # Scope is part of the stable identity so two recommendations with the same
    # action/value but different cohort evidence produce distinct guard shadows.
    identity = json.dumps(
        {
            "action": action, "field": field, "value": value,
            "scope": scope, "source_id": source_id,
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

    guard_candidate_id = str(getattr(item, "id", "") or "")
    if not guard_candidate_id:
        return {
            "action": "guard_shadow_candidate",
            "guard_id": guard_id,
            "hypothesis_status": "source_identity_unavailable",
            "original_action": action,
        }
    hypothesis_action = {
        "type": "candidate_guard",
        "guard_id": guard_id,
        "block_candidate": True,
        "score_penalty": 0,
    }
    try:
        from aletheia.learning.hypotheses import nominate_learning_hypothesis

        hypothesis = nominate_learning_hypothesis(
            path_id="ranker_learned_guard",
            scope={"guard_id": guard_id},
            action=hypothesis_action,
            source_type="guard_candidate",
            source_artifact_ids=[source_id, guard_candidate_id],
            source_cohort=f"learned_guard:{guard_id}:v1",
            consumer="paper_trader_ranker",
            target_horizon="4h",
            evidence_summary={
                "guard_candidate_id": guard_candidate_id,
                "applies_to": scope,
                "evidence": evidence,
            },
        )
    except Exception as exc:
        return {
            "action": "guard_shadow_candidate",
            "guard_id": guard_id,
            "guard_candidate_id": guard_candidate_id,
            "hypothesis_status": "nomination_failed",
            "nomination_error": f"{type(exc).__name__}:{str(exc)[:160]}",
            "original_action": action,
        }
    return {
        "action": "guard_shadow_candidate",
        "guard_id": guard_id,
        "guard_candidate_id": guard_candidate_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "hypothesis_status": "proposed",
        "original_action": action,
    }


def _predicate(action: str, recommendation: dict) -> tuple[str | None, str | None]:
    if action == "block_scanner":
        return "scanner_family", recommendation.get("scanner")
    if action == "block_direction":
        return "direction", recommendation.get("direction")
    if action == "block_symbol":
        return "symbol", recommendation.get("symbol")
    return None, None


def _derive_scope(action: str, recommendation: dict, evidence: dict) -> dict | None:
    """Derive a non-global applies_to scope from evidence factor keys.

    Returns None (fail-closed) if neither explicit evidence nor factor keys
    supply the required symbol and scanner_family cohort (#197).

    factor_key format: "{scanner}:{symbol}:{direction}:{conf}:{late}"
    """
    # Extract symbols and scanner families from factor_key evidence strings.
    symbols: list[str] = []
    scanners_from_keys: list[str] = []
    factor_keys = (
        evidence["supporting_factor_keys"]
        if "supporting_factor_keys" in evidence
        else evidence.get("factor_keys") or []
    )
    for fk in factor_keys:
        parts = str(fk).split(":")
        if len(parts) == 5:
            sc, sym = parts[0], parts[1]
            if sym and sym != "unknown":
                symbols.append(sym)
            if sc and sc != "unknown":
                scanners_from_keys.append(sc)

    # Deduplicate while preserving order.
    symbols = list(dict.fromkeys(symbols))
    scanners_from_keys = list(dict.fromkeys(scanners_from_keys))
    scanners_from_evidence = [
        s for s in (evidence.get("scanner_families") or []) if s
    ]

    if action == "block_symbol":
        # The symbol is the recommendation value itself; scanner from evidence narrows scope.
        sym = recommendation.get("symbol")
        if not sym:
            return None
        sc_list = scanners_from_evidence or scanners_from_keys
        if not sc_list:
            return None
        return {"symbol": [sym], "scanner_family": sc_list}

    if action == "block_scanner":
        scanner = recommendation.get("scanner")
        if not scanner:
            return None
        if not symbols:
            return None
        return {"symbol": symbols, "scanner_family": [scanner]}

    if action == "block_direction":
        if not symbols:
            return None
        sc_list = scanners_from_evidence or scanners_from_keys
        if not sc_list:
            return None
        return {"symbol": symbols, "scanner_family": sc_list}

    return None

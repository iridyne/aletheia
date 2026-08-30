"""Route automatic block suggestions to non-blocking guard shadow candidates."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from aletheia.knowledge.manager import create_guard_candidate
from aletheia.learning.hypothesis_research import research_learning_hypothesis
from aletheia.learning.hypotheses import HypothesisResearchEvidenceV1
from aletheia.learning.t0_context import T0ContextDomainV1, T0ContextEnvelopeV1
from aletheia.learning.t0_coverage import get_t0_coverage_contract


_BLOCK_ACTIONS = {"block_scanner", "block_direction", "block_symbol"}


async def route_automatic_block_recommendation(
    recommendation: dict | None,
    *,
    evidence: dict,
    source_id: str,
    research_evidence: HypothesisResearchEvidenceV1 | dict[str, Any] | None = None,
    deadline: float | None = None,
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
    source_ids = list(dict.fromkeys(
        value
        for value in (
            str(source_id).strip(),
            *(str(item).strip() for item in evidence.get("supporting_source_ids") or []),
        )
        if value
    ))
    if research_evidence is None:
        research = HypothesisResearchEvidenceV1.model_validate(
            await research_learning_hypothesis(
                candidate_explanation=_candidate_explanation(
                    action=action, field=field, value=value, scope=scope,
                ),
                source_artifact_ids=source_ids,
                evidence=evidence,
                proposed_scope=scope,
                target_horizon="4h",
                path_id="ranker_learned_guard",
                consumer="paper ranker",
                t0_envelopes=_build_ranker_guard_t0_envelopes(source_ids),
                deadline=deadline,
            )
        )
    else:
        research = HypothesisResearchEvidenceV1.model_validate(research_evidence)
    if set(source_ids) != set(research.observation.source_artifact_ids):
        return {
            "action": "guard_shadow_unavailable",
            "reason": "research_source_identity_mismatch",
            "research_status": "insufficient_context",
        }
    research_payload = research.canonical_payload()
    if research.research_status not in {"ready", "narrowed"}:
        return {
            "action": "guard_shadow_unavailable",
            "reason": f"research_{research.research_status}",
            "research_status": research.research_status,
            "research_evidence": research_payload,
        }
    if research.relevant_context.material_scope:
        return {
            "action": "guard_shadow_unavailable",
            "reason": "research_material_scope_not_expressible_by_guard",
            "research_status": research.research_status,
            "research_evidence": research_payload,
        }

    hypothesis_action = {
        "type": "candidate_guard",
        "guard_id": guard_id,
        "block_candidate": True,
        "score_penalty": 0,
    }
    # Match the exact identity consumed by learned_guard_block_authorized().
    # ``guard_id`` already hashes the bounded applies_to scope.
    hypothesis_scope: dict[str, Any] = {"guard_id": guard_id}
    source_cohort = f"learned_guard:{guard_id}:v1"
    from aletheia.learning.hypotheses import (
        hypothesis_fingerprint,
        nominate_learning_hypothesis,
    )
    from aletheia.learning.ledger import get_hypothesis_by_fingerprint
    from aletheia.learning.registry import get_learning_path

    path = get_learning_path("ranker_learned_guard")
    fingerprint = hypothesis_fingerprint(
        path_id=path.path_id,
        scope=hypothesis_scope,
        action=hypothesis_action,
        target_horizon="4h",
        source_cohort=source_cohort,
        behavior_contract_version=path.behavior_contract_version,
    )
    existing = get_hypothesis_by_fingerprint(fingerprint)
    if existing is not None:
        try:
            existing_sources = json.loads(existing["source_artifact_ids_json"])
            existing_research = json.loads(existing["evidence_summary_json"])
        except (KeyError, TypeError, ValueError):
            return {
                "action": "guard_shadow_unavailable",
                "reason": "existing_nomination_provenance_invalid",
            }
        linked = [
            value for value in existing_sources
            if str(value) not in set(source_ids)
        ]
        if set(source_ids).issubset(existing_sources) and existing_research == research_payload:
            return {
                "action": "guard_shadow_candidate",
                "guard_id": guard_id,
                "guard_candidate_id": str(linked[-1]) if linked else "",
                "hypothesis_id": str(existing["hypothesis_id"]),
                "hypothesis_status": "proposed",
                "research_status": research.research_status,
                "original_action": action,
            }
        return {
            "action": "guard_shadow_unavailable",
            "reason": "changed_evidence_requires_new_nomination",
            "hypothesis_id": str(existing["hypothesis_id"]),
        }

    metadata = {
        "type": "Trading Guard", "guard_id": guard_id, "status": "candidate",
        "scope": "paper_entry", "enforcement": "advisory", "severity": "warning",
        "score_penalty": 0, "block_candidate": False, "auto_generated": True,
        "proposed_score_penalty": 0, "proposed_block_candidate": True,
        "shadow_only": True,
        "source_type": "automatic_lesson_recommendation", "source_id": source_id,
        "predicates": [{"field": field, "op": "eq", "value": value}],
        "applies_to": scope, "evidence_summary": evidence,
        "hypothesis_research": research_payload,
        "research_status": research.research_status,
        "tags": ["auto-generated", "shadow-only", action],
    }
    try:
        item = create_guard_candidate(
            title=f"Shadow guard proposal: {action} {value}",
            content=(
                "Automatic Learning recommendation routed to guard shadow evaluation. "
                "It has no Risk Engine or paper-entry blocking authority.\n\n"
                f"Research evidence: {json.dumps(research_payload, sort_keys=True, default=str)}"
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
    try:
        hypothesis = nominate_learning_hypothesis(
            path_id="ranker_learned_guard",
            scope=hypothesis_scope,
            action=hypothesis_action,
            source_type="guard_candidate",
            source_artifact_ids=[*source_ids, guard_candidate_id],
            source_cohort=source_cohort,
            consumer="paper_trader_ranker",
            target_horizon="4h",
            evidence_summary=research,
            require_research_evidence=True,
        )
    except Exception:
        from aletheia.knowledge.manager import archive_item

        archive_item(guard_candidate_id)
        return {
            "action": "guard_shadow_unavailable",
            "reason": "hypothesis_nomination_failed",
        }
    # Exact retries returned before candidate creation; this is the first publication.
    linked_candidate_id = guard_candidate_id
    try:
        artifact_ids = json.loads(hypothesis.source_artifact_ids_json or "[]")
    except (TypeError, ValueError, AttributeError):
        artifact_ids = []
    for artifact_id in reversed(artifact_ids):
        if str(artifact_id) and str(artifact_id) != source_id:
            linked_candidate_id = str(artifact_id)
            break
    return {
        "action": "guard_shadow_candidate",
        "guard_id": guard_id,
        "guard_candidate_id": linked_candidate_id,
        "hypothesis_id": hypothesis.hypothesis_id,
        "hypothesis_status": "proposed",
        "research_status": research.research_status,
        "original_action": action,
    }


def _build_ranker_guard_t0_envelopes(
    source_artifact_ids: list[str],
) -> list[T0ContextEnvelopeV1]:
    """Reconstruct exact Ranker T0 projections from immutable decision facts."""
    from aletheia.knowledge.document import read_knowledge_document
    from aletheia.storage import repositories as repo
    from aletheia.storage.sqlite import get_db

    contract = get_t0_coverage_contract("ranker_learned_guard")
    db = get_db()
    envelopes: list[T0ContextEnvelopeV1] = []
    for artifact_id in source_artifact_ids:
        item = repo.get_knowledge_item(artifact_id)
        if item is None or item.get("item_type") != "trade_case_study":
            continue
        document = read_knowledge_document(item)
        if document.state in {"integrity_conflict", "unavailable"}:
            continue
        position_id = str(document.metadata.get("position_id") or "").strip()
        position = repo.get_position(position_id) if position_id else None
        if not position:
            continue
        opportunity_id = str(position.get("opportunity_id") or "").strip()
        opened_at = str(position.get("opened_at") or "").strip()
        opened = _t0_instant(opened_at)
        if not opportunity_id or opened is None:
            continue
        candidates = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM filter_decisions WHERE opportunity_id=? "
                "AND stage='paper_ranker' ORDER BY evaluated_at, sequence_no, id",
                (opportunity_id,),
            ).fetchall()
            if (
                (evaluated := _t0_instant(str(row["evaluated_at"] or ""))) is not None
                and evaluated <= opened
            )
        ]
        if not candidates:
            continue
        anchor_times = [
            value
            for row in candidates
            if (value := _t0_instant(str(row["evaluated_at"]))) is not None
        ]
        anchor = max(anchor_times)
        cohort = [
            row for row in candidates
            if _t0_instant(str(row["evaluated_at"])) == anchor
        ]
        if len(cohort) > 20:
            continue
        run_ids = {str(row.get("run_id") or "") for row in cohort}
        if len(run_ids) > 1:
            continue
        source_refs = [f"filter_decision:{row['id']}" for row in cohort]
        source_hashes = [
            hashlib.sha256(
                json.dumps(row, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            for row in cohort
        ]
        inputs = {
            str(row["filter_name"]): parsed
            for row in cohort
            if (parsed := _json_object(row.get("input_snapshot")))
        }
        ordering = [
            {
                "filter_name": row["filter_name"],
                "sequence_no": row["sequence_no"],
                "filter_version": row["filter_version"],
                "parameters": _json_object(row.get("parameter_snapshot")),
            }
            for row in cohort
        ]
        first = cohort[0]
        population = {
            key: first.get(key)
            for key in ("run_id", "universe_snapshot_id", "universe_member_id")
            if first.get(key)
        }
        guard_rows = [
            row for row in cohort if row["filter_name"] == "guard_candidate_block"
        ]
        guard_manifest = {
            "decisions": [
                {
                    "decision": row["decision"],
                    "reason_code": row["reason_code"],
                    "inputs": _json_object(row.get("input_snapshot")),
                    "parameters": _json_object(row.get("parameter_snapshot")),
                }
                for row in guard_rows
            ]
        } if guard_rows else {}
        outcome = db.execute(
            "SELECT id, outcome_candidate_key, horizon, entry_price_usd, "
            "entry_spread_bps, funding_rate_at_entry, entry_ts, cost_model_version "
            "FROM filter_candidate_outcomes "
            "WHERE outcome_candidate_key=? AND horizon='4h' "
            "ORDER BY entry_ts, id LIMIT 1",
            (f"opportunity:{opportunity_id}",),
        ).fetchone()
        outcome_data = dict(outcome) if outcome is not None else {}
        entry_at = _t0_instant(str(outcome_data.get("entry_ts") or ""))
        outcome_matches_cohort = (
            outcome_data.get("outcome_candidate_key")
            == f"opportunity:{opportunity_id}"
            and outcome_data.get("horizon") == "4h"
            and entry_at is not None
            and entry_at <= anchor
        )
        liquidity = {
            key: outcome_data[key]
            for key in (
                "entry_price_usd", "entry_spread_bps",
                "funding_rate_at_entry", "cost_model_version",
            )
            if outcome_matches_cohort and outcome_data.get(key) is not None
        }
        liquidity_refs = (
            [f"filter_candidate_outcome:{outcome_data['id']}"]
            if liquidity else []
        )
        liquidity_hashes = (
            [
                hashlib.sha256(
                    json.dumps(
                        outcome_data, sort_keys=True, default=str,
                    ).encode("utf-8")
                ).hexdigest()
            ]
            if liquidity else []
        )
        domain_values = {
            "candidate": {
                key: first.get(key)
                for key in (
                    "candidate_key", "opportunity_id", "decision", "reason_code",
                )
                if first.get(key)
            },
            "population": population,
            "features": inputs,
            "market_identity": {
                key: first.get(key)
                for key in (
                    "venue", "symbol", "market_type", "direction", "scanner_family",
                )
                if first.get(key)
            },
            "regime": (
                {"regime_tag": first["regime_tag"]} if first.get("regime_tag") else {}
            ),
            "liquidity_cost": liquidity,
            "ordering_policy": {"gates": ordering} if ordering else {},
            "guard_manifest": guard_manifest,
        }
        domains = [
            _t0_domain(
                name,
                domain_values.get(name, {}),
                source_refs=(
                    liquidity_refs if name == "liquidity_cost" else source_refs
                ),
                source_hashes=(
                    liquidity_hashes if name == "liquidity_cost" else source_hashes
                ),
                observed_at=(
                    entry_at.isoformat()
                    if name == "liquidity_cost" and entry_at is not None
                    else anchor.isoformat()
                ),
            )
            for name in (*contract.required_domains, *contract.recommended_domains)
        ]
        population_identity = (
            f"universe_snapshot:{population['universe_snapshot_id']}"
            if population.get("universe_snapshot_id")
            else f"ranker_run:{population['run_id']}"
            if population.get("run_id")
            else None
        )
        observed_world_hash = hashlib.sha256(
            json.dumps(domain_values, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        versions = sorted({str(row["filter_version"]) for row in cohort})
        envelopes.append(T0ContextEnvelopeV1(
            path_id=contract.path_id,
            consumer="paper ranker",
            consumer_stage=contract.consumer_stage,
            t0_anchor_kind=contract.anchor_kind,
            anchor_entity_id=f"filter_decision:{first['id']}",
            anchor_at=anchor.isoformat(),
            capture_mode="reconstructed_exact",
            runtime_session_id=None,
            producer_commit=(
                str(first["code_commit"]) if first.get("code_commit") else None
            ),
            behavior_fingerprint=hashlib.sha256(
                json.dumps(versions).encode("utf-8")
            ).hexdigest(),
            policy_epoch=None,
            required_domains=list(contract.required_domains),
            recommended_domains=list(contract.recommended_domains),
            domains=domains,
            root_causal_identity=f"position:{position_id}",
            population_identity=population_identity,
            observed_world_hash=observed_world_hash,
            completeness_reasons=[
                f"required_domain_unavailable:{domain.domain}"
                for domain in domains
                if domain.domain in contract.required_domains
                and domain.state != "available"
            ],
        ))
    return envelopes


def _t0_domain(
    name: str,
    values: dict[str, Any],
    *,
    source_refs: list[str],
    source_hashes: list[str],
    observed_at: str,
) -> T0ContextDomainV1:
    if not values:
        return T0ContextDomainV1(
            domain=name,
            state="unavailable",
            source_class="observed_fact",
            unavailable_reason="canonical decision-time fact unavailable",
        )
    return T0ContextDomainV1(
        domain=name,
        state="available",
        source_class="observed_fact",
        values=values,
        source_refs=source_refs,
        source_hashes=source_hashes,
        source_observed_at=observed_at,
        received_at=observed_at,
    )


def _json_object(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _t0_instant(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _candidate_explanation(
    *, action: str, field: str, value: str, scope: dict[str, Any],
) -> str:
    return (
        f"Applying the proposed {action} treatment for {field}={value} within "
        f"the recorded scope {json.dumps(scope, sort_keys=True)} may improve net "
        "candidate utility over the declared 4h horizon."
    )


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

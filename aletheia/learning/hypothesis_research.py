"""Bounded causal red-team research for explicit Learning nominations."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia.agents.helpers import llm_structured
from aletheia.learning.hypotheses import (
    HypothesisCounterfactualCheckV1,
    HypothesisRedTeamSummaryV1,
    HypothesisRelevantContextV1,
    HypothesisResearchEvidenceV1,
    HypothesisResearchObservationV1,
    ResearchStatus,
    ResearchText,
)
from aletheia.model_provider.router import get_provider_for_tier


_MISSING_CONTEXT_DIMENSIONS = {
    "regime": ("regime",),
    "volatility": ("volatil", "atr"),
    "funding_oi": ("funding", "open_interest", "oi_"),
    "liquidity_tradability": ("liquidity", "tradability", "spread", "slippage"),
    "catalyst_news": ("catalyst", "news", "headline", "macro"),
    "decision_evidence": ("reasoning", "bear_case", "assumption", "evidence"),
}
_BASE_SCOPE_KEYS = {"symbol", "direction", "scanner_family", "market_type"}


class _CausalRedTeamReview(BaseModel):
    """One structured review; this is reasoning evidence, never causal proof."""

    model_config = ConfigDict(extra="forbid")

    competing_explanations: list[ResearchText] = Field(default_factory=list, max_length=3)
    no_material_alternative_reason: ResearchText | None = None
    material_confounders_or_missing: list[ResearchText] = Field(
        default_factory=list, max_length=10,
    )
    smallest_falsification_test: ResearchText
    decision: ResearchStatus
    narrowed_candidate_explanation: ResearchText | None = None
    material_context_keys: list[ResearchText] = Field(default_factory=list, max_length=3)
    reasoning: ResearchText

    @model_validator(mode="after")
    def _review_is_complete(self) -> _CausalRedTeamReview:
        if not self.competing_explanations and not self.no_material_alternative_reason:
            raise ValueError(
                "red-team review requires a competing explanation or explicit reason"
            )
        if self.decision == "narrowed" and (
            not self.narrowed_candidate_explanation or not self.material_context_keys
        ):
            raise ValueError("narrowed review requires an explanation and material context")
        return self


async def research_learning_hypothesis(
    *,
    candidate_explanation: str,
    source_artifact_ids: Iterable[str],
    evidence: dict[str, Any],
    proposed_scope: dict[str, Any],
    target_horizon: str,
    max_counterfactual_checks: int = 3,
    deadline: float | None = None,
) -> HypothesisResearchEvidenceV1:
    """Return canonical evidence after exact-context checks and one red-team call."""
    if not 0 <= max_counterfactual_checks <= 3:
        raise ValueError("hypothesis research permits at most three counterfactual checks")
    artifact_ids = list(dict.fromkeys(
        str(value).strip() for value in source_artifact_ids if str(value).strip()
    ))
    if not artifact_ids:
        raise ValueError("hypothesis research requires a source artifact identity")

    target_horizon_hours = _target_horizon_hours(target_horizon)
    recorded, missing, outcome_ids, cases = _assemble_exact_context(
        artifact_ids, target_horizon,
    )
    try:
        declared_case_count = int(evidence["case_count"])
    except (KeyError, TypeError, ValueError):
        declared_case_count = None
    if declared_case_count is not None and declared_case_count != len(artifact_ids):
        missing.append("selected_source_aggregate_count_mismatch")
    observation = HypothesisResearchObservationV1(
        statement=_observation_statement(cases),
        source_artifact_ids=artifact_ids,
        outcome_identities=outcome_ids,
    )
    if (
        not recorded
        or len(outcome_ids) != len(artifact_ids)
        or "selected_source_aggregate_count_mismatch" in missing
    ):
        return _insufficient_evidence(
            observation=observation,
            candidate_explanation=candidate_explanation,
            recorded=recorded,
            missing=missing or ["exact_source_outcome_context_incomplete"],
            reason=(
                "Exact source-to-outcome identity or recorded T0 context is incomplete; "
                "causal review cannot proceed."
            ),
        )

    checks = _bounded_counterfactual_checks(
        cases, max_counterfactual_checks, target_horizon_hours,
    )
    if not checks:
        missing.append("exact_counterfactual_evidence_unavailable")
    allowed_material_keys = sorted(
        key for key, values in recorded.items()
        if key not in _BASE_SCOPE_KEYS and len(values) == len(artifact_ids)
    )
    explicit_missing = _bounded_missing(missing)
    review_payload = {
        "candidate_explanation": candidate_explanation,
        "proposed_scope": _bounded(proposed_scope),
        "target_horizon": target_horizon,
        "observation": observation.model_dump(mode="json"),
        "recorded_t0_context": recorded,
        "explicitly_missing_context": explicit_missing,
        "counterfactual_checks": [check.model_dump(mode="json") for check in checks],
        "allowed_material_context_keys": allowed_material_keys,
    }
    payload, error = await llm_structured(
        system=(
            "You are the single bounded causal red-team reviewer for a proposed "
            "paper-trading Learning hypothesis. Use only the supplied exact identities, "
            "recorded T0 context, and bounded counterfactual checks. Never infer a missing "
            "fact from later outcomes. Answer: the strongest simpler competing explanation, "
            "material missing context, the smallest future falsification test, and whether "
            "the candidate is ready, narrowed, rejected, or insufficient_context. If narrowed, "
            "select at most three keys only from allowed_material_context_keys and rewrite the "
            "candidate as a conditional claim. This review is reasoning evidence only and "
            "cannot grant experiment or trading authority. Return only the schema JSON."
        ),
        user=json.dumps(review_payload, sort_keys=True, ensure_ascii=True, default=str),
        schema=_CausalRedTeamReview,
        max_tokens=800,
        provider=get_provider_for_tier("deep"),
        caller="learning.hypothesis_causal_red_team",
        reasoning_effort="none",
        deadline=deadline,
    )
    if error or payload is None:
        return _insufficient_evidence(
            observation=observation,
            candidate_explanation=candidate_explanation,
            recorded=recorded,
            missing=[*missing, f"causal_red_team_unavailable:{str(error or 'empty_payload')[:160]}"],
            checks=checks,
            reason="The structured causal red-team review was unavailable.",
        )

    review = _CausalRedTeamReview.model_validate(payload)
    selected_keys = list(dict.fromkeys(review.material_context_keys))
    invalid_keys = [key for key in selected_keys if key not in allowed_material_keys]
    status: ResearchStatus = review.decision
    candidate = candidate_explanation
    material_scope: dict[str, Any] = {}
    if (review.material_confounders_or_missing or invalid_keys) and status in {
        "ready", "narrowed",
    }:
        status = "insufficient_context"
    elif status == "narrowed":
        narrowed_candidate = str(review.narrowed_candidate_explanation).strip()
        if (
            invalid_keys
            or not selected_keys
            or narrowed_candidate == candidate_explanation.strip()
        ):
            status = "insufficient_context"
            if narrowed_candidate == candidate_explanation.strip():
                missing.append("narrowed_candidate_explanation_unchanged")
        else:
            candidate = narrowed_candidate
            material_scope = {key: recorded[key] for key in selected_keys}

    falsification = review.smallest_falsification_test
    competing = list(dict.fromkeys(review.competing_explanations))[:3]
    missing_evidence = _bounded_missing([
        *missing,
        *review.material_confounders_or_missing,
        *([f"invalid_material_context_key:{key}" for key in invalid_keys]),
    ])
    return HypothesisResearchEvidenceV1(
        observation=observation,
        original_candidate_explanation=candidate_explanation,
        candidate_explanation=candidate,
        competing_explanations=competing,
        no_material_alternative_reason=(
            review.no_material_alternative_reason if not competing else None
        ),
        relevant_context=HypothesisRelevantContextV1(
            recorded_at_t0=recorded,
            missing=_bounded_missing(missing),
            material_scope=material_scope,
        ),
        falsification_condition=falsification,
        supporting_evidence=_supporting_evidence(evidence),
        opposing_evidence=_counterfactual_summaries(checks),
        missing_evidence=missing_evidence,
        counterfactual_checks=checks,
        red_team_summary=HypothesisRedTeamSummaryV1(
            strongest_simpler_competing_explanation=(competing[0] if competing else None),
            material_confounders_or_missing=review.material_confounders_or_missing,
            smallest_falsification_test=falsification,
            decision=status,
            reasoning=review.reasoning,
        ),
        research_status=status,
    )


def _assemble_exact_context(
    artifact_ids: list[str], target_horizon: str,
) -> tuple[dict[str, Any], list[str], list[str], list[dict[str, Any]]]:
    from aletheia.analytics.trade_attribution import build_trade_attribution_for_position
    from aletheia.knowledge.frontmatter import parse_frontmatter
    from aletheia.storage import repositories as repo

    recorded: dict[str, list[dict[str, Any]]] = {}
    missing: list[str] = []
    outcome_ids: list[str] = []
    cases: list[dict[str, Any]] = []
    for artifact_id in artifact_ids:
        item = repo.get_knowledge_item(artifact_id)
        if item is None or item.get("item_type") != "trade_case_study":
            missing.append(f"source_artifact_unavailable:{artifact_id}")
            continue
        metadata: dict[str, Any] = {}
        file_path = str(item.get("file_path") or "").strip()
        if file_path:
            try:
                metadata, _ = parse_frontmatter(Path(file_path))
                metadata = metadata or {}
            except Exception:
                missing.append(f"source_frontmatter_unavailable:{artifact_id}")
        position_id = str(metadata.get("position_id") or "").strip()
        if not position_id:
            missing.append(f"position_identity_unavailable:{artifact_id}")
            continue
        attribution = build_trade_attribution_for_position(position_id)
        if attribution is None:
            missing.append(f"exact_trade_attribution_unavailable:{artifact_id}")
            continue
        if not _holding_matches_horizon(attribution.get("holding_hours"), target_horizon):
            missing.append(f"outcome_horizon_mismatch:{artifact_id}")
            continue
        position = repo.get_position(position_id) or {}
        opportunity_id = str(attribution.get("opportunity_id") or "").strip()
        opportunity = repo.get_opportunity(opportunity_id) if opportunity_id else None
        context = _recorded_case_context(attribution, position, opportunity or {})
        for dimension in _MISSING_CONTEXT_DIMENSIONS:
            if dimension not in context:
                missing.append(f"t0_context_missing:{dimension}:{artifact_id}")
        for key, value in context.items():
            recorded.setdefault(key, []).append({
                "source_artifact_id": artifact_id,
                "value": _bounded(value),
            })
        outcome_ids.append(f"position:{position_id}")
        cases.append({
            "source_artifact_id": artifact_id,
            "position_id": position_id,
            "opportunity_id": opportunity_id,
            "realized_pnl_pct": attribution.get("realized_pnl_pct"),
            "opened_at": attribution.get("opened_at"),
        })
    return (
        {key: recorded[key] for key in sorted(recorded)},
        list(dict.fromkeys(missing)),
        list(dict.fromkeys(outcome_ids)),
        cases,
    )


def _recorded_case_context(
    attribution: dict[str, Any], position: dict[str, Any], opportunity: dict[str, Any],
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key in ("symbol", "direction", "scanner_family", "market_type"):
        value = attribution.get(key)
        if _available(value):
            context[key] = value
    regime = position.get("regime_tag") or opportunity.get("regime_tag")
    if _available(regime):
        context["regime"] = regime

    funding_oi = {
        key: opportunity.get(key)
        for key in ("funding_rate", "open_interest", "oi_delta_1h")
        if _available(opportunity.get(key))
    }
    if funding_oi:
        context["funding_oi"] = funding_oi
    liquidity = {
        key: value for key, value in {
            "tradability_score": attribution.get("tradability_score"),
            "risk_flags": attribution.get("risk_flags"),
            "crowding_risk": attribution.get("crowding_risk"),
        }.items() if _available(value)
    }
    if liquidity:
        context["liquidity_tradability"] = liquidity

    decision = {
        key: attribution.get(key)
        for key in (
            "pi_decision_reasoning",
            "pi_decision_bear_case",
            "pi_decision_assumptions",
            "proposal_agent_rationale",
            "decision_evidence_manifest",
        )
        if _available(attribution.get(key))
    }
    if decision:
        context["decision_evidence"] = decision
    source_context = [
        position.get("entry_market_summary"),
        opportunity.get("key_observations"),
        attribution.get("decision_evidence_manifest"),
    ]
    for dimension, keywords in {
        "volatility": _MISSING_CONTEXT_DIMENSIONS["volatility"],
        "catalyst_news": _MISSING_CONTEXT_DIMENSIONS["catalyst_news"],
    }.items():
        matches = _matching_context(source_context, keywords)
        if matches:
            context[dimension] = matches
    return context


def _bounded_counterfactual_checks(
    cases: list[dict[str, Any]], max_checks: int, target_horizon_hours: float,
) -> list[HypothesisCounterfactualCheckV1]:
    from aletheia.storage import repositories as repo

    if max_checks == 0:
        return []
    checks: list[HypothesisCounterfactualCheckV1] = []
    # ponytail: inspect at most three exact batches; add broader replay only if
    # runtime evidence shows this screen misses a material failure class.
    for case in cases[:3]:
        opportunity_id = str(case.get("opportunity_id") or "")
        selected_rows = repo.list_shadow_trades(
            opportunity_id=opportunity_id,
            was_selected=True,
            resolved=True,
            limit=2,
        )
        if len(selected_rows) != 1:
            continue
        selected = selected_rows[0]
        if (
            not selected.get("batch_id")
            or not _at_or_before(selected.get("created_at"), case.get("opened_at"))
            or not _shadow_matches_horizon(selected, target_horizon_hours)
            or selected.get("net_pnl_pct") is None
        ):
            continue
        batch = repo.list_shadow_trades(
            batch_id=str(selected["batch_id"]), resolved=True, limit=10,
        )
        alternatives = sorted(
            (
                row for row in batch
                if row.get("id") != selected.get("id")
                and not row.get("was_selected")
                and row.get("outcome_candidate_key")
                and row.get("net_pnl_pct") is not None
                and _shadow_matches_horizon(row, target_horizon_hours)
            ),
            key=lambda row: (int(row.get("rank") or 10_000), str(row.get("id") or "")),
        )
        for alternative in alternatives:
            checks.append(HypothesisCounterfactualCheckV1(
                check_type="selected_vs_exact_shadow_alternative",
                source_identity=f"shadow_trade:{selected['id']}",
                comparison_identity=f"shadow_trade:{alternative['id']}",
                evidence={
                    "batch_id": selected["batch_id"],
                    "selected_opportunity_id": opportunity_id,
                    "selected_net_pnl_pct": selected["net_pnl_pct"],
                    "alternative_opportunity_id": alternative.get("opportunity_id"),
                    "alternative_net_pnl_pct": alternative.get("net_pnl_pct"),
                    "alternative_rank": alternative.get("rank"),
                },
            ))
            if len(checks) >= max_checks:
                return checks
    return checks


def _insufficient_evidence(
    *,
    observation: HypothesisResearchObservationV1,
    candidate_explanation: str,
    recorded: dict[str, Any],
    missing: list[str],
    reason: str,
    checks: list[HypothesisCounterfactualCheckV1] | None = None,
) -> HypothesisResearchEvidenceV1:
    falsification = (
        "Collect exact recorded T0 context and rerun the bounded causal review before nomination."
    )
    return HypothesisResearchEvidenceV1(
        observation=observation,
        original_candidate_explanation=candidate_explanation,
        candidate_explanation=candidate_explanation,
        no_material_alternative_reason=reason,
        relevant_context=HypothesisRelevantContextV1(
            recorded_at_t0=recorded,
            missing=_bounded_missing(missing),
        ),
        falsification_condition=falsification,
        missing_evidence=_bounded_missing(missing),
        counterfactual_checks=checks or [],
        red_team_summary=HypothesisRedTeamSummaryV1(
            material_confounders_or_missing=list(dict.fromkeys(missing))[:10],
            smallest_falsification_test=falsification,
            decision="insufficient_context",
            reasoning=reason,
        ),
        research_status="insufficient_context",
    )


def _target_horizon_hours(target_horizon: str) -> float:
    text = str(target_horizon).strip().lower()
    try:
        hours = float(text[:-1]) if text.endswith("h") else math.nan
    except ValueError:
        hours = math.nan
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError(f"unsupported hypothesis target horizon: {target_horizon}")
    return hours


def _holding_matches_horizon(value: Any, target_horizon: str) -> bool:
    try:
        observed = float(value)
    except (TypeError, ValueError):
        return False
    return math.isclose(
        observed,
        _target_horizon_hours(target_horizon),
        rel_tol=0.0,
        abs_tol=1e-6,
    )


def _shadow_matches_horizon(row: dict[str, Any], target_hours: float) -> bool:
    price_source = str(row.get("settlement_price_source") or "")
    lag_seconds = row.get("settlement_lag_sec")
    if price_source in {"position_close", "stale_forward_ohlcv"}:
        try:
            observed_hours = float(lag_seconds) / 3_600.0
        except (TypeError, ValueError):
            return False
    else:
        created_at = _parse_timestamp(row.get("created_at"))
        resolved_at = _parse_timestamp(row.get("resolved_at"))
        if created_at is None or resolved_at is None:
            return False
        observed_hours = (resolved_at - created_at).total_seconds() / 3_600.0
        if lag_seconds is not None and price_source:
            try:
                observed_hours -= float(lag_seconds) / 3_600.0
            except (TypeError, ValueError):
                return False
    return math.isclose(
        observed_hours, target_hours, rel_tol=0.0, abs_tol=1e-6,
    )


def _at_or_before(value: Any, boundary: Any) -> bool:
    parsed = _parse_timestamp(value)
    parsed_boundary = _parse_timestamp(boundary)
    return (
        parsed is not None
        and parsed_boundary is not None
        and parsed <= parsed_boundary
    )


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_missing(values: Iterable[Any]) -> list[str]:
    entries = list(dict.fromkeys(
        text
        for value in values
        if (text := str(value).strip()[:2_000])
    ))
    if len(entries) <= 20:
        return entries
    return [
        *entries[:19],
        f"additional_missing_context_entries:{len(entries) - 19}",
    ]


def _observation_statement(cases: list[dict[str, Any]]) -> str:
    compact = [
        {
            "source_artifact_id": case["source_artifact_id"],
            "position_id": case["position_id"],
            "realized_pnl_pct": case.get("realized_pnl_pct"),
        }
        for case in cases
    ]
    return (
        f"{len(cases)} exact horizon-matched trade case(s) produced these outcomes: "
        f"{json.dumps(_bounded(compact), sort_keys=True, default=str)}"
    )[:2_000]


def _supporting_evidence(evidence: dict[str, Any]) -> list[str]:
    compact = {
        key: evidence.get(key)
        for key in (
            "case_count", "outcome_counts", "avg_pnl_pct", "factor_keys",
            "supporting_factor_keys", "scanner_families",
        )
        if evidence.get(key) is not None
    }
    return [(
        "Recorded aggregate: "
        f"{json.dumps(_bounded(compact), sort_keys=True, default=str)}"
    )[:2_000]]


def _counterfactual_summaries(
    checks: list[HypothesisCounterfactualCheckV1],
) -> list[str]:
    return [
        (
            f"{check.comparison_identity}: "
            f"{json.dumps(check.evidence, sort_keys=True, default=str)}"
        )[:2_000]
        for check in checks
    ]


def _available(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {
            "", "unknown", "not recorded", "not available", "unavailable",
            "ambiguous_integrity_error", "conflicting_integrity_error",
        }
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _matching_context(values: list[Any], keywords: tuple[str, ...]) -> list[Any]:
    matches = []
    for value in values:
        if not _available(value):
            continue
        text = json.dumps(value, sort_keys=True, default=str).lower()
        if any(keyword in text for keyword in keywords):
            matches.append(_bounded(value))
    return matches[:3]


def _bounded(value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return str(value)[:500]
    if isinstance(value, dict):
        return {
            str(key): _bounded(item, depth + 1)
            for key, item in list(sorted(value.items(), key=lambda pair: str(pair[0])))[:20]
        }
    if isinstance(value, (list, tuple)):
        return [_bounded(item, depth + 1) for item in list(value)[:10]]
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in {"{", "["}:
            try:
                return _bounded(json.loads(text), depth + 1)
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        return text[:1_000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1_000]

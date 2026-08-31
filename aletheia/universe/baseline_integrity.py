"""Read-only Universe baseline integrity for pre-competition measurement.

This module deliberately has no persistence, Learning, Risk, Entry Plan, Gateway,
or allocation authority.  It exposes the full family evidence that the current
winner-take-all scanner computes while preserving ``universe-step-v3`` as the
frozen production control.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from aletheia.universe.scanners import (
    UNIVERSE_SCORER_BEHAVIOR_VERSION,
    run_all_scanners,
    score_derivatives,
    score_event,
    score_mean_reversion,
    score_momentum,
    score_short_bias,
    score_volatility,
)

BASELINE_INTEGRITY_CONTRACT_VERSION = "universe-baseline-integrity/v1"

_DERIVATIVE_MARKET_TYPES = frozenset({"perp", "swap", "futures", "usdt_futures"})

_FAMILY_ORDER = (
    "momentum",
    "mean_reversion",
    "volatility",
    "derivatives",
    "event",
    "short_bias",
)

_SCORERS: dict[str, Callable[[dict], tuple[float, list[str], str | None]]] = {
    "momentum": score_momentum,
    "mean_reversion": score_mean_reversion,
    "volatility": score_volatility,
    "derivatives": score_derivatives,
    "event": score_event,
    "short_bias": score_short_bias,
}

_FAMILY_SEMANTICS: dict[str, dict[str, Any]] = {
    "momentum": {
        "display_name": "Momentum",
        "semantic_role": "directional_return_archetype",
        "true_external_event_evidence": None,
        "capability_gaps": (),
    },
    "mean_reversion": {
        "display_name": "Mean Reversion",
        "semantic_role": "directional_return_archetype",
        "true_external_event_evidence": None,
        "capability_gaps": (),
    },
    "volatility": {
        "display_name": "Volatility State / Breakout Proxy",
        "semantic_role": "market_state_conditioned_directional_proxy",
        "true_external_event_evidence": None,
        "capability_gaps": (),
    },
    "derivatives": {
        "display_name": "Derivatives Positioning Proxy",
        "semantic_role": "positioning_evidence_domain_proxy",
        "true_external_event_evidence": None,
        "capability_gaps": (
            "basis",
            "liquidation",
            "cross_venue_positioning",
        ),
    },
    "event": {
        "display_name": "Market Shock / Event Proxy",
        "semantic_role": "market_shock_proxy",
        "true_external_event_evidence": False,
        "capability_gaps": (
            "news_provenance",
            "announcement_provenance",
            "social_attention_provenance",
        ),
    },
    "short_bias": {
        "display_name": "Short Bias",
        "semantic_role": "directional_specialization",
        "true_external_event_evidence": None,
        "capability_gaps": (),
    },
}


class UniverseBaselineIntegrityError(ValueError):
    """The read-only reconstruction disagrees with the frozen authority output."""


def family_semantics() -> dict[str, dict[str, Any]]:
    """Return a defensive copy of the frozen family semantic registry."""

    return {
        family: {
            **metadata,
            "capability_gaps": tuple(metadata["capability_gaps"]),
        }
        for family, metadata in _FAMILY_SEMANTICS.items()
    }


def _applicability(family: str, feature: Mapping[str, object]) -> str:
    market_type = str(feature.get("market_type") or "").lower()
    context_kind = str((feature.get("analysis_context") or {}).get("kind") or "")
    if family == "derivatives" and market_type not in _DERIVATIVE_MARKET_TYPES:
        return "not_applicable"
    if family == "short_bias" and context_kind == "stock_linked_perp":
        return "not_applicable"
    return "applicable"


def build_lossless_family_evidence(
    feature: Mapping[str, object],
) -> dict[str, dict[str, Any]]:
    """Reconstruct every family result without granting any family new authority."""

    raw_feature = dict(feature)
    evidence: dict[str, dict[str, Any]] = {}
    for family in _FAMILY_ORDER:
        score, reasons, direction = _SCORERS[family](raw_feature)
        applicability = _applicability(family, raw_feature)
        if applicability == "not_applicable":
            score, reasons, direction = 0.0, [], None
        semantics = _FAMILY_SEMANTICS[family]
        evidence[family] = {
            "family": family,
            "display_name": semantics["display_name"],
            "semantic_role": semantics["semantic_role"],
            "score": float(score),
            "direction": direction,
            "reasons": list(reasons),
            "applicability": applicability,
            "behavior_version": UNIVERSE_SCORER_BEHAVIOR_VERSION,
            "true_external_event_evidence": semantics["true_external_event_evidence"],
            "capability_gaps": list(semantics["capability_gaps"]),
        }
    return evidence


def audit_universe_baseline(feature: Mapping[str, object]) -> dict[str, Any]:
    """Return the pre-competition integrity view of one frozen scanner input.

    The function intentionally reuses ``run_all_scanners`` as the authority and
    independently reconstructs each family result.  A mismatch fails closed so a
    benchmark cannot silently compare a different family implementation while
    calling it the current baseline.
    """

    authority = run_all_scanners(dict(feature))
    family_evidence = build_lossless_family_evidence(feature)
    expected_scores = {
        family: float(family_evidence[family]["score"])
        for family in _FAMILY_ORDER
    }
    authority_scores = authority.get("all_scores")
    if not isinstance(authority_scores, dict):
        raise UniverseBaselineIntegrityError("authority_all_scores_unavailable")
    if set(authority_scores) != set(expected_scores):
        raise UniverseBaselineIntegrityError("authority_family_set_mismatch")
    for family, expected in expected_scores.items():
        try:
            observed = float(authority_scores[family])
        except (TypeError, ValueError) as exc:
            raise UniverseBaselineIntegrityError(
                f"authority_family_score_invalid:{family}"
            ) from exc
        if observed != expected:
            raise UniverseBaselineIntegrityError(
                f"authority_family_score_mismatch:{family}"
            )

    primary_family = str(authority.get("scanner_family") or "")
    if primary_family not in family_evidence:
        raise UniverseBaselineIntegrityError("authority_primary_family_unknown")

    return {
        "contract_version": BASELINE_INTEGRITY_CONTRACT_VERSION,
        "authority_behavior_version": UNIVERSE_SCORER_BEHAVIOR_VERSION,
        "authority": {
            "scanner_family": primary_family,
            "scanner_direction": authority.get("scanner_direction"),
            "proposed_direction": authority.get("proposed_direction"),
            "opportunity_score": authority.get("opportunity_score"),
            "reasons": list(authority.get("reasons") or []),
            "all_scores": dict(authority_scores),
            "is_proxy": bool(authority.get("is_proxy")),
        },
        "family_evidence": family_evidence,
        "frozen_control": {
            "selection_authority": "winner_take_all_primary_family",
            "absolute_step_score_authority": True,
            "relative_rank_authority": False,
            "selection_behavior_changed_by_audit": False,
        },
        "competition_questions": {
            "relative_rank_better_than_step_score": "unresolved",
            "complex_universe_beats_simple_momentum": "unresolved",
            "basis_liquidation_cross_venue_add_derivatives_lift": "unresolved",
        },
        "integrity": {
            "family_score_vector_matches_authority": True,
            "non_winner_direction_and_reasons_retained": True,
            "event_semantics_truthful": (
                family_evidence["event"]["semantic_role"] == "market_shock_proxy"
                and family_evidence["event"]["true_external_event_evidence"] is False
            ),
            "derivatives_capability_gaps_explicit": True,
        },
    }


__all__ = [
    "BASELINE_INTEGRITY_CONTRACT_VERSION",
    "UniverseBaselineIntegrityError",
    "audit_universe_baseline",
    "build_lossless_family_evidence",
    "family_semantics",
]

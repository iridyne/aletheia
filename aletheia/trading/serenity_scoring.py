"""Serenity research scoring and replay post-processing helpers."""

from __future__ import annotations

import json


def _safe_float(value, fallback=None):
    try:
        if value is None:
            return fallback
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return fallback
            return float(text)
        return fallback
    except (TypeError, ValueError):
        return fallback


def _safe_int(value, fallback=None):
    try:
        if value is None:
            return fallback
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return fallback
            return int(float(text))
        return fallback
    except (TypeError, ValueError):
        return fallback


def _normalize_stage_for_score(value: str | None, fallback: str) -> str:
    stage = _normalize_text(value, fallback)
    return {
        "validation": "research",
        "designin": "supply_chain",
        "design-in": "supply_chain",
        "sample": "supply_chain",
        "order": "order",
        "order_night": "order_night",
        "order-night": "order_night",
    }.get(stage, stage)


SERENITY_STAGE_ORDER = [
    "research",
    "validation",
    "demand_wave",
    "supply_chain",
    "ramp",
    "order_night",
    "candidate_position",
]


def _stage_index(stage: str) -> int:
    normalized = _normalize_text(stage, "research")
    if normalized in {"research", "validation", "demand_wave", "supply_chain", "ramp", "order_night", "order", "candidate_position", "candidate", "sample"}:
        if normalized == "candidate":
            return SERENITY_STAGE_ORDER.index("candidate_position")
        if normalized in {"design_in", "designin", "design-in", "sample"}:
            return SERENITY_STAGE_ORDER.index("supply_chain")
        if normalized in {"order", "order-night"}:
            return SERENITY_STAGE_ORDER.index("order_night")
        return SERENITY_STAGE_ORDER.index(normalized) if normalized in SERENITY_STAGE_ORDER else 0
    return 0


def _build_average_engine_state(opportunity: dict) -> dict:
    """Return an explainable average-module block used by score cards and UI."""
    mode = _normalize_text(opportunity.get("average_mode"), "none")
    risk_metrics = _safe_json_loads(opportunity.get("risk_metrics"), {})
    if not isinstance(risk_metrics, dict):
        risk_metrics = {}

    dca_low = _safe_float(risk_metrics.get("dca_fair_value_low"), None)
    dca_high = _safe_float(risk_metrics.get("dca_fair_value_high"), None)
    dca_max_adds = _safe_int(risk_metrics.get("dca_max_adds"), None)
    dca_current_adds = _safe_int(risk_metrics.get("dca_current_adds"), None)

    implied_iv = _safe_float(risk_metrics.get("implied_vol"), None)
    historical_iv = _safe_float(risk_metrics.get("historical_iv"), None)
    historical_gap = None
    if implied_iv is not None and historical_iv not in (None, 0):
        historical_gap = round((implied_iv - historical_iv) / historical_iv, 4)

    dca_ladder = None
    if dca_low is not None and dca_high is not None and dca_low <= dca_high:
        dca_ladder = {
            "fair_value_low": dca_low,
            "fair_value_high": dca_high,
            "max_adds": dca_max_adds,
            "current_adds": dca_current_adds,
        }

    return {
        "mode": mode,
        "active": mode not in {"none", "off", ""},
        "dca_ladder": dca_ladder,
        "historical_mismatch": {
            "implied_iv": implied_iv,
            "historical_iv": historical_iv,
            "regime_gap": historical_gap,
            "active": historical_gap is not None,
        },
        "signals": {
            "has_dca": dca_ladder is not None,
            "has_regime_gap": historical_gap is not None,
        },
    }


def _safe_json_loads(value: str | None, fallback):
    if value is None:
        return fallback
    if isinstance(value, (list, dict)):
        return value
    if not isinstance(value, str):
        return fallback
    if not value.strip():
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _clamp_score(value: float) -> int:
    if value < 0:
        return 0
    if value > 100:
        return 100
    return int(round(value))


def _normalize_text(value: str | None, fallback: str) -> str:
    return (value or "").strip().lower() if value else fallback


def _count_fail_warn_gates(risk_gates: list[dict]) -> tuple[int, int]:
    fail = 0
    warn = 0
    for gate in risk_gates:
        status = _normalize_text(gate.get("status"), "")
        if status == "fail":
            fail += 1
        elif status == "warn":
            warn += 1
    return fail, warn


# S2 (2026-06-28, AI Berkshire researchability): score ceiling per grade.
# Grade C = speculative / thin evidence — caps at 40 (below research_priority threshold 52).
# Grade B = reasonable evidence base — caps at 70.
# Grade A = high-quality, fact-checkable research — no ceiling.
_RESEARCHABILITY_CEILING = {"A": 100, "B": 70, "C": 40}


def score_serenity_opportunity(
    opportunity: dict,
    evidence: list[dict],
    risk_gates: list[dict],
) -> dict:
    """Compute a deterministic 0-100 score for a Serenity opportunity.

    The score is intentionally conservative and transparent:
    - Evidence tier is weighted first (disclosed > high_confidence > likely > speculative).
    - Stage and average module usage are added when they suggest more concrete research
      execution.
    - Risk gates and manual risk flags subtract points (fail > warn > flags).
    - Confidence score adds a bounded precision edge.
    - Researchability grade (A/B/C) applies a ceiling to prevent low-information
      opportunities from reaching research_priority bucket.
    """
    # D2 (2026-06-27): gate evidence weight on the INDEPENDENTLY assessed tier, not
    # the generator's self-grade. `assessed_tier` is set by the deterministic evaluator
    # (aletheia/trading/serenity_evidence.py) at insert time; NULL (legacy/unassessed)
    # floors to "speculative". `evidence_tier` is kept only as the advisory proposal.
    proposed_tier = _normalize_text(opportunity.get("evidence_tier"), "speculative")
    evidence_tier = _normalize_text(opportunity.get("assessed_tier"), "speculative")
    stage = _normalize_stage_for_score(opportunity.get("stage"), "research")
    average_mode = _normalize_text(opportunity.get("average_mode"), "none")

    tier_score_map = {
        "disclosed": 28,
        "high confidence": 24,
        "high_confidence": 24,
        "likely": 18,
        "likely customers": 18,
        "speculative": 10,
    }

    stage_score_map = {
        "research": 8,
        "demand_wave": 12,
        "demand": 12,
        "supply_chain": 20,
        "ramp": 22,
        "order_night": 22,
        "order": 22,
        "candidate": 26,
        "candidate_position": 26,
    }

    avg_score_map = {
        "none": 2,
        "ladder": 8,
        "dca": 8,
        "dca_ladder": 10,
        "historical_mismatch": 12,
        "historical": 12,
    }

    evidence_score = tier_score_map.get(evidence_tier, 10)
    stage_score = stage_score_map.get(stage, 10)
    average_score = avg_score_map.get(average_mode, 2)

    risk_flags = _safe_json_loads(opportunity.get("risk_flags"), [])
    if not isinstance(risk_flags, list):
        risk_flags = [risk_flags]
    risk_flag_count = len(risk_flags)

    fail_gates, warn_gates = _count_fail_warn_gates(risk_gates)

    confidence_score = opportunity.get("confidence_score")
    try:
        confidence = float(confidence_score) if confidence_score is not None else None
    except (TypeError, ValueError):
        confidence = None
    confidence_component = max(0, min(20, (confidence / 5.0) if confidence is not None else 8))

    # Each valid evidence record contributes small support; evidence decay beyond 10.
    evidence_count = len([e for e in evidence if e and e.get("title")])
    evidence_bonus = min(10, evidence_count * 0.8)

    # risk penalties:
    # - fail gates: hard blocker with heavier penalty
    # - warn gates: soft friction
    # - manual risk flags: soft friction, often incomplete by design
    raw = (
        evidence_score
        + stage_score
        + average_score
        + evidence_bonus
        + confidence_component
        - fail_gates * 18
        - warn_gates * 6
        - risk_flag_count * 4
    )

    score = _clamp_score(raw)

    # S2: apply researchability ceiling (A=100, B=70, C=40).
    # Legacy / pre-S2 records default to "C" (conservative floor).
    # Note: uppercase(.strip()) not _normalize_text() — ceiling keys are A/B/C, not lowercase.
    r_grade = (opportunity.get("researchability_grade") or "C").strip().upper()
    if r_grade not in _RESEARCHABILITY_CEILING:
        r_grade = "C"
    ceiling = _RESEARCHABILITY_CEILING[r_grade]
    if score > ceiling:
        score = ceiling

    average_engine = _build_average_engine_state(opportunity)

    bucket = "watch"
    if fail_gates == 0 and score >= 72:
        bucket = "candidate_position"
    elif fail_gates == 0 and score >= 52:
        bucket = "research_priority"

    return {
        "opportunity_id": opportunity.get("id"),
        "score": score,
        "bucket": bucket,
        "evidence_score": _clamp_score(evidence_score),
        "stage_score": _clamp_score(stage_score),
        "average_score": _clamp_score(average_score),
        "confidence_component": _clamp_score(confidence_component),
        "evidence_count": evidence_count,
        "risk_fail_gates": fail_gates,
        "risk_warn_gates": warn_gates,
        "risk_flag_count": risk_flag_count,
        "average_engine": average_engine,
        "stage_index": _stage_index(opportunity.get("stage") or "research"),
        "reasons": {
            "evidence_tier": evidence_tier or "speculative",  # authoritative (assessed)
            "proposed_tier": proposed_tier or "speculative",   # generator's self-grade (advisory)
            "stage": stage or "research",
            "average_mode": average_mode or "none",
            "risk_gate_failures": fail_gates,
            "risk_gate_warn": warn_gates,
            "risk_flag_count": risk_flag_count,
            "raw": round(raw, 2),
            "researchability_grade": r_grade,
            "researchability_ceiling": ceiling,
        },
    }


def make_replay_snapshot_payload(opportunity: dict, score: dict) -> dict:
    """Build a compact replay snapshot payload for storage and auditability."""
    return {
        "opportunity_id": opportunity.get("id"),
        "symbol": opportunity.get("symbol"),
        "score_total": int(score.get("score", 0)),
        "bucket": score.get("bucket"),
        "stage": opportunity.get("stage"),
        "evidence_count": score.get("evidence_count", 0),
        "risk_fail_gates": score.get("risk_fail_gates", 0),
        "risk_warn_gates": score.get("risk_warn_gates", 0),
        "average_engine": score.get("average_engine") or _build_average_engine_state(opportunity),
        "reasons": score.get("reasons", {}),
    }

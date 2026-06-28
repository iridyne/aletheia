"""Deterministic, independent evaluator for Serenity evidence quality (D2).

Owner decision 2026-06-27: Serenity must NOT "self-produce + self-evaluate". The
LLM (or external source) that PROPOSES an evidence tier is the *generator*; the
authoritative tier that GATES scoring/promotion is assigned here by an independent
*evaluator* using objective source constraints. The generator's self-grade is kept
on the record (``tier`` / ``evidence_tier``) purely as an advisory proposal — it
never gates anything on its own.

This module is intentionally pure (no LLM, no DB, no I/O) so the rules are fully
unit-testable and deterministic.

Canonical tier vocabulary (single source of truth, mirrored by
``serenity_scoring.tier_score_map``), weakest → strongest::

    speculative < likely < high_confidence < disclosed
"""

from __future__ import annotations

# Canonical ladder, weakest first. Index = strength rank.
TIER_LADDER: tuple[str, ...] = ("speculative", "likely", "high_confidence", "disclosed")
_TIER_RANK: dict[str, int] = {t: i for i, t in enumerate(TIER_LADDER)}

# Legacy / free-form synonyms emitted by older producers, mapped to canonical.
# Unknown values fall through to ``speculative`` (the safe floor).
_TIER_SYNONYMS: dict[str, str] = {
    "disclosed": "disclosed",
    "verified": "disclosed",
    "high_confidence": "high_confidence",
    "high confidence": "high_confidence",
    "credible": "high_confidence",
    "likely": "likely",
    "likely customers": "likely",
    "medium": "likely",
    "speculative": "speculative",
    "low": "speculative",
    "weak": "speculative",
    "unsupported": "speculative",
}

# Sources whose evidence can never exceed the given tier, regardless of self-grade.
#   prediction_market : crypto sentiment laundering, not industrial supply-chain proof
#   *_methodology / price_action : model-generated trading playbook, no primary source
#   llm / model : pure model narrative recalled from weights
_SOURCE_CEILING: dict[str, str] = {
    "prediction_market": "speculative",
    "ict_methodology": "speculative",
    "smc_methodology": "speculative",
    "price_action": "speculative",
    "llm": "speculative",
    "model": "speculative",
}

# Tiers that REQUIRE a verifiable external locator (filing / customer guide / URL).
# A self-asserted "disclosed" with no external reference is over-claiming.
_REQUIRES_EXTERNAL_REF: frozenset[str] = frozenset({"disclosed", "high_confidence"})

# Stamp recorded on every record this evaluator assesses, for provenance/audit.
ASSESSOR_ID = "deterministic_v1"

# Tier an over-claimed (ref-less) high tier is demoted to.
_NO_REF_DEMOTION = "likely"


def normalize_tier(value: object) -> str:
    """Map any free-form / legacy tier string to the canonical vocabulary.

    Unknown or empty values floor to ``speculative``.
    """
    if not value:
        return "speculative"
    return _TIER_SYNONYMS.get(str(value).strip().lower(), "speculative")


def _min_tier(a: str, b: str) -> str:
    return a if _TIER_RANK[a] <= _TIER_RANK[b] else b


def _has_external_ref(evidence: dict) -> bool:
    """True when the record carries a verifiable external locator (URL / filing)."""
    for key in ("url", "source_ref"):
        ref = str(evidence.get(key) or "").strip().lower()
        if ref.startswith(("http://", "https://", "www.", "sec.gov", "ftp://")):
            return True
    return False


def assess_evidence_tier(evidence: dict) -> tuple[str, str]:
    """Authoritative tier for ONE evidence record.

    The generator's self-grade (``proposed_tier`` or ``tier``) is treated as a
    *proposal* and only ever lowered, never trusted upward:

    1. normalise the proposed tier to canonical vocabulary;
    2. clamp to the source ceiling (prediction_market / methodology / llm → speculative);
    3. demote a ``disclosed`` / ``high_confidence`` claim to ``likely`` when no external
       reference backs it.

    Returns ``(assessed_tier, assessor_id)``.
    """
    proposed = normalize_tier(evidence.get("proposed_tier") or evidence.get("tier"))
    source = str(evidence.get("source") or "").strip().lower()

    assessed = proposed
    ceiling = _SOURCE_CEILING.get(source)
    if ceiling is not None:
        assessed = _min_tier(assessed, ceiling)

    if assessed in _REQUIRES_EXTERNAL_REF and not _has_external_ref(evidence):
        assessed = _NO_REF_DEMOTION

    return assessed, ASSESSOR_ID


def assess_researchability(evidence: list[dict]) -> tuple[str, str]:
    """Deterministic A/B/C researchability grade for a set of evidence records.

    Grades the information quality available for an opportunity — independent
    of what the LLM claims about its own confidence:

    - A: ≥2 supporting records with assessed_tier ≥ high_confidence AND at least
         one carries a verifiable external URL. High-quality, fact-checkable research.
    - B: ≥3 supporting records with assessed_tier ≥ likely. Reasonable evidence base.
    - C: Everything else — speculative-only, thin, or no supporting evidence.

    Grade C triggers a scoring ceiling (≤40) in score_serenity_opportunity to
    prevent low-information opportunities from reaching research_priority bucket.

    Returns (grade, reason).
    """
    supporting = [e for e in (evidence or []) if e and e.get("supports", True)]
    if not supporting:
        return "C", "no_supporting_evidence"

    hc_with_url = [
        e for e in supporting
        if _TIER_RANK.get(
            normalize_tier(e.get("assessed_tier") or e.get("tier")), 0
        ) >= _TIER_RANK["high_confidence"]
        and _has_external_ref(e)
    ]
    if len(hc_with_url) >= 2:
        return "A", f"hc_with_url={len(hc_with_url)}"

    likely_or_above = [
        e for e in supporting
        if _TIER_RANK.get(
            normalize_tier(e.get("assessed_tier") or e.get("tier")), 0
        ) >= _TIER_RANK["likely"]
    ]
    if len(likely_or_above) >= 3:
        return "B", f"likely_or_above={len(likely_or_above)}"

    return "C", f"insufficient_evidence (hc_url={len(hc_with_url)}, likely={len(likely_or_above)})"


def assess_opportunity_tier(opportunity: dict, evidence: list[dict]) -> tuple[str, str]:
    """Authoritative gating tier for an OPPORTUNITY, derived from its evidence.

    An opportunity is only as strong as the strongest *independently assessed*
    supporting evidence record it actually has. With no supporting evidence it is
    ``speculative`` — regardless of the generator's self-graded ``evidence_tier``.
    This is what breaks the self-produce/self-evaluate loop: the opportunity LLM can
    no longer hand itself ``disclosed``.

    Each evidence record's tier is read from a precomputed ``assessed_tier`` when
    present (set at insert time), otherwise assessed on the fly. ``opportunity`` is
    accepted for signature symmetry / future use but is intentionally not trusted.

    Returns ``(assessed_tier, assessor_id)``.
    """
    supporting = [e for e in (evidence or []) if e and e.get("supports", True)]
    if not supporting:
        return "speculative", ASSESSOR_ID

    best = "speculative"
    for record in supporting:
        precomputed = record.get("assessed_tier")
        tier = normalize_tier(precomputed) if precomputed else assess_evidence_tier(record)[0]
        if _TIER_RANK[tier] > _TIER_RANK[best]:
            best = tier
    return best, ASSESSOR_ID

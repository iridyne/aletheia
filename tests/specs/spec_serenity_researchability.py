"""Spec: Serenity Researchability Grade (S2 — AI Berkshire integration)

Owner decision 2026-06-28 (AI Berkshire integration): Serenity opportunities must
carry a machine-assigned A/B/C researchability grade reflecting the quality of
available evidence, not the LLM's self-confidence. Grade C caps scoring at ≤40,
preventing speculative information vacuums from reaching research_priority bucket.

Borrowed from: ai-berkshire A/B/C researchability grading concept.
Fills gap: opportunities could previously reach high scores driven by LLM confidence
alone, even when all evidence was assessed as speculative.

Contract: serenity_researchability (logical contract — behavioral pipeline spec,
not in REQUIRED_CONTRACTS.)
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_researchability"
_ROOT = Path(__file__).parent.parent.parent
_SERENITY_EVIDENCE = _ROOT / "aletheia" / "trading" / "serenity_evidence.py"
_SERENITY_SCORING = _ROOT / "aletheia" / "trading" / "serenity_scoring.py"
_MODELS = _ROOT / "aletheia" / "storage" / "models.py"
_MIGRATIONS = _ROOT / "aletheia" / "storage" / "migrations.py"
_RESEARCH_WORKER = _ROOT / "aletheia" / "agents" / "research_worker.py"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SR-S01", _CONTRACT, "§1 assess_researchability function exported", "L2a",
        "aletheia/trading/serenity_evidence.py exports callable assess_researchability "
        "that returns a (grade: str, reason: str) tuple",
    ),
)
def test_assess_researchability_exported():
    from aletheia.trading import serenity_evidence as se
    assert callable(se.assess_researchability), (
        "serenity_evidence.py does not export assess_researchability"
    )


@spec_claim(
    SpecClaim(
        "SR-S02", _CONTRACT, "§2 researchability_grade field on SerenityOpportunity", "L2a",
        "SerenityOpportunity model has researchability_grade field AND migrations.py "
        "includes ('researchability_grade', 'serenity_opportunities', ...) in _ADDITIVE_COLUMNS",
    ),
)
def test_researchability_grade_field_exists():
    models_src = _MODELS.read_text(encoding="utf-8")
    assert "researchability_grade" in models_src, (
        "SerenityOpportunity missing researchability_grade in models.py"
    )
    migrations_src = _MIGRATIONS.read_text(encoding="utf-8")
    assert "researchability_grade" in migrations_src, (
        "migrations.py missing researchability_grade column in _ADDITIVE_COLUMNS"
    )


@spec_claim(
    SpecClaim(
        "SR-S03", _CONTRACT, "§3 scoring applies researchability ceiling", "L2a",
        "serenity_scoring.py references RESEARCHABILITY_CEILING or researchability_grade "
        "to cap C-grade scores",
    ),
)
def test_scoring_applies_ceiling():
    src = _SERENITY_SCORING.read_text(encoding="utf-8")
    assert "researchability" in src.lower(), (
        "serenity_scoring.py does not reference researchability ceiling"
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral (pure-function unit tests — no DB required)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SR-B01", _CONTRACT, "§4 C-grade evidence list yields ≤40 score ceiling", "L2b",
        "When all evidence is speculative (C-grade), score_serenity_opportunity caps "
        "the final score at ≤40, blocking research_priority bucket (needs 52+)",
    ),
)
def test_c_grade_score_ceiling():
    from aletheia.trading.serenity_evidence import assess_researchability
    from aletheia.trading.serenity_scoring import score_serenity_opportunity

    speculative_evidence = [
        {"title": f"ev{i}", "source": "llm", "assessed_tier": "speculative",
         "supports": True, "tier": "speculative"}
        for i in range(5)
    ]
    grade, _ = assess_researchability(speculative_evidence)
    assert grade == "C", f"All-speculative evidence should yield grade C, got {grade!r}"

    opportunity = {
        "id": "test-opp",
        "symbol": "BTC/USDT",
        "direction": "long",
        "thesis": "test",
        "evidence_tier": "speculative",
        "assessed_tier": "speculative",
        "confidence_score": 80,  # High LLM self-confidence, but should be capped
        "stage": "ramp",         # High stage — would be 22 pts, but ceiling at 40
        "average_mode": "dca",
        "researchability_grade": "C",
    }
    result = score_serenity_opportunity(opportunity, speculative_evidence, [])
    assert result["score"] <= 40, (
        f"C-grade opportunity scored {result['score']}, expected ≤40 "
        f"(ceiling for grade C prevents false research_priority)"
    )
    assert result["bucket"] == "watch", (
        f"C-grade should always land in 'watch', got {result['bucket']!r}"
    )


@spec_claim(
    SpecClaim(
        "SR-B02", _CONTRACT, "§5 A-grade evidence list receives no ceiling", "L2b",
        "When evidence contains ≥2 high_confidence records with URLs (A-grade), "
        "score_serenity_opportunity is NOT capped by researchability",
    ),
)
def test_a_grade_no_ceiling():
    from aletheia.trading.serenity_evidence import assess_researchability
    from aletheia.trading.serenity_scoring import score_serenity_opportunity

    a_grade_evidence = [
        {
            "title": f"hc_ev{i}", "source": "news",
            "url": f"https://example.com/filing{i}",
            "assessed_tier": "high_confidence",
            "supports": True, "tier": "high_confidence",
        }
        for i in range(3)
    ]
    grade, _ = assess_researchability(a_grade_evidence)
    assert grade == "A", f"2+ high_confidence+URL evidence should yield A, got {grade!r}"

    opportunity = {
        "id": "test-opp-a",
        "symbol": "AAPL/USD",
        "direction": "long",
        "thesis": "Strong fundamentals",
        "evidence_tier": "high_confidence",
        "assessed_tier": "high_confidence",
        "confidence_score": 80,
        "stage": "supply_chain",
        "average_mode": "none",
        "researchability_grade": "A",
    }
    result = score_serenity_opportunity(opportunity, a_grade_evidence, [])
    assert result["score"] > 40, (
        f"A-grade opportunity scored {result['score']}, expected >40 — ceiling should not apply"
    )

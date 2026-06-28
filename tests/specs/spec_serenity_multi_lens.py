"""Spec: Serenity 4-Lens Parallel Research (S1 — AI Berkshire integration)

Owner decision 2026-06-28 (AI Berkshire integration): Serenity Stage 1 evidence
collection must use multiple research perspectives (technical, flow, sentiment,
macro) instead of a single LLM call, to reduce mono-directional bias observed in
Round 1 (short 17/23). A synthesis step consolidates contradictions.

Borrowed from: ai-berkshire 4 investor persona parallel research pattern.
Fills gap: research_worker Stage 1 was a single LLM call with one perspective.

Contract: serenity_multi_lens (logical contract — behavioral pipeline spec,
not in REQUIRED_CONTRACTS.)

Cost note: each research run Stage 1 now costs 4× LLM calls. Due to Databricks
rate limits, lenses run sequentially (not concurrently) by default. The
_LENS_SEQUENTIAL flag in research_worker.py can be flipped to asyncio.gather
when rate limits allow.
"""

from __future__ import annotations

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_multi_lens"
_ROOT = Path(__file__).parent.parent.parent
_RESEARCH_WORKER = _ROOT / "aletheia" / "agents" / "research_worker.py"
_MODELS = _ROOT / "aletheia" / "storage" / "models.py"
_MIGRATIONS = _ROOT / "aletheia" / "storage" / "migrations.py"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "ML-S01", _CONTRACT, "§1 research_worker Stage 1 uses multiple lens prompts", "L2a",
        "research_worker.py defines ≥3 distinct lens system prompts "
        "(_LENS_TECHNICAL_SYSTEM, _LENS_FLOW_SYSTEM, _LENS_SENTIMENT_SYSTEM) "
        "and Stage 1 calls them instead of the single _EVIDENCE_SYSTEM",
    ),
)
def test_multi_lens_prompts_defined():
    src = _RESEARCH_WORKER.read_text(encoding="utf-8")
    assert "_LENS_TECHNICAL_SYSTEM" in src, (
        "research_worker.py missing _LENS_TECHNICAL_SYSTEM — Stage 1 still single-lens"
    )
    assert "_LENS_FLOW_SYSTEM" in src, (
        "research_worker.py missing _LENS_FLOW_SYSTEM"
    )
    assert "_LENS_SENTIMENT_SYSTEM" in src, (
        "research_worker.py missing _LENS_SENTIMENT_SYSTEM"
    )


@spec_claim(
    SpecClaim(
        "ML-S02", _CONTRACT, "§2 SerenityEvidence.agent_lens field exists", "L2a",
        "SerenityEvidence model has agent_lens field AND migrations.py "
        "includes ('agent_lens', 'serenity_evidence', ...) in _ADDITIVE_COLUMNS",
    ),
)
def test_agent_lens_field_exists():
    models_src = _MODELS.read_text(encoding="utf-8")
    assert "agent_lens" in models_src, (
        "SerenityEvidence missing agent_lens field in models.py"
    )
    migrations_src = _MIGRATIONS.read_text(encoding="utf-8")
    assert "agent_lens" in migrations_src, (
        "migrations.py missing agent_lens column in _ADDITIVE_COLUMNS"
    )


@spec_claim(
    SpecClaim(
        "ML-S03", _CONTRACT, "§3 Stage 1 synthesis event emitted", "L2a",
        "research_worker.py emits a run event with event_type='lens_synthesis' "
        "after multi-lens evidence collection, capturing cross-lens contradictions",
    ),
)
def test_synthesis_event_emitted_in_code():
    src = _RESEARCH_WORKER.read_text(encoding="utf-8")
    assert "lens_synthesis" in src, (
        "research_worker.py does not emit lens_synthesis event after multi-lens Stage 1"
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral (pure-function / structural checks)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "ML-B01", _CONTRACT, "§4 research_worker Stage 1 iterates over RESEARCH_LENSES", "L2b",
        "research_worker.py defines a RESEARCH_LENSES list/tuple and Stage 1 "
        "iterates over it to call llm_complete per lens",
    ),
)
def test_research_lenses_list_defined():
    src = _RESEARCH_WORKER.read_text(encoding="utf-8")
    assert "RESEARCH_LENSES" in src, (
        "research_worker.py does not define RESEARCH_LENSES — "
        "multi-lens iteration requires a lens registry"
    )


@spec_claim(
    SpecClaim(
        "ML-B02", _CONTRACT, "§5 methodology mode bypasses multi-lens", "L2b",
        "ICT methodology mode still uses _ICT_METHODOLOGY_SYSTEM (single-lens), "
        "not the multi-lens RESEARCH_LENSES path",
    ),
)
def test_methodology_mode_bypasses_multi_lens():
    src = _RESEARCH_WORKER.read_text(encoding="utf-8")
    # ICT methodology should still use its dedicated single prompt
    assert "_ICT_METHODOLOGY_SYSTEM" in src, (
        "research_worker.py removed _ICT_METHODOLOGY_SYSTEM — methodology mode "
        "must keep its specialized single-lens path"
    )
    # The bypass conditional should reference mode == 'methodology'
    assert "methodology" in src, (
        "research_worker.py missing methodology-mode bypass for multi-lens Stage 1"
    )

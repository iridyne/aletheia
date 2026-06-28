"""Spec: Serenity Evidence Quality (D2 — generator/evaluator separation)

Owner decision 2026-06-27: Serenity must NOT self-produce + self-evaluate. The
LLM/source that PROPOSES a tier is the generator; the authoritative tier that
GATES scoring/promotion is assigned by an independent deterministic evaluator
(`aletheia/trading/serenity_evidence.py`) using objective source constraints.

Contract: serenity_evidence_quality (logical contract — registered in the
conformance matrix but intentionally NOT in REQUIRED_CONTRACTS, mirroring the
behavioral pipeline specs).
"""

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_evidence_quality"
_ROOT = Path(__file__).parent.parent.parent
_RESEARCH_WORKER = _ROOT / "aletheia" / "agents" / "research_worker.py"
_PRED_OBS = _ROOT / "aletheia" / "agents" / "prediction_observer.py"
_TRADING_CONTEXT = _ROOT / "aletheia" / "agent_shell" / "trading_context.py"
_TRADING_DECISION = _ROOT / "aletheia" / "agent_shell" / "trading_decision.py"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SE-S01", _CONTRACT, "§2 evaluator module", "L2a",
        "aletheia/trading/serenity_evidence.py exists and exports callable "
        "assess_evidence_tier, assess_opportunity_tier, normalize_tier",
    ),
)
def test_evaluator_module_exists_and_exports():
    from aletheia.trading import serenity_evidence as se

    assert callable(se.assess_evidence_tier)
    assert callable(se.assess_opportunity_tier)
    assert callable(se.normalize_tier)


@spec_claim(
    SpecClaim(
        "SE-S02", _CONTRACT, "§2 generator routes through evaluator", "L2a",
        "research_worker.py routes generated evidence AND opportunities through "
        "the independent evaluator (assess_evidence_tier + assess_opportunity_tier)",
    ),
)
def test_research_worker_routes_through_evaluator():
    src = _RESEARCH_WORKER.read_text(encoding="utf-8")
    assert "assess_evidence_tier" in src, (
        "research_worker.py does not call assess_evidence_tier — evidence is still self-graded"
    )
    assert "assess_opportunity_tier" in src, (
        "research_worker.py does not call assess_opportunity_tier — opportunity tier is still self-graded"
    )


@spec_claim(
    SpecClaim(
        "SE-S03", _CONTRACT, "§2 prediction_observer routes through evaluator", "L2a",
        "prediction_observer.py routes prediction-market evidence through assess_evidence_tier",
    ),
)
def test_prediction_observer_routes_through_evaluator():
    src = _PRED_OBS.read_text(encoding="utf-8")
    assert "assess_evidence_tier" in src, (
        "prediction_observer.py does not call assess_evidence_tier"
    )


@spec_claim(
    SpecClaim(
        "SE-S04", _CONTRACT, "§2 PI consumes the assessed tier", "L2a",
        "trading_context.py surfaces the independently assessed_tier (not just the "
        "generator's self-graded evidence_tier) into the Serenity opportunities the "
        "PI agent consumes",
    ),
)
def test_pi_context_surfaces_assessed_tier():
    src = _TRADING_CONTEXT.read_text(encoding="utf-8")
    assert '"assessed_tier"' in src, (
        "trading_context.py does not surface assessed_tier — PI would judge on the "
        "generator's self-grade, making the D2 split cosmetic"
    )


@spec_claim(
    SpecClaim(
        "SE-S05", _CONTRACT, "§2 PI prompt renders the assessed tier", "L2a",
        "trading_decision.py renders the assessed_tier into the Serenity block of the "
        "PI decision prompt (falling back to evidence_tier only when unassessed)",
    ),
)
def test_pi_prompt_renders_assessed_tier():
    src = _TRADING_DECISION.read_text(encoding="utf-8")
    assert "assessed_tier" in src, (
        "trading_decision.py does not render assessed_tier into the PI prompt"
    )


# ---------------------------------------------------------------------------
# L2b — Invariants (behavioral, pure evaluator)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SE-I01", _CONTRACT, "§2 source ceiling — prediction_market", "L2b",
        "prediction_market evidence is capped at 'speculative' regardless of self-graded tier",
    ),
)
def test_prediction_market_capped_at_speculative():
    from aletheia.trading.serenity_evidence import assess_evidence_tier

    tier, assessor = assess_evidence_tier(
        {"source": "prediction_market", "tier": "disclosed", "url": "https://example.com/x"}
    )
    assert tier == "speculative", tier
    assert assessor  # provenance stamp present


@spec_claim(
    SpecClaim(
        "SE-I02", _CONTRACT, "§2 source ceiling — methodology/model", "L2b",
        "ict/smc methodology, price_action and llm/model sources are capped at 'speculative'",
    ),
)
def test_methodology_and_model_sources_capped():
    from aletheia.trading.serenity_evidence import assess_evidence_tier

    for source in ("ict_methodology", "smc_methodology", "price_action", "llm", "model"):
        tier, _ = assess_evidence_tier({"source": source, "tier": "disclosed"})
        assert tier == "speculative", f"{source} -> {tier}"


@spec_claim(
    SpecClaim(
        "SE-I03", _CONTRACT, "§2 external-ref requirement", "L2b",
        "A 'disclosed'/'high_confidence' claim without an external reference is "
        "demoted to 'likely'; with a verifiable URL it survives",
    ),
)
def test_high_tier_requires_external_ref():
    from aletheia.trading.serenity_evidence import assess_evidence_tier

    # Use 'official' source (not capped by SOURCE_RELIABILITY_TIER) to isolate the
    # external-ref requirement. A source that is reliability-capped to 'likely' would
    # always end up 'likely' regardless of the external-ref rule — the two mechanisms
    # are orthogonal, so test each in isolation.
    # No external ref → demoted
    tier_no_ref, _ = assess_evidence_tier(
        {"source": "official", "tier": "disclosed", "source_ref": "internal note"}
    )
    assert tier_no_ref == "likely", tier_no_ref

    # With a verifiable URL → survives
    tier_ref, _ = assess_evidence_tier(
        {"source": "official", "tier": "disclosed", "url": "https://sec.gov/filing/123"}
    )
    assert tier_ref == "disclosed", tier_ref


@spec_claim(
    SpecClaim(
        "SE-I04", _CONTRACT, "§2 opportunity needs supporting evidence", "L2b",
        "An opportunity with no supporting evidence assesses to 'speculative' even "
        "if the generator self-graded it 'disclosed'",
    ),
)
def test_opportunity_without_evidence_is_speculative():
    from aletheia.trading.serenity_evidence import assess_opportunity_tier

    tier, _ = assess_opportunity_tier({"evidence_tier": "disclosed"}, [])
    assert tier == "speculative", tier


@spec_claim(
    SpecClaim(
        "SE-I05", _CONTRACT, "§2 opportunity = strongest supporting evidence", "L2b",
        "An opportunity's assessed tier equals the strongest assessed tier among its "
        "supporting evidence",
    ),
)
def test_opportunity_takes_strongest_supporting_evidence():
    from aletheia.trading.serenity_evidence import assess_opportunity_tier

    evidence = [
        {"assessed_tier": "speculative", "supports": True},
        {"assessed_tier": "high_confidence", "supports": True},
        # A 'disclosed' record that does NOT support the thesis must be ignored.
        {"assessed_tier": "disclosed", "supports": False},
    ]
    tier, _ = assess_opportunity_tier({}, evidence)
    assert tier == "high_confidence", tier


# ---------------------------------------------------------------------------
# L2c — Invariants (scorer gates on assessed tier, vocab unified)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SE-C01", _CONTRACT, "§2 scorer gates on assessed_tier", "L2c",
        "score_serenity_opportunity gates evidence weight on the independently "
        "assessed_tier, NOT the generator's self-graded evidence_tier",
    ),
)
def test_scorer_ignores_self_graded_tier():
    from aletheia.trading.serenity_scoring import score_serenity_opportunity

    # Self-graded 'disclosed' but no independent assessment → must score at the
    # speculative floor (the self-grade does not gate).
    self_graded = score_serenity_opportunity(
        {"id": "o1", "evidence_tier": "disclosed", "stage": "research"},
        evidence=[],
        risk_gates=[],
    )
    # Independently assessed 'disclosed' → full evidence weight.
    assessed = score_serenity_opportunity(
        {"id": "o2", "evidence_tier": "disclosed", "assessed_tier": "disclosed", "stage": "research"},
        evidence=[],
        risk_gates=[],
    )
    assert assessed["evidence_score"] > self_graded["evidence_score"], (
        f"assessed={assessed['evidence_score']} self_graded={self_graded['evidence_score']}"
    )
    assert self_graded["reasons"]["evidence_tier"] == "speculative"
    assert assessed["reasons"]["evidence_tier"] == "disclosed"


@spec_claim(
    SpecClaim(
        "SE-S06", _CONTRACT, "§3 source reliability tier ceiling", "L2a",
        "serenity_evidence.py defines SOURCE_RELIABILITY_TIER mapping "
        "(e.g. 'news'→'likely', 'market_data'→'high_confidence') that "
        "caps assess_evidence_tier on a per-source basis",
        status="implemented",
    ),
)
def test_source_reliability_tier_constant_exists():
    from aletheia.trading import serenity_evidence as se
    assert hasattr(se, "SOURCE_RELIABILITY_TIER"), (
        "serenity_evidence.py missing SOURCE_RELIABILITY_TIER constant"
    )
    srt = se.SOURCE_RELIABILITY_TIER
    assert isinstance(srt, dict), "SOURCE_RELIABILITY_TIER must be a dict"
    assert "news" in srt, "SOURCE_RELIABILITY_TIER missing 'news' entry"
    assert "market_data" in srt, "SOURCE_RELIABILITY_TIER missing 'market_data' entry"


@spec_claim(
    SpecClaim(
        "SE-I06", _CONTRACT, "§3 source reliability cap applied in assess_evidence_tier", "L2b",
        "A 'news' evidence self-graded 'disclosed' is capped at 'likely' "
        "(news cannot verify supply-chain/regulatory facts); "
        "'market_data' self-graded 'disclosed' is capped at 'high_confidence'",
        status="implemented",
    ),
)
def test_source_reliability_cap_applied():
    from aletheia.trading.serenity_evidence import assess_evidence_tier

    # news can at best be 'likely', not 'disclosed'
    tier_news, _ = assess_evidence_tier(
        {"source": "news", "tier": "disclosed", "url": "https://example.com/news"}
    )
    assert tier_news == "likely", f"news ceiling should be 'likely', got {tier_news!r}"

    # market_data can reach 'high_confidence' but not 'disclosed'
    tier_md, _ = assess_evidence_tier(
        {"source": "market_data", "tier": "disclosed", "url": "https://exchange.com/data"}
    )
    assert tier_md == "high_confidence", f"market_data ceiling should be 'high_confidence', got {tier_md!r}"

    # market_data self-graded 'high_confidence' with URL should survive
    tier_md_hc, _ = assess_evidence_tier(
        {"source": "market_data", "tier": "high_confidence", "url": "https://exchange.com/data"}
    )
    assert tier_md_hc == "high_confidence", f"market_data high_confidence with URL should survive, got {tier_md_hc!r}"


@spec_claim(
    SpecClaim(
        "SE-C02", _CONTRACT, "§2 unified tier vocabulary", "L2c",
        "normalize_tier maps legacy free-form vocab (verified/credible/medium) onto "
        "the canonical 4-tier ladder used by the scorer's tier_score_map",
    ),
)
def test_legacy_vocab_normalized_to_canonical():
    from aletheia.trading.serenity_evidence import normalize_tier
    from aletheia.trading.serenity_scoring import score_serenity_opportunity

    assert normalize_tier("verified") == "disclosed"
    assert normalize_tier("credible") == "high_confidence"
    assert normalize_tier("medium") == "likely"
    assert normalize_tier("nonsense") == "speculative"
    assert normalize_tier(None) == "speculative"

    # Every canonical tier the evaluator can emit must be priced by the scorer
    # (no silent fallback to the speculative floor for a recognised tier).
    from aletheia.trading.serenity_evidence import TIER_LADDER

    base = {"id": "x", "stage": "research"}
    scores = {
        t: score_serenity_opportunity({**base, "assessed_tier": t}, evidence=[], risk_gates=[])["evidence_score"]
        for t in TIER_LADDER
    }
    # Strictly increasing weight along the ladder (speculative < ... < disclosed).
    ordered = [scores[t] for t in TIER_LADDER]
    assert ordered == sorted(ordered) and len(set(ordered)) == len(ordered), scores

"""Spec: Serenity Decision Attribution (Phase 2 — signal_source discriminator)

Owner decision 2026-06-27 (Option A, System Operator-centered): Serenity research is a
first-class input to the apex System Operator, NOT an independent executor. When the System Operator
acts on a Serenity-sourced pick, the decision MUST carry a traceable source so the
attribution chain is complete on the signal side.

Why a discriminator is mandatory: both `opportunities.id` (market_observer) and
`serenity_opportunities.id` are `new_id()` = uuid4().hex[:12] — bare ids with no
type tag and no FK, so an id alone is cross-table ambiguous. A Serenity id stuffed
into `pi_decisions.opportunity_id` without a `signal_source` flag would silently
join against the WRONG table (`opportunities`). The fix is a decision-level
discriminator: `pi_decisions.signal_source ∈ {'market_observer','serenity'}`.

Scope boundary (deliberate): this discriminator lives ONLY on `pi_decisions`
(decision-level attribution). Proposal/position-level source-awareness is Phase 4's
`trade_school` field — a DIFFERENT concern (which school governs risk/exit), landing
in its own phase so `aletheia/risk/` (a change-controlled trust boundary) is not
touched here.

Contract: serenity_attribution (logical contract — registered in the conformance
matrix but intentionally NOT in REQUIRED_CONTRACTS, mirroring the behavioral
pipeline specs and the D2 serenity_evidence_quality contract).
"""

from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim

_CONTRACT = "serenity_attribution"
_ROOT = Path(__file__).parent.parent.parent
_MODELS = _ROOT / "core" / "aletheia" / "storage" / "models.py"
_MIGRATIONS = _ROOT / "core" / "aletheia" / "storage" / "migrations.py"
_REFLECTION = _ROOT / "core" / "aletheia" / "agent_shell" / "reflection.py"


# ---------------------------------------------------------------------------
# L2a — Structural
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "SA-S01", _CONTRACT, "§3.2 PiDecision carries signal_source", "L2a",
        "PiDecision model has a signal_source field (default 'market_observer') so a "
        "bare opportunity_id can be resolved against the right table",
    ),
)
def test_pi_decision_has_signal_source():
    from aletheia.storage.models import PiDecision

    dec = PiDecision()
    assert hasattr(dec, "signal_source"), "PiDecision has no signal_source field"
    assert dec.signal_source == "market_observer", (
        f"default signal_source should be 'market_observer', got {dec.signal_source!r}"
    )




@spec_claim(
    SpecClaim(
        "SA-S03", _CONTRACT, "§3.2 additive migration for signal_source", "L2a",
        "migrations._ADDITIVE_COLUMNS adds signal_source to pi_decisions so existing "
        "app.db files gain the column (additive-only, not in CREATE TABLE)",
    ),
)
def test_migration_adds_signal_source_column():
    src = _MIGRATIONS.read_text(encoding="utf-8")
    assert '("signal_source", "pi_decisions"' in src, (
        "no additive ALTER for pi_decisions.signal_source — existing DBs would lack it"
    )




# ---------------------------------------------------------------------------
# L2b — Invariants (behavioral, pure attribution function)
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# L2c — Invariant (signal_source survives the persistence round-trip)
# ---------------------------------------------------------------------------

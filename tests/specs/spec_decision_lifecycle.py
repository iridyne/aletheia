"""Spec: Decision Lifecycle Contract (decision_lifecycle_contract.md)

The cross-cutting spine opportunity → PiDecision(signal_pending) → TradingDecision →
TradeProposal. This contract governs the *seams* between subsystems (the carriers and
their field semantics) that the per-subsystem contracts do not cover.

Contract: decision_lifecycle_contract
Claim prefix: DL
"""

import dataclasses
from pathlib import Path

from tests.specs.spec_meta import SpecClaim, spec_claim


# ---------------------------------------------------------------------------
# L2a — Structural claims (carriers + their fields)
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-S01", "decision_lifecycle_contract", "§2.1 Opportunity carrier", "L2a",
        "Opportunity model carries the spine fields the downstream consumers rely on: "
        "market, direction_bias, confidence (categorical), confidence_score (numeric), "
        "status, scanner_family",
    ),
)
def test_opportunity_carrier_fields():
    from aletheia.storage.models import Opportunity

    mf = Opportunity.model_fields
    for name in ("market", "direction_bias", "confidence", "confidence_score", "status", "scanner_family"):
        assert name in mf, f"Opportunity missing seam field {name!r} (decision_lifecycle §2.1)"


@spec_claim(
    SpecClaim(
        "DL-S02", "decision_lifecycle_contract", "§2.2 TradingDecision carrier", "L2a",
        "TradingDecision dataclass — the sole LLM→proposal carrier — exposes "
        "action/conviction/reasoning (required) + market_type/opportunity_id/signal_source",
    ),
)
def test_trading_decision_carrier_fields():
    from aletheia.agent_shell.trading_decision import TradingDecision

    field_names = {f.name for f in dataclasses.fields(TradingDecision)}
    required = {"action", "conviction", "reasoning", "market_type", "opportunity_id", "signal_source"}
    missing = required - field_names
    assert not missing, f"TradingDecision missing seam fields {missing} (decision_lifecycle §2.2)"


@spec_claim(
    SpecClaim(
        "DL-S04", "decision_lifecycle_contract", "§2.2 signal_source provenance", "L2a",
        "TradingDecision.signal_source defaults to 'market_observer'; 'serenity' is the "
        "only other provenance (resolves serenity_opportunities.id). Two-loop discriminator "
        "is pi_decisions.action, NOT signal_source (see PI_OPERATOR_DESIGN_DECISIONS §问题2).",
    ),
)
def test_signal_source_default_provenance():
    from aletheia.agent_shell.trading_decision import TradingDecision

    fields = {f.name: f for f in dataclasses.fields(TradingDecision)}
    assert "signal_source" in fields
    assert fields["signal_source"].default == "market_observer", (
        "signal_source default must be 'market_observer' (decision_lifecycle §2.2)"
    )


# ---------------------------------------------------------------------------
# L2b — Behavioral / seam-existence claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-S03", "decision_lifecycle_contract", "§2.3 signal_pending seam", "L2b",
        "The paper_trader→PI seam exists: trading_context reads pi_decisions "
        "WHERE action='signal_pending' and populates layer['pending_signals'] "
        "(the seam carrier between hop 2 and hop 4)",
    ),
)
def test_signal_pending_seam_present():
    src = Path(__file__).parents[2] / "aletheia" / "agent_shell" / "trading_context.py"
    text = src.read_text(encoding="utf-8")
    assert "signal_pending" in text, (
        "trading_context.py must read the signal_pending seam (decision_lifecycle §2.3)"
    )
    assert "pending_signals" in text, (
        "trading_context.py must populate layer['pending_signals'] (decision_lifecycle §2.3)"
    )


@spec_claim(
    SpecClaim(
        "DL-S05", "decision_lifecycle_contract", "§2.3 executable candidate view", "L2b",
        "System Operator context separates executable ranker candidates from excluded "
        "candidate summaries so unsupported/expired/guard-blocked rows do not occupy "
        "the main prompt candidate slots.",
    ),
)
def test_executable_candidate_view_separates_skips_from_main_slots():
    root = Path(__file__).parents[2]
    context_text = (root / "aletheia" / "agent_shell" / "trading_context.py").read_text(encoding="utf-8")
    decision_text = (root / "aletheia" / "agent_shell" / "trading_decision.py").read_text(encoding="utf-8")

    assert "executable_candidates" in context_text
    assert "excluded_candidates_summary" in context_text
    assert "## Executable Candidates" in decision_text
    assert "## Excluded Candidate Summary" in decision_text


# ---------------------------------------------------------------------------
# L2c — Invariant claims
# ---------------------------------------------------------------------------


@spec_claim(
    SpecClaim(
        "DL-C01", "decision_lifecycle_contract", "§4 DL-INV-2 conviction gate", "L2c",
        "_MIN_CONVICTION_TO_TRADE == 55: a TradingDecision below this conviction is forced "
        "to skip. Changing this threshold must update decision_lifecycle_contract §2.2/§4.",
    ),
)
def test_min_conviction_threshold_is_55():
    from aletheia.agent_shell import trading_decision

    assert trading_decision._MIN_CONVICTION_TO_TRADE == 55, (
        "Conviction gate drifted from 55 — sync decision_lifecycle_contract §4 (DL-INV-2)"
    )

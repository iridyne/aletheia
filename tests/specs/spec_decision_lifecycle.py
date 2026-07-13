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
        "action/conviction/reasoning/bear_case (required) + market_type/opportunity_id/signal_source",
    ),
)
def test_trading_decision_carrier_fields():
    from aletheia.agent_shell.trading_decision import TradingDecision

    field_names = {f.name for f in dataclasses.fields(TradingDecision)}
    required = {
        "action", "conviction", "reasoning", "bear_case",
        "market_type", "opportunity_id", "signal_source",
    }
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
        "DL-S07", "decision_lifecycle_contract", "§2.3 signal_pending attribution", "L2b",
        "A market_observer signal_pending carrier with a real opportunity_id is a valid "
        "System Operator attribution source even after the originating Opportunity is reviewed.",
        status="implemented",
    ),
)
def test_market_observer_pending_signal_attaches_opportunity_id():
    from aletheia.agent_shell.trading_decision import (
        TradingDecision,
        _attach_opportunity_context,
    )

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=70,
        reasoning="consume the queued scanner signal",
    )
    ctx = {
        "layer1": {
            "opportunities": [
                {
                    "id": "opp-competing-open",
                    "symbol": "BTC/USDT",
                    "direction_bias": "long",
                }
            ]
        },
        "layer2": {
            "ranker_candidates": [],
            "pending_signals": [
                {
                    "symbol": "BTC/USDT",
                    "direction": "long",
                    "opportunity_id": "opp-market-pending",
                    "signal_source": "market_observer",
                }
            ],
        },
        "layer3": {},
    }

    _attach_opportunity_context(decision, ctx)

    assert decision.opportunity_id == "opp-market-pending"
    assert decision.signal_source == "market_observer"


@spec_claim(
    SpecClaim(
        "DL-S08", "decision_lifecycle_contract", "§1 ranked operator handoff", "L2a",
        "Opportunity producers persist opportunities only; paper_trader is the market pipeline "
        "component that wakes the System Operator after ranking and signal_pending persistence.",
        status="implemented",
    ),
)
def test_opportunity_producers_do_not_bypass_ranker_when_waking_operator():
    root = Path(__file__).parents[2]
    observer = (root / "aletheia" / "agents" / "market_observer.py").read_text(encoding="utf-8")
    emitter = (root / "aletheia" / "universe" / "signal_emitter.py").read_text(encoding="utf-8")
    paper_trader = (root / "aletheia" / "agents" / "paper_trader.py").read_text(encoding="utf-8")

    assert "notify_pi_daemon" not in observer
    assert "notify_pi_daemon" not in emitter
    assert "insert_pi_decision(pi_dec)" in paper_trader
    assert "notify_pi_daemon" in paper_trader


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


@spec_claim(
    SpecClaim(
        "DL-S06", "decision_lifecycle_contract", "§4 DL-INV-5 instrument price", "L2a",
        "submit_decision_as_proposal resolves the final market_type before calling "
        "latest_snapshot and passes that market_type into the price lookup.",
    ),
)
def test_proposal_price_lookup_is_market_type_scoped():
    from aletheia.agent_shell import trading_decision

    src = Path(trading_decision.__file__).read_text(encoding="utf-8")
    start = src.index("async def submit_decision_as_proposal")
    end = src.index("\ndef _proposal_direction_overheated", start)
    body = src[start:end]

    resolution = body.index("market_type = decision.market_type")
    lookup = body.index("latest_snapshot(decision.symbol, market_type=market_type)")
    assert resolution < lookup


@spec_claim(
    SpecClaim(
        "DL-C02", "decision_lifecycle_contract", "§4 DL-INV-9 proposal outcome", "L2c",
        "A trade-like System Operator intent that does not create a proposal must retain a "
        "structured proposal_submission rejection reason; it must not collapse into an "
        "unexplained NULL proposal_id.",
        status="implemented",
    ),
)
def test_failed_proposal_submission_keeps_structured_reason():
    from aletheia.agent_shell import trading_decision

    src = Path(trading_decision.__file__).read_text(encoding="utf-8")
    start = src.index("async def submit_decision_as_proposal")
    end = src.index("\ndef _proposal_direction_overheated", start)
    body = src[start:end]

    assert "proposal_price_availability" in body
    assert '"status": "failed"' in body
    assert '"reason": "market_price_unavailable"' in body

"""Regression: proposal agent_rationale must never slice its meta JSON.

Historical bug (2026-06-25): submit_decision_as_proposal built
`f"{reasoning[:200]}\n---\n{json.dumps(meta)}"` then applied a SECOND `[:500]`
to the combined string. decision_meta JSON is ~1.1KB, so the 500-cap guillotined
it mid-token → unparseable. The Proposals drawer fell back to dumping the raw
"---{broken json}" tail to the user ("夹杂弱代码"), and the thesis itself was cut
to 200 chars mid-sentence ("没搞全"). analytics/reasoning_feedback also lost
scanner_family. These tests lock the fix: thesis capped alone, JSON always whole.
"""

import json
from unittest.mock import patch, AsyncMock

import pytest

from aletheia.agent_shell.trading_decision import (
    TradingDecision,
    submit_decision_as_proposal,
    _MAX_RATIONALE_TEXT,
)


def _capture_rationale(monkeypatch, decision: TradingDecision) -> str:
    """Run submit_decision_as_proposal with I/O mocked; return the agent_rationale
    string passed to create_paper_proposal."""
    captured: dict = {}

    def fake_create_paper_proposal(**kwargs):
        captured["rationale"] = kwargs.get("agent_rationale")
        return {"id": "prop-test", "status": "pending"}

    # Mock only the I/O boundaries — the rationale-building logic runs for real.
    monkeypatch.setattr(
        "aletheia.data.price_feed.get_fresh_price_for_protective_close",
        lambda _symbol, _market_type, *, venue: 100.0,
    )
    monkeypatch.setattr(
        "aletheia.agent_shell.trading_decision._latest_exact_entry_snapshot",
        lambda symbol, market_type, venue: {
            "symbol": symbol,
            "market_type": market_type,
            "exchange": venue,
            "price": 100.0,
        },
    )
    monkeypatch.setattr(
        "aletheia.execution.paper_tools.create_paper_proposal",
        fake_create_paper_proposal,
    )
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.run_guards", lambda ctx: []
    )
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda v: {"guards_run": 0, "blocks": 0, "warnings": 0, "details": []},
    )

    import asyncio
    asyncio.run(submit_decision_as_proposal(decision, "okx_demo"))
    return captured.get("rationale")


def _decision(reasoning: str) -> TradingDecision:
    return TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="short",
        conviction=64,
        reasoning=reasoning,
        supporting_signals=["sig " + "x" * 90] * 5,   # large, to inflate meta JSON
        opposing_signals=["opp " + "y" * 90] * 5,
        position_size_usdt=25.0,
        debate_ran=True,
        debate_bull_confidence=32,
        debate_bear_confidence=72,
    )


def test_rationale_meta_json_is_always_parseable(monkeypatch):
    """The `\\n---\\n` JSON half must always json.loads cleanly, even when the
    thesis is long and the supporting/opposing lists make the JSON ~1KB+."""
    long_reasoning = "BTC short thesis. " * 60  # ~1080 chars, well over the cap
    rationale = _capture_rationale(monkeypatch, _decision(long_reasoning))

    assert rationale is not None
    assert "\n---\n" in rationale
    text, _, meta_json = rationale.partition("\n---\n")
    # The whole JSON survives — this is the core regression guard.
    meta = json.loads(meta_json)
    assert meta["conviction"] == 64
    assert meta["debate_bear"] == 72
    assert len(meta["supporting"]) == 5
    assert len(meta["supporting_signals"]) == 5
    assert len(meta["opposing_signals"]) == 5
    motivation = meta["trade_motivation"]
    assert motivation["schema_version"] == "structured-trade-motivation/v1"
    assert motivation["action_intent"] == "short"
    assert motivation["thesis"] == long_reasoning
    assert motivation["supporting_evidence"] == _decision(long_reasoning).supporting_signals[:5]
    assert motivation["opposing_evidence"] == _decision(long_reasoning).opposing_signals[:5]
    assert motivation["observed_facts"] == []
    assert motivation["evidence_manifest"] == {}


def test_rationale_thesis_capped_but_not_the_whole_string(monkeypatch):
    """Only the thesis is capped (at _MAX_RATIONALE_TEXT, with an ellipsis); the
    combined string is NOT re-truncated to a fixed 500 (the old bug)."""
    long_reasoning = "Z" * 2000
    rationale = _capture_rationale(monkeypatch, _decision(long_reasoning))

    text, _, meta_json = rationale.partition("\n---\n")
    assert text.endswith("…")
    assert len(text) <= _MAX_RATIONALE_TEXT + 1  # +1 for the ellipsis char
    # The old bug forced len(rationale)==500; now it's thesis + full JSON.
    assert len(rationale) != 500
    assert len(rationale) > 500  # thesis(600) + ~1KB JSON
    json.loads(meta_json)  # still parseable


def test_rationale_short_thesis_not_truncated(monkeypatch):
    """A normal short thesis is preserved verbatim (no ellipsis, no cut)."""
    short = "Short BTC: bear regime, RSI exhaustion across timeframes."
    rationale = _capture_rationale(monkeypatch, _decision(short))

    text, _, meta_json = rationale.partition("\n---\n")
    assert text == short          # untouched
    assert not text.endswith("…")
    json.loads(meta_json)


def test_direction_overheated_skips_proposal_creation(monkeypatch):
    """Normal-conviction same-direction trades should not add to an overheated stream."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    called = {"create": 0}

    def fake_create_paper_proposal(**kwargs):
        called["create"] += 1
        return {"id": "unexpected", "status": "pending"}

    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda direction, conviction: "6h native proposals are 5/5 long")
    monkeypatch.setattr(
        "aletheia.trading.market_data.latest_snapshot",
        lambda symbol, market_type=None: {"price": 100.0},
    )
    monkeypatch.setattr("aletheia.execution.paper_tools.create_paper_proposal", fake_create_paper_proposal)

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=70,
        reasoning="another same-direction setup",
        position_size_usdt=25.0,
    )
    result = asyncio.run(submit_decision_as_proposal(decision, "okx_demo"))

    assert result == {
        "status": "skipped",
        "reason": "direction_overheated",
        "detail": "6h native proposals are 5/5 long",
    }
    assert called["create"] == 0

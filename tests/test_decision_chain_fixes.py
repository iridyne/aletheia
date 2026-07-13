"""Focused regressions for decision-chain fixes (batch B4).

#12  non-integer LLM conviction must not crash _parse_decision.
#152 attribution must not be severed by a conflicting same-symbol Layer-1 opp
     when a matching-direction ranker candidate exists.
#21/#102 serenity opportunities store the instrument under "market_type" — the
     decision path must read that key (not "market").
#9   _make_candidate must carry the opportunity's market_type into the candidate.
#102 (pending) promoted serenity pending-signals recover market_type from the
     source serenity_opportunity (pending_signals don't carry it).
"""

from aletheia.agent_shell.trading_decision import (
    TradingDecision,
    _parse_decision,
    _attach_opportunity_context,
)


def test_parse_decision_guards_non_integer_conviction():
    base = {"action": "trade", "symbol": "BTC/USDT", "direction": "long", "reasoning": ""}
    assert _parse_decision({**base, "conviction": "high"}).conviction == 0     # non-numeric → 0 (safe skip)
    assert _parse_decision({**base, "conviction": "8.5"}).conviction == 8      # float-string → int
    assert _parse_decision({**base, "conviction": 7}).conviction == 7          # plain int preserved
    assert _parse_decision({**base, "conviction": None}).conviction == 0       # null → 0


def test_submit_decision_reads_snapshot_for_final_market_type(monkeypatch):
    """A perp proposal must not build quantity/entry geometry from a spot row."""
    import asyncio
    import aletheia.agent_shell.trading_decision as td

    snapshot_calls: list[tuple[str, str | None]] = []
    captured: dict = {}

    def latest(symbol, market_type=None):
        snapshot_calls.append((symbol, market_type))
        return {"price": 200.0 if market_type == "usdt_futures" else 100.0}

    def create(**kwargs):
        captured.update(kwargs)
        return {"status": "pending"}

    monkeypatch.setattr("aletheia.trading.market_data.latest_snapshot", latest)
    monkeypatch.setattr("aletheia.execution.paper_tools.create_paper_proposal", create)
    monkeypatch.setattr(
        "aletheia.trading.entry_snapshot.capture_attribution_snapshot_json",
        lambda _symbol: None,
    )
    monkeypatch.setattr("aletheia.trust.guard_chain.run_guards", lambda ctx: [])
    monkeypatch.setattr(
        "aletheia.trust.guard_chain.summarize",
        lambda verdicts: {"blocks": [], "details": []},
    )
    monkeypatch.setattr(td, "_proposal_direction_overheated", lambda *args: None)

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=80,
        reasoning="perp setup",
        position_size_usdt=20.0,
        market_type="usdt_futures",
    )
    asyncio.run(td.submit_decision_as_proposal(decision, "paper_internal"))

    assert snapshot_calls == [("BTC/USDT", "usdt_futures")]
    assert captured["price"] == 200.0
    assert captured["quantity"] == 0.1


def test_attribution_survives_conflicting_layer1_via_ranker():
    """#152: a conflicting-direction Layer-1 opp must not block the ranker fallback."""
    dec = TradingDecision(action="trade", symbol="BTC/USDT", direction="long",
                          conviction=60, reasoning="")
    ctx = {
        "layer1": {"opportunities": [
            {"symbol": "BTC/USDT", "direction_bias": "short", "id": "opp-short"},
        ]},
        "layer2": {
            "pending_signals": [
                {"symbol": "BTC/USDT", "direction": "long", "opportunity_id": "opp-long",
                 "market_type": "usdt_futures", "signal_source": "market_observer"},
            ],
            "ranker_candidates": [
                {"symbol": "BTC/USDT", "direction": "long", "opportunity_id": "opp-long",
                 "market_type": "usdt_futures"},
            ],
        },
    }
    _attach_opportunity_context(dec, ctx)
    assert dec.opportunity_id == "opp-long"          # not severed by the conflicting opp
    assert dec.signal_source == "market_observer"
    assert dec.market_type == "usdt_futures"


def test_serenity_opportunity_market_type_key():
    """#21/#102: serenity fallback must read market_type (not the non-existent 'market')."""
    dec = TradingDecision(action="trade", symbol="TAO/USDT", direction="long",
                          conviction=60, reasoning="")
    ctx = {
        "layer2": {"pending_signals": [
            {"symbol": "TAO/USDT", "direction": "long", "opportunity_id": "ser-1",
             "market_type": "usdt_futures", "signal_source": "serenity"},
        ]},
        "layer3": {"serenity_opportunities": [
            {"symbol": "TAO/USDT", "direction": "long", "id": "ser-1",
             "market_type": "usdt_futures", "execution_eligible": True,
             "asset_class": "crypto"},
        ]},
    }
    _attach_opportunity_context(dec, ctx)
    assert dec.opportunity_id == "ser-1"
    assert dec.signal_source == "serenity"
    assert dec.market_type == "usdt_futures"


def test_make_candidate_emits_market_type():
    """#9: _make_candidate must carry the opportunity's instrument type."""
    from aletheia.agents.paper_trader_ranker import _make_candidate

    cand = _make_candidate(
        {"id": "opp-1", "symbol": "BTC/USDT", "market": "usdt_futures"},
        direction="long",
    )
    assert cand.get("market_type") == "usdt_futures", (
        "candidate dropped market_type — trading_decision ranker branch reads "
        "c.get('market_type') and would get None (#9)"
    )
    # Legacy opps that already used the market_type key still work.
    cand2 = _make_candidate({"id": "o2", "symbol": "ETH/USDT", "market_type": "spot"})
    assert cand2.get("market_type") == "spot"


def test_serenity_pending_signal_carries_exact_market_type(monkeypatch):
    """A promoted Serenity carrier includes its executable instrument identity."""
    dec = TradingDecision(action="trade", symbol="TAO/USDT", direction="long",
                          conviction=60, reasoning="")
    ctx = {
        "layer2": {"pending_signals": [
            {"symbol": "TAO/USDT", "direction": "long",
             "market_type": "usdt_futures", "opportunity_id": "ser-pending",
             "signal_source": "serenity"},
        ]},
    }
    _attach_opportunity_context(dec, ctx)
    assert dec.opportunity_id == "ser-pending"
    assert dec.signal_source == "serenity"
    assert dec.market_type == "usdt_futures", (
        "pending-signal branch dropped the carrier's exact market_type"
    )

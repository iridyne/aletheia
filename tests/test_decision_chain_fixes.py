"""Focused regressions for decision-chain fixes (batch B4).

#12  non-integer LLM conviction must not crash _parse_decision.
#152 attribution must not be severed by a conflicting same-symbol Layer-1 opp
     when a matching-direction ranker candidate exists.
#21/#102 serenity opportunities store the instrument under "market_type" — the
     decision path must read that key (not "market").
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


def test_attribution_survives_conflicting_layer1_via_ranker():
    """#152: a conflicting-direction Layer-1 opp must not block the ranker fallback."""
    dec = TradingDecision(action="trade", symbol="BTC/USDT", direction="long",
                          conviction=60, reasoning="")
    ctx = {
        "layer1": {"opportunities": [
            {"symbol": "BTC/USDT", "direction_bias": "short", "id": "opp-short"},
        ]},
        "layer2": {"ranker_candidates": [
            {"symbol": "BTC/USDT", "direction": "long", "opportunity_id": "opp-long",
             "market_type": "usdt_futures"},
        ]},
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

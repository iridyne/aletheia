from __future__ import annotations

import json


def test_structured_trade_motivation_has_stable_json_round_trip():
    from aletheia.trading.trade_motivation import (
        parse_trade_motivation,
        serialize_trade_motivation,
    )

    motivation = {
        "schema_version": "structured-trade-motivation/v1",
        "action_intent": "long",
        "thesis": "Breakout continuation after a confirmed retest.",
        "observed_facts": [
            {
                "fact": "One-hour volume is above its recent baseline.",
                "evidence_refs": ["technical_snapshot:snap-1", "opportunity:opp-1"],
            }
        ],
        "interpretations": ["Demand is absorbing the retest."],
        "supporting_evidence": ["volume expansion"],
        "opposing_evidence": ["funding is elevated"],
        "key_assumptions": ["liquidity remains available"],
        "invalidation_conditions": ["price closes below the retest low"],
        "expected_horizon": {"hours": 4.0},
        "confidence": 71,
        "uncertainty_reason": "Only one completed retest is available.",
        "evidence_manifest": {
            "source_chain": {"opportunity_id": "opp-1"},
            "technical_snapshot": {"id": "snap-1"},
        },
    }

    encoded = serialize_trade_motivation(motivation)

    assert encoded == serialize_trade_motivation(parse_trade_motivation(encoded))
    assert json.loads(encoded) == parse_trade_motivation(encoded)


def test_legacy_or_invalid_trade_motivation_is_unavailable_not_invented():
    from aletheia.trading.trade_motivation import parse_trade_motivation

    assert parse_trade_motivation(None) is None
    assert parse_trade_motivation("{broken") is None
    assert parse_trade_motivation({"thesis": "legacy prose only"}) is None
    assert parse_trade_motivation({
        "schema_version": "structured-trade-motivation/v2",
        "action_intent": "long",
        "thesis": "unknown version",
        "observed_facts": [],
        "interpretations": [],
        "supporting_evidence": [],
        "opposing_evidence": [],
        "key_assumptions": [],
        "invalidation_conditions": [],
        "expected_horizon": None,
        "confidence": 50,
        "uncertainty_reason": "unavailable",
        "evidence_manifest": {},
    }) is None


def test_action_intent_is_normalized_from_action_and_direction():
    from types import SimpleNamespace
    from aletheia.trading.trade_motivation import (
        build_trade_motivation,
        parse_trade_motivation,
    )

    canonical = build_trade_motivation(SimpleNamespace(
        action="trade", symbol="BTC/USDT", direction="short", conviction=60,
        reasoning="short thesis",
    ))
    assert canonical["action_intent"] == "short"
    assert build_trade_motivation(SimpleNamespace(
        action="skip", symbol="BTC/USDT", direction="long", conviction=20,
        reasoning="no trade",
    ))["action_intent"] == "hold"
    assert build_trade_motivation(SimpleNamespace(
        action="trade", symbol="BTC/USDT", direction=None, conviction=60,
        reasoning="conflicting entry intent",
    ))["action_intent"] == "unavailable"
    for allowed in ("long", "short", "hold", "close", "reduce"):
        assert parse_trade_motivation({**canonical, "action_intent": allowed})[
            "action_intent"
        ] == allowed
    assert parse_trade_motivation({**canonical, "action_intent": "trade"})[
        "action_intent"
    ] == "unavailable"




def test_observed_facts_keep_only_refs_resolved_by_exact_manifest():
    from types import SimpleNamespace
    from aletheia.trading.trade_motivation import build_trade_motivation

    decision = SimpleNamespace(
        action="trade", symbol="BTC/USDT", direction="long", conviction=70,
        reasoning="grounded setup",
        observed_facts=[
            {
                "fact": "Exact opportunity and snapshot are present.",
                "evidence_refs": [
                    "opportunity:opp-exact",
                    "market_snapshot:snap-invented",
                    "technical_snapshot:snap-exact",
                ],
            },
            {
                "fact": "Invented evidence should not survive.",
                "evidence_refs": ["news:item-invented"],
            },
        ],
    )
    motivation = build_trade_motivation(decision, {
        "source_chain": {"opportunity_id": "opp-exact"},
        "technical_snapshot": {"id": "snap-exact"},
    })

    assert motivation["observed_facts"] == [{
        "fact": "Exact opportunity and snapshot are present.",
        "evidence_refs": [
            "opportunity:opp-exact",
            "technical_snapshot:snap-exact",
        ],
    }]

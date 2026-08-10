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
                "evidence_refs": ["market_snapshot:snap-1", "opportunity:opp-1"],
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
        "evidence_manifest": {"t0": {"source_id": "opp-1"}},
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
    from aletheia.agent_shell.trading_decision import TradingDecision
    from aletheia.trading.trade_motivation import (
        build_trade_motivation,
        parse_trade_motivation,
    )

    canonical = build_trade_motivation(TradingDecision(
        action="trade", symbol="BTC/USDT", direction="short", conviction=60,
        reasoning="short thesis",
    ))
    assert canonical["action_intent"] == "short"
    assert build_trade_motivation(TradingDecision(
        action="skip", symbol="BTC/USDT", direction="long", conviction=20,
        reasoning="no trade",
    ))["action_intent"] == "hold"
    assert build_trade_motivation(TradingDecision(
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


def test_pi_decision_persists_the_same_structured_motivation_in_evidence_manifest():
    from aletheia.agent_shell.reflection import _build_pi_decision
    from aletheia.trading.trade_motivation import build_trade_motivation
    from aletheia.agent_shell.trading_decision import TradingDecision

    decision = TradingDecision(
        action="trade",
        symbol="BTC/USDT",
        direction="long",
        conviction=68,
        reasoning="Retest held with expanding volume.",
        supporting_signals=["volume expansion"],
        opposing_signals=["funding elevated"],
        key_assumptions=["liquidity holds"],
        invalidation_conditions=["close below retest low"],
        expected_horizon_hours=4.0,
    )
    evidence_manifest = {"t0": {"source_id": "opp-1"}}
    motivation = build_trade_motivation(decision, evidence_manifest)

    persisted = _build_pi_decision({
        "action": "trade",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "usdt_futures",
        "conviction": 68,
        "reasoning": decision.reasoning,
        "evidence_manifest": evidence_manifest,
        "trade_motivation": motivation,
    })

    assert json.loads(persisted.evidence_manifest_json) == {
        "t0": {"source_id": "opp-1"},
        "trade_motivation": motivation,
    }

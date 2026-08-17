"""Causal attribution and semantic routing contracts for Learning consumers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _ready_research(*source_ids: str) -> dict:
    falsification = "Reject if prospective exact-scope treatment underperforms control."
    return {
        "observation": {
            "statement": "The exact source cases recorded the declared loss pattern.",
            "source_artifact_ids": list(source_ids),
            "outcome_identities": [f"position:{source_id}" for source_id in source_ids],
        },
        "candidate_explanation": (
            "The scoped candidate guard may improve net utility over 4h."
        ),
        "competing_explanations": ["The recorded regime may explain the loss pattern."],
        "relevant_context": {
            "recorded_at_t0": {"symbol": "BTC/USDT"},
            "missing": ["t0_context_missing:catalyst_news"],
        },
        "falsification_condition": falsification,
        "red_team_summary": {
            "strongest_simpler_competing_explanation": (
                "The recorded regime may explain the loss pattern."
            ),
            "smallest_falsification_test": falsification,
            "decision": "ready",
            "reasoning": "The bounded exact evidence is sufficient for Shadow testing only.",
        },
        "research_status": "ready",
    }


@spec_claim(SpecClaim(
    "LE-B15", "learning_evolution", "§9.4 causal reflection", "L2b",
    "Reflection resolves outcomes only through proposal/opportunity causal ids; symbol-only movement "
    "or an unrelated same-symbol position is never presented as the decision outcome.",
    status="implemented",
))
def test_reflection_rejects_symbol_only_outcome_inference(monkeypatch):
    from aletheia.agent_shell import reflection
    from aletheia.storage.models import Position
    from aletheia.storage.repositories import insert_position
    from tests.test_outcome_closure import _insert_entry_chain

    opportunity_id, proposal_id, order_id = _insert_entry_chain(
        position_id="unrelated",
        proposal_id="proposal-linked",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
    )
    insert_position(Position(
        id="unrelated",
        symbol="BTC/USDT",
        market_type="usdt_futures",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        current_price=110.0,
        execution_mode="paper_internal",
        status="closed",
        source="native",
        opened_at="2026-07-12T08:00:00+00:00",
        realized_pnl=10.0,
        closed_at="2026-07-12T09:00:00+00:00",
        close_reason="take_profit",
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="local_simulation",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        close_category="strategy",
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        outcome_semantics_version="oi-v1",
        opportunity_id=opportunity_id,
        proposal_id=proposal_id,
        order_id=order_id,
    ))

    assert "unavailable" in reflection._compute_outcome(
        "BTC/USDT", None, "2026-07-12T08:00:00+00:00"
    ).lower()
    exact = reflection._compute_outcome(
        "IGNORED/USDT", "proposal-linked", "2026-07-12T08:00:00+00:00"
    )
    assert "causally linked" in exact.lower() and "+10.00" in exact
    assert "unavailable" in reflection._compute_missed_opportunity(
        None, "market_observer", "long", "2026-07-12T08:00:00+00:00"
    ).lower()


@spec_claim(
    SpecClaim(
        "LE-B07", "learning_evolution", "§7 prompt manifest", "L2b",
        "Every Operator prompt decision stores one exact Learning bundle manifest.",
        status="implemented",
    ),
    SpecClaim(
        "LE-B14", "learning_evolution", "§9.4 prompt causal links", "L2b",
        "The Operator bundle manifest has one bundle id, component fingerprints, and the decision's "
        "causal opportunity/proposal ids rather than separate credit rows per component.",
        status="implemented",
    ),
)
def test_operator_prompt_bundle_manifest_is_one_durable_unit(monkeypatch):
    from aletheia.learning.prompt_bundles import (
        build_observer_learning_manifest,
        build_operator_learning_manifest,
    )
    from aletheia.agent_shell import reflection
    from aletheia.storage.models import Opportunity, PiDecision
    from aletheia.storage.repositories import insert_opportunity, insert_pi_decision
    from aletheia.storage.sqlite import get_db

    ctx = {
        "layer1": {},
        "layer2": {
            "reflection": "exact decision reflection",
            "trading_memo": {"generated_at": "t1", "key_insight": "memo"},
            "reasoning_feedback": "reasoning",
            "calibration_feedback": "calibration",
        },
        "layer3": {
            "lessons": [{"content": "case"}],
            "ps_lessons": [{"summary": "structured lesson"}],
        },
    }
    manifest = build_operator_learning_manifest(ctx)
    assert manifest == build_operator_learning_manifest(ctx)
    assert set(manifest["components"]) == {
        "reflection", "trading_memo", "reasoning_feedback",
        "calibration_feedback", "trade_cases", "structured_lessons",
    }

    monkeypatch.setattr(reflection, "_REFLECTION_FILE", SimpleNamespace(
        parent=SimpleNamespace(mkdir=lambda **kwargs: None),
        write_text=lambda *args, **kwargs: None,
    ))
    opportunity_id = "learning-manifest-opp-1"
    insert_opportunity(Opportunity(
        id=opportunity_id,
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        status="queued",
    ))
    insert_pi_decision(PiDecision(
        id="pending-learning-manifest-opp-1",
        action="signal_pending",
        opportunity_id=opportunity_id,
        signal_source="market_observer",
        symbol="BTC/USDT",
        direction="long",
        market_type="spot",
    ))
    reflection.record_decision({
        "action": "skip", "symbol": "BTC/USDT", "direction": "long",
        "market_type": "spot",
        "opportunity_id": opportunity_id, "signal_source": "market_observer",
        "proposal_id": "proposal-1",
        "learning_manifest": manifest,
    })
    # Select the finalized decision (action='skip') for this exact opportunity.
    # A signal_pending carrier with proposal_id=NULL may be present for the same
    # opportunity; querying only by created_at order is non-deterministic (#212).
    row = get_db().execute(
        "SELECT opportunity_id, proposal_id, learning_manifest_json "
        "FROM pi_decisions "
        "WHERE opportunity_id=? AND action='skip' "
        "ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    stored = json.loads(row["learning_manifest_json"])
    assert row["opportunity_id"] == opportunity_id
    assert row["proposal_id"] == "proposal-1"
    assert stored["bundle_id"] == manifest["bundle_id"]
    assert len(stored["components"]) == 6
    observer = build_observer_learning_manifest("memo+cases", "symbol guard")
    assert set(observer["components"]) == {"advisory_memory", "guard_advisory"}
    assert observer["attribution_unit"] == "observer_advisory_bundle"


@spec_claim(SpecClaim(
    "LE-B19", "learning_evolution", "§9.7 automatic block routing", "L2b",
    "Automatic block recommendations become non-blocking guard shadow candidates with "
    "evidence-backed source scope (symbol + scanner_family). Candidates without "
    "derivable scope fail closed. Scoped guards are not coerced into category=risk "
    "lessons and are never promoted while carrying the shadow-only tag (#197).",
    status="implemented",
))
async def test_automatic_block_recommendation_routes_to_guard_shadow(monkeypatch):
    from aletheia.learning import guard_routing

    captured = {}
    created_ids = iter(("candidate-item", "candidate-item-2"))

    def fake_create(*, title, content, metadata):
        captured.update(metadata)
        return SimpleNamespace(id=next(created_ids))

    monkeypatch.setattr(guard_routing, "create_guard_candidate", fake_create)
    # Evidence must include factor_keys encoding symbol so scope can be derived (#197).
    evidence_with_scope = {
        "case_count": 12,
        "outcome_counts": {"loss": 9},
        "factor_keys": ["momentum:ETHUSDT:long:medium_confidence:not_late"],
        "supporting_factor_keys": ["momentum:BTCUSDT:long:medium_confidence:not_late"],
        "scanner_families": ["momentum"],
    }
    routed = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence=evidence_with_scope,
        source_id="case-1",
        research_evidence=_ready_research("case-1"),
    )

    assert routed["action"] == "guard_shadow_candidate"
    assert routed["hypothesis_status"] == "proposed"
    assert routed["hypothesis_id"]
    assert routed["guard_candidate_id"] == "candidate-item"
    from aletheia.learning.ledger import get_record, list_records
    hypothesis_row = get_record("learning_hypotheses", routed["hypothesis_id"])
    assert hypothesis_row is not None
    assert hypothesis_row["path_id"] == "ranker_learned_guard"
    assert hypothesis_row["consumer"] == "paper_trader_ranker"
    assert hypothesis_row["target_horizon"] == "4h"
    assert hypothesis_row["source_type"] == "guard_candidate"
    assert json.loads(hypothesis_row["scope_json"]) == {
        "applies_to": {"scanner_family": ["momentum"], "symbol": ["BTCUSDT"]},
        "guard_id": routed["guard_id"],
    }
    assert json.loads(hypothesis_row["action_json"]) == {
        "block_candidate": True,
        "guard_id": routed["guard_id"],
        "score_penalty": 0,
        "type": "candidate_guard",
    }
    assert json.loads(hypothesis_row["source_artifact_ids_json"]) == [
        "case-1", "candidate-item",
    ]
    repeated = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence=evidence_with_scope,
        source_id="case-1",
        research_evidence=_ready_research("case-1"),
    )
    assert repeated["hypothesis_id"] == routed["hypothesis_id"]
    assert repeated["guard_candidate_id"] == "candidate-item"
    assert len(list_records("learning_hypotheses")) == 1
    assert list_records("learning_experiments") == []
    assert list_records("learning_exposures") == []
    assert list_records("learning_contribution_verdicts") == []
    assert captured["status"] == "candidate"
    assert captured["enforcement"] == "advisory"
    assert captured["shadow_only"] is True
    assert captured["block_candidate"] is False
    assert captured["score_penalty"] == 0
    assert captured["proposed_block_candidate"] is True
    assert captured["proposed_score_penalty"] == 0
    assert "shadow-only" in captured["tags"]
    # Scope must carry specific symbol and scanner family, not a global crypto scope (#197).
    applies_to = captured["applies_to"]
    assert "symbol" in applies_to, "applies_to must contain symbol scope"
    assert "BTCUSDT" in applies_to["symbol"]
    assert "scanner_family" in applies_to, "applies_to must contain scanner_family scope"
    assert "momentum" in applies_to["scanner_family"]
    unavailable = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={
            "supporting_factor_keys": [],
            "factor_keys": ["momentum:ETHUSDT:long:medium_confidence:not_late"],
        },
        source_id="case-2",
    )
    assert unavailable == {
        "action": "guard_shadow_unavailable", "reason": "invalid_block_scope",
    }
    from pathlib import Path
    dreaming_source = Path("aletheia/agents/dreaming_agent.py").read_text(encoding="utf-8")
    assert "route_automatic_block_recommendation" in dreaming_source

    from aletheia.analytics import guard_effectiveness, guard_promotion, trade_attribution
    from aletheia.learning import control
    from aletheia.knowledge.guards import TradingGuard
    monkeypatch.setattr(
        guard_promotion, "load_config",
        lambda: SimpleNamespace(guard=SimpleNamespace(
            auto_promote_enabled=True, min_matching_trades=1,
        )),
    )
    monkeypatch.setattr(control, "promotion_refreeze_reason", lambda: None)
    monkeypatch.setattr(guard_promotion, "load_trading_guards", lambda status: [
        TradingGuard(
            guard_id="shadow", title="shadow", status="candidate",
            tags=["shadow-only"], predicates=[{
                "field": "scanner_family", "op": "eq", "value": "momentum",
            }],
        )
    ])
    monkeypatch.setattr(trade_attribution, "build_trade_attribution_rows", lambda limit=500: [])
    monkeypatch.setattr(
        guard_effectiveness, "_rows_matching_guard",
        lambda *args: (_ for _ in ()).throw(AssertionError("shadow-only candidate evaluated for promotion")),
    )
    result = guard_promotion.run_promotion_check()
    assert result["promotions"] == 0


@spec_claim(SpecClaim(
    "LE-B19c", "learning_evolution", "§9.7 nomination retry", "L2b",
    "A failed hypothesis nomination propagates instead of returning a lesson-ready "
    "recommendation, so the enclosing Dreaming batch leaves its source case pending "
    "for a later retry.",
    status="implemented",
))
async def test_automatic_block_recommendation_propagates_nomination_failure(monkeypatch):
    from aletheia.learning import guard_routing, hypotheses

    monkeypatch.setattr(
        guard_routing,
        "create_guard_candidate",
        lambda **_: SimpleNamespace(id="candidate-item"),
    )

    def fail_nomination(**_):
        raise RuntimeError("temporary nomination failure")

    monkeypatch.setattr(hypotheses, "nominate_learning_hypothesis", fail_nomination)

    with pytest.raises(RuntimeError, match="temporary nomination failure"):
        await guard_routing.route_automatic_block_recommendation(
            {"action": "block_scanner", "scanner": "momentum"},
            evidence={
                "factor_keys": ["momentum:BTCUSDT:long:medium_confidence:not_late"],
                "scanner_families": ["momentum"],
            },
            source_id="case-retry",
            research_evidence=_ready_research("case-retry"),
        )


@spec_claim(SpecClaim(
    "LE-B19b", "learning_evolution", "§9.7 automatic block routing fail-closed", "L2b",
    "Automatic block recommendations without derivable symbol/scanner scope fail "
    "closed: route_automatic_block_recommendation returns guard_shadow_unavailable / "
    "invalid_block_scope without writing a guard candidate (#197).",
    status="implemented",
))
async def test_automatic_block_recommendation_fails_closed_without_scope(monkeypatch):
    from aletheia.learning import guard_routing

    create_called = []

    def fake_create(**_):
        create_called.append(True)
        return SimpleNamespace(id="should-not-be-called")

    monkeypatch.setattr(guard_routing, "create_guard_candidate", fake_create)
    # Evidence has no factor_keys, so symbol scope cannot be derived.
    result = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={"case_count": 5, "outcome_counts": {"loss": 4}},
        source_id="case-no-scope",
    )

    assert result["action"] == "guard_shadow_unavailable"
    assert result["reason"] == "invalid_block_scope"
    assert not create_called, "create_guard_candidate must not be called when scope is missing"


@spec_claim(SpecClaim(
    "LE-C14", "learning_evolution", "§10 disabled consumer baseline", "L2c",
    "autonomous_learning=false removes the complete Operator Learning bundle while retaining "
    "non-Learning market, position, and safety context.",
    status="implemented",
))
def test_disabled_learning_removes_operator_bundle_only():
    from aletheia.learning.prompt_bundles import gate_operator_learning_context

    ctx = {
        "layer1": {"positions": [{"id": "p1"}], "risk": {"kill_switch": False}},
        "layer2": {"reflection": "r", "trading_memo": {"x": 1},
                   "reasoning_feedback": "rf", "calibration_feedback": "cf",
                   "outcome_stats": {"total_trades": 1}},
        "layer3": {"lessons": [{"content": "x"}], "ps_lessons": [{"summary": "y"}],
                   "guard_candidates": [{"content": "g"}], "serenity_opportunities": [{"id": "s"}]},
    }
    gated = gate_operator_learning_context(ctx, enabled=False)
    assert gated["layer1"]["positions"] == [{"id": "p1"}]
    assert gated["layer1"]["risk"] == {"kill_switch": False}
    assert gated["layer2"]["outcome_stats"] == {"total_trades": 1}
    assert gated["layer2"]["reflection"] is None
    assert gated["layer3"]["lessons"] == []
    assert gated["layer3"]["serenity_opportunities"] == []

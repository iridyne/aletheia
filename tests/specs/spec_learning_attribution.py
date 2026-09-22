"""Causal attribution and semantic routing contracts for Learning consumers."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.specs.spec_meta import SpecClaim, spec_claim


def _ready_research(*source_ids: str, symbol: str = "BTCUSDT") -> dict:
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
            "recorded_at_t0": {
                "market_identity": [
                    {
                        "anchor_entity_id": f"filter_decision:{source_id}",
                        "value": {
                            "symbol": symbol,
                            "scanner_family": "momentum",
                            "direction": "long",
                        },
                    }
                    for source_id in source_ids
                ],
            },
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
    from aletheia.storage.models import ShadowTrade
    from aletheia.storage.repositories import insert_shadow_trade
    from aletheia.storage.sqlite import get_db
    from tests.test_outcome_closure import _insert_closed_position_via_producer

    position = _insert_closed_position_via_producer(
        position_id="unrelated", proposal_id="proposal-linked",
        symbol="BTC/USDT", market_type="usdt_futures", direction="long",
        quantity=1.0, entry_price=100.0, realized_pnl=10.0,
        execution_mode="bitget_demo", closed_at="2026-07-12T09:00:00+00:00",
    )
    opportunity_id = position["opportunity_id"]

    assert "unavailable" in reflection._compute_outcome(
        "BTC/USDT", None, "2026-07-12T08:00:00+00:00"
    ).lower()
    exact = reflection._compute_outcome(
        "IGNORED/USDT", "proposal-linked", "2026-07-12T08:00:00+00:00"
    )
    assert "causally linked" in exact.lower() and "+10.00" in exact
    db = get_db()
    db.execute(
        "UPDATE positions SET execution_mode='paper_internal',status='open',"
        "unrealized_pnl=999 WHERE id='unrelated'"
    )
    archived = dict(db.execute("SELECT * FROM positions WHERE id='unrelated'").fetchone())
    assert reflection._compute_outcome(
        "BTC/USDT", "proposal-linked", "2026-07-12T08:00:00+00:00"
    ) == "Outcome unavailable: archived simulator position."
    assert dict(db.execute("SELECT * FROM positions WHERE id='unrelated'").fetchone()) == archived
    assert "unavailable" in reflection._compute_missed_opportunity(
        None, "market_observer", "long", "2026-07-12T08:00:00+00:00"
    ).lower()
    insert_shadow_trade(ShadowTrade(
        id="orphan-keyed-reflection",
        opportunity_id=opportunity_id,
        symbol="BTC/USDT",
        direction="long",
        entry_price=100.0,
        outcome_candidate_key="orphan-keyed-reflection",
    ))
    get_db().execute(
        "UPDATE shadow_trades SET exit_price=101,pnl_pct=1,gross_return_pct=1,"
        "net_pnl_pct=1,estimated_fee_pct=0,estimated_spread_slippage_pct=0,"
        "estimated_funding_pct=0,min_tradable_edge_pct=0,"
        "settlement_price_source='horizon_ohlcv',settlement_lag_sec=0,"
        "cost_model_version='shadow-test-v1',"
        "resolved_at='2026-07-13T08:00:00+00:00' "
        "WHERE id='orphan-keyed-reflection'"
    )
    assert "unavailable" in reflection._compute_missed_opportunity(
        opportunity_id, "market_observer", "long", "2026-07-12T08:00:00+00:00"
    ).lower()




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
    # Contract Delta (2026-09-07, learning_contract.md preamble): "Observation Lab
    # is the only online Experiment authority. Legacy ledgers remain
    # migration/history inputs, never an executable fallback." Routing a guard
    # shadow candidate therefore records the hypothesis and NOTHING in the legacy
    # executable ledgers — the retired `_ensure_experiment` seam is gone, and this
    # inverted assertion locks it out.
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
    mismatched = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={
            "factor_keys": [
                "momentum:ETHUSDT:long:medium_confidence:not_late",
            ],
        },
        source_id="case-mismatch",
        research_evidence=_ready_research("case-mismatch"),
    )
    assert mismatched["reason"] == "research_scope_identity_mismatch"
    from pathlib import Path
    dreaming_source = Path("extensions/research/dreaming_agent.py").read_text(encoding="utf-8")
    assert "route_automatic_block_recommendation" in dreaming_source

    from aletheia.analytics import guard_effectiveness, guard_promotion, trade_attribution
    from aletheia.knowledge.guards import TradingGuard
    from aletheia.learning import control
    from aletheia.agent_runtime import scheduler
    monkeypatch.setattr(scheduler, "load_config", lambda: SimpleNamespace(runtime=SimpleNamespace(paused=False)))
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
    scheduler._run_guard_promotion()


@spec_claim(SpecClaim(
    "LE-B19c", "learning_evolution", "§9.7 nomination publication", "L2b",
    "A failed first hypothesis publication archives its newly-created candidate "
    "and returns a fail-closed result, so no active orphan enters review.",
    status="implemented",
))
async def test_automatic_block_recommendation_compensates_nomination_failure(monkeypatch):
    from aletheia.knowledge import manager
    from aletheia.learning import guard_routing, hypotheses

    monkeypatch.setattr(
        guard_routing,
        "create_guard_candidate",
        lambda **_: SimpleNamespace(id="candidate-item"),
    )
    archived = []
    monkeypatch.setattr(manager, "archive_item", archived.append)

    def fail_nomination(**_):
        raise RuntimeError("temporary nomination failure")

    monkeypatch.setattr(hypotheses, "nominate_learning_hypothesis", fail_nomination)

    result = await guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={
            "factor_keys": ["momentum:BTCUSDT:long:medium_confidence:not_late"],
            "scanner_families": ["momentum"],
        },
        source_id="case-retry",
        research_evidence=_ready_research("case-retry"),
    )
    assert result == {
        "action": "guard_shadow_unavailable",
        "reason": "hypothesis_nomination_failed",
    }
    assert archived == ["candidate-item"]


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

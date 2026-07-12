"""Causal attribution and semantic routing contracts for Learning consumers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "LE-B15", "learning_evolution", "§9.4 causal reflection", "L2b",
    "Reflection resolves outcomes only through proposal/opportunity causal ids; symbol-only movement "
    "or an unrelated same-symbol position is never presented as the decision outcome.",
    status="implemented",
))
def test_reflection_rejects_symbol_only_outcome_inference(monkeypatch):
    from aletheia.agent_shell import reflection
    from aletheia.storage.sqlite import get_db

    db = get_db()
    was_in_tx = db.in_transaction
    db.execute(
        "INSERT INTO positions (id,symbol,market_type,direction,quantity,entry_price,"
        "execution_mode,status,source,opened_at,realized_pnl,closed_at,close_reason,"
        "close_category,exit_price_source,exit_fill_verified,outcome_eligible,"
        "outcome_exclusion_reason,outcome_semantics_version) "
        "VALUES ('unrelated','BTC/USDT','perp','long',1,100,'paper_internal','closed',"
        "'native','2026-07-12T08:00:00+00:00',10,'2026-07-12T09:00:00+00:00',"
        "'take_profit','strategy','local_fill',1,1,'eligible','oi-v1')"
    )
    if not was_in_tx:
        db.commit()

    assert "unavailable" in reflection._compute_outcome(
        "BTC/USDT", None, "2026-07-12T08:00:00+00:00"
    ).lower()
    from aletheia.storage.models import TradeProposal
    from aletheia.storage.repositories import insert_trade_proposal
    insert_trade_proposal(TradeProposal(
        id="proposal-linked", symbol="BTC/USDT", market_type="perp",
        direction="long", quantity=1, price=100,
    ))
    was_in_tx = db.in_transaction
    db.execute("UPDATE positions SET proposal_id='proposal-linked' WHERE id='unrelated'")
    if not was_in_tx:
        db.commit()
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
    reflection.record_decision({
        "action": "skip", "symbol": "BTC/USDT", "direction": "long",
        "opportunity_id": "opp-1", "signal_source": "market_observer",
        "proposal_id": "proposal-1",
        "learning_manifest": manifest,
    })
    row = get_db().execute(
        "SELECT opportunity_id, proposal_id, learning_manifest_json "
        "FROM pi_decisions ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    stored = json.loads(row["learning_manifest_json"])
    assert row["opportunity_id"] == "opp-1"
    assert row["proposal_id"] == "proposal-1"
    assert stored["bundle_id"] == manifest["bundle_id"]
    assert len(stored["components"]) == 6
    observer = build_observer_learning_manifest("memo+cases", "symbol guard")
    assert set(observer["components"]) == {"advisory_memory", "guard_advisory"}
    assert observer["attribution_unit"] == "observer_advisory_bundle"


@spec_claim(SpecClaim(
    "LE-B19", "learning_evolution", "§9.7 automatic block routing", "L2b",
    "Automatic block recommendations become non-blocking guard shadow candidates and are not "
    "coerced into category=risk lessons.",
    status="implemented",
))
def test_automatic_block_recommendation_routes_to_guard_shadow(monkeypatch):
    from aletheia.learning import guard_routing

    captured = {}

    def fake_create(*, title, content, metadata):
        captured.update(metadata)
        return SimpleNamespace(id="candidate-item")

    monkeypatch.setattr(guard_routing, "create_guard_candidate", fake_create)
    routed = guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={"case_count": 12, "outcome_counts": {"loss": 9}},
        source_id="case-1",
    )

    assert routed["action"] == "guard_shadow_candidate"
    assert captured["status"] == "candidate"
    assert captured["enforcement"] == "advisory"
    assert captured["shadow_only"] is True
    assert captured["block_candidate"] is False
    assert captured["score_penalty"] == 0
    assert "shadow-only" in captured["tags"]
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
    assert gated["layer3"]["serenity_opportunities"] == [{"id": "s"}]

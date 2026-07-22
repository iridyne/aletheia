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
    row = get_db().execute(
        "SELECT opportunity_id, proposal_id, learning_manifest_json "
        "FROM pi_decisions ORDER BY created_at DESC LIMIT 1"
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
    assert captured["proposed_block_candidate"] is True
    assert captured["proposed_score_penalty"] == 0
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


def test_guard_shadow_block_scanner_scope_includes_symbol_from_factor_keys(monkeypatch):
    """block_scanner shadow derives symbol from evidence factor_keys — not globally scoped."""
    from aletheia.learning import guard_routing

    captured = {}

    def fake_create(*, title, content, metadata):
        captured.update(metadata)
        return SimpleNamespace(id="g-1")

    monkeypatch.setattr(guard_routing, "create_guard_candidate", fake_create)
    routed = guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={
            "factor_keys": ["momentum:BTC/USDT:long:high:late", "momentum:ETH/USDT:short:high:late"],
            "scanner_families": ["momentum"],
        },
        source_id="src-1",
    )

    assert routed["action"] == "guard_shadow_candidate"
    at = captured["applies_to"]
    assert at["scanner_family"] == ["momentum"]
    assert "BTC/USDT" in at["symbol"]
    assert "ETH/USDT" in at["symbol"]
    assert "asset_class" in at


def test_guard_shadow_block_symbol_scope_includes_scanner_from_factor_keys(monkeypatch):
    """block_symbol shadow derives scanner_family from evidence factor_keys."""
    from aletheia.learning import guard_routing

    captured = {}

    def fake_create(*, title, content, metadata):
        captured.update(metadata)
        return SimpleNamespace(id="g-2")

    monkeypatch.setattr(guard_routing, "create_guard_candidate", fake_create)
    routed = guard_routing.route_automatic_block_recommendation(
        {"action": "block_symbol", "symbol": "BTC/USDT"},
        evidence={
            "factor_keys": ["momentum:BTC/USDT:long:high:late"],
            "scanner_families": ["momentum"],
        },
        source_id="src-2",
    )

    assert routed["action"] == "guard_shadow_candidate"
    at = captured["applies_to"]
    assert at["symbol"] == ["BTC/USDT"]
    assert at["scanner_family"] == ["momentum"]


def test_guard_shadow_block_direction_scope_from_factor_keys(monkeypatch):
    """block_direction shadow derives both symbol and scanner_family from factor_keys."""
    from aletheia.learning import guard_routing

    captured = {}

    def fake_create(*, title, content, metadata):
        captured.update(metadata)
        return SimpleNamespace(id="g-3")

    monkeypatch.setattr(guard_routing, "create_guard_candidate", fake_create)
    routed = guard_routing.route_automatic_block_recommendation(
        {"action": "block_direction", "direction": "long"},
        evidence={
            "factor_keys": ["momentum:BTC/USDT:long:high:late"],
            "scanner_families": ["momentum"],
        },
        source_id="src-3",
    )

    assert routed["action"] == "guard_shadow_candidate"
    at = captured["applies_to"]
    assert at["direction"] == ["long"]
    assert at["symbol"] == ["BTC/USDT"]
    assert at["scanner_family"] == ["momentum"]


def test_guard_shadow_block_direction_fails_closed_on_empty_evidence():
    """block_direction with no factor_keys must fail closed rather than produce a global guard."""
    from aletheia.learning import guard_routing

    routed = guard_routing.route_automatic_block_recommendation(
        {"action": "block_direction", "direction": "long"},
        evidence={},
        source_id="src-4",
    )

    assert routed is not None
    assert routed["action"] == "guard_shadow_unavailable"
    assert routed["reason"] == "invalid_block_scope"


def test_guard_shadow_scope_included_in_identity_hash(monkeypatch):
    """Different derived scopes must produce different guard_ids even for the same action+value."""
    from aletheia.learning import guard_routing

    ids = []

    def fake_create(*, title, content, metadata):
        ids.append(metadata["guard_id"])
        return SimpleNamespace(id="g-x")

    monkeypatch.setattr(guard_routing, "create_guard_candidate", fake_create)

    # Same scanner, different symbols → different scope → different guard_id
    guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={"factor_keys": ["momentum:BTC/USDT:long:high:late"]},
        source_id="src-5",
    )
    guard_routing.route_automatic_block_recommendation(
        {"action": "block_scanner", "scanner": "momentum"},
        evidence={"factor_keys": ["momentum:ETH/USDT:long:high:late"]},
        source_id="src-5",
    )

    assert len(ids) == 2
    assert ids[0] != ids[1]


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

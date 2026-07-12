"""Executable inventory for paper-candidate behavior authority."""

from __future__ import annotations

import inspect

from tests.specs.spec_meta import SpecClaim, spec_claim


@spec_claim(SpecClaim(
    "BA-S01", "behavior_authority", "block_paper_candidate inventory", "L2a",
    "Paper candidate blocking has one complete registry entry with disjoint allowed and forbidden sources.",
))
def test_candidate_block_inventory_is_complete_and_consistent():
    from aletheia.trust.behavior_authority import BEHAVIOR_AUTHORITIES

    assert len(BEHAVIOR_AUTHORITIES) == 1
    authority = BEHAVIOR_AUTHORITIES[0]
    assert authority.behavior_id == "block_paper_candidate"
    assert set(authority.allowed_sources).isdisjoint(authority.forbidden_sources)
    assert set(authority.prospective_authority_sources) <= set(authority.allowed_sources)
    assert dict(authority.learning_path_by_source) == {
        "learned_guard": "ranker_learned_guard",
        "factor_filter": "ranker_factor_filter",
    }
    assert set(authority.forbidden_sources) >= {
        "lesson", "historical_auto_guard", "inline_historical_ranker",
    }
    assert authority.fallback == "observe_only"


@spec_claim(SpecClaim(
    "BA-B01", "behavior_authority", "block_paper_candidate decisions", "L2b",
    "Static owner safety guards retain authority; Learning sources require exact prospective authority; forbidden and unknown sources never block.",
))
def test_candidate_block_authority_fails_closed_by_source_and_path():
    from aletheia.trust.behavior_authority import authorize_candidate_block

    assert authorize_candidate_block(source="seed_guard") is True
    assert authorize_candidate_block(source="manual_guard") is True
    assert authorize_candidate_block(source="learned_guard") is False
    assert authorize_candidate_block(
        source="learned_guard",
        prospective_authority=True,
        learning_path_id="ranker_learned_guard",
    ) is True
    assert authorize_candidate_block(
        source="learned_guard",
        prospective_authority=True,
        learning_path_id="ranker_factor_filter",
    ) is False
    assert authorize_candidate_block(
        source="factor_filter",
        prospective_authority=True,
        learning_path_id="ranker_factor_filter",
    ) is True
    for forbidden in (
        "lesson", "historical_auto_guard", "inline_historical_ranker",
        "arbitrary_ranker_condition", "unknown",
    ):
        assert authorize_candidate_block(
            source=forbidden,
            prospective_authority=True,
            learning_path_id="ranker_learned_guard",
        ) is False


@spec_claim(SpecClaim(
    "BA-S02", "behavior_authority", "consumer coverage", "L2a",
    "Every declared paper-candidate behavior consumer calls the central authority inventory.",
))
def test_all_declared_candidate_block_consumers_use_inventory():
    from aletheia.agents import paper_trader_ranker
    from aletheia.knowledge import guards
    from aletheia.trust.behavior_authority import get_behavior_authority

    authority = get_behavior_authority("block_paper_candidate")
    assert authority.consumer_entrypoints == (
        "aletheia.knowledge.guards.evaluate_guards",
        "aletheia.agents.paper_trader_ranker._evaluate_candidate",
    )
    guard_source = inspect.getsource(guards._guard_influence_suppressed)
    ranker_source = inspect.getsource(paper_trader_ranker._evaluate_candidate)
    assert "authorize_candidate_block(" in guard_source
    assert "authorize_candidate_block(" in ranker_source
    assert "ranker_learned_guard" in guard_source
    assert "learning_path_id=\"ranker_factor_filter\"" in ranker_source


@spec_claim(SpecClaim(
    "BA-B02", "behavior_authority", "Guard consumer enforcement", "L2b",
    "Even an authored blocking Guard has no effect when the final behavior inventory denies it.",
))
def test_guard_consumer_cannot_bypass_final_behavior_gate(monkeypatch):
    from aletheia.knowledge.guards import (
        GuardAppliesTo,
        GuardPredicate,
        TradingGuard,
        evaluate_guards,
    )

    monkeypatch.setattr(
        "aletheia.trust.behavior_authority.authorize_candidate_block",
        lambda **kwargs: False,
    )
    guard = TradingGuard(
        guard_id="repeated_losing_factor_v1",
        title="static fixture guard",
        status="active",
        enforcement="blocking",
        block_candidate=True,
        applies_to=GuardAppliesTo(symbol=["BTC/USDT"]),
        predicates=[GuardPredicate(field="scanner_family", op="eq", value="momentum")],
    )
    result = evaluate_guards(
        {"symbol": "BTC/USDT", "scanner_family": "momentum"}, guards=[guard]
    )
    assert result.blocked is False
    assert result.hits[0].block_candidate is False

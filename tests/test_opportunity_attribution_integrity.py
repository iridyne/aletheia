"""Focused regressions for Opportunity T0 and post-trade attribution integrity."""

from __future__ import annotations


import pytest


def _proposal(**overrides):
    proposal = {
        "id": "proposal-exact",
        "opportunity_id": "opportunity-exact",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "agent_rationale": "proposal rationale",
    }
    proposal.update(overrides)
    return proposal


def _decision(**overrides):
    decision = {
        "id": "decision-exact",
        "proposal_id": "proposal-exact",
        "opportunity_id": "opportunity-exact",
        "symbol": "BTC/USDT",
        "direction": "long",
        "market_type": "spot",
        "action": "trade",
        "reasoning": "exact reasoning",
        "bear_case": "exact bear case",
        "key_assumptions": '["assumption"]',
        "evidence_manifest_json": '{"ids":["evidence"]}',
        "learning_manifest_json": '{"ids":["learning"]}',
    }
    decision.update(overrides)
    return decision


def test_serenity_trade_selects_namespace_before_uuid_lookup(monkeypatch):
    from aletheia.analytics import trade_attribution

    collision_id = "same-uuid"
    proposal = _proposal(opportunity_id=collision_id)
    order = {
        "id": "order-exact",
        "proposal_id": proposal["id"],
        "risk_decision_id": None,
    }
    market_opportunity = {
        "id": collision_id,
        "source_agent": "market_observer",
        "scanner_family": "momentum",
        "confidence_score": 10,
        "status": "reviewed",
        "universe_member_id": "wrong-member",
    }
    serenity_opportunity = {
        "id": collision_id,
        "confidence_score": 88,
        "status": "active",
    }
    monkeypatch.setattr(
        trade_attribution.repo,
        "get_trade_proposal",
        lambda proposal_id: proposal if proposal_id == proposal["id"] else None,
    )
    monkeypatch.setattr(
        trade_attribution.repo,
        "get_order",
        lambda order_id: order if order_id == order["id"] else None,
    )
    monkeypatch.setattr(
        trade_attribution.repo, "get_opportunity", lambda _opportunity_id: market_opportunity
    )
    monkeypatch.setattr(
        trade_attribution.repo,
        "get_serenity_opportunity",
        lambda _opportunity_id: serenity_opportunity,
    )
    monkeypatch.setattr(trade_attribution.repo, "get_strategy", lambda _strategy_id: None)
    monkeypatch.setattr(
        trade_attribution.repo,
        "get_pi_decision_for_proposal",
        lambda _proposal_id: _decision(opportunity_id=collision_id),
    )

    row = trade_attribution._build_row({
        "id": "position-serenity",
        "proposal_id": proposal["id"],
        "order_id": order["id"],
        "opportunity_id": collision_id,
        "trade_school": "serenity",
        "symbol": "BTC/USDT",
        "market_type": "spot",
        "direction": "long",
        "entry_price": 100,
        "current_price": 105,
        "quantity": 1,
        "realized_pnl": 5,
    })

    assert row is not None
    assert row["source_agent"] == "serenity_research"
    assert row["scanner_family"] == "serenity"
    assert row["confidence_score"] == 88
    assert row["universe_member_id"] is None

    from extensions.learning.trade_case_builder import _build_case_from_row

    case = _build_case_from_row(row)
    assert case["scanner_family"] == "serenity"
    assert case["has_full_chain"] is True


def test_unique_final_decision_with_complete_identity_is_available(monkeypatch):
    from aletheia.analytics import trade_attribution

    monkeypatch.setattr(
        trade_attribution.repo,
        "get_pi_decision_for_proposal",
        lambda _proposal_id: _decision(),
    )

    context = trade_attribution.proposal_decision_context(
        "proposal-exact", proposal=_proposal()
    )

    assert context["decision_context_status"] == "available"
    assert context["pi_decision_reasoning"] == "exact reasoning"
    assert context["decision_evidence_manifest"] == {"ids": ["evidence"]}


@pytest.mark.parametrize(
    ("field", "conflicting_value"),
    (
        ("proposal_id", "proposal-other"),
        ("opportunity_id", "opportunity-other"),
        ("symbol", "ETH/USDT"),
        ("direction", "short"),
        ("market_type", "perp"),
    ),
)
def test_unique_final_decision_conflicting_identity_is_typed(
    monkeypatch, field, conflicting_value
):
    from aletheia.analytics import trade_attribution

    monkeypatch.setattr(
        trade_attribution.repo,
        "get_pi_decision_for_proposal",
        lambda _proposal_id: _decision(**{field: conflicting_value}),
    )

    context = trade_attribution.proposal_decision_context(
        "proposal-exact", proposal=_proposal()
    )

    assert context["decision_context_status"] == "conflicting_integrity_error"
    assert context["pi_decision_reasoning"] == "conflicting_integrity_error"
    assert context["decision_evidence_manifest"] == "conflicting_integrity_error"
    assert context["proposal_agent_rationale"] == "proposal rationale"


@pytest.mark.parametrize(
    "field",
    ("proposal_id", "opportunity_id", "symbol", "direction", "market_type"),
)
def test_unique_legacy_decision_missing_identity_is_not_available(monkeypatch, field):
    from aletheia.analytics import trade_attribution

    monkeypatch.setattr(
        trade_attribution.repo,
        "get_pi_decision_for_proposal",
        lambda _proposal_id: _decision(**{field: None}),
    )

    context = trade_attribution.proposal_decision_context(
        "proposal-exact", proposal=_proposal()
    )

    assert context["decision_context_status"] == "not recorded"
    assert context["pi_decision_reasoning"] == "not recorded"
    assert context["decision_context_status"] != "available"


def test_case_preserves_typed_conflicting_integrity_error():
    from extensions.learning.trade_case_builder import (
        _build_case_from_row,
        _render_case_body,
    )

    case = _build_case_from_row({
        "proposal_id": "proposal-exact",
        "opportunity_id": "opportunity-exact",
        "realized_pnl_pct": 1,
        "decision_context_status": "conflicting_integrity_error",
        "pi_decision_reasoning": "conflicting_integrity_error",
    })

    assert case["decision_context_status"] == "conflicting_integrity_error"
    assert "Context status: conflicting_integrity_error" in _render_case_body(case)

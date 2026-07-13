"""Fail-closed Outcome attribution for positions assembled from entry legs."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.specs.spec_meta import SpecClaim, spec_claim


def _insert_multileg_position(*, position_id: str, mixed_proposals: bool) -> dict:
    from aletheia.storage import repositories as repo
    from aletheia.storage.models import (
        Opportunity,
        Order,
        Position,
        RiskDecision,
        TradeProposal,
    )

    opportunity = Opportunity(
        id=f"opportunity-{position_id}",
        symbol="BTC/USDT",
        market="spot",
        direction_bias="long",
        source_agent="spec",
        scanner_family="trend",
    )
    repo.insert_opportunity(opportunity)
    primary = TradeProposal(
        id=f"proposal-primary-{position_id}",
        opportunity_id=opportunity.id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="executed",
    )
    repo.insert_trade_proposal(primary)
    secondary = primary
    if mixed_proposals:
        secondary = TradeProposal(
            id=f"proposal-secondary-{position_id}",
            opportunity_id=opportunity.id,
            symbol=opportunity.symbol,
            market_type="spot",
            direction="long",
            quantity=1.0,
            price=100.0,
            execution_mode="paper_internal",
            status="executed",
        )
        repo.insert_trade_proposal(secondary)

    primary_decision = RiskDecision(
        id=f"risk-primary-{position_id}",
        proposal_id=primary.id,
        approved=True,
        allowed_execution_mode=primary.execution_mode,
        proposal_execution_mode=primary.execution_mode,
        decision_role="authoritative",
        evaluated_quantity=primary.quantity,
        proposal_commit_hash=primary.commit_hash,
    )
    repo.insert_risk_decision(primary_decision)
    secondary_decision = primary_decision
    if mixed_proposals:
        secondary_decision = RiskDecision(
            id=f"risk-secondary-{position_id}",
            proposal_id=secondary.id,
            approved=True,
            allowed_execution_mode=secondary.execution_mode,
            proposal_execution_mode=secondary.execution_mode,
            decision_role="authoritative",
            evaluated_quantity=secondary.quantity,
            proposal_commit_hash=secondary.commit_hash,
        )
        repo.insert_risk_decision(secondary_decision)

    primary_order = Order(
        id=f"order-primary-{position_id}",
        proposal_id=primary.id,
        risk_decision_id=primary_decision.id,
        position_id=position_id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="filled",
    )
    secondary_order = Order(
        id=f"order-secondary-{position_id}",
        proposal_id=secondary.id,
        risk_decision_id=secondary_decision.id,
        position_id=position_id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        order_type="market",
        quantity=1.0,
        price=100.0,
        execution_mode="paper_internal",
        status="filled",
    )
    repo.insert_order(primary_order)
    repo.insert_order(secondary_order)

    position = Position(
        id=position_id,
        symbol=opportunity.symbol,
        market_type="spot",
        direction="long",
        quantity=2.0,
        entry_price=100.0,
        current_price=110.0,
        entry_fee=0.0,
        exit_fee=0.0,
        funding_cost=0.0,
        funding_source="not_applicable",
        realized_pnl=20.0,
        execution_mode="paper_internal",
        opportunity_id=opportunity.id,
        proposal_id=primary.id,
        order_id=primary_order.id,
        status="closed",
        close_reason="take_profit",
        closed_at=datetime.now(timezone.utc).isoformat(),
        close_category="strategy",
        entry_price_source="local_fill",
        entry_fill_verified=True,
        exit_price_source="local_fill",
        exit_fill_verified=True,
        outcome_eligible=True,
        outcome_exclusion_reason="eligible",
        outcome_semantics_version="oi-v1",
    )
    repo.insert_position(position)
    return position.model_dump()


@spec_claim(
    SpecClaim(
        "PL-OF-ML-01",
        "pipeline_outcome_feedback",
        "multileg relational attribution",
        "L2c",
        "Every entry Order merged into one Position belongs to the same Proposal and "
        "Opportunity before the realized Outcome may enter attribution or Learning.",
        status="implemented",
    )
)
def test_mixed_proposal_entry_leg_fails_relational_attribution():
    from aletheia.analytics.outcome_integrity import (
        attribution_chain_complete,
        outcome_write_eligibility,
    )

    row = _insert_multileg_position(
        position_id="position-mixed-attribution",
        mixed_proposals=True,
    )

    assert attribution_chain_complete(row) is False
    assert outcome_write_eligibility(
        row, exit_fill_verified=True
    ) == (False, "missing_attribution")


@spec_claim(
    SpecClaim(
        "PL-OF-ML-02",
        "pipeline_outcome_feedback",
        "direct assessment attribution guard",
        "L2c",
        "A caller that does not inject a relational-attribution marker cannot bypass "
        "the database-backed entry-chain check.",
        status="implemented",
    )
)
def test_direct_assessment_without_marker_rechecks_all_entry_legs():
    from aletheia.analytics.outcome_integrity import assess_position_closure

    row = _insert_multileg_position(
        position_id="position-direct-attribution",
        mixed_proposals=True,
    )
    row.pop("_attribution_complete", None)

    assessment = assess_position_closure(row)

    assert assessment.eligible is False
    assert assessment.reason == "missing_attribution"


def test_raw_outcome_read_marks_mixed_entry_legs_incomplete():
    from aletheia.analytics.outcome_integrity import raw_account_events

    row = _insert_multileg_position(
        position_id="position-read-attribution",
        mixed_proposals=True,
    )

    [stored] = raw_account_events(execution_mode=row["execution_mode"])

    assert stored["_attribution_complete"] == 0


def test_same_proposal_entry_legs_remain_eligible_without_injected_marker():
    from aletheia.analytics.outcome_integrity import (
        assess_position_closure,
        attribution_chain_complete,
    )

    row = _insert_multileg_position(
        position_id="position-consistent-attribution",
        mixed_proposals=False,
    )
    row.pop("_attribution_complete", None)

    assert attribution_chain_complete(row) is True
    assessment = assess_position_closure(row)
    assert assessment.eligible is True
    assert assessment.reason == "eligible"

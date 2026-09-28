"""Test helper for exercising broker entry points through real native authority."""

from aletheia.storage import repositories as repo
from aletheia.storage.models import RiskDecision, TradeProposal


def seed_order_authority(
    *,
    proposal_id: str,
    risk_decision_id: str,
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str,
    quantity: float,
    price: float,
    execution_mode: str,
    strategy_id: str | None = None,
    leverage: int | None = None,
    stop_price: float | None = None,
    take_profit: float | None = None,
    agent_rationale: str | None = None,
    attribution_snapshot: str | None = None,
    trade_school: str = "short_term",
) -> tuple[TradeProposal, RiskDecision]:
    proposal = TradeProposal(
        id=proposal_id,
        strategy_id=strategy_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        agent_rationale=agent_rationale,
        attribution_snapshot=attribution_snapshot,
        trade_school=trade_school,
        status="approved",
    )
    repo.insert_trade_proposal(proposal)
    decision = RiskDecision(
        id=risk_decision_id,
        proposal_id=proposal.id,
        approved=True,
        allowed_execution_mode=execution_mode,
        proposal_execution_mode=execution_mode,
        decision_role="authoritative",
        evaluated_quantity=quantity,
        proposal_commit_hash=proposal.commit_hash,
    )
    repo.insert_risk_decision(decision)
    return proposal, decision

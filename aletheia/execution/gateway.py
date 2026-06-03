"""Execution Gateway — all orders flow through here. No direct exchange access."""

from aletheia.config.loader import load_config
from aletheia.risk.engine import evaluate_proposal
from aletheia.execution.paper_broker import execute_paper_order
from aletheia.storage.models import TradeProposal
from aletheia.storage.repositories import insert_trade_proposal, update_trade_proposal
from aletheia.files.jsonl import write_audit


def submit_proposal(
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str = "market",
    quantity: float = 0.0,
    price: float = 0.0,
    execution_mode: str = "paper_internal",
    strategy_id: str | None = None,
    agent_rationale: str | None = None,
) -> dict:
    """Submit a trade proposal through the full execution pipeline.

    Flow: Proposal -> Risk Engine -> Permission -> Live State -> Execution -> Fill -> Audit
    """
    cfg = load_config()

    # Create proposal
    proposal = TradeProposal(
        strategy_id=strategy_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        execution_mode=execution_mode,
        agent_rationale=agent_rationale,
    )
    insert_trade_proposal(proposal)

    # Risk check
    risk_result = evaluate_proposal(proposal)

    if not risk_result.approved:
        update_trade_proposal(proposal.id, status="rejected")
        write_audit({
            "actor": "system",
            "source": "execution_gateway",
            "event_type": "proposal_rejected",
            "entity_type": "trade_proposal",
            "entity_id": proposal.id,
            "message": risk_result.message,
        })
        return {
            "status": "rejected",
            "proposal_id": proposal.id,
            "risk_violations": risk_result.violations,
            "message": risk_result.message,
        }

    # Execute based on allowed mode
    allowed_mode = risk_result.allowed_execution_mode

    if allowed_mode in ("paper_internal", "bitget_demo"):
        result = execute_paper_order(
            proposal_id=proposal.id,
            risk_decision_id=risk_result.decision_id,
            symbol=symbol,
            market_type=market_type,
            direction=direction,
            order_type=order_type,
            quantity=quantity,
            price=price,
            execution_mode=allowed_mode,
            strategy_id=strategy_id,
        )
        update_trade_proposal(proposal.id, status="executed")
        write_audit({
            "actor": "system",
            "source": "execution_gateway",
            "event_type": "paper_order_executed",
            "entity_type": "order",
            "entity_id": result["order_id"],
            "message": f"Paper order filled: {symbol} {direction} {quantity}@{price}",
        })
        return {"status": "executed", "proposal_id": proposal.id, **result}

    elif allowed_mode == "bitget_live_limited":
        # Defense in depth: risk engine already checked live_disabled, but re-check here.
        if cfg.live.state == "live_disabled":
            update_trade_proposal(proposal.id, status="rejected")
            return {"status": "rejected", "proposal_id": proposal.id, "message": "Live trading is disabled"}
        try:
            from aletheia.execution.bitget_broker import execute_live_order
            result = execute_live_order(
                proposal_id=proposal.id,
                risk_decision_id=risk_result.decision_id,
                symbol=symbol,
                market_type=market_type,
                direction=direction,
                order_type=order_type,
                quantity=quantity,
                price=price,
                strategy_id=strategy_id,
            )
        except Exception as e:
            update_trade_proposal(proposal.id, status="error")
            write_audit({
                "actor": "system", "source": "execution_gateway",
                "event_type": "live_order_error", "entity_type": "trade_proposal",
                "entity_id": proposal.id, "message": f"Live order failed: {e}",
            })
            return {"status": "error", "proposal_id": proposal.id, "message": str(e)}

        update_trade_proposal(proposal.id, status="executed")
        write_audit({
            "actor": "system", "source": "execution_gateway",
            "event_type": "live_order_executed", "entity_type": "order",
            "entity_id": result["order_id"],
            "message": f"Live order placed: {symbol} {direction} {quantity}@{price} (ex:{result['exchange_order_id']})",
        })
        return {"status": "executed", "proposal_id": proposal.id, **result}

    return {"status": "unknown_mode", "proposal_id": proposal.id}

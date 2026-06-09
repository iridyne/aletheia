"""Execution Gateway — all orders flow through here. No direct exchange access."""

from aletheia.config.loader import load_config
from aletheia.risk.engine import evaluate_proposal
from aletheia.execution.paper_broker import execute_paper_order
from aletheia.storage.models import TradeProposal
from aletheia.storage.repositories import (
    get_trade_proposal,
    insert_trade_proposal,
    transaction,
    update_trade_proposal,
)
from aletheia.files.jsonl import write_audit


def submit_proposal(
    symbol: str,
    market_type: str,
    direction: str,
    order_type: str = "market",
    quantity: float = 0.0,
    price: float = 0.0,
    leverage: int | None = None,
    stop_price: float | None = None,
    execution_mode: str = "paper_internal",
    strategy_id: str | None = None,
    opportunity_id: str | None = None,
    agent_rationale: str | None = None,
) -> dict:
    """Submit a trade proposal through the full execution pipeline.

    Flow: Proposal -> Risk Engine -> Permission -> Live State -> Execution -> Fill -> Audit
    """
    proposal = TradeProposal(
        strategy_id=strategy_id,
        opportunity_id=opportunity_id,
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        leverage=leverage,
        stop_price=stop_price,
        execution_mode=execution_mode,
        agent_rationale=agent_rationale,
    )
    insert_trade_proposal(proposal)
    return _execute_proposal(proposal)


def execute_existing_proposal(proposal_id: str, execution_mode: str = "paper_internal") -> dict:
    """Execute a stored proposal without creating a duplicate proposal row."""
    row = get_trade_proposal(proposal_id)
    if row is None:
        return {"status": "not_found", "proposal_id": proposal_id, "message": "Proposal not found"}

    data = dict(row)
    data["execution_mode"] = execution_mode
    proposal = TradeProposal.model_validate(data)
    if proposal.status == "executed":
        return {
            "status": "already_executed",
            "proposal_id": proposal.id,
            "message": "Proposal has already been executed; no new order was created.",
        }
    if proposal.status in {"error", "rejected"}:
        return {
            "status": proposal.status,
            "proposal_id": proposal.id,
            "message": f"Proposal is already marked as {proposal.status}.",
        }
    update_trade_proposal(proposal.id, execution_mode=execution_mode)
    return _execute_proposal(proposal)


def _execute_proposal(proposal: TradeProposal) -> dict:
    """Run risk and execution for an already-created proposal."""
    cfg = load_config()

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
            "before": {"status": proposal.status},
            "after": {"status": "rejected"},
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
    try:
        if allowed_mode == "paper_internal":
            with transaction():
                result = execute_paper_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=proposal.symbol,
                    market_type=proposal.market_type,
                    direction=proposal.direction,
                    order_type=proposal.order_type,
                    quantity=proposal.quantity or 0.0,
                    price=proposal.price or 0.0,
                    execution_mode=allowed_mode,
                    strategy_id=proposal.strategy_id,
                    stop_price=proposal.stop_price,
                )
                update_trade_proposal(proposal.id, status="executed")
            write_audit({
                "actor": "system",
                "source": "execution_gateway",
                "event_type": "paper_order_executed",
                "entity_type": "order",
                "entity_id": result["order_id"],
                "before": {"status": proposal.status},
                "after": {"status": "executed"},
                "message": (
                    f"Paper order filled: {proposal.symbol} {proposal.direction} "
                    f"{proposal.quantity or 0.0}@{proposal.price or 0.0}"
                ),
            })
            return {"status": "executed", "proposal_id": proposal.id, **result}

        elif allowed_mode == "bitget_demo":
            # Real Bitget demo/simulated order — exchange-side record, no live guard
            # (live_disabled gates real funds only; demo is risk-free). On any failure
            # we mark the proposal error and audit; we NEVER fall back to a fabricated
            # local fill (honesty: a demo trade either hit the demo exchange or it
            # didn't).
            try:
                from aletheia.execution.bitget_broker import execute_live_order
                result = execute_live_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=proposal.symbol,
                    market_type=proposal.market_type,
                    direction=proposal.direction,
                    order_type=proposal.order_type,
                    quantity=proposal.quantity or 0.0,
                    price=proposal.price or 0.0,
                    strategy_id=proposal.strategy_id,
                    execution_mode="bitget_demo",
                )
                update_trade_proposal(proposal.id, status="executed")
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "demo_order_executed", "entity_type": "order",
                    "entity_id": result["order_id"],
                    "before": {"status": proposal.status},
                    "after": {"status": "executed"},
                    "message": (
                        f"Demo order placed: {proposal.symbol} {proposal.direction} "
                        f"{proposal.quantity or 0.0}@{result.get('fill_price') or proposal.price or 0.0} "
                        f"(ex:{result['exchange_order_id']})"
                    ),
                })
                return {"status": "executed", "proposal_id": proposal.id, **result}
            except Exception as e:
                exchange_oid = getattr(e, "exchange_order_id", None)
                with transaction():
                    update_trade_proposal(proposal.id, status="error")
                msg = f"Demo order failed: {e}"
                if exchange_oid:
                    msg += f" (exchange_order_id={exchange_oid})"
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "demo_order_error", "entity_type": "trade_proposal",
                    "entity_id": proposal.id, "before": {"status": proposal.status},
                    "after": {"status": "error"}, "message": msg,
                })
                return {"status": "error", "proposal_id": proposal.id, "message": str(e),
                        "exchange_order_id": exchange_oid}

        elif allowed_mode == "bitget_live_limited":
            # Defense in depth: risk engine already checked live_disabled, but re-check here.
            if cfg.live.state == "live_disabled":
                update_trade_proposal(proposal.id, status="rejected")
                write_audit({
                    "actor": "system",
                    "source": "execution_gateway",
                    "event_type": "proposal_rejected",
                    "entity_type": "trade_proposal",
                    "entity_id": proposal.id,
                    "before": {"status": proposal.status},
                    "after": {"status": "rejected"},
                    "message": "Live trading is disabled",
                })
                return {"status": "rejected", "proposal_id": proposal.id, "message": "Live trading is disabled"}
            try:
                from aletheia.execution.bitget_broker import execute_live_order
                result = execute_live_order(
                    proposal_id=proposal.id,
                    risk_decision_id=risk_result.decision_id,
                    symbol=proposal.symbol,
                    market_type=proposal.market_type,
                    direction=proposal.direction,
                    order_type=proposal.order_type,
                    quantity=proposal.quantity or 0.0,
                    price=proposal.price or 0.0,
                    strategy_id=proposal.strategy_id,
                )
                update_trade_proposal(proposal.id, status="executed")
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "live_order_executed", "entity_type": "order",
                    "entity_id": result["order_id"],
                    "before": {"status": proposal.status},
                    "after": {"status": "executed"},
                    "message": (
                        f"Live order placed: {proposal.symbol} {proposal.direction} "
                        f"{proposal.quantity or 0.0}@{proposal.price or 0.0} "
                        f"(ex:{result['exchange_order_id']})"
                    ),
                })
                return {"status": "executed", "proposal_id": proposal.id, **result}

            except Exception as e:
                exchange_oid = getattr(e, "exchange_order_id", None)
                with transaction():
                    update_trade_proposal(proposal.id, status="error")
                msg = f"Live order failed: {e}"
                if exchange_oid:
                    msg += f" (exchange_order_id={exchange_oid})"
                write_audit({
                    "actor": "system", "source": "execution_gateway",
                    "event_type": "live_order_error", "entity_type": "trade_proposal",
                    "entity_id": proposal.id, "before": {"status": proposal.status},
                    "after": {"status": "error"}, "message": msg,
                })
                return {"status": "error", "proposal_id": proposal.id, "message": str(e),
                        "exchange_order_id": exchange_oid}

    except Exception as e:
        with transaction():
            update_trade_proposal(proposal.id, status="error")
        write_audit({
            "actor": "system", "source": "execution_gateway",
            "event_type": "execution_error", "entity_type": "trade_proposal",
            "entity_id": proposal.id, "before": {"status": proposal.status},
            "after": {"status": "error"}, "message": f"Execution failed: {e}",
        })
        return {"status": "error", "proposal_id": proposal.id, "message": str(e)}

    return {"status": "unknown_mode", "proposal_id": proposal.id}

"""Paper-only proposal tool for autonomous agents.

This is the single entry point the paper_trader agent uses to submit a proposal.
It HARD-CODES paper execution and rejects any other mode, so an autonomous agent
can never reach the live path through this wrapper. All order flow still goes
through the Execution Gateway + Risk Engine (no direct exchange access).
"""

from aletheia.execution.gateway import execute_existing_proposal, submit_proposal
from aletheia.storage.models import TradeProposal
from aletheia.storage.repositories import insert_trade_proposal

_PAPER_MODES = {"paper_internal", "bitget_demo", "binance_testnet", "okx_demo"}


def _validate_paper_proposal(proposal: TradeProposal) -> None:
    if proposal.execution_mode not in _PAPER_MODES:
        raise ValueError(
            f"paper proposal submit only accepts paper modes {_PAPER_MODES}, "
            f"got {proposal.execution_mode!r}"
        )
    if proposal.execution_mode == "paper_internal" and proposal.order_type != "market":
        raise ValueError("paper_internal_market_only")
    if proposal.market_type == "spot" and proposal.leverage not in (None, 1):
        raise ValueError("spot leverage must be 1")
    if proposal.leverage is not None and proposal.leverage < 1:
        raise ValueError(f"leverage must be >= 1, got {proposal.leverage}")


def submit_prepared_paper_proposal(proposal: TradeProposal) -> dict:
    """Persist and execute the exact prepared paper Proposal."""
    _validate_paper_proposal(proposal)
    insert_trade_proposal(proposal)
    return execute_existing_proposal(proposal.id, proposal.execution_mode)


def create_paper_proposal(
    *,
    symbol: str,
    market_type: str,
    direction: str,
    quantity: float,
    price: float,
    leverage: int | None = None,
    execution_mode: str = "paper_internal",
    strategy_id: str | None = None,
    opportunity_id: str | None = None,
    stop_price: float | None = None,
    take_profit: float | None = None,
    risk_reward: float | None = None,
    order_type: str = "market",
    agent_rationale: str | None = None,
    trade_school: str = "short_term",
    expected_horizon_hours: float | None = None,
    stop_source: str | None = None,
) -> dict:
    """Submit a PAPER proposal through the gateway. Rejects any non-paper mode.

    Raises ValueError if execution_mode is not a paper/demo mode — defense in depth
    so an autonomous caller cannot smuggle a live order through this path.

    paper_internal is an immediate local simulation, so its only honest entry
    type is market. Exchange demo/testnet callers may still pass limit explicitly.
    """
    proposal = TradeProposal(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        risk_reward=risk_reward,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        opportunity_id=opportunity_id,
        agent_rationale=agent_rationale,
        trade_school=trade_school,
        expected_horizon_hours=expected_horizon_hours,
        stop_source=stop_source,
    )
    _validate_paper_proposal(proposal)
    return submit_proposal(
        symbol=symbol,
        market_type=market_type,
        direction=direction,
        order_type=order_type,
        quantity=quantity,
        price=price,
        leverage=leverage,
        stop_price=stop_price,
        take_profit=take_profit,
        risk_reward=risk_reward,
        execution_mode=execution_mode,
        strategy_id=strategy_id,
        opportunity_id=opportunity_id,
        agent_rationale=agent_rationale,
        trade_school=trade_school,
        expected_horizon_hours=expected_horizon_hours,
        stop_source=stop_source,
    )

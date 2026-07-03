"""Paper-only proposal tool for autonomous agents.

This is the single entry point the paper_trader agent uses to submit a proposal.
It HARD-CODES paper execution and rejects any other mode, so an autonomous agent
can never reach the live path through this wrapper. All order flow still goes
through the Execution Gateway + Risk Engine (no direct exchange access).
"""

from aletheia.execution.gateway import submit_proposal

_PAPER_MODES = {"paper_internal", "bitget_demo", "binance_testnet", "okx_demo"}


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
    order_type: str = "limit",
    agent_rationale: str | None = None,
    trade_school: str = "short_term",
) -> dict:
    """Submit a PAPER proposal through the gateway. Rejects any non-paper mode.

    Raises ValueError if execution_mode is not a paper/demo mode — defense in depth
    so an autonomous caller cannot smuggle a live order through this path.

    order_type defaults to "limit" (2026-07-03, owner discussion #6): a System
    Operator entry rests as a maker at `price` rather than crossing the spread as
    a taker, saving fees. Callers that must guarantee a fill (a market close)
    pass order_type="market" explicitly.
    """
    if execution_mode not in _PAPER_MODES:
        raise ValueError(
            f"create_paper_proposal only accepts paper modes {_PAPER_MODES}, got {execution_mode!r}"
        )
    if leverage is not None and leverage < 1:
        raise ValueError(f"leverage must be >= 1, got {leverage}")
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
    )

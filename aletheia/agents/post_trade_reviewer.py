"""Post-trade Reviewer agent — reviews recent fills and writes a post-trade review."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_post_trade_review
from aletheia.storage.repositories import get_trade_proposal, list_fills, list_orders


@register_agent("post_trade_reviewer")
async def run_post_trade_reviewer(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Review recent fills grouped by strategy and record per-strategy reviews."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_post_trade_review", permission_mode):
        errors.append("create_post_trade_review not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    fills = list_fills(limit=200)
    orders = list_orders(limit=200)
    log_tool(run_id, "get_positions", "observe", True)

    # Group fills by strategy_id
    strategy_fills: dict[str | None, list] = {}
    for fill in fills:
        order_id = fill.get("order_id")
        if not order_id:
            continue
        linked_order = next((o for o in orders if o["id"] == order_id), None)
        if not linked_order:
            continue
        proposal_id = linked_order.get("proposal_id")
        if not proposal_id:
            strategy_fills.setdefault(None, []).append(fill)
            continue
        proposal = get_trade_proposal(proposal_id)
        sid = proposal.get("strategy_id") if proposal else None
        strategy_fills.setdefault(sid, []).append(fill)

    if not strategy_fills:
        strategy_fills[None] = []

    for strategy_id, strat_fills in strategy_fills.items():
        ctx = f"Strategy: {strategy_id or 'global'}. Fills: {len(strat_fills)}; total orders: {len(orders)}."

        content, err = await llm_complete(
            system="You are a post-trade reviewer. Given recent fills/orders, write a short review: "
                   "execution quality, what worked, what to improve. Under 200 words.",
            user=f"{ctx}\n\nWrite a post-trade review.",
            max_tokens=600,
        )
        log_tool(run_id, "llm_complete", "observe", True)
        if err:
            errors.append(err)
            content = f"Post-trade review (LLM unavailable). Context: {ctx}"

        item = create_post_trade_review(
            "Post-Trade Review",
            content,
            metadata={"source": "post_trade_reviewer"},
            strategy_id=strategy_id,
        )
        log_tool(run_id, "create_post_trade_review", "observe", True, output=item.id)
        outputs.append(f"post_trade_review:{item.id}")

    return {"outputs": outputs, "errors": errors}

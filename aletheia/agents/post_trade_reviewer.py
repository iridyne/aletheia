"""Post-trade Reviewer agent — reviews recent fills and writes a post-trade review."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_research_note
from aletheia.storage.repositories import list_fills, list_orders


@register_agent("post_trade_reviewer")
async def run_post_trade_reviewer(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    """Review recent fills and record a post-trade review note."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_research_note", permission_mode):
        errors.append("create_research_note not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    fills = list_fills(limit=20)
    orders = list_orders(limit=20)
    log_tool(run_id, "get_positions", "read_tool", True)
    ctx = f"Recent fills: {len(fills)}; recent orders: {len(orders)}."

    content, err = await llm_complete(
        system="You are a post-trade reviewer. Given recent fills/orders, write a short review: "
               "execution quality, what worked, what to improve. Under 200 words.",
        user=f"{ctx}\n\nWrite a post-trade review.",
        max_tokens=600,
    )
    log_tool(run_id, "llm_complete", "research_tool", True)
    if err:
        errors.append(err)
        content = f"Post-trade review (LLM unavailable). Context: {ctx}"

    item = create_research_note("Post-Trade Review", content, confidence="medium_confidence",
                                metadata={"source": "post_trade_reviewer"})
    log_tool(run_id, "create_research_note", "research_tool", True, output=item.id)
    outputs.append(f"research_note:{item.id}")

    return {"outputs": outputs, "errors": errors}

"""Strategy Designer agent — turns opportunities/hypotheses into strategy drafts."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.trading.strategy_lifecycle import create_strategy


@register_agent("strategy_designer")
async def run_strategy_designer(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    """Draft a candidate strategy (created in 'draft' lifecycle state)."""
    outputs: list[str] = []
    errors: list[str] = []

    # Designing strategies is a research-class activity (creating hypotheses/drafts).
    if not is_tool_permitted("create_strategy_hypothesis", permission_mode):
        errors.append("create_strategy_hypothesis not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    description, err = await llm_complete(
        system="You are a crypto trading strategy designer. Propose ONE concrete paper-tradable "
               "strategy for BTC/ETH/SOL: name, timeframe, entry rule, exit rule, position sizing idea, "
               "and rationale. Keep it under 250 words. Risk engine will gate execution.",
        user="Design one candidate strategy.",
        max_tokens=800,
    )
    log_tool(run_id, "llm_complete", "research_tool", True)
    if err:
        errors.append(err)
        description = "Strategy draft (LLM unavailable)."

    name = "Designed Strategy"
    strategy = create_strategy(name=name, description=description[:1000] if description else name)
    log_tool(run_id, "create_strategy_hypothesis", "research_tool", True, output=strategy.id)
    outputs.append(f"strategy:{strategy.id}")

    return {"outputs": outputs, "errors": errors}

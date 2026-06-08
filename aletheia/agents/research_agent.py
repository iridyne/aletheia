"""Research Agent — creates research notes and strategy hypotheses."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_research_note


@register_agent("research_agent")
async def run_research_agent(permission_mode: str = "Observe", run_id: str | None = None, **kwargs) -> dict:
    """Generate a research note using the model provider (graceful if unavailable)."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_research_note", permission_mode):
        errors.append("create_research_note not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    title = "Market Structure Analysis"
    content, err = await llm_complete(
        system="You are a trading research analyst. Write a concise research note (markdown) on "
               "current crypto market structure for BTC/ETH/SOL: key concepts, a trading hypothesis, "
               "counterarguments, and a confidence level. Keep it under 300 words.",
        user="Write a research note on current crypto market structure.",
        max_tokens=900,
    )
    log_tool(run_id, "llm_complete", "observe", True)
    if err:
        errors.append(err)
        content = ("Research note (LLM unavailable). Configure a model provider in Settings to "
                   "enable real analysis.")

    item = create_research_note(title, content, confidence="speculative",
                                metadata={"source": "research_agent"})
    log_tool(run_id, "create_research_note", "observe", True, output=item.id)
    outputs.append(f"research_note:{item.id}")

    return {"outputs": outputs, "errors": errors}

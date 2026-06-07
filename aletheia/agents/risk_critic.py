"""Risk Critic agent — reviews recent proposals/strategies and writes risk lessons."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_research_note
from aletheia.storage.repositories import list_trade_proposals, list_risk_decisions


@register_agent("risk_critic")
async def run_risk_critic(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    """Critique recent risk decisions and record a risk-lesson note."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("create_research_note", permission_mode):
        errors.append("create_research_note not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    proposals = list_trade_proposals(limit=20)
    decisions = list_risk_decisions(limit=20)
    log_tool(run_id, "get_risk_boundaries", "read_tool", True)
    ctx = (f"Recent proposals: {len(proposals)}; recent risk decisions: {len(decisions)}. "
           f"Rejections: {sum(1 for d in decisions if not d.get('approved'))}.")

    content, err = await llm_complete(
        system="You are a risk critic. Given recent proposal/risk-decision stats, identify risk "
               "patterns or lessons and recommend guardrails. You cannot change hard limits — only advise. "
               "Keep under 200 words.",
        user=f"{ctx}\n\nWrite a risk lesson note.",
        max_tokens=600,
    )
    log_tool(run_id, "llm_complete", "research_tool", True)
    if err:
        errors.append(err)
        content = f"Risk critique (LLM unavailable). Context: {ctx}"

    item = create_research_note("Risk Lesson", content, confidence="medium_confidence",
                                metadata={"source": "risk_critic", "subtype": "risk_lesson"})
    log_tool(run_id, "create_research_note", "research_tool", True, output=item.id)
    outputs.append(f"research_note:{item.id}")

    return {"outputs": outputs, "errors": errors}

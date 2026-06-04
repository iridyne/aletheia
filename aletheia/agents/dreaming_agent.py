"""Dreaming Agent — consolidates daily activity into learning summaries."""

from aletheia.agents import register_agent
from aletheia.agents.helpers import llm_complete, log_tool
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.knowledge.manager import create_daily_review, create_dreaming_log
from aletheia.storage.repositories import list_agent_runs


@register_agent("dreaming_agent")
async def run_dreaming_agent(permission_mode: str = "Research", run_id: str | None = None, **kwargs) -> dict:
    """Consolidate recent activity into a dreaming/learning log (guide §17.2)."""
    outputs: list[str] = []
    errors: list[str] = []

    if not is_tool_permitted("write_dreaming_log", permission_mode):
        errors.append("write_dreaming_log not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    recent_runs = list_agent_runs(limit=20)
    log_tool(run_id, "get_recent_agent_runs", "read_tool", True)
    summary_lines = [f"- {r['agent_name']}: {r['status']} ({r.get('started_at', '')[:16]})" for r in recent_runs]
    activity = "\n".join(summary_lines) if summary_lines else "No recent agent activity."

    content, err = await llm_complete(
        system="You are the system's nightly consolidation process. Given recent agent activity, "
               "produce a short markdown 'dreaming log': (1) what happened, (2) one insight or pattern, "
               "(3) one suggested focus for tomorrow. Keep it brief.",
        user=f"Recent agent activity:\n{activity}\n\nWrite the dreaming log.",
        max_tokens=700,
    )
    log_tool(run_id, "llm_complete", "research_tool", True)
    if err:
        errors.append(err)
        content = f"## Daily Consolidation\n\nRecent activity:\n{activity}\n\n(LLM unavailable for synthesis.)"

    item = create_dreaming_log("Daily Consolidation", content)
    log_tool(run_id, "write_dreaming_log", "research_tool", True, output=item.id)
    review = create_daily_review("Daily Review", content)
    log_tool(run_id, "write_daily_review", "research_tool", True, output=review.id)
    outputs.append(f"dreaming_log:{item.id}")
    outputs.append(f"daily_review:{review.id}")

    return {"outputs": outputs, "errors": errors}

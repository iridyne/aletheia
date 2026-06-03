"""Dreaming Agent — consolidates daily activity into learning summaries."""

from aletheia.agents import register_agent
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import KnowledgeItem
from aletheia.storage.repositories import insert_knowledge_item, list_agent_runs
from aletheia.files.markdown import write_dreaming_log


@register_agent("dreaming_agent")
async def run_dreaming_agent(permission_mode: str = "Research", **kwargs) -> dict:
    """Consolidate recent activity into dreaming/learning logs."""
    outputs = []
    errors = []

    if not is_tool_permitted("write_dreaming_log", permission_mode):
        errors.append("write_dreaming_log not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    # Summarize recent agent runs
    recent_runs = list_agent_runs(limit=10)
    summary_lines = [f"- {r['agent_name']}: {r['status']}" for r in recent_runs]
    content = "## Daily Consolidation\n\n"
    if summary_lines:
        content += "Recent agent activity:\n" + "\n".join(summary_lines)
    else:
        content += "No recent agent activity to consolidate."

    content += "\n\n## Insights\n\nPlaceholder — will use model for real synthesis."

    path = write_dreaming_log("Daily Consolidation", content)

    item = KnowledgeItem(
        item_type="dreaming_log",
        title="Daily Consolidation",
        file_path=str(path),
        confidence="medium_confidence",
    )
    insert_knowledge_item(item)
    outputs.append(f"dreaming_log:{item.id}")

    return {"outputs": outputs, "errors": errors}

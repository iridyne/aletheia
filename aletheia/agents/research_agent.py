"""Research Agent — creates research notes and strategy hypotheses."""

from aletheia.agents import register_agent
from aletheia.agent_runtime.permissions import is_tool_permitted
from aletheia.storage.models import KnowledgeItem
from aletheia.storage.repositories import insert_knowledge_item
from aletheia.files.markdown import write_research_note


@register_agent("research_agent")
async def run_research_agent(permission_mode: str = "Research", **kwargs) -> dict:
    """Generate research notes based on available data."""
    outputs = []
    errors = []

    if not is_tool_permitted("create_research_note", permission_mode):
        errors.append("create_research_note not permitted in current mode")
        return {"outputs": outputs, "errors": errors}

    # Create a research note
    title = "Market Structure Analysis"
    content = "Placeholder research note — will use model provider for real analysis when configured."
    metadata = {"confidence": "speculative", "source": "research_agent"}

    path = write_research_note(title, content, metadata)

    item = KnowledgeItem(
        item_type="research_note",
        title=title,
        file_path=str(path),
        confidence="speculative",
    )
    insert_knowledge_item(item)
    outputs.append(f"research_note:{item.id}")

    return {"outputs": outputs, "errors": errors}

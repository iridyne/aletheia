"""Knowledge Base manager — handles lifecycle of KB items (no hard delete)."""

from aletheia.storage.models import KnowledgeItem
from aletheia.storage.repositories import (
    insert_knowledge_item, update_knowledge_item, list_knowledge_items,
)
from aletheia.files.markdown import write_research_note, write_dreaming_log, write_strategy_doc

VALID_CONFIDENCES = {"high_confidence", "medium_confidence", "speculative", "deprecated"}
VALID_STATUSES = {"active", "archived", "deprecated"}


def create_research_note(title: str, content: str, confidence: str = "medium_confidence", metadata: dict | None = None) -> KnowledgeItem:
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    path = write_research_note(title, content, metadata)
    item = KnowledgeItem(
        item_type="research_note",
        title=title,
        file_path=str(path),
        confidence=confidence,
    )
    insert_knowledge_item(item)
    return item


def create_dreaming_log(title: str, content: str) -> KnowledgeItem:
    path = write_dreaming_log(title, content)
    item = KnowledgeItem(
        item_type="dreaming_log",
        title=title,
        file_path=str(path),
        confidence="medium_confidence",
    )
    insert_knowledge_item(item)
    return item


def create_strategy_memory(strategy_id: str, title: str, content: str) -> KnowledgeItem:
    path = write_strategy_doc(strategy_id, title, content)
    item = KnowledgeItem(
        item_type="strategy_memory",
        title=title,
        file_path=str(path),
        confidence="medium_confidence",
    )
    insert_knowledge_item(item)
    return item


def archive_item(item_id: str) -> None:
    """Archive a KB item — soft removal, not deletion."""
    update_knowledge_item(item_id, status="archived")


def deprecate_item(item_id: str) -> None:
    """Mark a KB item as deprecated — it stays but loses active relevance."""
    update_knowledge_item(item_id, status="deprecated", confidence="deprecated")


def delete_item(item_id: str) -> None:
    """Hard delete is NOT allowed. Raises an error."""
    raise PermissionError("Hard deletion of knowledge items is not allowed. Use archive_item() or deprecate_item() instead.")


def detect_contradictions(items: list[dict]) -> list[dict]:
    """Stub for contradiction detection — returns empty for MVP.

    Future: compare confidence levels and conflicting theses.
    """
    return []

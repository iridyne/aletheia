"""Knowledge Base manager — handles lifecycle of KB items (no hard delete)."""

from aletheia.storage.models import KnowledgeItem
from aletheia.storage.repositories import (
    insert_knowledge_item, update_knowledge_item, list_knowledge_items,
)
from aletheia.files.markdown import (
    write_daily_review,
    write_dreaming_log,
    write_post_trade_review,
    write_research_note,
    write_strategy_doc,
)

VALID_CONFIDENCES = {"high_confidence", "medium_confidence", "speculative", "deprecated"}
VALID_STATUSES = {"active", "archived", "deprecated"}


def _index(item, content: str) -> None:
    """Best-effort full-text index of a knowledge item (experience loop, §6).

    Only high_confidence items become retrievable (the quality gate); others are
    written/audited as before but kept out of decision retrieval. Never raises.
    """
    try:
        if item.confidence != "high_confidence":
            return
        from aletheia.knowledge.retrieval import index_knowledge_item
        index_knowledge_item(item, content)
    except Exception:
        pass


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
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_dreaming_log(title: str, content: str) -> KnowledgeItem:
    path = write_dreaming_log(title, content)
    item = KnowledgeItem(
        item_type="dreaming_log",
        title=title,
        file_path=str(path),
        confidence="medium_confidence",
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_daily_review(title: str, content: str) -> KnowledgeItem:
    path = write_daily_review(content, title=title)
    item = KnowledgeItem(
        item_type="daily_review",
        title=title,
        file_path=str(path),
        confidence="medium_confidence",
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_post_trade_review(
    title: str,
    content: str,
    metadata: dict | None = None,
    strategy_id: str | None = None,
    confidence: str = "medium_confidence",
) -> KnowledgeItem:
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    path = write_post_trade_review(title, content, metadata)
    item = KnowledgeItem(
        item_type="post_trade_review",
        title=title,
        file_path=str(path),
        strategy_id=strategy_id,
        confidence=confidence,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
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
    _index(item, content)
    return item


def _unindex(item_id: str) -> None:
    """Best-effort removal from the FTS index (archived/deprecated → not recalled)."""
    try:
        from aletheia.knowledge.retrieval import remove_from_index
        remove_from_index(item_id)
    except Exception:
        pass


def archive_item(item_id: str) -> None:
    """Archive a KB item — soft removal, not deletion."""
    update_knowledge_item(item_id, status="archived")
    _unindex(item_id)


def deprecate_item(item_id: str) -> None:
    """Mark a KB item as deprecated — it stays but loses active relevance."""
    update_knowledge_item(item_id, status="deprecated", confidence="deprecated")
    _unindex(item_id)


def delete_item(item_id: str) -> None:
    """Hard delete is NOT allowed. Raises an error."""
    raise PermissionError("Hard deletion of knowledge items is not allowed. Use archive_item() or deprecate_item() instead.")


def detect_contradictions(items: list[dict]) -> list[dict]:
    """IMPLEMENTATION_STATUS: stub — returns empty for MVP.

    Callers must not present results as "no contradictions found" in the UI.
    Future: compare confidence levels and conflicting theses.
    """
    import logging
    logging.getLogger("aletheia.knowledge").debug("detect_contradictions called (stub, returns [])")
    return []

"""Knowledge Base manager — handles lifecycle of KB items (no hard delete)."""

import hashlib
import logging

from aletheia.storage.models import KnowledgeItem
from aletheia.storage.repositories import (
    insert_knowledge_item, update_knowledge_item, list_knowledge_items,
    knowledge_hash_exists, knowledge_title_exists_within,
)

logger = logging.getLogger("aletheia.knowledge.manager")

_MIN_CONTENT_LENGTH = 80
_PLACEHOLDER_PATTERNS = [
    "Content here",
    "(LLM unavailable",
    "Synthesized 3 research notes",
    "LLM unavailable for synthesis",
]


def _content_hash(content: str) -> str:
    """SHA-256 prefix (16 hex chars) of stripped content."""
    return hashlib.sha256(content.strip().encode()).hexdigest()[:16]


def _is_quality_pass(content: str) -> bool:
    """Reject placeholder or near-empty content."""
    if len(content.strip()) < _MIN_CONTENT_LENGTH:
        return False
    return not any(p in content for p in _PLACEHOLDER_PATTERNS)
from aletheia.files.markdown import (
    write_daily_review,
    write_dreaming_log,
    write_guard_candidate,
    write_post_trade_review,
    write_research_note,
    write_strategy_doc,
    write_trade_case_study,
    write_trading_guard,
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
        # Extract outcome from frontmatter for structured ranker scoring (Phase 1a).
        outcome = ""
        try:
            from aletheia.knowledge.frontmatter import parse_frontmatter_text
            meta, _ = parse_frontmatter_text(content)
            outcome = (meta.get("outcome") or "") if meta else ""
            if not outcome and getattr(item, "file_path", None):
                from pathlib import Path
                path = Path(item.file_path)
                if path.exists():
                    file_meta, _ = parse_frontmatter_text(
                        path.read_text(encoding="utf-8", errors="replace")
                    )
                    outcome = (file_meta.get("outcome") or "") if file_meta else ""
        except Exception:
            pass
        index_knowledge_item(item, content, outcome=outcome)
    except Exception as e:
        logger.warning("_index failed for %s: %s", getattr(item, "id", "?"), e)


def create_research_note(title: str, content: str, confidence: str = "medium_confidence", metadata: dict | None = None) -> KnowledgeItem | None:
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    if not _is_quality_pass(content):
        logger.info("knowledge_skipped:quality_gate item_type=research_note len=%d", len(content))
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("research_note", ch):
        logger.info("knowledge_skipped:duplicate item_type=research_note hash=%s", ch)
        return None
    path = write_research_note(title, content, metadata)
    item = KnowledgeItem(
        item_type="research_note",
        title=title,
        file_path=str(path),
        confidence=confidence,
        content_hash=ch,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_dreaming_log(title: str, content: str) -> KnowledgeItem | None:
    if not _is_quality_pass(content):
        logger.info("knowledge_skipped:quality_gate item_type=dreaming_log len=%d", len(content))
        return None
    if knowledge_title_exists_within("dreaming_log", title, hours=6):
        logger.info("knowledge_skipped:title_window item_type=dreaming_log title=%s", title)
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("dreaming_log", ch):
        logger.info("knowledge_skipped:duplicate item_type=dreaming_log hash=%s", ch)
        return None
    path = write_dreaming_log(title, content)
    item = KnowledgeItem(
        item_type="dreaming_log",
        title=title,
        file_path=str(path),
        confidence="high_confidence",
        content_hash=ch,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_daily_review(title: str, content: str) -> KnowledgeItem | None:
    if not _is_quality_pass(content):
        logger.warning("knowledge_skipped:quality_gate item_type=daily_review len=%d", len(content.strip()))
        return None
    path = write_daily_review(content, title=title)
    item = KnowledgeItem(
        item_type="daily_review",
        title=title,
        file_path=str(path),
        confidence="high_confidence",
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
    tags: list[str] | None = None,
) -> KnowledgeItem | None:
    import json as _json
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    if not _is_quality_pass(content):
        logger.info("knowledge_skipped:quality_gate item_type=post_trade_review len=%d", len(content))
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("post_trade_review", ch):
        logger.info("knowledge_skipped:duplicate item_type=post_trade_review hash=%s", ch)
        return None
    path = write_post_trade_review(title, content, metadata)
    item = KnowledgeItem(
        item_type="post_trade_review",
        title=title,
        file_path=str(path),
        strategy_id=strategy_id,
        confidence=confidence,
        content_hash=ch,
        tags=_json.dumps(tags or [], ensure_ascii=False),
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
        confidence="high_confidence",
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_trade_case_study(
    title: str,
    content: str,
    metadata: dict | None = None,
    strategy_id: str | None = None,
    confidence: str = "high_confidence",
    tags: list[str] | None = None,
) -> KnowledgeItem | None:
    """Create a structured trade case study from a closed position."""
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    if not _is_quality_pass(content):
        # WARNING (not INFO): quality gate failures indicate a content-generation
        # problem (too short, placeholder) — distinguishable from normal dedup.
        logger.warning("knowledge_skipped:quality_gate item_type=trade_case_study len=%d title=%r", len(content), title)
        return None
    ch = _content_hash(content)
    if knowledge_hash_exists("trade_case_study", ch):
        logger.info("knowledge_skipped:duplicate item_type=trade_case_study hash=%s", ch)
        return None
    path = write_trade_case_study(title, content, metadata)
    item = KnowledgeItem(
        item_type="trade_case_study",
        title=title,
        file_path=str(path),
        strategy_id=strategy_id,
        confidence=confidence,
        content_hash=ch,
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _index(item, content)
    return item


def create_trading_guard(
    title: str,
    content: str,
    metadata: dict | None = None,
    confidence: str = "high_confidence",
    tags: list[str] | None = None,
) -> KnowledgeItem:
    """Create an active trading guard (seed or human-confirmed)."""
    if confidence not in VALID_CONFIDENCES:
        raise ValueError(f"Invalid confidence: {confidence}")
    path = write_trading_guard(title, content, metadata)
    item = KnowledgeItem(
        item_type="trading_guard",
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


def create_guard_candidate(
    title: str,
    content: str,
    metadata: dict | None = None,
    strategy_id: str | None = None,
    tags: list[str] | None = None,
) -> KnowledgeItem:
    """Create a guard candidate (medium_confidence, not retrievable)."""
    path = write_guard_candidate(title, content, metadata)
    item = KnowledgeItem(
        item_type="guard_candidate",
        title=title,
        file_path=str(path),
        strategy_id=strategy_id,
        confidence="medium_confidence",
    )
    try:
        insert_knowledge_item(item)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    # guard_candidate is medium_confidence → _index skips it (quality gate)
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

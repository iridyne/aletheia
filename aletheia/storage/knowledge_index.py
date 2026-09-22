"""Core Knowledge FTS maintenance; optional retrieval owns advisory scoring."""

import logging
import re

from aletheia.storage.sqlite import get_db
from aletheia.files.knowledge_document import canonicalize_written_document, read_knowledge_document

logger = logging.getLogger("aletheia.retrieval")
_ALLOWED_CONFIDENCE = ("high_confidence",)


def _fts_table_exists(db) -> bool:
    try:
        row = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_fts'"
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _clean_body(text: str, *, title: str | None = None) -> str:
    """Strip only the canonical leading metadata/header prefix from Markdown."""
    from aletheia.files.frontmatter import parse_frontmatter_text

    has_leading_frontmatter_candidate = text.startswith("---\n") or re.match(
        r"#[^\n]*\n+(?=---\n)", text
    ) is not None
    _metadata, body = parse_frontmatter_text(text)
    if body != text:
        return body.strip()

    if has_leading_frontmatter_candidate:
        logger.warning(
            "knowledge FTS frontmatter parse failed; preserving Markdown body"
        )
        return text.strip()

    # Legacy files without frontmatter still have a generated title. Remove it
    # only when it matches the item's title; body-level H1 headings are prose.
    lines = text.splitlines()
    if title and lines and lines[0].strip() == f"# {title}":
        return "\n".join(lines[1:]).strip()
    return text.strip()


def index_knowledge_item(
    item,
    body: str,
    outcome: str = "",
    *,
    db=None,
    strict: bool = False,
) -> bool:
    """Add or refresh an item's searchable text in the FTS index.

    Returns ``False`` for an absent FTS table or best-effort failure. Migration
    callers pass ``strict=True`` so a failed one-time rebuild fails closed.
    """
    try:
        connection = db or get_db()
        if not _fts_table_exists(connection):
            return False
        already_in_tx = connection.in_transaction
        # Replace any prior row for this item (idempotent re-index).
        connection.execute("DELETE FROM knowledge_fts WHERE item_id = ?", (item.id,))
        connection.execute(
            "INSERT INTO knowledge_fts (title, body, item_type, item_id, confidence, created_at, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item.title or "", _clean_body(body or "", title=item.title), item.item_type, item.id,
             item.confidence, getattr(item, "created_at", "") or "", outcome or ""),
        )
        if not already_in_tx:
            connection.commit()
        return True
    except Exception as e:
        if strict:
            raise
        logger.warning("index_knowledge_item failed for %s: %s", getattr(item, "id", "?"), e)
        return False


def remove_from_index(item_id: str) -> None:
    """Drop an item from the index (archive/deprecate → no longer recalled). Never raises."""
    try:
        db = get_db()
        if not _fts_table_exists(db):
            return
        db.execute("DELETE FROM knowledge_fts WHERE item_id = ?", (item_id,))
        if not db.in_transaction:
            db.commit()
    except Exception as e:
        logger.warning("remove_from_index failed for %s: %s", item_id, e)


def reindex_all(*, db=None, strict: bool = False) -> int:
    """Backfill the index from existing high_confidence + active knowledge items.

    Reads each item's Markdown body from disk and returns the indexed count.
    Normal maintenance is best-effort; migrations pass ``strict=True``.
    """
    try:
        connection = db or get_db()
        if not _fts_table_exists(connection):
            return 0
        from aletheia.storage.models import KnowledgeItem
        already_in_tx = connection.in_transaction
        if not already_in_tx:
            connection.execute("BEGIN")
        try:
            connection.execute("DELETE FROM knowledge_fts")
            count = 0
            skipped_empty = 0
            rows = connection.execute(
                "SELECT * FROM knowledge_items "
                "WHERE status = 'active' OR status IS NULL LIMIT 100000"
            )
            for raw in rows:
                row = dict(raw)
                if row.get("confidence") not in _ALLOWED_CONFIDENCE:
                    continue
                document = read_knowledge_document(row)
                if not document.text.strip() or document.state == "integrity_conflict":
                    skipped_empty += 1
                    continue
                if (
                    "content_format" in row
                    and document.state == "legacy_current_root"
                    and document.resolved_path is not None
                ):
                    canonical = canonicalize_written_document(document.resolved_path)
                    connection.execute(
                        "UPDATE knowledge_items SET file_path=?, content=?, "
                        "content_format=?, content_sha256=?, metadata_json=?, symbol=?, "
                        "direction=?, scanner_family=?, outcome=? WHERE id=?",
                        (
                            canonical["file_path"],
                            canonical["content"],
                            canonical["content_format"],
                            canonical["content_sha256"],
                            canonical["metadata_json"],
                            canonical["symbol"],
                            canonical["direction"],
                            canonical["scanner_family"],
                            canonical["outcome"],
                            row["id"],
                        ),
                    )
                    row.update(canonical)
                    document = read_knowledge_document(row)
                body = document.text
                item = KnowledgeItem(
                    **{k: row[k] for k in row if k in KnowledgeItem.model_fields}
                )
                outcome = str(row.get("outcome") or document.metadata.get("outcome") or "")
                if index_knowledge_item(
                    item,
                    body,
                    outcome=outcome,
                    db=connection,
                    strict=strict,
                ):
                    count += 1
            if skipped_empty:
                logger.warning(
                    "reindex_all: skipped %d items with empty body "
                    "(file missing or deleted — these items are unrecoverable from FTS)",
                    skipped_empty,
                )
            if not already_in_tx:
                connection.commit()
        except Exception:
            if not already_in_tx:
                connection.rollback()
            raise
        return count
    except Exception as e:
        if strict:
            raise
        logger.warning("reindex_all failed: %s", e)
        return 0


def _index(item, content: str) -> None:
    """Best-effort full-text index of a canonical Knowledge document."""
    try:
        if item.confidence != "high_confidence":
            return
        index_knowledge_item(item, item.content or content, outcome=item.outcome or "")
    except Exception as e:
        logging.getLogger("aletheia.knowledge.manager").warning("_index failed for %s: %s", getattr(item, "id", "?"), e)



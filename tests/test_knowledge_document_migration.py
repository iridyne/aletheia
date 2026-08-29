from __future__ import annotations

import sqlite3


def test_knowledge_document_migration_is_additive_and_idempotent():
    from aletheia.storage.migrations import _apply_knowledge_document_v51

    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE knowledge_items ("
        "id TEXT PRIMARY KEY, item_type TEXT, status TEXT, confidence TEXT)"
    )

    _apply_knowledge_document_v51(connection)
    _apply_knowledge_document_v51(connection)

    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(knowledge_items)")
    }
    assert {
        "content_format",
        "content_sha256",
        "metadata_json",
        "symbol",
        "direction",
        "scanner_family",
        "outcome",
    } <= columns
    indexes = {
        row[1] for row in connection.execute("PRAGMA index_list(knowledge_items)")
    }
    assert "idx_knowledge_structured_retrieval" in indexes

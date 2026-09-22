from __future__ import annotations

from tests.test_experience_loop import setup_env


def test_writer_persists_one_canonical_relative_document(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import CONTENT_FORMAT, content_sha256
    from extensions.knowledge.manager import create_research_note

    item = create_research_note(
        "Canonical provenance",
        "A sufficiently detailed evidence note for canonical Knowledge persistence "
        "with explicit source context and an independently checkable outcome record.",
        confidence="high_confidence",
        metadata={
            "symbol": "BTC/USDT",
            "direction": "LONG",
            "scanner_family": "fast-trigger",
            "outcome": "Win",
        },
    )

    assert item is not None
    assert item.file_path is not None
    assert item.file_path.startswith("knowledge/")
    assert not item.file_path.startswith("/")
    assert item.content_format == CONTENT_FORMAT
    assert item.content_sha256 == content_sha256(item.content or "")
    assert item.symbol == "BTC/USDT"
    assert item.direction == "long"
    assert item.scanner_family == "fast_trigger"
    assert item.outcome == "win"

def test_repository_canonicalizes_inline_knowledge_writes(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import CONTENT_FORMAT, read_knowledge_document
    from aletheia.storage.models import KnowledgeItem
    from aletheia.storage.repositories import get_knowledge_item, insert_knowledge_item

    item = KnowledgeItem(
        item_type="operator_assessment",
        title="Inline assessment",
        content="A database-only assessment with explicit advisory evidence.",
        confidence="high_confidence",
    )
    insert_knowledge_item(item)
    row = get_knowledge_item(item.id)

    assert row is not None
    assert row["content_format"] == CONTENT_FORMAT
    assert read_knowledge_document(row).state == "available_inline"

def test_yaml_timestamp_metadata_has_stable_json_provenance(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import (
        canonicalize_written_document,
        read_knowledge_document,
    )

    path = tmp_path / "knowledge" / "research_notes" / "timestamp.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nobserved_at: 2026-08-29T13:00:00Z\n---\nTimestamp evidence.",
        encoding="utf-8",
    )
    canonical = canonicalize_written_document(path)
    document = read_knowledge_document(canonical)

    assert document.state == "available"
    assert str(document.metadata["observed_at"]) == "2026-08-29 13:00:00+00:00"


def test_foreign_absolute_path_never_becomes_read_authority(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import read_knowledge_document

    foreign = tmp_path.parent / "foreign" / "knowledge" / "research_notes" / "item.md"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("foreign runtime body", encoding="utf-8")

    unavailable = read_knowledge_document({"file_path": str(foreign), "content": None})
    assert unavailable.state == "unavailable"
    assert unavailable.text == ""

    current = tmp_path / "knowledge" / "research_notes" / "item.md"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text("current restored body", encoding="utf-8")
    restored = read_knowledge_document({"file_path": str(foreign), "content": None})
    assert restored.state == "legacy_current_root"
    assert restored.text == "current restored body"

def test_legacy_path_uses_knowledge_segment_below_data_root(tmp_path, monkeypatch):
    data_root = tmp_path / "knowledge" / "runtime"
    setup_env(data_root, monkeypatch)
    from aletheia.files.knowledge_document import resolve_knowledge_path

    current = data_root / "knowledge" / "research_notes" / "item.md"
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text("current body", encoding="utf-8")

    assert resolve_knowledge_path(str(current)) == current


def test_tampered_file_is_not_retrieved(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import read_knowledge_document
    from extensions.knowledge.manager import create_research_note
    from extensions.knowledge.retrieval import retrieve_lessons
    from aletheia.storage.repositories import get_knowledge_item

    item = create_research_note(
        "Tamper sentinel",
        "Tamper sentinel evidence remains long enough to pass the quality gate cleanly "
        "and retains explicit source context for an independently checkable record.",
        confidence="high_confidence",
    )
    assert item is not None
    assert item.file_path is not None
    path = tmp_path / item.file_path
    path.write_text("changed outside the canonical write path", encoding="utf-8")

    row = get_knowledge_item(item.id)
    assert row is not None
    assert read_knowledge_document(row).state == "integrity_conflict"
    assert retrieve_lessons("Tamper sentinel evidence", k=3) == []
    path.write_text("", encoding="utf-8")
    assert read_knowledge_document(row).state == "integrity_conflict"

def test_invalid_metadata_snapshot_is_an_integrity_conflict(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import (
        canonicalize_written_document,
        read_knowledge_document,
    )

    path = tmp_path / "knowledge" / "research_notes" / "metadata.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nsymbol: BTC/USDT\n---\nMetadata evidence.", encoding="utf-8")
    canonical = canonicalize_written_document(path)
    canonical["metadata_json"] = "not-json"

    assert read_knowledge_document(canonical).state == "integrity_conflict"
    canonical = canonicalize_written_document(path)
    canonical["symbol"] = "ETH/USDT"
    assert read_knowledge_document(canonical).state == "integrity_conflict"


def test_old_schema_reindex_does_not_write_v51_columns(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    import sqlite3

    from aletheia.storage.knowledge_index import reindex_all

    path = tmp_path / "knowledge" / "research_notes" / "legacy.md"
    path.write_text("# Legacy\n\nLegacy body", encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(
        "CREATE TABLE knowledge_items (id TEXT PRIMARY KEY, item_type TEXT, "
        "title TEXT, file_path TEXT, confidence TEXT, status TEXT, content TEXT, "
        "created_at TEXT)"
    )
    db.execute(
        "CREATE VIRTUAL TABLE knowledge_fts USING fts5("
        "title, body, item_type UNINDEXED, item_id UNINDEXED, confidence UNINDEXED, "
        "created_at UNINDEXED, outcome UNINDEXED)"
    )
    db.execute(
        "INSERT INTO knowledge_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy",
            "research_note",
            "Legacy",
            str(path),
            "high_confidence",
            "active",
            None,
            "2026-01-01T00:00:00+00:00",
        ),
    )

    assert reindex_all(db=db, strict=True) == 1


def test_structured_filters_apply_before_candidate_limit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from extensions.knowledge.manager import create_research_note
    from extensions.knowledge.retrieval import retrieve_lessons

    for index in range(60):
        assert create_research_note(
            f"Generic candidate {index}",
            f"common needle retrieval evidence generic candidate number {index} remains "
            "detailed with explicit source context and an independently checkable record.",
            confidence="high_confidence",
            metadata={"symbol": "ETH/USDT", "direction": "short", "scanner_family": "slow"},
        ) is not None
    target = create_research_note(
        "Exact scoped candidate",
        "common needle retrieval evidence exact scoped candidate remains detailed with "
        "explicit source context and an independently checkable outcome record.",
        confidence="high_confidence",
        metadata={"symbol": "BTC/USDT", "direction": "long", "scanner_family": "fast"},
    )
    assert target is not None

    hits = retrieve_lessons(
        "common needle retrieval evidence",
        k=1,
        symbol="BTC/USDT",
        direction="long",
        scanner_family="fast",
    )
    assert [hit.item_id for hit in hits] == [target.id]

def test_result_limit_applies_after_recency_scoring(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from extensions.knowledge.manager import create_research_note
    from extensions.knowledge.retrieval import retrieve_lessons
    from aletheia.storage.sqlite import get_db

    old = create_research_note(
        "Old exact match",
        ("needle " * 12) + "old exact evidence remains independently checkable.",
        confidence="high_confidence",
    )
    recent = create_research_note(
        "Recent match",
        "needle recent evidence remains independently checkable and sufficiently detailed.",
        confidence="high_confidence",
    )
    assert old is not None and recent is not None
    db = get_db()
    db.execute(
        "UPDATE knowledge_items SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?",
        (old.id,),
    )
    db.execute(
        "UPDATE knowledge_fts SET created_at='2020-01-01T00:00:00+00:00' WHERE item_id=?",
        (old.id,),
    )
    db.commit()

    top_one = retrieve_lessons("needle", k=1)
    top_two = retrieve_lessons("needle", k=2)
    assert top_one and top_two
    assert top_one[0].item_id == top_two[0].item_id == recent.id

def test_unknown_content_format_fails_closed(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import read_knowledge_document
    from extensions.knowledge.manager import create_research_note
    from aletheia.storage.repositories import get_knowledge_item

    item = create_research_note(
        "Unsupported format",
        "A sufficiently detailed canonical note with independently checkable evidence "
        "and explicit source context that remains stable across runtime verification.",
        confidence="high_confidence",
    )
    assert item is not None
    row = get_knowledge_item(item.id)
    assert row is not None
    row["content_format"] = "knowledge-markdown/v2"
    assert read_knowledge_document(row).state == "integrity_conflict"


def test_retrieval_snippet_comes_from_verified_document(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from extensions.knowledge.manager import create_research_note
    from extensions.knowledge.retrieval import retrieve_lessons
    from aletheia.storage.sqlite import get_db

    item = create_research_note(
        "Verified snippet",
        "needle verified canonical evidence remains independently checkable and detailed.",
        confidence="high_confidence",
    )
    assert item is not None
    db = get_db()
    db.execute(
        "UPDATE knowledge_fts SET body='needle unverified injected text' WHERE item_id=?",
        (item.id,),
    )
    db.commit()
    hits = retrieve_lessons("needle", k=1)
    assert hits
    assert "verified canonical evidence" in hits[0].snippet
    assert "unverified injected" not in hits[0].snippet

def test_legacy_dual_write_mismatch_is_an_integrity_conflict(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import read_knowledge_document
    from extensions.knowledge.manager import create_research_note
    from aletheia.storage.repositories import get_knowledge_item

    item = create_research_note(
        "Legacy mismatch",
        "Original evidence remains sufficiently detailed and independently checkable "
        "with explicit source context and a stable recorded outcome.",
        confidence="high_confidence",
    )
    assert item is not None and item.file_path is not None
    row = get_knowledge_item(item.id)
    assert row is not None
    row.update(
        content_format=None,
        content_sha256=None,
        metadata_json=None,
        symbol=None,
        direction=None,
        scanner_family=None,
        outcome=None,
    )
    path = tmp_path / item.file_path
    path.write_text("Changed evidence from a different source.", encoding="utf-8")
    assert read_knowledge_document(row).state == "integrity_conflict"


def test_legacy_frontmatter_conflict_is_not_backfilled(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.files.knowledge_document import read_knowledge_document
    from extensions.knowledge.maintenance import backfill_content_from_disk
    from extensions.knowledge.manager import create_research_note
    from aletheia.storage.repositories import get_knowledge_item
    from aletheia.storage.sqlite import get_db

    item = create_research_note(
        "Legacy provenance conflict",
        "The same evidence body remains sufficiently detailed and independently "
        "checkable while its structured source identity conflicts.",
        confidence="high_confidence",
        metadata={"symbol": "BTC/USDT"},
    )
    assert item is not None and item.file_path is not None
    row = get_knowledge_item(item.id)
    assert row is not None
    inline = str(row["content"]).replace("BTC/USDT", "ETH/USDT", 1)
    db = get_db()
    db.execute(
        "UPDATE knowledge_items SET content=?, content_format=NULL, "
        "content_sha256=NULL, metadata_json=NULL WHERE id=?",
        (inline, item.id),
    )
    db.commit()
    legacy = get_knowledge_item(item.id)
    assert legacy is not None
    assert read_knowledge_document(legacy).state == "integrity_conflict"
    assert backfill_content_from_disk(dry_run=True)["matched"] == 0

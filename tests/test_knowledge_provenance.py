from __future__ import annotations

from tests.test_experience_loop import setup_env


def test_writer_persists_one_canonical_relative_document(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.document import CONTENT_FORMAT, content_sha256
    from aletheia.knowledge.manager import create_research_note

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
    assert item.symbol == "BTCUSDT"
    assert item.direction == "long"
    assert item.scanner_family == "fast_trigger"
    assert item.outcome == "win"

def test_yaml_timestamp_metadata_has_stable_json_provenance(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.document import (
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
    from aletheia.knowledge.document import read_knowledge_document

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


def test_tampered_file_is_not_retrieved(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.document import read_knowledge_document
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons
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


def test_structured_filters_apply_before_candidate_limit(tmp_path, monkeypatch):
    setup_env(tmp_path, monkeypatch)
    from aletheia.knowledge.manager import create_research_note
    from aletheia.knowledge.retrieval import retrieve_lessons

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

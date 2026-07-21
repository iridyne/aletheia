from __future__ import annotations

import json
import sqlite3

import pytest

from aletheia.storage import sqlite as sqlite_store
from aletheia.storage.event_store import EventStore, make_event
from aletheia.storage.migrations import run_migrations


@pytest.fixture
def event_store_env(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    monkeypatch.setattr(loader, "CONFIG_PATH", tmp_path / "config.yaml")
    sqlite_store.reset_db()
    run_migrations()
    try:
        yield EventStore(), sqlite_store.get_db(), tmp_path
    finally:
        sqlite_store.reset_db()


def _mirrored_events(root):
    paths = list((root / "events").glob("event_log_*.jsonl"))
    assert len(paths) == 1
    return [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]


def test_append_idempotency_returns_persisted_event_without_duplicate_mirror(event_store_env):
    store, db, root = event_store_env

    first = store.append(
        make_event(
            "EXAMPLE_CREATED",
            "example",
            "example-1",
            payload={"attempt": 1},
            idempotency_key="example-created-1",
        )
    )
    replay = store.append(
        make_event(
            "EXAMPLE_CREATED",
            "example",
            "example-1",
            payload={"attempt": 2},
            idempotency_key="example-created-1",
        )
    )

    assert replay.event_id == first.event_id
    assert replay.payload_json == first.payload_json
    assert db.execute(
        "SELECT COUNT(*) FROM ps_event_log WHERE idempotency_key=?",
        ("example-created-1",),
    ).fetchone()[0] == 1
    mirrored = _mirrored_events(root)
    assert [event["event_id"] for event in mirrored] == [first.event_id]


def test_append_many_deduplicates_db_results_and_jsonl(event_store_env):
    store, db, root = event_store_env

    persisted = store.append_many(
        [
            make_event(
                "EXAMPLE_BATCHED",
                "example",
                "example-2",
                payload={"attempt": 1},
                idempotency_key="example-batched-1",
            ),
            make_event(
                "EXAMPLE_BATCHED",
                "example",
                "example-2",
                payload={"attempt": 2},
                idempotency_key="example-batched-1",
            ),
        ]
    )

    assert persisted[0].event_id == persisted[1].event_id
    assert db.execute(
        "SELECT COUNT(*) FROM ps_event_log WHERE idempotency_key=?",
        ("example-batched-1",),
    ).fetchone()[0] == 1
    assert len(_mirrored_events(root)) == 1


def test_non_idempotency_integrity_error_is_not_mirrored_as_success(event_store_env):
    store, db, root = event_store_env
    original = store.append(make_event("EXAMPLE_CREATED", "example", "example-3"))
    duplicate = make_event("EXAMPLE_CHANGED", "example", "example-3")
    duplicate.event_id = original.event_id

    with pytest.raises(sqlite3.IntegrityError):
        store.append(duplicate)

    assert db.execute(
        "SELECT COUNT(*) FROM ps_event_log WHERE event_id=?",
        (original.event_id,),
    ).fetchone()[0] == 1
    mirrored = _mirrored_events(root)
    assert [event["event_id"] for event in mirrored] == [original.event_id]

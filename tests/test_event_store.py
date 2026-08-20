"""Regression tests for EventStore idempotency and JSONL consistency."""

import json
import sqlite3

import pytest

from aletheia.storage.event_store import EventStore, make_event


def _store() -> EventStore:
    return EventStore()


def _jsonl_lines(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# append — single-event idempotency
# ---------------------------------------------------------------------------


def test_append_idempotency_returns_existing_event(tmp_path, monkeypatch):
    """Repeating the same idempotency_key returns the original event_id, not a new one."""
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    ev1 = store.append(make_event("test.thing", "thing", "a1", idempotency_key="idem-1"))
    ev2 = store.append(make_event("test.thing", "thing", "a1", idempotency_key="idem-1"))

    assert ev1.event_id == ev2.event_id


def test_append_idempotency_leaves_one_db_row(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    store.append(make_event("test.thing", "thing", "a1", idempotency_key="idem-1"))
    store.append(make_event("test.thing", "thing", "a1", idempotency_key="idem-1"))

    count = get_db().execute(
        "SELECT count(*) FROM ps_event_log WHERE idempotency_key = ?", ("idem-1",)
    ).fetchone()[0]
    assert count == 1


def test_append_idempotency_leaves_one_jsonl_line(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    ev = store.append(make_event("test.thing", "thing", "a1", idempotency_key="idem-1"))
    store.append(make_event("test.thing", "thing", "a1", idempotency_key="idem-1"))

    events_dir = tmp_path / "events"
    jsonl_files = list(events_dir.glob("event_log_*.jsonl"))
    assert len(jsonl_files) == 1
    lines = _jsonl_lines(jsonl_files[0])
    assert len(lines) == 1
    assert lines[0]["event_id"] == ev.event_id


# ---------------------------------------------------------------------------
# append_many — batch idempotency
# ---------------------------------------------------------------------------


def test_append_many_idempotency_returns_existing_events(tmp_path, monkeypatch):
    """append_many with duplicate idempotency_key returns the original event_ids."""
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    batch1 = store.append_many([
        make_event("test.a", "thing", "x", idempotency_key="k-a"),
        make_event("test.b", "thing", "x", idempotency_key="k-b"),
    ])
    batch2 = store.append_many([
        make_event("test.a", "thing", "x", idempotency_key="k-a"),
        make_event("test.b", "thing", "x", idempotency_key="k-b"),
    ])

    assert batch1[0].event_id == batch2[0].event_id
    assert batch1[1].event_id == batch2[1].event_id


def test_append_many_idempotency_leaves_one_db_row_per_key(tmp_path, monkeypatch):
    import aletheia.config.loader as loader
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    store.append_many([make_event("test.a", "thing", "x", idempotency_key="k-a")])
    store.append_many([make_event("test.a", "thing", "x", idempotency_key="k-a")])

    count = get_db().execute(
        "SELECT count(*) FROM ps_event_log WHERE idempotency_key = ?", ("k-a",)
    ).fetchone()[0]
    assert count == 1


def test_append_many_idempotency_leaves_one_jsonl_line_per_key(tmp_path, monkeypatch):
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    store.append_many([
        make_event("test.a", "thing", "x", idempotency_key="k-a"),
        make_event("test.b", "thing", "x", idempotency_key="k-b"),
    ])
    store.append_many([
        make_event("test.a", "thing", "x", idempotency_key="k-a"),
        make_event("test.b", "thing", "x", idempotency_key="k-b"),
    ])

    events_dir = tmp_path / "events"
    lines = _jsonl_lines(list(events_dir.glob("event_log_*.jsonl"))[0])
    assert len(lines) == 2  # one per key, not four


# ---------------------------------------------------------------------------
# IntegrityError propagation — no idempotency_key
# ---------------------------------------------------------------------------


def test_duplicate_event_id_without_idempotency_key_raises(tmp_path, monkeypatch):
    """A duplicate event_id with no idempotency_key propagates IntegrityError."""
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    ev = store.append(make_event("test.thing", "thing", "a1"))
    duplicate = make_event("test.thing", "thing", "a1")
    duplicate.event_id = ev.event_id  # force PK collision, no idempotency_key

    with pytest.raises(sqlite3.IntegrityError):
        store.append(duplicate)


def test_duplicate_event_id_without_idempotency_key_does_not_write_jsonl(
    tmp_path, monkeypatch
):
    """IntegrityError path must not produce a JSONL mirror entry."""
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    store.append(make_event("test.thing", "thing", "a1"))
    events_dir = tmp_path / "events"
    lines_before = _jsonl_lines(list(events_dir.glob("event_log_*.jsonl"))[0])

    ev2 = make_event("test.thing", "thing", "a1")
    ev2.event_id = lines_before[0]["event_id"]

    with pytest.raises(sqlite3.IntegrityError):
        store.append(ev2)

    lines_after = _jsonl_lines(list(events_dir.glob("event_log_*.jsonl"))[0])
    assert len(lines_after) == len(lines_before)


# ---------------------------------------------------------------------------
# IntegrityError propagation — WITH idempotency_key on a non-idempotency
# constraint violation (regression for #1114)
# ---------------------------------------------------------------------------


def test_duplicate_event_id_with_distinct_idempotency_key_raises(tmp_path, monkeypatch):
    """PK collision must propagate even when the candidate has a fresh idempotency_key."""
    import aletheia.config.loader as loader
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    ev_a = store.append(make_event("test.thing", "thing", "a1"))

    ev_b = make_event("test.thing", "thing", "a1", idempotency_key="new-idem-key")
    ev_b.event_id = ev_a.event_id  # force PK collision with a fresh idempotency_key

    with pytest.raises(sqlite3.IntegrityError):
        store.append(ev_b)

    # No row must exist for the never-persisted idempotency_key
    row = get_db().execute(
        "SELECT event_id FROM ps_event_log WHERE idempotency_key = ?", ("new-idem-key",)
    ).fetchone()
    assert row is None


def test_duplicate_event_id_with_distinct_idempotency_key_does_not_write_jsonl(
    tmp_path, monkeypatch
):
    """No JSONL entry for a candidate whose PK collision was re-raised."""
    import aletheia.config.loader as loader

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    ev_a = store.append(make_event("test.thing", "thing", "a1"))
    events_dir = tmp_path / "events"
    lines_before = _jsonl_lines(list(events_dir.glob("event_log_*.jsonl"))[0])

    ev_b = make_event("test.thing", "thing", "a1", idempotency_key="new-idem-key")
    ev_b.event_id = ev_a.event_id

    with pytest.raises(sqlite3.IntegrityError):
        store.append(ev_b)

    lines_after = _jsonl_lines(list(events_dir.glob("event_log_*.jsonl"))[0])
    assert len(lines_after) == len(lines_before)


def test_append_many_duplicate_event_id_with_distinct_idempotency_key_raises(
    tmp_path, monkeypatch
):
    """append_many: PK collision propagates even with a fresh idempotency_key."""
    import aletheia.config.loader as loader
    from aletheia.storage.sqlite import get_db

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)

    store = _store()
    ev_a = store.append(make_event("test.thing", "thing", "a1"))

    ev_b = make_event("test.thing", "thing", "a1", idempotency_key="many-idem-key")
    ev_b.event_id = ev_a.event_id

    with pytest.raises(sqlite3.IntegrityError):
        store.append_many([ev_b])

    row = get_db().execute(
        "SELECT event_id FROM ps_event_log WHERE idempotency_key = ?", ("many-idem-key",)
    ).fetchone()
    assert row is None

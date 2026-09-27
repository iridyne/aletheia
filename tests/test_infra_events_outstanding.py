"""Outstanding infra-event projection: non-evicting critical registry, exact
matching ``resolves`` suppression, durable reconstruction hooks, and the WS
fingerprint's resolution sensitivity.

All events here are process-local in-memory buffers, so every test starts from a
clean slate via the autouse fixture (which also resets ``_resolution_seq``).
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _clean_infra_events():
    from aletheia.infra.events import clear_infra_events

    clear_infra_events()
    yield
    clear_infra_events()


def _emit(**kwargs):
    from aletheia.infra.events import emit_infra_event

    return emit_infra_event(**kwargs)


def test_critical_unresolved_survives_100_plus_event_pressure():
    from aletheia.infra.events import list_outstanding_infra_events

    _emit(severity="critical", source="s", code="c1", message="critical incident", entity_id="e1")
    for i in range(150):
        _emit(severity="info", source="s", code=f"i{i}", message="noise", entity_id="e-noise")

    outstanding = list_outstanding_infra_events(limit=500)
    codes = {event["code"] for event in outstanding}
    assert "c1" in codes
    # The registry keeps it even though the 100-slot deque evicted it (c1 emitted first).
    from aletheia.infra.events import list_infra_events

    assert all(event["code"] != "c1" for event in list_infra_events(limit=200))


def test_critical_cleared_only_by_resolves():
    from aletheia.infra.events import list_outstanding_infra_events

    _emit(severity="critical", source="s", code="c1", message="critical incident", entity_id="e1")
    for i in range(150):
        _emit(severity="info", source="s", code=f"i{i}", message="noise", entity_id="e-noise")

    outstanding_codes = {e["code"] for e in list_outstanding_infra_events(limit=500)}
    assert "c1" in outstanding_codes

    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="c1 is over",
        entity_id="e1",
        resolves="c1",
    )

    outstanding_codes = {e["code"] for e in list_outstanding_infra_events(limit=500)}
    assert "c1" not in outstanding_codes


def test_resolves_requires_exact_entity_id():
    from aletheia.infra.events import list_outstanding_infra_events

    _emit(severity="critical", source="s", code="c1", message="venue_a incident", entity_id="venue_a")
    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="wrong venue",
        entity_id="venue_b",
        resolves="c1",
    )

    codes = {e["code"] for e in list_outstanding_infra_events(limit=50)}
    assert "c1" in codes


def test_none_entity_id_is_not_a_wildcard():
    from aletheia.infra.events import list_outstanding_infra_events

    _emit(severity="critical", source="s", code="c1", message="venue_a incident", entity_id="venue_a")
    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="unspecified entity",
        entity_id=None,
        resolves="c1",
    )

    codes = {e["code"] for e in list_outstanding_infra_events(limit=50)}
    assert "c1" in codes


def test_code_matching_is_exact_no_prefix():
    from aletheia.infra.events import list_outstanding_infra_events

    _emit(severity="critical", source="s", code="foo:bar", message="incident", entity_id="e")
    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="prefix only",
        entity_id="e",
        resolves="foo",
    )

    codes = {e["code"] for e in list_outstanding_infra_events(limit=50)}
    assert "foo:bar" in codes


def test_duplicate_code_keeps_only_latest():
    from aletheia.infra.events import list_outstanding_infra_events

    _emit(severity="info", source="s", code="X", message="old", entity_id="e")
    _emit(severity="info", source="s", code="X", message="new", entity_id="e")

    xs = [e for e in list_outstanding_infra_events(limit=50) if e["code"] == "X"]
    assert len(xs) == 1
    assert xs[0]["message"] == "new"


def test_list_infra_events_unchanged():
    from aletheia.infra.events import list_infra_events

    _emit(severity="info", source="s", code="a", message="first", entity_id="e")
    _emit(severity="warning", source="s", code="b", message="second", entity_id="e")

    raw = list_infra_events(limit=10)
    # Newest first, unchanged raw-deque semantics.
    assert [e["code"] for e in raw] == ["b", "a"]
    # The sole additive change is the (backward-compatible) `resolves` field.
    assert raw[0]["resolves"] is None

    warnings = list_infra_events(limit=10, min_severity="warning")
    assert [e["code"] for e in warnings] == ["b"]


def test_clear_infra_events_clears_registry_and_resets_seq():
    from aletheia.infra.events import (
        clear_infra_events,
        list_outstanding_infra_events,
        resolution_seq,
    )

    _emit(severity="critical", source="s", code="c1", message="incident", entity_id="e1")
    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="clear it",
        entity_id="e1",
        resolves="c1",
    )
    assert resolution_seq() == 1

    clear_infra_events()

    assert list_outstanding_infra_events(limit=50) == []
    assert resolution_seq() == 0


def test_critical_registry_cap_logs_and_does_not_grow_unbounded(caplog):
    from aletheia.infra.events import (
        _MAX_CRITICAL_UNRESOLVED,
        _critical_unresolved,
        list_infra_events,
    )

    caplog.set_level(logging.CRITICAL, logger="aletheia.infra.events")
    total = _MAX_CRITICAL_UNRESOLVED + 1
    for i in range(total):
        _emit(severity="critical", source="s", code=f"c{i}", message="incident", entity_id="e")

    # The registry never grows past its cap and drops the overflow (newest) entry...
    assert len(_critical_unresolved) == _MAX_CRITICAL_UNRESOLVED
    assert f"s:c{total - 1}:e" not in _critical_unresolved
    # ...but the overflow event is still appended to the raw deque history.
    raw = list_infra_events(limit=1000)
    assert len(raw) == 100
    assert raw[0]["code"] == f"c{total - 1}"
    # ...and a critical log line records the overflow.
    assert any(record.levelno == logging.CRITICAL for record in caplog.records)


def test_resolution_seq_increments_on_actual_deletion_only():
    from aletheia.infra.events import resolution_seq

    seq_before = resolution_seq()
    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="nothing to resolve",
        entity_id="e",
        resolves="never_emitted",
    )
    assert resolution_seq() == seq_before

    _emit(severity="critical", source="s", code="never_emitted", message="incident", entity_id="e")
    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="now it resolves",
        entity_id="e",
        resolves="never_emitted",
    )
    assert resolution_seq() == seq_before + 1


def test_upsert_critical_unresolved_is_idempotent():
    from aletheia.infra.events import (
        list_infra_events,
        list_outstanding_infra_events,
        resolution_seq,
        upsert_critical_unresolved,
    )

    before = len(list_infra_events(limit=1000))

    upsert_critical_unresolved("s", "c", "e", "message one")
    upsert_critical_unresolved("s", "c", "e", "message two")

    entries = [e for e in list_outstanding_infra_events(limit=50) if e["code"] == "c"]
    assert len(entries) == 1
    assert entries[0]["message"] == "message two"
    assert resolution_seq() == 0
    assert len(list_infra_events(limit=1000)) == before


def test_ws_fingerprint_changes_on_info_severity_resolution():
    from aletheia.server.routes import ws

    _emit(severity="critical", source="s", code="c1", message="incident", entity_id="e1")
    before = ws._get_latest_infra_ts()

    _emit(
        severity="info",
        source="s",
        code="resolved",
        message="info-severity resolution",
        entity_id="e1",
        resolves="c1",
    )
    after = ws._get_latest_infra_ts()

    assert before != after

"""Focused proof that the cash-flow ledger is exhaustible and keyset-stable."""

from __future__ import annotations

from aletheia.storage.models import CapitalEvent
from aletheia.storage import repositories as repo


def test_capital_events_page_exposes_every_retained_event():
    for index in range(3):
        repo.insert_capital_event(CapitalEvent(
            id=f"capital-{index}",
            broker_id="paper_internal",
            event_type="deposit",
            amount_usdt=float(index + 1),
            detected_at="2026-08-21T00:00:00+00:00",
        ))

    first = repo.list_capital_events_page(limit=2)
    assert first["total"] == 3
    assert first["has_more"] is True
    assert [event["id"] for event in first["events"]] == ["capital-2", "capital-1"]

    cursor = first["next_cursor"]
    assert cursor is not None
    second = repo.list_capital_events_page(
        limit=2,
        before_detected_at=cursor["detected_at"],
        before_id=cursor["id"],
    )
    assert second["total"] == 3
    assert second["has_more"] is False
    assert [event["id"] for event in second["events"]] == ["capital-0"]
    assert [event["id"] for event in first["events"] + second["events"]] == [
        "capital-2", "capital-1", "capital-0",
    ]


def test_capital_events_page_rejects_partial_cursor():
    try:
        repo.list_capital_events_page(limit=2, before_id="capital-1")
    except ValueError as exc:
        assert "provided together" in str(exc)
    else:
        raise AssertionError("partial keyset cursor must fail closed")

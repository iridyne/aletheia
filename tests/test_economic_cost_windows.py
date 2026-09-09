"""Runtime cost accounting window boundaries."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aletheia.economics.schema import ensure_economics_schema
from aletheia.storage.sqlite import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_known_llm_cost(
    *,
    event_id: str,
    cost: float,
    run_id: str | None = None,
    metadata: dict | None = None,
    started_at: str | None = None,
) -> None:
    now = started_at or _now()
    ensure_economics_schema()
    get_db().execute(
        """INSERT INTO runtime_cost_events (
            id,event_type,run_id,call_id,provider,model_or_tool,attempt_index,status,
            started_at,finished_at,usage_json,estimated_cost_usd,currency,cost_status,
            metadata_json,created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id,
            "llm",
            run_id,
            event_id,
            "test",
            "model",
            0,
            "success",
            now,
            now,
            "{}",
            cost,
            "USD",
            "estimated",
            json.dumps(metadata or {}),
            now,
        ),
    )


@pytest.mark.parametrize("run_id", [None, "boundary-run"])
def test_cost_summary_uses_half_open_adjacent_windows(run_id):
    from aletheia.economics.tracing import list_cost_summary

    left_start = "2026-08-23T11:00:00+00:00"
    boundary = "2026-08-23T12:00:00+00:00"
    right_end = "2026-08-23T13:00:00+00:00"
    for event_id, started_at in (
        ("left-cost", left_start),
        ("boundary-cost", boundary),
        ("right-cost", "2026-08-23T12:00:01+00:00"),
    ):
        _insert_known_llm_cost(
            event_id=event_id,
            cost=1.0,
            run_id=run_id,
            started_at=started_at,
        )

    left = list_cost_summary(
        window_start=left_start,
        window_end=boundary,
        run_id=run_id,
    )
    right = list_cost_summary(
        window_start=boundary,
        window_end=right_end,
        run_id=run_id,
    )

    assert {row["id"] for row in left["events"]} == {"left-cost"}
    assert {row["id"] for row in right["events"]} == {
        "boundary-cost",
        "right-cost",
    }
    assert left["known_cost_subtotal_usd"] + right["known_cost_subtotal_usd"] == 3.0



from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def collector_db(tmp_path, monkeypatch):
    from aletheia.config import loader
    from aletheia.storage import migrations
    from aletheia.storage import sqlite as sq

    monkeypatch.setattr(loader, "DATA_DIR", tmp_path)
    sq.close_db()
    migrations.run_migrations()
    yield
    sq.close_db()


def test_collector_health_keeps_one_durable_row_per_domain(collector_db):
    from aletheia.data.collector_health import record_collector_status
    from aletheia.storage.repositories import list_collector_health_states

    record_collector_status("macro", "success", tick=1, duration_ms=25)
    record_collector_status("macro", "empty", tick=6, duration_ms=12)

    rows = list_collector_health_states()
    assert len(rows) == 1
    assert rows[0]["domain"] == "macro"
    assert rows[0]["last_status"] == "empty"
    assert rows[0]["attempts"] == 2
    assert rows[0]["successes"] == 1
    assert rows[0]["empty_results"] == 1


def test_three_consecutive_collector_problems_emit_one_operator_warning(
    collector_db, monkeypatch
):
    from aletheia.data import collector_health
    from aletheia.infra.events import clear_infra_events, list_infra_events

    clear_infra_events()
    for tick in (1, 6, 11):
        collector_health.record_collector_status(
            "defillama", "failed", tick=tick
        )

    rows = collector_health.get_collector_health_report()["domains"]
    row = next(item for item in rows if item["domain"] == "defillama")
    assert row["consecutive_problems"] == 3
    warnings = [
        event
        for event in list_infra_events()
        if event["code"] == "collector_consecutive_problem"
    ]
    assert len(warnings) == 1
    assert warnings[0]["entity_id"] == "defillama"
    assert "3 consecutive" in warnings[0]["message"]


def test_skipped_budget_does_not_count_toward_consecutive_problems(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    for tick in (1, 6, 11):
        record_collector_status("defillama", "skipped_budget", tick=tick)

    rows = get_collector_health_report()["domains"]
    row = next(item for item in rows if item["domain"] == "defillama")
    assert row["consecutive_problems"] == 0
    assert row["degraded"] is False
    assert row["budget_skips"] == 3


def test_success_resets_consecutive_problem_counter(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    record_collector_status("derivatives", "timed_out", tick=1)
    record_collector_status("derivatives", "failed", tick=2)
    record_collector_status("derivatives", "partial", tick=3)

    row = next(
        item
        for item in get_collector_health_report()["domains"]
        if item["domain"] == "derivatives"
    )
    assert row["consecutive_problems"] == 0
    assert row["last_status"] == "partial"


def test_collector_report_marks_overdue_domain_degraded(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    record_collector_status(
        "microstructure",
        "success",
        tick=1,
        occurred_at=(now - timedelta(minutes=20)).isoformat(),
    )

    report = get_collector_health_report(now=now)
    row = next(item for item in report["domains"] if item["domain"] == "microstructure")
    assert row["overdue"] is True
    assert report["status"] == "degraded"


def test_result_classifier_distinguishes_empty_partial_and_not_configured():
    from aletheia.data.collector_health import classify_collector_result

    assert classify_collector_result({"snapshots": [], "errors": []}) == "empty"
    assert classify_collector_result({"snapshots": [object()], "errors": []}) == "success"
    assert classify_collector_result({"snapshots": [object()], "errors": ["one"]}) == "partial"
    assert classify_collector_result({"snapshots": [], "errors": ["one"]}) == "failed"
    assert classify_collector_result({"errors": ["no_key"]}) == "not_configured"

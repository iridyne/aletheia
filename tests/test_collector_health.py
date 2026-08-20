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

    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.get_status",
        lambda: {"external_connectivity": {"state": "reachable"}},
    )
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


def test_connectivity_dependent_collector_problem_is_informational(
    collector_db, monkeypatch
):
    from aletheia.data import collector_health
    from aletheia.infra.events import clear_infra_events, list_infra_events

    monkeypatch.setattr(
        "aletheia.agent_runtime.connectivity.get_status",
        lambda: {"external_connectivity": {"state": "unavailable"}},
    )
    clear_infra_events()
    for tick in (1, 6, 11):
        collector_health.record_collector_status("defillama", "failed", tick=tick)

    events = [
        event
        for event in list_infra_events()
        if event["code"] == "collector_problem_dependent_connectivity"
    ]
    assert len(events) == 1
    assert events[0]["severity"] == "info"
    assert events[0]["metadata"]["dependent_on"] == "external_connectivity"


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


def test_report_attributes_cross_source_failures_to_external_connectivity(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    occurred_at = (now - timedelta(seconds=20)).isoformat()
    record_collector_status("microstructure", "failed", tick=1, occurred_at=occurred_at)
    record_collector_status("coingecko_top", "timed_out", tick=1, occurred_at=occurred_at)

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "unavailable",
            "severity": "warning",
            "priority": 1,
            "observed_at": (now - timedelta(seconds=5)).timestamp(),
            "outage_started_at": (now - timedelta(seconds=30)).timestamp(),
            "last_recovery_at": None,
            "stale_after_sec": 180,
        },
    )

    incident = report["infrastructure_incident"]
    assert incident["code"] == "external_connectivity_unavailable"
    assert incident["status"] == "active"
    assert incident["priority"] == 1
    assert incident["affected_domains"] == ["microstructure", "coingecko_top"]
    affected = {
        row["domain"]: row
        for row in report["domains"]
        if row["domain"] in incident["affected_domains"]
    }
    assert {row["display_status"] for row in affected.values()} == {
        "blocked_by_connectivity"
    }
    assert {row["attributed_cause"] for row in affected.values()} == {
        "external_connectivity_unavailable"
    }
    assert {row["display_severity"] for row in affected.values()} == {"info"}


def test_report_keeps_collector_warning_when_external_network_is_reachable(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    occurred_at = (now - timedelta(seconds=20)).isoformat()
    record_collector_status("microstructure", "failed", tick=1, occurred_at=occurred_at)
    record_collector_status("derivatives", "failed", tick=1, occurred_at=occurred_at)

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "reachable",
            "severity": "ok",
            "priority": 4,
            "observed_at": (now - timedelta(seconds=5)).timestamp(),
            "outage_started_at": None,
            "last_recovery_at": None,
            "stale_after_sec": 180,
        },
    )

    assert report["infrastructure_incident"] is None
    rows = {
        row["domain"]: row
        for row in report["domains"]
        if row["domain"] in {"microstructure", "derivatives"}
    }
    assert {row["display_status"] for row in rows.values()} == {"failed"}
    assert {row["attributed_cause"] for row in rows.values()} == {None}
    assert {row["display_severity"] for row in rows.values()} == {"warning"}


def test_report_does_not_attribute_failures_that_predate_current_outage(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    old_failure = (now - timedelta(hours=1)).isoformat()
    record_collector_status("microstructure", "failed", tick=1, occurred_at=old_failure)
    record_collector_status("coingecko_top", "timed_out", tick=1, occurred_at=old_failure)

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "unavailable",
            "severity": "warning",
            "priority": 1,
            "observed_at": (now - timedelta(seconds=5)).timestamp(),
            "outage_started_at": (now - timedelta(seconds=30)).timestamp(),
            "last_recovery_at": None,
            "stale_after_sec": 180,
        },
    )

    assert report["infrastructure_incident"]["code"] == "external_connectivity_unavailable"
    assert report["infrastructure_incident"]["affected_domains"] == []


def test_options_structure_success_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "success", tick=1, duration_ms=1500)
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "success"
    assert row["successes"] == 1
    assert row["display_severity"] == "ok"


def test_options_structure_empty_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "empty", tick=1)
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "empty"
    assert row["empty_results"] == 1


def test_options_structure_timeout_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "timed_out", tick=1, error_code="asyncio_timeout")
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "timed_out"
    assert row["timeouts"] == 1
    assert row["display_severity"] == "warning"


def test_options_structure_disabled_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status("options_structure", "disabled", tick=1)
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "disabled"
    assert row["display_severity"] == "muted"


def test_options_structure_persist_failure_appears_in_health_report(collector_db):
    from aletheia.data.collector_health import get_collector_health_report, record_collector_status

    record_collector_status(
        "options_structure", "failed", tick=1,
        error_code="options_structure_persist_failed",
    )
    row = next(r for r in get_collector_health_report()["domains"] if r["domain"] == "options_structure")
    assert row["last_status"] == "failed"
    assert row["failures"] == 1
    assert row["last_error_code"] == "options_structure_persist_failed"
    assert row["display_severity"] == "warning"


def test_report_keeps_pre_recovery_failures_under_one_recovery_notice(collector_db):
    from aletheia.data.collector_health import (
        get_collector_health_report,
        record_collector_status,
    )

    now = datetime.now(timezone.utc)
    failed_at = now - timedelta(minutes=2)
    recovered_at = now - timedelta(seconds=30)
    record_collector_status(
        "coingecko_top", "timed_out", tick=1, occurred_at=failed_at.isoformat()
    )
    record_collector_status(
        "macro", "timed_out", tick=1, occurred_at=failed_at.isoformat()
    )

    report = get_collector_health_report(
        now=now,
        connectivity_status={
            "state": "recovering",
            "severity": "info",
            "priority": 2,
            "observed_at": recovered_at.timestamp(),
            "outage_started_at": (now - timedelta(minutes=10)).timestamp(),
            "last_recovery_at": recovered_at.timestamp(),
            "stale_after_sec": 180,
        },
    )

    incident = report["infrastructure_incident"]
    assert incident["code"] == "external_connectivity_recovering"
    assert incident["status"] == "recovering"
    assert incident["affected_domains"] == ["coingecko_top", "macro"]
    rows = {
        row["domain"]: row
        for row in report["domains"]
        if row["domain"] in incident["affected_domains"]
    }
    assert {row["display_status"] for row in rows.values()} == {"waiting_for_retry"}
    assert {row["display_severity"] for row in rows.values()} == {"info"}
